"""The Python and TypeScript SDKs against a REAL running server: a socket, real HTTP, PostgreSQL with row-level security (ZID-INT-002; GID-063).

The SDK unit tests stand a fake transport in for the API. That cannot show that the SDK and the platform agree, so this starts the app on a local port and
makes the SDKs talk to it. Synthetic data only.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn
from admin_fixture import make_admin
from conftest import ctx, engine
from test_v2_facade_pg import ACTIONS, AUD, BODY, ISS, RESOURCES, uid
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ROOT = Path(__file__).resolve().parents[4]
WEBHOOK_ACTIONS = ["webhook.endpoint.manage", "webhook.endpoint.read"]
PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


@contextmanager
def live(
    rt_url: str, *, bind: str = "127.0.0.1", public: str = "127.0.0.1"
) -> Iterator[tuple[str, str]]:
    """One company with an administrator, the app served on a free local port. Yields (base URL, bearer token).

    `bind` and `public` let a container reach it: the Docker tests bind 0.0.0.0 and call it host.docker.internal for the few seconds they run."""
    tenant, admin = uid(), uid()
    ring = KeyRing(LocalSigner("issuer-sdk-live"))

    async def seed() -> str:
        sid, now = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await make_admin(
                c, tenant, [admin], [*ACTIONS, *WEBHOOK_ACTIONS], [*RESOURCES, "webhook_endpoint:*"]
            )
            principal, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(
                    sid, tenant, admin, now - timedelta(seconds=30), "native:webauthn", 2,
                    principal.security_revision, 1, SessionStatus.ACTIVE, now + timedelta(hours=8),
                ),
            )  # fmt: skip
        return sid

    sid = asyncio.run(seed())
    token = issue_access_token(
        ring, issuer=ISS, audience=AUD, subject=admin, tenant_id=tenant, client_id="admin-console",
        now=datetime.now(UTC), session_id=sid, scope=frozenset(),
    )  # fmt: skip
    with socket.socket() as s:
        s.bind((bind, 0))
        port = s.getsockname()[1]
    base = f"http://{public}:{port}"
    # the public base URL is what the platform puts in the links it returns (the upload URL), so it must be the address the SDK really uses
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=base, environment="test", upload_retention_days=30),
        keyring=ring,
    )  # fmt: skip

    async def public_dns(
        host: str,
    ) -> list[str]:  # a documentation address: the webhook guard refuses anything private
        return ["93.184.216.34"]

    app.state.webhook_resolver = public_dns
    server = uvicorn.Server(
        uvicorn.Config(app, host=bind, port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(150):
            if server.started:
                break
            time.sleep(0.1)
        assert server.started, "the test server did not start"
        yield base, token
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_the_python_sdk_agrees_with_the_platform_over_real_http(rt_url: str) -> None:
    sys.path.insert(0, str(ROOT / "sdks" / "python" / "src"))
    from zoiko_credentials.v2 import V2Client, V2Error

    with live(rt_url) as (base, token):
        sdk = V2Client(base, token)

        assert sdk.capabilities()
        alternatives = sdk.capture_alternatives("US")
        assert (
            alternatives["counts_as_failed_attempt"] is False
            and alternatives["rejection_possible"] is False
        )
        assert sdk.coverage("US")

        first = sdk.verify_credential(
            BODY["credential_type"], BODY["issuer_claim"], BODY["credential_claim"], BODY["jurisdiction"],
            idempotency_key="sdk-live-0001",
        )  # fmt: skip
        ref = first["credential_ref"]
        assert first["dimensions"][0]["dimension"] == "professional_license"
        replay = sdk.verify_credential(
            BODY["credential_type"], BODY["issuer_claim"], BODY["credential_claim"], BODY["jurisdiction"],
            idempotency_key="sdk-live-0001",
        )  # fmt: skip
        assert replay["credential_ref"] == ref  # the same key is the same check
        assert sdk.credential(ref)["credential_ref"] == ref

        stored = sdk.upload_evidence(PDF, "application/pdf", idempotency_key="sdk-live-up-1")
        evidence_ref = stored["evidence_ref"]
        assert sdk.evidence_manifest(evidence_ref)["evidence_ref"] == evidence_ref
        assert sdk.delete_evidence(evidence_ref)

        hook = sdk.register_webhook(
            "https://hooks.example.test/zoiko",
            ["credential.status_changed"],
            idempotency_key="sdk-live-hook-1",
        )
        assert hook and sdk.webhook_endpoints()

        # a wrong token is a problem document the SDK turns into V2Error, never a crash
        with pytest.raises(V2Error) as bad:
            V2Client(base, "not-a-token").capabilities()
        assert bad.value.status == 401


def test_the_typescript_sdk_agrees_with_the_platform_over_real_http(
    rt_url: str, tmp_path: Path
) -> None:
    node, tsc = (
        shutil.which("node"),
        ROOT / "frontend" / "node_modules" / "typescript" / "bin" / "tsc",
    )
    if not node or not tsc.exists():
        pytest.skip("needs node and the frontend TypeScript compiler")
    sdk = ROOT / "sdks" / "typescript"
    built = subprocess.run(
        [node, str(tsc), "-p", str(sdk / "tsconfig.build.json"), "--outDir", str(tmp_path / "dist")],
        capture_output=True, text=True, cwd=sdk, timeout=240,
        check=False,
    )  # fmt: skip
    assert built.returncode == 0, built.stdout + built.stderr
    (tmp_path / "package.json").write_text('{"type": "module"}')
    script = tmp_path / "run.mjs"
    script.write_text(
        """
import { V2Client, V2Error } from "./dist/v2.js";
const [base, token, body] = process.argv.slice(2);
const b = JSON.parse(body);
const sdk = new V2Client(base, token);
const out = {};
out.capabilities = Object.keys(await sdk.capabilities()).length > 0;
const alt = await sdk.captureAlternatives("US");
out.alternatives = [alt.counts_as_failed_attempt, alt.rejection_possible];
const first = await sdk.verifyCredential(b.credential_type, b.issuer_claim, b.credential_claim, b.jurisdiction, "ts-live-0001");
const replay = await sdk.verifyCredential(b.credential_type, b.issuer_claim, b.credential_claim, b.jurisdiction, "ts-live-0001");
out.replaySame = first.credential_ref === replay.credential_ref;
out.read = (await sdk.credential(first.credential_ref)).credential_ref === first.credential_ref;
const stored = await sdk.uploadEvidence(new TextEncoder().encode("%PDF-1.4\\n1 0 obj\\n<< /Type /Catalog >>\\nendobj\\ntrailer\\n<< /Root 1 0 R >>\\n%%EOF\\n"), "application/pdf", "ts-live-up-1");
out.upload = (await sdk.evidenceManifest(stored.evidence_ref)).evidence_ref === stored.evidence_ref;
await sdk.deleteEvidence(stored.evidence_ref);
try { await new V2Client(base, "not-a-token").capabilities(); out.bad = "no error"; }
catch (e) { out.bad = e instanceof V2Error ? e.status : String(e); }
console.log(JSON.stringify(out));
"""
    )
    with live(rt_url) as (base, token):
        ran = subprocess.run(
            [node, str(script), base, token, json.dumps(BODY)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            check=False,
            timeout=120,
        )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    out = json.loads(ran.stdout.strip().splitlines()[-1])
    assert out == {
        "capabilities": True, "alternatives": [False, False], "replaySame": True, "read": True, "upload": True, "bad": 401,
    }, out  # fmt: skip


EXPECTED = {
    "capabilities": True,
    "alternatives": [False, False],
    "replaySame": True,
    "read": True,
    "upload": True,
    "bad": 401,
}
DOCKER = shutil.which("docker")


def image_present(image: str) -> bool:
    if not DOCKER:
        return False
    return (
        subprocess.run(
            [DOCKER, "image", "inspect", image], capture_output=True, check=False, timeout=60
        ).returncode
        == 0
    )


def docker_run(
    image: str,
    command: list[str],
    env: dict[str, str],
    mounts: dict[Path, str],
    workdir: str,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        DOCKER or "docker",
        "run",
        "--rm",
        "--add-host=host.docker.internal:host-gateway",
        "-w",
        workdir,
    ]
    for host, inside in mounts.items():
        cmd += ["-v", f"{host}:{inside}:ro"]
    for key, value in env.items():
        cmd += ["-e", f"{key}={value}"]
    return subprocess.run(
        [*cmd, image, *command], capture_output=True, text=True, timeout=timeout, check=False
    )


def last_json(run: subprocess.CompletedProcess[str]) -> dict:
    assert run.returncode == 0, run.stdout[-1500:] + run.stderr[-1500:]
    line = next(x for x in reversed(run.stdout.strip().splitlines()) if x.startswith("{"))
    return json.loads(line)


def test_the_go_sdk_agrees_with_the_platform_over_real_http(rt_url: str) -> None:
    image = "golang:1.23-alpine"
    if not image_present(image):
        pytest.skip(f"needs Docker and the {image} image")
    with live(rt_url, bind="0.0.0.0", public="host.docker.internal") as (base, token):
        ran = docker_run(
            image, ["go", "run", "./cmd/livecheck", base, token, json.dumps(BODY)],
            {"GOCACHE": "/tmp/gocache", "GOPATH": "/tmp/gopath", "GOFLAGS": "-buildvcs=false"},
            {ROOT / "sdks" / "go": "/src"}, "/src",
        )  # fmt: skip
    assert last_json(ran) == EXPECTED


def test_the_dotnet_sdk_agrees_with_the_platform_over_real_http(rt_url: str) -> None:
    image = "mcr.microsoft.com/dotnet/sdk:8.0"
    if not image_present(image):
        pytest.skip(f"needs Docker and the {image} image")
    with live(rt_url, bind="0.0.0.0", public="host.docker.internal") as (base, token):
        ran = docker_run(
            image, ["sh", "-c", 'cp -r /src /work && cd /work/live && dotnet run -- "$BASE" "$TOKEN" "$BODY"'],
            {"BASE": base, "TOKEN": token, "BODY": json.dumps(BODY), "DOTNET_NOLOGO": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "HOME": "/tmp"},
            {ROOT / "sdks" / "dotnet": "/src"}, "/tmp",
        )  # fmt: skip
    assert last_json(ran) == EXPECTED


def test_the_java_sdk_agrees_with_the_platform_over_real_http(rt_url: str) -> None:
    image = "maven:3.9-eclipse-temurin-21"
    if not image_present(image):
        pytest.skip(f"needs Docker and the {image} image")
    script = (
        "cp -r /src /work && cd /work && mvn -q -B dependency:copy-dependencies -DoutputDirectory=/tmp/lib >/dev/null && "
        "javac -cp '/tmp/lib/*' -d /tmp/out src/main/java/com/zoiko/id/*.java live/LiveCheck.java && "
        'java -cp \'/tmp/out:/tmp/lib/*\' LiveCheck "$BASE" "$TOKEN" "$BODY"'
    )
    with live(rt_url, bind="0.0.0.0", public="host.docker.internal") as (base, token):
        ran = docker_run(
            image, ["sh", "-c", script], {"BASE": base, "TOKEN": token, "BODY": json.dumps(BODY), "HOME": "/tmp"},
            {ROOT / "sdks" / "java": "/src"}, "/tmp",
        )  # fmt: skip
    assert last_json(ran) == EXPECTED

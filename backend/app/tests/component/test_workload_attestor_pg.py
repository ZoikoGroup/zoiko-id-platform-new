"""A real container, a real attestor, the real cell: the whole attestation path (CR-042), and a concurrent token check.

The container is started on the local Docker engine from an image already present on the machine; the test skips itself
where there is no engine or no such image."""

from __future__ import annotations

import asyncio
import json
import shutil
import statistics
import subprocess
import time
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from conftest import ctx, engine
from cryptography.hazmat.primitives.serialization import Encoding
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_workload_identity_pg import AUD, DOMAIN, ISS, POLICY, _ca, _svid, seed_agent
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.attestor import registration, service
from zid_platform.attestor.inspectors import DockerInspector
from zid_platform.main import create_app
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.machine.application import workload
from zid_platform.settings import Settings

IMAGE = "postgres:16-alpine"
ATTESTOR = LocalSigner("attestor-1", "Ed25519")


def _docker(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=60, check=True
    ).stdout.strip()


@pytest.fixture(scope="module")
def image_digest() -> str:
    if shutil.which("docker") is None:
        pytest.skip("no docker engine")
    try:
        return _docker("image", "inspect", "--format", "{{.Id}}", IMAGE)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        pytest.skip(f"{IMAGE} is not on this machine")


def _start(name: str) -> None:
    _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "--label",
        "team=payments",
        "--entrypoint",
        "sleep",
        IMAGE,
        "600",
    )


def _stop(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60, check=False)


def _world(rt_url: str, tmp_path: Path, digest: str, sid: str, tenant: str, agent_id: str):  # type: ignore[no-untyped-def]
    ca = _ca()
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(ca[0].public_bytes(Encoding.PEM))
    svid = _svid(ca, sid)
    xfcc = {
        "x-forwarded-client-cert": f'Cert="{urllib.parse.quote(svid.public_bytes(Encoding.PEM).decode())}"'
    }
    keys = tmp_path / "attestors.json"
    keys.write_text(json.dumps({"keys": [ATTESTOR.public_jwk()]}), encoding="utf-8")

    async def seed() -> None:
        configure_runtime(development_runtime("uk-1"))
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await seed_agent(c, tenant, agent_id)
            # the agent's approved release runs the container's real image, which is what the binding must name (CR-064)
            await c.execute(
                text(
                    "UPDATE agent.blueprint_release SET artifact_digest = :d WHERE blueprint_id = 'bp_pay'"
                ),
                {"d": digest},
            )
            await workload.register_binding(
                c,
                tenant,
                policy=POLICY,
                agent_principal_id=agent_id,
                spiffe_id=sid,
                blueprint_version="1.0.0",
                artifact_digest=digest,
                environment_id="prod",
                attestation_required=2,
                valid_to=datetime.now(UTC) + timedelta(days=1),
                actor=str(uuid.uuid4()),
            )

    asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            spiffe_trust_domain=DOMAIN,
            spiffe_bundle_files={DOMAIN: str(bundle)},
            mesh_trusted_proxies=("testclient",),
            attestor_keys_file=str(keys),
        ),
        keyring=KeyRing(LocalSigner("issuer-rt")),
    )
    return app, xfcc


def _entry(sid: str, digest: str, container: str) -> registration.Entry:
    return registration.parse(
        {
            "workloads": [
                {
                    "spiffe_id": sid,
                    "environment_id": "prod",
                    "selectors": {"docker.container": container, "docker.label.team": "payments"},
                    "approved_digests": [digest],
                }
            ]
        }
    )[0]


def test_a_running_container_is_attested_by_inspection_and_only_while_it_runs(
    rt_url: str, tmp_path: Path, image_digest: str
) -> None:
    tenant, agent_id = str(uuid.uuid4()), str(uuid.uuid4())
    sid = f"spiffe://{DOMAIN}/t/{tenant}/ns/payments/sa/real"
    name = f"zid-attest-{uuid.uuid4().hex[:8]}"
    app, xfcc = _world(rt_url, tmp_path, image_digest, sid, tenant, agent_id)
    body = {"environment_id": "prod", "artifact_digest": image_digest}
    inspector = DockerInspector()
    _start(name)
    try:
        with TestClient(app) as api:
            # nothing attested yet: the workload is refused however it is registered
            assert (
                api.post("/v1/machine/workload-token", json=body, headers=xfcc).status_code == 403
            )

            # the attestor looks at the engine, finds the approved image running under the registered name and label
            found, token = service.attest_once(
                ATTESTOR, _entry(sid, image_digest, name), inspector, name
            )
            assert found.level == 2 and token is not None, found
            sent = api.post("/v1/machine/attestations:verify", json={"statement": token})
            assert sent.status_code == 200 and sent.json()["level"] == 2, sent.text
            assert (
                api.post("/v1/machine/workload-token", json=body, headers=xfcc).status_code == 200
            )

            # a registration that approves a different artifact finds the same container is not running it: level 1 only
            wrong = _entry(sid, "sha256:" + "00" * 32, name)
            low, low_token = service.attest_once(ATTESTOR, wrong, inspector, name)
            assert low.level == 1 and low_token is not None

            # a name that is not the registered one earns nothing, whatever the workload says about itself
            nothing, none_token = service.attest_once(
                ATTESTOR, _entry(sid, image_digest, "payments-agent"), inspector, name
            )
            assert nothing.level == 0 and none_token is None

            # a short statement runs out: with the container gone the attestor has nothing to renew it with
            short_found, short_token = service.attest_once(
                ATTESTOR,
                _entry(sid, image_digest, name),
                inspector,
                name,
                lifetime=timedelta(seconds=3),
            )
            assert short_token is not None and short_found.level == 2
        _stop(name)
        gone, gone_token = service.attest_once(
            ATTESTOR, _entry(sid, image_digest, name), inspector, name
        )
        assert gone.level == 0 and gone_token is None and "could not inspect" in gone.why
    finally:
        _stop(name)


def test_concurrent_token_exchanges_are_served_and_measured(rt_url: str, tmp_path: Path) -> None:
    """MACH-26, as far as one process on a developer machine can show it: 20 workloads' worth of concurrent exchanges
    all succeed. The numbers are printed and recorded in tests/load/machine-token/RESULTS.md; the gate rate itself is a
    PERF-environment run."""
    tenant, agent_id = str(uuid.uuid4()), str(uuid.uuid4())
    sid = f"spiffe://{DOMAIN}/t/{tenant}/ns/payments/sa/load"
    digest = "sha256:" + "ab" * 32
    app, xfcc = _world(rt_url, tmp_path, digest, sid, tenant, agent_id)
    now = datetime.now(UTC)
    from zid_crypto import sign_jws

    statement = sign_jws(
        ATTESTOR,
        {
            "sub": sid,
            "environment_id": "prod",
            "artifact_digest": digest,
            "level": 2,
            "iat": int(now.timestamp()) - 5,
            "exp": int(now.timestamp()) + 1800,
        },
        typ="attestation+jwt",
    )
    body = {"environment_id": "prod", "artifact_digest": digest}
    total, workers = 400, 20

    async def go() -> tuple[list[float], int, float]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, client=("testclient", 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://cell.test") as c:
                ok = await c.post("/v1/machine/attestations:verify", json={"statement": statement})
                assert ok.status_code == 200, ok.text
                walls: list[float] = []
                failed = 0
                queue: asyncio.Queue[int] = asyncio.Queue()
                for i in range(total):
                    queue.put_nowait(i)

                async def worker() -> None:
                    nonlocal failed
                    while True:
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            return
                        t0 = time.perf_counter()
                        r = await c.post("/v1/machine/workload-token", json=body, headers=xfcc)
                        walls.append((time.perf_counter() - t0) * 1000)
                        failed += r.status_code != 200

                started = time.perf_counter()
                await asyncio.gather(*(worker() for _ in range(workers)))
                return walls, failed, time.perf_counter() - started

    walls, failed, elapsed = asyncio.run(go())
    walls.sort()
    print(
        "CONCURRENT_TOKEN_EXCHANGE",
        f"n={total} concurrency={workers} failed={failed} rate={total / elapsed:.0f}/s "
        f"p50={statistics.median(walls):.0f}ms p95={walls[int(total * 0.95)]:.0f}ms "
        f"p99={walls[int(total * 0.99)]:.0f}ms max={walls[-1]:.0f}ms",
    )
    assert failed == 0

"""A build pipeline's whole path, with nothing mocked on the platform side, and step-up ENFORCED as in production (CR-066).

The company registers an application for the pipeline (Applications); the pipeline signs in with its private key (OAuth client credentials, a signed
assertion) and holds one grant; `register_build.py` registers a build, which then waits for a person. This is the path the console's "Let your build
pipeline register builds" guide describes.

The second half is the point of doing it narrowly: the same signed-key client, given every other machine grant there is, is still refused everything else,
and a person (or anything that is not that client) gets no exemption."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, Onboarded, onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tools" / "scripts"))
import register_build
from zid_machine_client import MachineClient

DIGEST = "sha256:" + "e" * 64
OTHER = "sha256:" + "f" * 64
SCOPE = "machine.build:register"  # a build pipeline's client
DECISIONS_SCOPE = "access:evaluate"  # the other machine client an application can have
ALL_MACHINE_GRANTS = [
    "machine.agent.read",
    "machine.build.register",
    "machine.build.manage",
    "machine.tool.manage",
    "machine.blueprint.manage",
]
RESOURCES = ["build:*", "tool:*", "blueprint:*"]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _key_pair(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    key = ec.generate_private_key(ec.SECP256R1())
    n = key.private_numbers()
    pub = {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64(n.public_numbers.x.to_bytes(32, "big")),
        "y": _b64(n.public_numbers.y.to_bytes(32, "big")),
        "kid": "ci-1",
    }
    path = tmp_path / "pipeline.jwk"
    path.write_text(
        json.dumps({**pub, "d": _b64(n.private_value.to_bytes(32, "big"))}), encoding="utf-8"
    )
    return path, pub


@dataclass
class Pipeline:
    api: TestClient
    o: Onboarded
    owner: dict[str, str]
    client_id: str
    principal: str
    key_file: Path
    scope: str = SCOPE

    def headers(self) -> dict[str, str]:
        """What the pipeline itself sends: a token it earned by signing in with its key."""
        return MachineClient(ISS, self.client_id, self.key_file, self.scope, self.api).headers()


@contextmanager
def pipeline(
    rt_url: str, tmp_path: Path, *, grants: list[str], purpose: str = "BUILD_PIPELINE"
) -> Iterator[Pipeline]:
    o = onboard(
        rt_url,
        extra_actions=["machine.agent.read", "machine.build.register"],
        extra_resources=["build:*"],
    )
    key_file, public = _key_pair(tmp_path)
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            step_up_enforcement="enforce",
        ),
        keyring=o.ring,
    )
    owner = o.bearer(o.owner)
    with TestClient(app, base_url=ISS) as api:
        made = api.post(
            "/v1/integration/oauth-clients",
            json={
                "name": "Invoice bot pipeline",
                "client_type": "CONFIDENTIAL",
                "purpose": purpose,
                "jwks": {"keys": [public]},
            },
            headers=owner,
        )
        assert made.status_code == 201, made.text
        client_id = made.json()["client_id"]

        async def seed() -> str:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
                row = (
                    await c.execute(
                        text(
                            "SELECT service_principal_id FROM authn.oauth_client WHERE client_id = :c"
                        ),
                        {"c": client_id},
                    )
                ).one()
                principal = str(row.service_principal_id)
                if grants:
                    await make_admin(c, o.tenant, [principal], grants, RESOURCES, release=False)
                return principal

        yield Pipeline(
            api,
            o,
            owner,
            client_id,
            asyncio.run(seed()),
            key_file,
            SCOPE if purpose == "BUILD_PIPELINE" else DECISIONS_SCOPE,
        )


def _run(p: Pipeline, *extra: str, digest: str = DIGEST) -> int:
    return register_build.main(
        [
            "--api-base",
            ISS,
            "--client-id",
            p.client_id,
            "--key",
            str(p.key_file),
            "--name",
            "Invoice bot 1.4",
            "--digest",
            digest,
            "--source",
            "ghcr.io/acme/invoice-bot:1.4",
            "--provenance",
            "https://ci.example.com/run/1",
            *extra,
        ],
        http=p.api,
    )


def test_a_pipeline_signs_in_with_its_own_key_and_registers_a_build_that_waits_for_a_person(
    rt_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pipeline(rt_url, tmp_path, grants=["machine.agent.read", "machine.build.register"]) as p:
        assert _run(p) == 0, capsys.readouterr().err
        out = capsys.readouterr()
        assert "waiting for a person to approve" in out.out

        (build,) = p.api.get("/v1/machine/builds", headers=p.owner).json()["items"]
        assert (
            build["artifact_digest"] == DIGEST
            and build["source_ref"] == "ghcr.io/acme/invoice-bot:1.4"
        )
        assert (
            build["registered_by"] == p.principal
            and build["review_status"] == "DRAFT"
            and build["approved_by"] is None
        )
        assert build["provenance_ref"] == "https://ci.example.com/run/1"
        assert (
            json.loads(p.key_file.read_text(encoding="utf-8"))["d"] not in out.out + out.err
        )  # the private key is never printed

        # a re-run of the same build changes nothing and is not an error when asked not to be
        assert _run(p, "--allow-existing") == 0
        assert len(p.api.get("/v1/machine/builds", headers=p.owner).json()["items"]) == 1


def test_given_every_other_machine_grant_the_same_client_still_cannot_do_anything_else(
    rt_url: str, tmp_path: Path
) -> None:
    """The exemption is for registering a build and nothing else: not approving or revoking it, not registering a tool, not drafting a release."""
    with pipeline(rt_url, tmp_path, grants=ALL_MACHINE_GRANTS) as p:
        h = p.headers()
        made = p.api.post(
            "/v1/machine/builds", headers=h, json={"name": "Nightly", "artifact_digest": DIGEST}
        )
        assert made.status_code == 201, made.text  # what it may do
        bid = made.json()["build_id"]

        tool = {
            "tool_id": "pay-invoice",
            "version": "1",
            "server_principal_id": p.principal,
            "canonical_action": "payments.invoice.pay",
            "default_risk_class": 2,
            "side_effect": "REVERSIBLE",
        }
        blueprint = {
            "blueprint_id": "invoice-bot",
            "version": "1.0.0",
            "artifact_digest": DIGEST,
            "tool_manifest_digest": OTHER,
            "publisher_principal_id": p.principal,
        }
        refused = {
            "register a tool": p.api.post("/v1/machine/tools", headers=h, json=tool),
            "draft a release": p.api.post("/v1/machine/blueprints", headers=h, json=blueprint),
            "approve its own build": p.api.post(f"/v1/machine/builds/{bid}:approve", headers=h),
            "revoke a build": p.api.post(
                f"/v1/machine/builds/{bid}:revoke", headers=h, json={"reason_code": "TEST"}
            ),
        }
        for what, res in refused.items():
            assert res.status_code == 403, (what, res.status_code, res.text)
        listed = p.api.get("/v1/machine/builds", headers=p.owner).json()["items"]
        assert [b["review_status"] for b in listed] == ["DRAFT"]  # nothing was approved or revoked


def test_a_pipeline_without_the_grant_is_refused(rt_url: str, tmp_path: Path) -> None:
    """Being a signed-key client is not authority: the grant still decides."""
    with pipeline(rt_url, tmp_path, grants=["machine.agent.read"]) as p:
        res = p.api.post(
            "/v1/machine/builds",
            headers=p.headers(),
            json={"name": "Nope", "artifact_digest": DIGEST},
        )
        assert res.status_code == 403, res.text
        assert p.api.get("/v1/machine/builds", headers=p.owner).json()["items"] == []


def test_people_get_no_exemption_and_a_borrowed_client_name_is_not_a_key(
    rt_url: str, tmp_path: Path
) -> None:
    """A person registering a build still has to give a fresh passkey proof, and a token that merely NAMES a service client (without being it) gets nothing."""
    with pipeline(rt_url, tmp_path, grants=["machine.agent.read", "machine.build.register"]) as p:
        body = {"name": "By a person", "artifact_digest": OTHER}
        person = p.api.post(
            "/v1/machine/builds", headers=p.o.bearer(p.o.second), json=body
        )  # a person with the grant, but no step-up proof
        assert person.status_code == 403 and person.json()["error"]["code"] == "STEP_UP_REQUIRED", (
            person.text
        )
        # a token for a person that names the pipeline's client id: the client's record says it is not that person's identity
        borrowed = p.o.bearer(p.o.second, client=p.client_id)
        assert p.api.post("/v1/machine/builds", headers=borrowed, json=body).status_code == 403
        assert p.api.get("/v1/machine/builds", headers=p.owner).json()["items"] == []


def test_a_client_made_for_decisions_cannot_use_the_exemption_even_with_the_grant(
    rt_url: str, tmp_path: Path
) -> None:
    """Only a client made for build pipelines is trusted to register without a passkey. An application's decision client is a machine client too, and
    may hold the same grant, but it does not carry the pipeline scope, so it is refused like any caller with no passkey."""
    with pipeline(
        rt_url,
        tmp_path,
        grants=["machine.agent.read", "machine.build.register"],
        purpose="DECISIONS",
    ) as p:
        res = p.api.post(
            "/v1/machine/builds",
            headers=p.headers(),
            json={"name": "Wrong kind of client", "artifact_digest": DIGEST},
        )
        assert res.status_code == 403, res.text
        assert p.api.get("/v1/machine/builds", headers=p.owner).json()["items"] == []

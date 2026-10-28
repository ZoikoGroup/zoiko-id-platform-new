"""The attestation level behind a workload token is verified evidence, never the requester's own claim (CR-042)."""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import ctx, engine
from cryptography.hazmat.primitives.serialization import Encoding
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_workload_identity_pg import (
    AUD,
    DIGEST,
    DOMAIN,
    ISS,
    POLICY,
    _ca,
    _svid,
    seed_agent,
    uid,
)
from zid_crypto import KeyRing, LocalSigner, sign_jws
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.machine.application import workload
from zid_platform.settings import Settings

ATTESTOR = LocalSigner("attestor-1", "Ed25519")


def statement(
    sid: str,
    *,
    level: int = 2,
    minutes: int = 30,
    digest: str = DIGEST,
    env: str = "prod",
    signer: LocalSigner = ATTESTOR,
) -> str:
    now = datetime.now(UTC)
    return sign_jws(
        signer,
        {
            "sub": sid,
            "environment_id": env,
            "artifact_digest": digest,
            "level": level,
            "iat": int((now - timedelta(seconds=5)).timestamp()),
            "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        },
        typ="attestation+jwt",
    )


def test_a_workload_that_needs_attestation_cannot_claim_it(rt_url: str, tmp_path: Path) -> None:
    tenant, agent_id, admin = uid(), uid(), uid()
    sid = f"spiffe://{DOMAIN}/t/{tenant}/ns/payments/sa/attested"
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
            await workload.register_binding(
                c,
                tenant,
                policy=POLICY,
                agent_principal_id=agent_id,
                spiffe_id=sid,
                blueprint_version="1.0.0",
                artifact_digest=DIGEST,
                environment_id="prod",
                attestation_required=2,  # this workload may only act with a verified artifact attestation
                valid_to=datetime.now(UTC) + timedelta(days=1),
                actor=admin,
            )

    asyncio.run(seed())

    def app_with(attestors: Path | None) -> TestClient:
        return TestClient(
            create_app(
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
                    attestor_keys_file=str(attestors) if attestors else None,
                ),
                keyring=KeyRing(LocalSigner("issuer-att")),
            )
        )

    claim = {"environment_id": "prod", "artifact_digest": DIGEST, "attestation": 2}

    # no attestor keys configured: the verify endpoint does not exist, and a claim alone earns nothing
    with app_with(None) as bare:
        assert (
            bare.post("/v1/machine/attestations:verify", json={"statement": "x" * 30}).status_code
            == 404
        )
        refused = bare.post("/v1/machine/workload-token", json=claim, headers=xfcc)
        assert refused.status_code == 403

    with app_with(keys) as api:
        # the requester says it is attested: ignored
        assert api.post("/v1/machine/workload-token", json=claim, headers=xfcc).status_code == 403

        # a statement signed by someone who is not an approved attestor is refused and changes nothing
        rogue = LocalSigner("attestor-1", "Ed25519")
        bad = api.post(
            "/v1/machine/attestations:verify", json={"statement": statement(sid, signer=rogue)}
        )
        assert (
            bad.status_code == 400
            and bad.json()["error"]["code"] == "ATTESTATION_SIGNATURE_INVALID"
        )
        assert api.post("/v1/machine/workload-token", json=claim, headers=xfcc).status_code == 403

        # level 1 is real but below what this binding needs
        low = api.post(
            "/v1/machine/attestations:verify", json={"statement": statement(sid, level=1)}
        )
        assert low.status_code == 200 and low.json()["level"] == 1
        assert api.post("/v1/machine/workload-token", json=claim, headers=xfcc).status_code == 403

        # level 3 from an attestor that is not cleared for hardware evidence is refused and kept as a failure
        three = api.post(
            "/v1/machine/attestations:verify", json={"statement": statement(sid, level=3)}
        )
        assert (
            three.status_code == 422
            and three.json()["error"]["code"] == "ATTESTATION_LEVEL_NOT_PERMITTED"
        )

        # attesting a different artifact digest does not help this one
        other = api.post(
            "/v1/machine/attestations:verify",
            json={"statement": statement(sid, digest="sha256:" + "cd" * 32)},
        )
        assert other.status_code == 200
        assert api.post("/v1/machine/workload-token", json=claim, headers=xfcc).status_code == 403

        # a genuinely signed statement that is already out of date is kept as a refusal, not accepted
        stale = api.post(
            "/v1/machine/attestations:verify", json={"statement": statement(sid, minutes=-10)}
        )
        assert stale.status_code == 422 and stale.json()["error"]["code"] == "ATTESTATION_STALE"
        assert api.post("/v1/machine/workload-token", json=claim, headers=xfcc).status_code == 403

        # the real thing: a level-2 statement for this workload, environment and artifact
        ok = api.post(
            "/v1/machine/attestations:verify", json={"statement": statement(sid, level=2)}
        )
        assert ok.status_code == 200 and ok.json()["status"] == "VALID"
        token = api.post(
            "/v1/machine/workload-token", json={**claim, "attestation": 0}, headers=xfcc
        )
        assert token.status_code == 200, (
            token.text
        )  # the body's own number is irrelevant either way

        # and only for that environment
        wrong_env = api.post(
            "/v1/machine/workload-token", json={**claim, "environment_id": "staging"}, headers=xfcc
        )
        assert wrong_env.status_code == 403

    async def rows() -> list[tuple[str, int, str | None]]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await c.execute(
                text(
                    "SELECT status, level, failure_code FROM agent.workload_attestation "
                    "WHERE workload_identity_id = :w ORDER BY verified_at"
                ),
                {"w": sid},
            )
            return [(a, b, d) for a, b, d in r]

    kept = asyncio.run(rows())
    assert ("FAILED", 2, "ATTESTATION_STALE") in kept
    assert ("FAILED", 3, "ATTESTATION_LEVEL_NOT_PERMITTED") in kept
    assert [k for k in kept if k[0] == "VALID"] == [
        ("VALID", 1, None),
        ("VALID", 2, None),
        ("VALID", 2, None),
    ]
    # the forged statement left no record
    assert len(kept) == 5

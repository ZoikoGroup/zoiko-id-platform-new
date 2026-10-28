"""Workload identity: SPIFFE ID -> binding -> logical principal, quarantine and revocation, and the
mesh-verified workload token, against PostgreSQL with RLS (F-W6; DEC-F7 acceptance; MACH-03/10/21).
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import ctx, engine
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_context.spiffe import SpiffeId, TrustPolicy
from zid_crypto import KeyRing, LocalSigner, sign_jws
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.machine.application import workload
from zid_platform.modules.machine.domain.agents import (
    ActivationEvidence,
    AgentProfile,
    AgentStatus,
    BlueprintRelease,
    MachineError,
    ReviewStatus,
)
from zid_platform.modules.machine.infrastructure import machine_store
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.settings import Settings

DIGEST = "sha256:" + "ab" * 32
DOMAIN = "uk-1.test.id.zoiko"
POLICY = TrustPolicy(DOMAIN, federated=frozenset({"partner.test.id.zoiko"}))
ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"


def uid() -> str:
    return str(uuid.uuid4())


async def seed_agent(c: object, tenant: str, agent_id: str) -> None:
    bp = BlueprintRelease("bp_pay", "1.0.0", DIGEST, "sha256:tools", uid(), ReviewStatus.APPROVED)
    sponsor = uid()
    await create_principal(
        c, Principal(sponsor, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
    )  # type: ignore[arg-type]
    await create_principal(  # type: ignore[arg-type]
        c,
        Principal(
            agent_id,
            tenant,
            PrincipalType.AI_AGENT,
            PrincipalStatus.ACTIVE,
            "uk-1",
            sponsor_principal_id=sponsor,
        ),
    )
    await machine_store.save_blueprint(c, tenant, bp)  # type: ignore[arg-type]
    await machine_store.save_agent(  # type: ignore[arg-type]
        c,
        AgentProfile(
            agent_id,
            tenant,
            "Payments agent",
            "bp_pay",
            "1.0.0",
            bp.publisher_principal_id,
            uid(),
            sponsor,
            uid(),
            "any",
            frozenset({"tool_pay"}),
            1,
            AgentStatus.PENDING_APPROVAL,
        ),
    )
    await machine_store.change_agent_status(  # type: ignore[arg-type]
        c,
        agent_id,
        AgentStatus.ACTIVE,
        evidence=ActivationEvidence(True, None, True, True, True, True, True),
    )


def test_workload_resolution_quarantine_and_revocation(rt_url: str) -> None:
    configure_runtime(development_runtime("uk-1"))
    tenant, agent_id, admin = uid(), uid(), uid()
    sid = f"spiffe://{DOMAIN}/t/{tenant}/ns/payments/sa/agent"

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await seed_agent(c, tenant, agent_id)
            reg = {
                "blueprint_version": "1.0.0",
                "artifact_digest": DIGEST,
                "environment_id": "prod",
                "attestation_required": 0,
                "valid_to": datetime.now(UTC) + timedelta(days=1),
                "actor": admin,
            }
            with pytest.raises(MachineError, match="SPIFFE_TRUST_DOMAIN_UNTRUSTED"):
                await workload.register_binding(
                    c,
                    tenant,
                    policy=POLICY,
                    agent_principal_id=agent_id,
                    spiffe_id=f"spiffe://eu-1.test.id.zoiko/t/{tenant}/sa/x",
                    **reg,
                )
            with pytest.raises(MachineError, match="another tenant"):
                await workload.register_binding(
                    c,
                    tenant,
                    policy=POLICY,
                    agent_principal_id=agent_id,
                    spiffe_id=f"spiffe://{DOMAIN}/t/{uid()}/sa/x",
                    **reg,
                )
            b = await workload.register_binding(
                c, tenant, policy=POLICY, agent_principal_id=agent_id, spiffe_id=sid, **reg
            )
            with pytest.raises(
                MachineError, match="ALREADY_BOUND"
            ):  # one live binding per SPIFFE ID
                await workload.register_binding(
                    c, tenant, policy=POLICY, agent_principal_id=agent_id, spiffe_id=sid, **reg
                )

            def resolve(spiffe: str = sid, digest: str = DIGEST):  # type: ignore[no-untyped-def]
                return workload.resolve_workload(
                    c,
                    spiffe_id=SpiffeId.parse(spiffe),
                    environment_id="prod",
                    artifact_digest=digest,
                    attestation=2,
                )

            p = await resolve()
            assert p.logical_principal_id == agent_id and p.binding_id == b.binding_id
            with pytest.raises(MachineError, match="WORKLOAD_IDENTITY_UNREGISTERED"):
                await resolve(sid + "-2")
            with pytest.raises(MachineError, match="WORKLOAD_ARTIFACT_NOT_APPROVED"):
                await resolve(digest="sha256:" + "00" * 32)
            rev = await workload.quarantine_environment(
                c, tenant, "prod", reason="COMPROMISE_SUSPECTED", actor=admin
            )
            assert rev > p.security_revision
            with pytest.raises(MachineError, match="ENVIRONMENT_QUARANTINED"):  # denied at once
                await resolve()
            await workload.release_environment(c, tenant, "prod", actor=admin)
            p2 = await resolve()
            assert p2.security_revision > p.security_revision  # caches keyed on it are invalid
            await workload.revoke_binding(c, tenant, sid, reason="ROTATED_OUT", actor=admin)
            with pytest.raises(MachineError, match="WORKLOAD_IDENTITY_UNREGISTERED"):
                await resolve()
            audits = set(
                (await c.execute(text("SELECT event_type FROM evidence.audit_record"))).scalars()
            )
            assert {
                "machine.workload.registered",
                "machine.environment.quarantined",
                "machine.workload.binding_revoked",
            } <= audits

    asyncio.run(run())
    configure_runtime(None)


def _ca() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, DOMAIN)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert, key


def _svid(ca: tuple[x509.Certificate, ec.EllipticCurvePrivateKey], uri: str) -> x509.Certificate:
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "w")]))
        .issuer_name(ca[0].subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(uri)]), critical=False
        )
        .sign(ca[1], hashes.SHA256())
    )


def test_mesh_verified_workload_gets_a_bound_token(rt_url: str, tmp_path: Path) -> None:
    """DEC-F7 acceptance: a pod without a valid SVID gets nothing; with one it acts as its
    registered principal only, through a token bound to that SVID.

    SEC-18: a token stolen from workload A is refused when presented from workload B or from nowhere.
    SEC-03: a request that arrives from inside the mesh without a verified workload identity is refused."""
    tenant, agent_id, admin = uid(), uid(), uid()
    sid = f"spiffe://{DOMAIN}/t/{tenant}/ns/payments/sa/agent"
    ca = _ca()
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(ca[0].public_bytes(Encoding.PEM))
    svid = _svid(ca, sid)
    xfcc = {
        "x-forwarded-client-cert": f'Cert="{urllib.parse.quote(svid.public_bytes(Encoding.PEM).decode())}"'
    }
    # Tokens are risk class R2, so the floor is a verified artifact attestation (ATTEST-2): the workload's attestor signs
    # a statement and the cell verifies it. What the requester says about itself is not evidence (CR-042).
    attestor = LocalSigner("attestor-1", "Ed25519")
    attestors = tmp_path / "attestors.json"
    attestors.write_text(json.dumps({"keys": [attestor.public_jwk()]}), encoding="utf-8")
    now_ts = int(datetime.now(UTC).timestamp())
    statement = sign_jws(
        attestor,
        {
            "sub": sid,
            "environment_id": "prod",
            "artifact_digest": DIGEST,
            "level": 2,
            "iat": now_ts - 5,
            "exp": now_ts + 1800,
        },
        typ="attestation+jwt",
    )

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
                attestation_required=0,
                valid_to=datetime.now(UTC) + timedelta(days=1),
                actor=admin,
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
            attestor_keys_file=str(attestors),
        ),
        keyring=KeyRing(LocalSigner("issuer-wl")),
    )
    body = {"environment_id": "prod", "artifact_digest": DIGEST, "attestation": 2}
    with TestClient(app) as api:
        r = api.post("/v1/machine/workload-token", json=body)
        assert r.status_code == 401 and r.json()["error"]["code"] == "SVID_MISSING"
        r = api.post(
            "/v1/machine/workload-token",
            json={**body, "artifact_digest": "sha256:" + "00" * 32},
            headers=xfcc,
        )
        assert r.status_code == 403
        # without a verified attestation the request's own claim (attestation: 2) earns nothing
        r = api.post("/v1/machine/workload-token", json=body, headers=xfcc)
        assert r.status_code == 403 and r.json()["error"]["code"] == "ATTESTATION_INSUFFICIENT"
        assert (
            api.post("/v1/machine/attestations:verify", json={"statement": statement}).status_code
            == 200
        )
        r = api.post("/v1/machine/workload-token", json=body, headers=xfcc)
        assert r.status_code == 200, r.text
        tok = r.json()
        assert tok["principal_id"] == agent_id and tok["expires_in"] == 300
        # MACH-26 single-client check: the exchange stays well inside its 150 ms p95 budget (MACHINE-001 §25).
        # The gate rate itself is a PERF-environment run (tests/load/machine-token).
        import time

        walls = []
        for _ in range(100):
            t0 = time.perf_counter()
            assert (
                api.post("/v1/machine/workload-token", json=body, headers=xfcc).status_code == 200
            )
            walls.append((time.perf_counter() - t0) * 1000)
        assert sorted(walls)[94] < 150
        auth = {"Authorization": f"Bearer {tok['access_token']}"}
        # the token works only together with the same mesh-verified SVID (RFC 8705 cnf.x5t)
        assert api.get("/v1/privacy/purposes", headers={**auth, **xfcc}).status_code == 200
        assert api.get("/v1/privacy/purposes", headers=auth).status_code == 401
        other = _svid(ca, f"spiffe://{DOMAIN}/t/{tenant}/ns/payments/sa/other")
        other_h = {
            "x-forwarded-client-cert": f'Cert="{urllib.parse.quote(other.public_bytes(Encoding.PEM).decode())}"'
        }
        assert api.get("/v1/privacy/purposes", headers={**auth, **other_h}).status_code == 401
    configure_runtime(None)

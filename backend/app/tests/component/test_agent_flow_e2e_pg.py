"""The whole flow, from a build's digest to a token and back to a kill (CR-064, CR-042, CR-063): as three different people over HTTP, with a real container.

An engineer registers the build; a reviewer approves it; an admin registers the tool, the blueprint release (from the approved build, as a deployed
cell requires), the agent and its runtime binding; the reviewer approves the release, attests the checks and activates. `attestor_sync` writes the
attestor's approved digests from what the cell holds; the real attestor inspects a real running container; the workload, presenting its SPIRE-shaped
identity, gets a token. Then the build is revoked and the same workload is refused.

Needs a Docker engine and the image below (the test skips itself without them), and runs the cell as a deployed one would for builds (`enforce`)."""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from admin_fixture import make_admin
from conftest import CELL, ctx, engine
from cryptography.hazmat.primitives.serialization import Encoding
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_workload_attestor_pg import (
    ATTESTOR,
    IMAGE,
    _start,
    _stop,
    image_digest,  # noqa: F401  (a fixture, used by name)
)
from test_workload_identity_pg import AUD, DOMAIN, ISS, _ca, _svid
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.attestor import registration, service, sync
from zid_platform.attestor.inspectors import DockerInspector
from zid_platform.entrypoints.attestor_sync import approved_by_workload
from zid_platform.main import create_app
from zid_platform.modules.organization.infrastructure.root_store import create_organization
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

BASE = "/v1/machine"
ACTIONS = [
    "machine.agent.read",
    "machine.agent.manage",
    "machine.agent.activate",
    "machine.blueprint.manage",
    "machine.tool.manage",
    "machine.build.register",
    "machine.build.manage",
    "machine.workload.manage",
]
RESOURCES = [
    "agent:*",
    "blueprint:*",
    "tool:*",
    "build:*",
    "workload_binding:*",
    "workload_environment:*",
]
MANIFEST = "sha256:" + "b" * 64


def uid() -> str:
    return str(uuid.uuid4())


def test_from_a_digest_to_a_token_and_back_to_a_kill(
    rt_url: str,
    owner_url: str,
    tmp_path: Path,
    image_digest: str,  # noqa: F811
) -> None:
    tenant, engineer, reviewer, admin, sponsor, operator, server, org = (
        uid(),
        uid(),
        uid(),
        uid(),
        uid(),
        uid(),
        uid(),
        uid(),
    )
    sid = f"spiffe://{DOMAIN}/t/{tenant}/ns/agents/sa/invoice"
    name = f"zid-flow-{uuid.uuid4().hex[:8]}"
    ring = KeyRing(LocalSigner("issuer-flow"))

    # ---- the workload's identity, as the mesh would present it, and the attestor's public key the cell trusts
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
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p, kind in (
                (engineer, PrincipalType.HUMAN),
                (reviewer, PrincipalType.HUMAN),
                (admin, PrincipalType.HUMAN),
                (sponsor, PrincipalType.HUMAN),
                (operator, PrincipalType.HUMAN),
                (server, PrincipalType.SERVICE),
            ):
                await create_principal(
                    c, Principal(p, tenant, kind, PrincipalStatus.ACTIVE, "uk-1")
                )
            await make_admin(c, tenant, [engineer, reviewer, admin], ACTIONS, RESOURCES)
            await create_organization(
                c,
                organization_id=org,
                tenant_id=tenant,
                legal_name="Acme Ltd",
                jurisdiction="GB",
                valid_from=datetime.now(UTC) - timedelta(days=30),
            )
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 1)"
                ),
                {"t": uuid.UUID(tenant), "c": uuid.UUID(CELL)},
            )

    async def session(p: str) -> str:
        sid_, t = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            principal, _ = await load_principal(c, p)
            await create_session(
                c,
                Session(
                    sid_,
                    tenant,
                    p,
                    t - timedelta(seconds=30),
                    "native:webauthn",
                    2,
                    principal.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    t + timedelta(hours=8),
                ),
            )
        return sid_

    asyncio.run(seed())
    sessions = {p: asyncio.run(session(p)) for p in (engineer, reviewer, admin)}

    def who(sub: str) -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=tenant,
            client_id="admin-console",
            now=datetime.now(UTC),
            session_id=sessions[sub],
            scope=frozenset(),
        )
        return {"Authorization": f"Bearer {tok}"}

    eng_h, rev_h, adm_h = who(engineer), who(reviewer), who(admin)
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            cell_uuid=CELL,
            agent_builds="enforce",
            spiffe_trust_domain=DOMAIN,
            spiffe_bundle_files={DOMAIN: str(bundle)},
            mesh_trusted_proxies=("testclient",),
            attestor_keys_file=str(keys),
        ),
        keyring=ring,
    )
    body = {"environment_id": "prod", "artifact_digest": image_digest}
    inspector = DockerInspector()
    _start(name)
    try:
        with TestClient(app, base_url=ISS) as http:
            # 1. the engineer registers the build; nobody can build a release from it yet
            built = http.post(
                f"{BASE}/builds",
                headers=eng_h,
                json={
                    "name": "Invoice agent 1.0",
                    "artifact_digest": image_digest,
                    "source_ref": IMAGE,
                },
            )
            assert built.status_code == 201, built.text
            build_id = built.json()["build_id"]
            bp = {
                "blueprint_id": "invoice-bot",
                "version": "1.0.0",
                "artifact_digest": image_digest,
                "tool_manifest_digest": MANIFEST,
                "publisher_principal_id": admin,
            }
            refused = http.post(f"{BASE}/blueprints", headers=adm_h, json=bp)
            assert (
                refused.status_code == 409
                and refused.json()["error"]["code"] == "BUILD_NOT_APPROVED"
            )

            # 2. a second person approves it; now the release can be drafted, and the first person cannot approve their own build
            assert http.post(f"{BASE}/builds/{build_id}:approve", headers=eng_h).status_code == 403
            assert http.post(f"{BASE}/builds/{build_id}:approve", headers=rev_h).status_code == 200
            tool = {
                "tool_id": "invoices.approve",
                "version": "1.0.0",
                "server_principal_id": server,
                "canonical_action": "invoice.approve",
                "default_risk_class": 2,
                "side_effect": "REVERSIBLE",
                "requires_mandate": False,
            }
            assert http.post(f"{BASE}/tools", headers=adm_h, json=tool).status_code == 201
            assert (
                http.post(f"{BASE}/tools/invoices.approve:activate", headers=adm_h).json()["status"]
                == "ACTIVE"
            )
            assert http.post(f"{BASE}/blueprints", headers=adm_h, json=bp).status_code == 201
            assert (
                http.post(
                    f"{BASE}/blueprints/invoice-bot/versions/1.0.0:approve", headers=rev_h
                ).status_code
                == 200
            )  # not the publisher

            # 3. the agent, and its binding: a binding must run its own release's build, so another digest is refused
            agent = http.post(
                f"{BASE}/agents",
                headers=adm_h,
                json={
                    "agent_name": "Acme invoice agent",
                    "blueprint_id": "invoice-bot",
                    "blueprint_version": "1.0.0",
                    "operator_principal_id": operator,
                    "sponsor_principal_id": sponsor,
                    "owning_organization_id": org,
                    "model_provider": "example-llm",
                    "allowed_tools": ["invoices.approve"],
                    "max_subagent_depth": 0,
                },
            )
            assert agent.status_code == 201, agent.text
            aid = agent.json()["agent_principal_id"]
            binding = {
                "agent_principal_id": aid,
                "spiffe_id": sid,
                "blueprint_version": "1.0.0",
                "artifact_digest": image_digest,
                "environment_id": "prod",
                "attestation_required": 2,
                "valid_to": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            }
            wrong = http.post(
                f"{BASE}/workload-bindings",
                headers=adm_h,
                json={**binding, "artifact_digest": "sha256:" + "9" * 64},
            )
            assert (
                wrong.status_code == 422
                and wrong.json()["error"]["code"] == "WORKLOAD_ARTIFACT_NOT_APPROVED"
            )
            assert (
                http.post(f"{BASE}/workload-bindings", headers=adm_h, json=binding).status_code
                == 201
            )

            # 4. the checks, the independent review, and activation
            assert (
                http.post(f"{BASE}/agents/{aid}:submit", headers=adm_h).json()["status"]
                == "PENDING_APPROVAL"
            )
            for gate in (
                "SECURITY_REVIEW",
                "KILL_PATH_TESTED",
                "SHORT_LIVED_CREDENTIALS",
                "OBSERVABILITY",
            ):
                assert (
                    http.post(
                        f"{BASE}/agents/{aid}:attest",
                        headers=rev_h,
                        json={"gate": gate, "note": "checked"},
                    ).status_code
                    == 200
                )
            active = http.post(f"{BASE}/agents/{aid}:activate", headers=rev_h)
            assert active.status_code == 200 and active.json()["status"] == "ACTIVE", active.text

            # 5. the attestor's registration, first with no digests listed: the container runs but nothing approved says it may, so level 1 only
            raw = {
                "workloads": [
                    {
                        "spiffe_id": sid,
                        "environment_id": "prod",
                        "selectors": {"docker.container": name, "docker.label.team": "payments"},
                        "approved_digests": [],
                    }
                ]
            }
            entry = registration.parse(raw)[0]
            low, low_token = service.attest_once(ATTESTOR, entry, inspector, name)
            assert low.level == 1 and low_token is not None
            assert (
                http.post(f"{BASE}/attestations:verify", json={"statement": low_token}).status_code
                == 200
            )
            denied = http.post(f"{BASE}/workload-token", json=body, headers=xfcc)
            assert (
                denied.status_code == 403
                and denied.json()["error"]["code"] == "ATTESTATION_INSUFFICIENT"
            )

            # 6. the digests come from the cell's own approved builds (attestor_sync), not from anyone typing them
            async def approved() -> Any:
                async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                    return await approved_by_workload(c)

            plan = sync.plan(raw, asyncio.run(approved()), tenant_id=tenant)
            assert plan.changed and plan.registrations["workloads"][0]["approved_digests"] == [
                image_digest
            ]
            synced = tmp_path / "workloads.json"
            synced.write_text(json.dumps(plan.registrations), encoding="utf-8")

            # 7. the real attestor inspects the real container and the workload gets its token, bound to its identity
            found, token = service.attest_once(
                ATTESTOR, registration.load(synced)[0], inspector, name
            )
            assert found.level == 2 and token is not None, found
            sent = http.post(f"{BASE}/attestations:verify", json={"statement": token})
            assert sent.status_code == 200 and sent.json()["level"] == 2
            issued = http.post(f"{BASE}/workload-token", json=body, headers=xfcc)
            assert issued.status_code == 200, issued.text
            assert (
                issued.json()["principal_id"] == aid
                and issued.json()["expires_in"] == 300
                and "x5t#S256" in issued.json()["cnf"]
            )

            # 8. the build turns out to be bad: revoking it stops the same workload at once
            gone = http.post(
                f"{BASE}/builds/{build_id}:revoke",
                headers=rev_h,
                json={"reason_code": "SUPPLY_CHAIN_COMPROMISE"},
            )
            assert gone.status_code == 200, gone.text
            assert (
                gone.json()["releases_revoked"],
                gone.json()["bindings_suspended"],
                gone.json()["agents_suspended"],
            ) == (1, 1, 1)
            after = http.post(f"{BASE}/workload-token", json=body, headers=xfcc)
            assert after.status_code == 403, after.text
            assert http.get(f"{BASE}/agents/{aid}", headers=adm_h).json()["status"] == "SUSPENDED"
    finally:
        _stop(name)

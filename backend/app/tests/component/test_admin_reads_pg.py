"""Admin console reads and mandate creation over HTTP (CR-010; ZID-AUTH-001 §24; ZID-MANDATE-001 §9-10).

Full app against PostgreSQL with RLS in force and real access tokens. Every list goes through the
protected-action PDP with a real authority grant (`make_admin`); a principal without authority is
refused whatever its assurance. Listing views never carry pinned keys, token hashes or secrets.
POST /v1/mandates attenuates the caller's own grant: never wider, never someone else's.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.authority.domain.composite import ApprovalPolicy, CompositeMode
from zid_platform.modules.authority.infrastructure import approval_store
from zid_platform.modules.credential.infrastructure import credential_store
from zid_platform.modules.federation.infrastructure import connection_store
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.provisioning.infrastructure.scim_store import Source, create_source
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.signals.infrastructure.signal_store import Stream, create_stream
from zid_platform.modules.verification.infrastructure import verification_store
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
JWK = {
    "kty": "EC",
    "crv": "P-256",
    "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
    "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0",
    "kid": "k1",
}
ADMIN_ACTIONS = [
    "authority.grant.read",
    "authority.approval.read",
    "mandate.read",
    "mandate.create",
    "credential.read",
    "signals.stream.read",
    "provisioning.source.read",
    "identity.read",
    "tenant.identity_provider.change",
    "invoice.approve",
]
ADMIN_RESOURCES = [
    "authority_grant:*",
    "approval_request:*",
    "mandate:*",
    "credential:*",
    "credential_configuration:*",
    "credential_issuer:*",
    "rp_registration:*",
    "ssf_stream:*",
    "provisioning_source:*",
    "principal:*",
    "federation_trust_anchor:*",
    "invoice:*",
]
PROTECTED_LISTS = [
    "/v1/authority-grants",
    "/v1/approval-requests",
    "/v1/mandates",
    "/credentials",
    "/credential-configurations",
    "/credential-issuer-trust",
    "/relying-party-registrations",
    "/ssf/streams",
    "/provisioning/sources",
    "/admin/principals",
    "/federation/trust-anchors",
]


def uid() -> str:
    return str(uuid.uuid4())


def test_admin_lists_and_mandate_attenuation(rt_url: str, owner_url: str) -> None:
    tenant, admin, admin2, bob, agent = uid(), uid(), uid(), uid(), uid()
    ids: dict[str, str] = {}
    now = datetime.now(UTC)
    ring = KeyRing(LocalSigner("issuer-1"))

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (admin, admin2, bob, agent):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            ids["grant"] = await make_admin(
                c, tenant, [admin, admin2], ADMIN_ACTIONS, ADMIN_RESOURCES
            )
            ids["approval"] = uid()
            await approval_store.create_request(
                c,
                tenant_id=tenant,
                request_id=ids["approval"],
                requester_id=bob,
                subject_ref="payment:pay_1",
                transaction_context="payment:pay_1|GBP|10.00",
                policy=ApprovalPolicy(CompositeMode.K_OF_N, ("invoice.approve",), 2),
                expires_at=now + timedelta(hours=1),
            )
            ids["stream"] = uid()
            await create_stream(
                c,
                Stream(
                    stream_id=ids["stream"],
                    tenant_id=tenant,
                    direction="RECEIVER",
                    issuer="https://ssf.idp.example",
                    audience=ISS,
                    jwks={"keys": [JWK]},
                    algorithms=("ES256",),
                    events=(
                        "https://schemas.openid.net/secevent/risc/event-type/account-disabled",
                    ),
                    subject_formats=("iss_sub",),
                    delivery_method="push",
                    push_token_hash=b"\x01" * 32,
                    source_trust="TRUSTED",
                    federation_issuer=None,
                    receiver_client_id=None,
                    status="PENDING",
                    metadata_revision=1,
                ),
            )
            ids["source"] = uid()
            await create_source(c, Source(ids["source"], tenant, "scim-client", "HR", "ACTIVE"))
            ids["anchor"] = await connection_store.create_anchor(
                c,
                tenant_id=tenant,
                entity_id="https://ta.example",
                jwks={"keys": [JWK]},
                allowed_domains=["example"],
            )
            ids["config"] = await credential_store.create_configuration(
                c,
                tenant,
                {
                    "credential_type": "urn:zoiko:employee",
                    "format": "SD_JWT_VC",
                    "adapter_version": "sd-jwt-vc/1",
                    "required_claims": ["given_name"],
                    "status_method": "TOKEN_STATUS_LIST",
                },
            )
            ids["trust"] = await credential_store.create_trust(
                c,
                tenant,
                {
                    "issuer_identifier": "https://issuer.example",
                    "credential_types": ["urn:zoiko:employee"],
                    "trust_source": "PINNED",
                    "jwks": {"keys": [JWK]},
                    "valid_to": now + timedelta(days=30),
                },
            )
            ids["rp"] = await credential_store.create_registration(
                c,
                tenant,
                {
                    "client_id": "rp-1",
                    "purpose": "age_check",
                    "allowed_attributes": ["age_over_18"],
                    "valid_to": now + timedelta(days=30),
                },
            )
            ids["credential"] = uid()
            await c.execute(
                text(
                    "INSERT INTO cred.credential_record (credential_id, tenant_id, "
                    "subject_principal_id, issuer_identifier, credential_type, format, "
                    "configuration_id, status, holder_key_thumbprint, issuance_transaction, "
                    "issued_at, expires_at) VALUES (:id, :t, :s, 'https://id.zoiko.test', "
                    "'urn:zoiko:employee', 'SD-JWT', :c, 'VALID', 'jkt-secret', 'tx-1', :now, :exp)"
                ),
                {
                    "id": uuid.UUID(ids["credential"]),
                    "t": uuid.UUID(tenant),
                    "s": uuid.UUID(bob),
                    "c": uuid.UUID(ids["config"]),
                    "now": now,
                    "exp": now + timedelta(days=30),
                },
            )
            await verification_store.create_adapter(
                c,
                {
                    "adapter_id": "idv-1",
                    "tenant_id": tenant,
                    "provider": "acme-idv",
                    "mapping_version": "1",
                    "submit_endpoint": "https://idv.example/submit",
                    "callback_jwks": {"keys": [JWK]},
                    "callback_algorithms": ["ES256"],
                    "equivalence_group": "doc",
                    "supported_evidence": ["passport"],
                    "jurisdictions": ["GB"],
                },
            )
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 1)"
                ),
                {"t": uuid.UUID(tenant), "c": uuid.UUID(CELL)},
            )

    async def new_session(p: str) -> str:
        sid = uid()
        t = datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            principal, _ = await load_principal(c, p)
            await create_session(
                c,
                Session(
                    sid,
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
        return sid

    asyncio.run(seed())
    sessions = {p: asyncio.run(new_session(p)) for p in (admin, admin2, bob)}
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_uuid=CELL,
        ),
        keyring=ring,
    )

    def bearer(sub: str, scope: str = "") -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=tenant,
            client_id="admin-console",
            now=datetime.now(UTC),
            session_id=sessions[sub],
            scope=frozenset(scope.split()),
        )
        return {"Authorization": f"Bearer {tok}"}

    adm, adm2, nobody = bearer(admin), bearer(admin2), bearer(bob)

    def items(path: str, **params: Any) -> list[dict[str, Any]]:
        r = http.get(path, headers=adm, params=params)
        assert r.status_code == 200, (path, r.text)
        out: list[dict[str, Any]] = r.json()["items"]
        return out

    with TestClient(app, base_url=ISS) as http:
        # --- every protected list refuses a principal without authority
        for path in PROTECTED_LISTS:
            r = http.get(path, headers=nobody)
            assert r.status_code == 403, (path, r.text)
            assert r.json()["error"]["code"] == "PROTECTED_ACTION_DENIED", path

        # --- authority grants (newest first; filters)
        grants = items("/v1/authority-grants")
        assert len(grants) == 2 and {g["grantee_principal_id"] for g in grants} == {admin, admin2}
        mine = items("/v1/authority-grants", grantee_principal_id=admin)
        assert [g["authority_grant_id"] for g in mine] == [ids["grant"]]
        assert items("/v1/authority-grants", status="REVOKED") == []
        assert http.get("/v1/authority-grants", headers=adm, params={"limit": 0}).status_code == 422

        # --- approval requests: derived state, no transaction context or hash
        (appr,) = items("/v1/approval-requests")
        assert appr["approval_request_id"] == ids["approval"]
        assert appr["state"] == "PENDING" and appr["subject_ref"] == "payment:pay_1"
        assert appr["required_approvals"] == 2 and appr["approvals_received"] == 0
        assert appr["requester_principal_id"] == bob
        assert not {"transaction_context", "transaction_context_hash"} & appr.keys()
        assert items("/v1/approval-requests", state="PENDING") == [appr]
        assert items("/v1/approval-requests", state="APPROVED") == []
        assert items("/v1/approval-requests", state="EXPIRED") == []

        # --- credentials: no holder key thumbprint, no status-list position, no keys
        (cred,) = items("/credentials")
        assert cred["credential_id"] == ids["credential"] and cred["status"] == "VALID"
        assert cred["subject_principal_id"] == bob
        assert "holder_key_thumbprint" not in cred and "status_index" not in cred
        assert items("/credentials", status="REVOKED") == []
        (cfg,) = items("/credential-configurations")
        assert cfg["configuration_id"] == ids["config"] and cfg["status"] == "DRAFT"
        (trust,) = items("/credential-issuer-trust")
        assert trust["trust_id"] == ids["trust"] and "jwks" not in trust
        assert "iaca_certificates" not in trust
        (rp,) = items("/relying-party-registrations")
        assert rp["registration_id"] == ids["rp"] and rp["allowed_attributes"] == ["age_over_18"]

        # --- SSF streams: no pinned keys, no push credential hash
        (stream,) = items("/ssf/streams")
        assert stream["stream_id"] == ids["stream"] and stream["status"] == "PENDING"
        assert not {"jwks", "push_token_hash", "push_authorization"} & stream.keys()

        # --- provisioning sources
        (src,) = items("/provisioning/sources")
        assert src == {
            "source_id": ids["source"],
            "client_id": "scim-client",
            "display_name": "HR",
            "status": "ACTIVE",
        }

        # --- principal directory
        everyone = {p["principal_id"]: p for p in items("/admin/principals")}
        assert {admin, admin2, bob, agent} <= everyone.keys()
        assert everyone[bob]["principal_type"] == "HUMAN" and everyone[bob]["status"] == "ACTIVE"
        assert items("/admin/principals", principal_type="AI_AGENT") == []
        assert len(items("/admin/principals", limit=2)) == 2

        # --- federation trust anchors: keys counted, never returned
        (anchor,) = items("/federation/trust-anchors")
        assert anchor["anchor_id"] == ids["anchor"] and anchor["pinned_keys"] == 1
        assert "jwks" not in anchor

        # --- verification adapters: scope-checked, no callback keys
        r = http.get("/verification-adapters", headers=adm)
        assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_INSUFFICIENT"
        r = http.get("/verification-adapters", headers=bearer(admin, "verification:read"))
        assert r.status_code == 200, r.text
        (adapter,) = r.json()["items"]
        assert adapter["adapter_id"] == "idv-1" and adapter["status"] == "ACTIVE"
        assert "callback_jwks" not in adapter and "tenant_id" not in adapter

        # --- mandates: the grantee attenuates its own grant, never wider
        assert items("/v1/mandates") == []
        purpose = uid()
        body = {
            "authority_grant_id": ids["grant"],
            "executor_principal_id": agent,
            "purpose_id": purpose,
            "allowed_actions": ["invoice.approve"],
            "resource_scopes": ["invoice:inv-1"],
            "tools": ["erp"],
            "jurisdictions": ["GB"],
            "value_limit": {"amount": "500.00", "currency": "GBP"},
            "per_transaction_value_limit": {"amount": "100", "currency": "GBP"},
            "count_limit": 10,
            "valid_from": (now - timedelta(hours=1)).isoformat(),
            "valid_to": (now + timedelta(days=30)).isoformat(),
        }

        def create(
            b: dict[str, Any], key: str, who: dict[str, str] = adm
        ) -> Any:  # requests.Response
            return http.post("/v1/mandates", json=b, headers={**who, "Idempotency-Key": key})

        assert http.post("/v1/mandates", json=body, headers=adm).status_code == 422  # no key
        r = create(body, "mandate-key-1")
        assert r.status_code == 201, r.text
        m = r.json()
        assert m["status"] == "PENDING_APPROVAL" and m["authority_grant_id"] == ids["grant"]
        assert m["executor_principal_id"] == agent and m["purpose_id"] == purpose
        assert m["represented_principal_id"] == mine[0]["represented_principal_id"]
        assert m["value_limit"] == "500.000000000" and m["currency"] == "GBP"
        assert m["per_transaction_value_limit"] == "100.000000000" and m["count_limit"] == 10
        assert m["allowed_actions"] == ["invoice.approve"] and m["tools"] == ["erp"]
        again = create(body, "mandate-key-1")
        assert again.status_code == 201 and again.json()["mandate_id"] == m["mandate_id"]

        def refused(b: dict[str, Any], key: str, who: dict[str, str] = adm) -> str:
            r = create(b, key, who)
            assert r.status_code in (403, 404, 409, 422), r.text
            code: str = r.json()["error"]["code"]
            return code

        # actions, resources, window beyond the grant
        wider = {**body, "allowed_actions": ["invoice.approve", "payment.release"]}
        assert refused(wider, "mandate-key-2") == "GRANT_SCOPE_WIDENING"
        assert (
            refused({**body, "resource_scopes": ["payment:*"]}, "mandate-key-3")
            == "GRANT_SCOPE_WIDENING"
        )
        late = {**body, "valid_to": (now + timedelta(days=400)).isoformat()}
        assert refused(late, "mandate-key-4") == "GRANT_SCOPE_WIDENING"
        deeper = {**body, "delegation": {"allowed": True, "max_depth": 1}}
        assert refused(deeper, "mandate-key-5") == "GRANT_DELEGATION_NOT_ALLOWED"
        mixed = {**body, "per_transaction_value_limit": {"amount": "1", "currency": "EUR"}}
        assert refused(mixed, "mandate-key-6") == "MANDATE_CURRENCY_MISMATCH"
        # someone else's grant: admin2 holds mandate.create but is not this grant's grantee
        assert refused(body, "mandate-key-7", adm2) == "GRANTOR_AUTHORITY_MISSING"
        # no authority at all
        assert refused(body, "mandate-key-8", nobody) == "PROTECTED_ACTION_DENIED"
        assert refused({**body, "authority_grant_id": uid()}, "mandate-key-9") == (
            "AUTHORITY_GRANT_NOT_FOUND"
        )

        listed = items("/v1/mandates")
        assert [x["mandate_id"] for x in listed] == [m["mandate_id"]] and listed[0] == m
        assert items("/v1/mandates", status="ACTIVE") == []
        assert len(items("/v1/mandates", executor_principal_id=agent)) == 1
        assert items("/v1/mandates", executor_principal_id=bob) == []

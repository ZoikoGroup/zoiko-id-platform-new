"""Outside identity provider bindings, as evidence (ZID-CP-WF07 v2.0 .03; A07-11, A07-12; CR-169), over the real routes against PostgreSQL, with tokens signed by a local issuer.

What these hold: a provider is bound to one agent by exact issuer, audience, subject and pinned PUBLIC keys; the same provider identifier cannot be bound to a second agent; a token proves the binding only when the signature, issuer,
audience, subject, expiry and one-time id all check out, and each failure is named and recorded; one token proves it once; what the provider says is kept as an outside claim and changes nothing the agent may do; a provider that
has gone quiet is STALE and not relied on; and a revoked binding stays revoked.
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
from zid_crypto import KeyRing, LocalSigner, sign_jws
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.machine.domain.agents import AgentProfile, AgentStatus, BlueprintRelease, ReviewStatus, SideEffect, Tool, ToolStatus
from zid_platform.modules.machine.domain.tool_gateway import args_digest
from zid_platform.modules.machine.infrastructure.machine_store import save_agent, save_blueprint, save_tool
from zid_platform.modules.organization.infrastructure.root_store import create_organization
from zid_platform.modules.principal.domain.principal import Principal, PrincipalStatus, PrincipalType
from zid_platform.modules.principal.infrastructure.principal_store import create_principal, load_principal
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
DIGEST = "sha256:" + "a" * 64
ARGS = args_digest({"invoice": "demo_492"})
READ = "machine.agent.read"
IDP_ISSUER, IDP_AUDIENCE, IDP_SUBJECT = "https://idp.example.com/tenant-1", "https://api.zoiko.test/agents", "agent-obj-77"


def uid() -> str:
    return str(uuid.uuid4())


def test_an_outside_provider_binding_is_evidence_pinned_by_key_checked_on_every_claim_one_use_per_token_and_never_authority(rt_url: str, owner_url: str) -> None:
    tenant, sponsor, operator, publisher, server, org, human = (uid() for _ in range(7))
    root, child = uid(), uid()
    ring = KeyRing(LocalSigner("issuer-idp-test"))

    def profile(agent_id: str, parent: str | None = None) -> AgentProfile:
        return AgentProfile(
            agent_id, tenant, f"bot-{agent_id[:4]}", "bp", "1", publisher, operator, sponsor, org, "", frozenset({"docs.read"}), 2 if parent is None else 0, AgentStatus.DRAFT, parent_agent_id=parent
        )  # fmt: skip

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p, kind in ((sponsor, PrincipalType.HUMAN), (operator, PrincipalType.HUMAN), (publisher, PrincipalType.HUMAN), (human, PrincipalType.HUMAN), (server, PrincipalType.SERVICE)):
                await create_principal(c, Principal(p, tenant, kind, PrincipalStatus.ACTIVE, "uk-1"))
            await create_organization(c, organization_id=org, tenant_id=tenant, legal_name="Acme Ltd", jurisdiction="GB", valid_from=datetime.now(UTC) - timedelta(days=30))
            await save_blueprint(c, tenant, BlueprintRelease("bp", "1", DIGEST, DIGEST, publisher, ReviewStatus.APPROVED))
            await save_tool(c, tenant, Tool("docs.read", "1", server, READ, 1, SideEffect.NONE, False, False, ToolStatus.DRAFT))
            await save_agent(c, profile(root))
            await save_agent(c, profile(child, root))
            for agent_id in (root, child):
                await create_principal(c, Principal(agent_id, tenant, PrincipalType.AI_AGENT, PrincipalStatus.ACTIVE, "uk-1", sponsor_principal_id=sponsor))
            await make_admin(c, tenant, [root, child, human], [READ, "machine.agent.manage"], ["agent:*"])
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text("UPDATE agent.agent_profile SET status = 'ACTIVE' WHERE tenant_id = :t"), {"t": uuid.UUID(tenant)})
            await c.execute(text("UPDATE agent.tool_capability SET status = 'ACTIVE' WHERE tenant_id = :t"), {"t": uuid.UUID(tenant)})

    async def session_for(p: str) -> str:
        sid, t = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            principal, _ = await load_principal(c, p)
            await create_session(c, Session(sid, tenant, p, t - timedelta(seconds=30), "native:webauthn", 2, principal.security_revision, 1, SessionStatus.ACTIVE, t + timedelta(hours=8)))
        return sid

    async def db(sql: str, **params: Any) -> list[Any]:
        async with engine(owner_url) as eng, eng.begin() as c:
            result = await c.execute(text(sql), {"t": uuid.UUID(tenant), **params})
            return list(result.all()) if result.returns_rows else []

    asyncio.run(seed())
    sessions = {p: asyncio.run(session_for(p)) for p in (root, child, human)}
    app = create_app(Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_uuid=CELL), keyring=ring)

    def bearer(sub: str, *, scope: frozenset[str] = frozenset({"workload"})) -> dict[str, str]:
        tok = issue_access_token(ring, issuer=ISS, audience=AUD, subject=sub, tenant_id=tenant, client_id="agent-runtime", now=datetime.now(UTC), session_id=sessions[sub], scope=scope)
        return {"Authorization": f"Bearer {tok}"}

    idp_key = LocalSigner("idp-key-1")
    wrong_key = LocalSigner("idp-key-1")  # the same key id, a different key

    def token(signer: LocalSigner = idp_key, **over: Any) -> str:
        ts = int(datetime.now(UTC).timestamp())
        claims = {"iss": IDP_ISSUER, "aud": IDP_AUDIENCE, "sub": IDP_SUBJECT, "iat": ts - 5, "exp": ts + 240, "jti": uid(), "agent_type": "docs-bot", "owner": "alice", "status": "ACTIVE", "is_admin": True}
        merged = {k: v for k, v in {**claims, **over}.items() if v is not None}
        return sign_jws(signer, merged, typ="JWT")

    with TestClient(app, base_url=ISS) as http:
        who = bearer(human, scope=frozenset({"admin"}))
        spec = {"profile": "okta", "issuer": IDP_ISSUER, "subject": IDP_SUBJECT, "audience": IDP_AUDIENCE, "keys": [idp_key.public_jwk()]}

        def post(path: str, **fields: Any) -> Any:
            return http.post(path, headers=who, json=fields)

        # a malformed binding is refused: a private key, a remote key, a plain-http issuer, an unknown profile, an unknown agent, a company named in the body
        assert post(f"/v1/machine/agents/{root}/idp-bindings", **{**spec, "keys": [{**idp_key.public_jwk(), "d": "secret"}]}).status_code == 422
        assert post(f"/v1/machine/agents/{root}/idp-bindings", **{**spec, "keys": [{**idp_key.public_jwk(), "jku": "https://evil.example"}]}).status_code == 422
        assert post(f"/v1/machine/agents/{root}/idp-bindings", **{**spec, "issuer": "http://idp.example.com"}).status_code == 422
        assert post(f"/v1/machine/agents/{root}/idp-bindings", **{**spec, "profile": "ldap"}).status_code == 422
        assert post(f"/v1/machine/agents/{uid()}/idp-bindings", **spec).status_code == 404
        assert post(f"/v1/machine/agents/{root}/idp-bindings", **{**spec, "tenant_id": uid()}).status_code == 422

        made = post(f"/v1/machine/agents/{root}/idp-bindings", **spec)
        assert made.status_code == 201, made.text
        b = made.json()
        assert b["state"] == "STALE" and b["certified"] is False and b["is_authority"] is False and b["key_ids"] == ["idp-key-1"] and "Not certified" in b["note"]
        assert b["external_claims"] == {}
        # the same provider identifier cannot be bound to a second agent: a name or an e-mail never merges agents
        dup = post(f"/v1/machine/agents/{child}/idp-bindings", **spec)
        assert dup.status_code == 409 and dup.json()["error"]["code"] == "IDP_BINDING_DUPLICATE"
        assert post(f"/v1/machine/agents/{child}/idp-bindings", **{**spec, "subject": "agent-obj-78"}).status_code == 201

        def present(tok: str) -> Any:
            return http.post(f"/v1/machine/idp-bindings/{b['binding_id']}:present", headers=who, json={"token": tok})

        # each check fails for its own named reason and is recorded; a refused token changes nothing the provider is said to say
        now_ts = int(datetime.now(UTC).timestamp())
        cases = [
            (token(wrong_key), "IDP_SIGNATURE_INVALID"),
            (token(iss="https://evil.example.com"), "IDP_ISSUER_MISMATCH"),
            (token(aud="https://other.service"), "IDP_AUDIENCE_MISMATCH"),
            (token(sub="agent-obj-99"), "IDP_SUBJECT_MISMATCH"),
            (token(exp=now_ts - 3600, iat=now_ts - 3700), "IDP_TOKEN_EXPIRED"),
            (token(exp=now_ts + 100000), "IDP_TOKEN_TOO_LONG_LIVED"),
            (token(jti=None), "IDP_TOKEN_NO_ID"),
        ]
        for tok, why in cases:
            r = present(tok)
            assert r.status_code == 200 and r.json()["accepted"] is False and r.json()["reason_codes"][0] == why, (why, r.text)
            assert r.json()["binding"]["state"] == "STALE" and r.json()["binding"]["external_claims"] == {}
        assert present("this.is-not.a-real-token-at-all").json()["reason_codes"] == ["IDP_TOKEN_REJECTED"]
        assert http.post(f"/v1/machine/idp-bindings/{b['binding_id']}:present", headers=who, json={"token": "short"}).status_code == 422  # too short to be a token at all
        assert asyncio.run(db("SELECT last_failure FROM agent.idp_binding WHERE binding_id = CAST(:b AS uuid)", b=b["binding_id"]))[0].last_failure == "IDP_TOKEN_REJECTED"

        # a good token is accepted: the binding is ACTIVE, what the provider says is kept as an outside claim, and no authority results
        good = token()
        ok = present(good)
        assert ok.status_code == 200 and ok.json()["accepted"] is True and ok.json()["grants_authority"] is False, ok.text
        nb = ok.json()["binding"]
        assert nb["state"] == "ACTIVE" and nb["last_failure"] is None and nb["is_authority"] is False
        assert nb["external_claims"] == {"blueprint": "docs-bot", "sponsor": "alice", "lifecycle": "ACTIVE"}
        assert "is_admin" not in str(nb) and good not in ok.text
        assert present(good).json()["reason_codes"] == ["IDP_TOKEN_REPLAYED"]  # one token proves a binding once
        stored = asyncio.run(db("SELECT jti_digest FROM agent.idp_proof WHERE tenant_id = :t"))
        assert len(stored) == 1 and stored[0].jti_digest.startswith("sha256:")
        # what the provider says changes nothing about what the agent may do
        assert asyncio.run(db("SELECT status::text AS s FROM agent.agent_profile WHERE agent_principal_id = CAST(:a AS uuid)", a=root))[0].s == "ACTIVE"
        call = {"tool_id": "docs.read", "resource_type": "agent", "resource_id": "any", "arguments_digest": ARGS}
        assert http.post("/v1/machine/tool-calls:authorize", headers=bearer(root), json=call).status_code == 200

        # a provider that goes quiet is stale and not relied on, with no fallback; a revoked binding stays revoked
        asyncio.run(db("UPDATE agent.idp_binding SET last_ok_at = now() - interval '3 days' WHERE binding_id = CAST(:b AS uuid)", b=b["binding_id"]))
        assert http.get(f"/v1/machine/idp-bindings/{b['binding_id']}", headers=who).json()["state"] == "STALE"
        gone = http.post(f"/v1/machine/idp-bindings/{b['binding_id']}:revoke", headers=who)
        assert gone.status_code == 200 and gone.json()["state"] == "REVOKED"
        assert http.post(f"/v1/machine/idp-bindings/{b['binding_id']}:revoke", headers=who).status_code == 409
        after = present(token())
        assert after.json()["accepted"] is False and after.json()["reason_codes"] == ["IDP_BINDING_REVOKED"]
        # a revoked binding frees its provider identifier for a new one; the listing names the profiles with their certification state
        assert post(f"/v1/machine/agents/{root}/idp-bindings", **spec).status_code == 201
        listing = http.get("/v1/machine/idp-bindings", headers=who).json()
        assert {p["profile"] for p in listing["profiles"]} == {"generic_oidc", "okta", "entra"} and all(p["certified"] is False for p in listing["profiles"])
        assert len(http.get("/v1/machine/idp-bindings", headers=who, params={"agent_id": child}).json()["items"]) == 1

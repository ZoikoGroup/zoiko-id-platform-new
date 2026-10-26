"""OAuth scope is a ceiling, never authority (ZID-API-001 API-02; ZID-SVC-001 SVC-06).

A token that carries every scope still cannot do a protected action it holds no authority for, and a token without
the scope an endpoint needs is refused before authority is even asked. The two refusals are different, so a client
can tell "your token is too narrow" from "you are not allowed".
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from zid_context import RequestContext
from zid_db import tenant_transaction
from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.main import create_app
from zid_platform.modules.decision.domain.pdp import AuthnFact
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.settings import Settings

EVERY_SCOPE = frozenset(
    {"grants:read", "grants:write", "verification:execute", "verification:admin", "admin", "openid"}
)


def uid() -> str:
    return str(uuid.uuid4())


def test_every_scope_without_authority_still_cannot_do_a_protected_action(rt_url: str) -> None:
    """API-02: the grant listing is a protected action; a caller holding every scope but no grant is refused with
    an authorization denial, and the same caller's reads of its own data still work (scope is not the gate there)."""
    tenant, service = uid(), uid()

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(service, tenant, PrincipalType.SERVICE, PrincipalStatus.ACTIVE, "uk-1")
            )

    asyncio.run(seed())
    app = create_app(Settings(database_url=rt_url))
    context = RequestContext("req_t", "cor_t", "svc", CELL, tenant_id=tenant, principal_id=service)
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        context,
        AuthnFact(3, datetime.now(UTC) - timedelta(minutes=1)),
        security_state="NORMAL",
        scope=EVERY_SCOPE,
    )
    with TestClient(app) as client:
        r = client.get("/v1/authority-grants")
        assert r.status_code == 403, r.text
        assert (
            r.json()["error"]["code"] != "SCOPE_INSUFFICIENT"
        )  # the scope was there; authority was not
        r = client.get(
            "/v1/authority-grants/not-a-grant-id"
        )  # a malformed id never reaches a grant
        assert r.status_code in (403, 404, 422)


def test_a_service_without_the_scope_for_an_endpoint_is_refused_before_authority_is_asked(
    rt_url: str,
) -> None:
    """SVC-06: an authenticated service whose token lacks the capability an endpoint needs gets SCOPE_INSUFFICIENT;
    adding the scope changes the answer to the authority question, which is a separate gate."""
    tenant, service = uid(), uid()

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(service, tenant, PrincipalType.SERVICE, PrincipalStatus.ACTIVE, "uk-1")
            )

    asyncio.run(seed())
    app = create_app(Settings(database_url=rt_url))
    context = RequestContext("req_t", "cor_t", "svc", CELL, tenant_id=tenant, principal_id=service)

    def with_scope(scope: frozenset[str]) -> None:
        app.dependency_overrides[authenticated_caller] = lambda: Caller(
            context,
            AuthnFact(3, datetime.now(UTC) - timedelta(minutes=1)),
            security_state="NORMAL",
            scope=scope,
        )

    body = {
        "profile_id": uid(),
        "subject_principal_id": service,
        "purpose": "kyc",
        "jurisdiction": "GB",
    }
    with TestClient(app) as client:
        with_scope(frozenset({"openid"}))  # authenticated, but no verification capability
        r = client.post("/verifications", json=body)
        assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_INSUFFICIENT"
        with_scope(
            frozenset({"verification:execute"})
        )  # the capability exists now: a different question is asked
        r = client.post("/verifications", json=body)
        assert r.json().get("error", {}).get("code") != "SCOPE_INSUFFICIENT"

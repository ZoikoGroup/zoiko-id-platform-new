"""Browser privacy signals over HTTP (ZID-PRIV-001 PRIV-21).

A valid Global Privacy Control signal (`Sec-GPC: 1`) is an opt-out of sale and sharing. It needs no identity proof,
no step-up and no form: the signed-in subject's preference changes, nobody else's does, and anything other than
the exact signal changes nothing.
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


def uid() -> str:
    return str(uuid.uuid4())


def test_a_global_privacy_control_signal_opts_out_without_identity_proof(rt_url: str) -> None:
    """PRIV-21: `Sec-GPC: 1` sets the opt-out for the signed-in subject at the weakest sign-in; no other value does,
    and another subject in the same tenant is untouched."""
    tenant, subject, bystander = uid(), uid(), uid()

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (subject, bystander):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )

    asyncio.run(seed())
    app = create_app(Settings(database_url=rt_url))

    def as_subject(principal: str) -> None:
        context = RequestContext(
            "req_t", "cor_t", "web", CELL, tenant_id=tenant, principal_id=principal
        )
        # level 1, an hour old: no strong or fresh authentication is available, and none is required
        app.dependency_overrides[authenticated_caller] = lambda: Caller(
            context, AuthnFact(1, datetime.now(UTC) - timedelta(hours=1)), security_state="NORMAL"
        )

    def preferences(client: TestClient) -> dict[str, bool]:
        items = client.get("/v1/privacy/preferences").json()["items"]
        return {i["preference_type"]: i["value"] for i in items}

    with TestClient(app) as client:
        as_subject(subject)
        assert preferences(client) == {}
        for header in ({}, {"Sec-GPC": "0"}, {"Sec-GPC": "true"}, {"Sec-GPC": " 1 "}):
            r = client.post("/v1/privacy/preferences:signal", headers=header)
            assert r.status_code == 200 and r.json() == {"applied": False}, header
        assert preferences(client) == {}  # nothing changed for anything but the exact signal
        r = client.post("/v1/privacy/preferences:signal", headers={"Sec-GPC": "1"})
        assert r.status_code == 200, r.text
        assert r.json() == {"applied": True, "preference_type": "OPT_OUT_SALE_SHARING"}
        assert preferences(client) == {"OPT_OUT_SALE_SHARING": True}
        again = client.post(
            "/v1/privacy/preferences:signal", headers={"Sec-GPC": "1"}
        )  # repeat: still opted out
        assert again.status_code == 200 and preferences(client) == {"OPT_OUT_SALE_SHARING": True}
        as_subject(bystander)
        assert (
            preferences(client) == {}
        )  # one person's browser signal is not another person's choice

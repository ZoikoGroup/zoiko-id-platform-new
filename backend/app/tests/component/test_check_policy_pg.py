"""A company's WF-05 verification policy over HTTP on PostgreSQL (A11; CR-118).

A draft is submitted by its author, refused when its author approves it, approved by someone else; a new case then asks for its required checks. The
table refuses an edited policy and a second approved policy for the same market and purpose.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_check_cases_pg import _tenant
from test_check_methods_pg import _signed_overlay
from test_policy_gate_http_pg import AUD, ISS
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings


def test_a_policy_takes_effect_only_when_someone_else_approves_it(rt_url: str) -> None:
    tenant, ana = _tenant(rt_url)
    ben = str(uuid.uuid4())

    async def second() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(ben, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )

    asyncio.run(second())
    ring = KeyRing(LocalSigner("issuer-1"))
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
        ),
        keyring=ring,
    )
    app.state.privacy_overlays = {"GB": _signed_overlay("GB")}

    def bearer(who: str) -> dict[str, str]:
        t = issue_access_token(
            ring, issuer=ISS, audience=AUD, subject=who, tenant_id=tenant, client_id="onboarding-svc", now=datetime.now(UTC),
            scope=frozenset({"verification:read", "verification:execute", "verification:review"}),
        )  # fmt: skip
        return {"Authorization": f"Bearer {t}"}

    a, b = bearer(ana), bearer(ben)
    with TestClient(app, base_url=ISS) as http:
        body = {
            "market": "GB",
            "purpose": "ONBOARDING",
            "required_methods": ["DEVICE_RISK"],
            "document_types": {"UTILITY_BILL": 60},
        }
        assert (
            http.post(
                "/v1/check-policies",
                headers=a,
                json=body | {"document_types": {"UTILITY_BILL": 400}},
            ).status_code
            == 422
        )
        pid = http.post("/v1/check-policies", headers=a, json=body).json()["policy_id"]
        assert (
            http.post(f"/v1/check-policies/{pid}:submit", headers=b).status_code == 403
        )  # only the author submits
        assert (
            http.post(f"/v1/check-policies/{pid}:submit", headers=a).json()["status"] == "SUBMITTED"
        )
        reason = {"reason": "checked against the GB rules"}
        assert (
            http.post(f"/v1/check-policies/{pid}:approve", headers=a, json=reason).status_code
            == 403
        )
        assert (
            http.post(f"/v1/check-policies/{pid}:approve", headers=b, json=reason).json()["status"]
            == "APPROVED"
        )

        case = http.post(
            "/v1/verification-cases", headers={**a, "Idempotency-Key": f"pol-{pid}"},
            json={"subject_ref": "cust-5", "purpose": "ONBOARDING", "market": "GB", "methods": ["EMAIL_CHALLENGE"]},
        ).json()  # fmt: skip
        assert case["requested_methods"] == [
            "EMAIL_CHALLENGE",
            "DEVICE_RISK",
        ]  # the policy's required check is asked for

    async def edit(c: Any) -> None:
        await c.execute(
            text("UPDATE verify.check_policy SET required_methods = '{}' WHERE policy_id = :p"),
            {"p": uuid.UUID(pid)},
        )

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await edit(c)

    with pytest.raises(DBAPIError):
        asyncio.run(run())

"""WF-05 verification cases on PostgreSQL and over HTTP (ZID-DTV-WF-05 §06, §19, §20, §21, A01, A09; CR-112).

1. Over HTTP: a case needs an Idempotency-Key and replays on it; it opens ELIGIBLE with only the methods available, or CREATED with "This step is
   unavailable"; an email as the subject reference is refused; requirements show a later pause at once; evidence lists each assertion on its own;
   another tenant cannot see the case; a closed case takes no evidence.
2. The tables keep the rules without the domain code: an assertion from a method the case was not cleared for, a reason code that says fraud, an
   edited assertion and an edited case binding are all refused.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_check_methods_pg import _signed_overlay, _to_production
from test_policy_gate_http_pg import AUD, ISS
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.verification.application import check_cases as svc
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import check_cases as cc
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain.assertions import Assertion
from zid_platform.modules.verification.infrastructure import check_method_config
from zid_platform.settings import Settings

EMAIL = cm.CheckMethod.EMAIL_CHALLENGE


def _tenant(rt_url: str) -> tuple[str, str]:
    tenant, admin = str(uuid.uuid4()), str(uuid.uuid4())

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            from zid_platform.modules.principal.infrastructure.principal_store import (
                create_principal,
            )

            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await make_admin(
                c, tenant, [admin], ["verification.profile.manage"], ["verification_profile:*"]
            )

    asyncio.run(seed())
    return tenant, admin


def _in_tenant(rt_url: str, tenant: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await work(c)

    return asyncio.run(go())


def test_a_case_over_http_and_its_tables(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = uuid.uuid4().hex[:6]
    adapter = cm.Adapter(EMAIL, "zid-email-challenge", version, cm.Route.OPEN)
    approved = {
        c: cm.RetentionRule(c, "APPROVED", 30, "controller", "L-1") for c in cm.ALL_DATA_CLASSES
    }
    monkeypatch.setattr(check_method_config, "adapters", lambda: {EMAIL: adapter})
    monkeypatch.setattr(check_method_config, "retention", lambda: approved)

    (a_tenant, a_admin), (b_tenant, b_admin) = _tenant(rt_url), _tenant(rt_url)
    sid = f"email-gb-{version}"
    scope = bm.Scope(EMAIL, "zid-email-challenge", version, "GB", "ONBOARDING", a_tenant)

    async def live(c: Any) -> bm.Record:
        return await _to_production(c, scope, sid)

    asyncio.run(_owner(owner_url, live))

    ring = KeyRing(LocalSigner("issuer-1"))
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test"),
        keyring=ring,
    )  # fmt: skip
    app.state.privacy_overlays = {"GB": _signed_overlay("GB")}

    def bearer(tenant: str, admin: str) -> dict[str, str]:
        token = issue_access_token(
            ring, issuer=ISS, audience=AUD, subject=admin, tenant_id=tenant, client_id="onboarding-svc",
            now=datetime.now(UTC), scope=frozenset({"verification:execute", "verification:read"}),
        )  # fmt: skip
        return {"Authorization": f"Bearer {token}"}

    a, b = bearer(a_tenant, a_admin), bearer(b_tenant, b_admin)
    body = {
        "subject_ref": "cust-0042",
        "purpose": "ONBOARDING",
        "market": "GB",
        "methods": ["EMAIL_CHALLENGE", "DEVICE_RISK"],
    }
    with TestClient(app, base_url=ISS) as http:
        assert (
            http.post("/v1/verification-cases", json=body, headers=a).status_code == 422
        )  # no key
        key = {**a, "Idempotency-Key": f"k-{version}"}
        first = http.post("/v1/verification-cases", json=body, headers=key)
        assert first.status_code == 201, first.text
        case = first.json()
        assert case["status"] == "ELIGIBLE" and case["available_methods"] == ["EMAIL_CHALLENGE"]
        assert any(r.startswith("DEVICE_RISK:NO_ADAPTER") for r in case["unavailable_reasons"])
        assert (
            http.post("/v1/verification-cases", json=body, headers=key).json()["case_id"]
            == case["case_id"]
        )
        assert (
            http.post(
                "/v1/verification-cases", json=body | {"market": "FR"}, headers=key
            ).status_code
            == 409
        )

        none = http.post(
            "/v1/verification-cases", json=body | {"methods": ["DEVICE_RISK"]}, headers={**a, "Idempotency-Key": f"n-{version}"}
        ).json()  # fmt: skip
        assert none["status"] == "CREATED" and none["message"] == "This step is unavailable."
        email_ref = body | {"subject_ref": "dana@example.com"}
        assert (
            http.post(
                "/v1/verification-cases",
                json=email_ref,
                headers={**a, "Idempotency-Key": f"e-{version}"},
            ).status_code
            == 422
        )

        cid = case["case_id"]
        req = http.get(f"/v1/verification-cases/{cid}/requirements", headers=a).json()
        assert req["usable_methods"] == ["EMAIL_CHALLENGE"]
        assert (
            http.get(f"/v1/verification-cases/{cid}", headers=b).status_code == 404
        )  # another tenant
        assert http.get(f"/v1/verification-cases/{cid}/evidence", headers=b).status_code == 404

        async def record(c: Any) -> cc.AssertionRecord:
            return await svc.record_assertion(
                c, cid, assertion=Assertion.CONTACT_CONTROL_CONFIRMED, verdict=cc.Verdict.PASS, method=EMAIL,
                source=f"zid-email-challenge@{version}", reason_codes=(), confidence=None, observed_at=datetime.now(UTC),
                expires_at=None, recorded_by="adapter", now=datetime.now(UTC),
            )  # fmt: skip

        _in_tenant(rt_url, a_tenant, record)
        ev = http.get(f"/v1/verification-cases/{cid}/evidence", headers=a).json()
        assert ev["status"] == "IN_PROGRESS"
        assert [x["type"] for x in ev["assertions"]] == ["CONTACT_CONTROL_CONFIRMED"]
        assert "overall" not in {k.lower() for k in ev}

        async def pause(c: Any) -> bm.Record:
            from zid_platform.modules.verification.application import check_methods

            return await check_methods.step_down(
                c, sid, target=bm.State.PAUSED, by="sec", reason="abuse on this route", evidence_ref="INC-1", now=datetime.now(UTC)
            )  # fmt: skip

        asyncio.run(_owner(owner_url, pause))
        paused = http.get(f"/v1/verification-cases/{cid}/requirements", headers=a).json()
        assert paused["usable_methods"] == [] and paused["message"] == "This step is unavailable."

        # the tables, without the domain code
        def refused(sql: str, params: dict[str, Any]) -> None:
            async def go(c: Any) -> None:
                await c.execute(text(sql), params)

            with pytest.raises(DBAPIError):
                _in_tenant(rt_url, a_tenant, go)

        insert = (
            "INSERT INTO verify.check_assertion (assertion_id, tenant_id, case_id, assertion, verdict, method, source, reason_codes, "
            "observed_at, recorded_by, recorded_at) VALUES (:id, :t, :c, :a, :v, :m, 's@1', :rc, now(), 'x', now())"
        )
        row = {
            "t": uuid.UUID(a_tenant),
            "c": uuid.UUID(cid),
            "a": "DEVICE_RISK_OBSERVED",
            "v": "REVIEW",
        }
        refused(
            insert, {**row, "id": uuid.uuid4(), "m": "DEVICE_RISK", "rc": ["EMULATOR"]}
        )  # not cleared
        refused(
            insert,
            {
                **row,
                "id": uuid.uuid4(),
                "a": "CONTACT_CONTROL_CONFIRMED",
                "m": "EMAIL_CHALLENGE",
                "rc": ["SUSPECTED_FRAUD"],
            },
        )
        refused(
            "UPDATE verify.check_assertion SET verdict = 'FAIL' WHERE case_id = :c",
            {"c": uuid.UUID(cid)},
        )
        refused(
            "UPDATE verify.check_case SET market = 'FR' WHERE case_id = :c", {"c": uuid.UUID(cid)}
        )

        closed = http.post(f"/v1/verification-cases/{cid}:close", headers=a)
        assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"
        assert http.post(f"/v1/verification-cases/{cid}:close", headers=a).status_code == 409
        with pytest.raises(cc.CaseError):
            _in_tenant(rt_url, a_tenant, record)


async def _owner(owner_url: str, work: Any) -> Any:
    async with engine(owner_url) as eng, eng.begin() as c:
        return await work(c)

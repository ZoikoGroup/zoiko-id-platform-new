"""Commercial catalogue, plans and entitlements (ZID-COM-001 §5, §6, §29; CR-024).

The seed is compared with the proposal table written out independently of the migration. A DRAFT price
book prices nothing; an approved one is frozen by the database; entitlements keep their history, one is
active per tenant, rows are tenant-isolated, and the gate refuses paid extras without ever blocking a
security path.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from scim_fixture import KEY
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.application import service
from zid_platform.modules.commercial.domain.catalogue import CommercialError
from zid_platform.modules.commercial.infrastructure import catalogue_store as store
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.settings import Settings

PLANS = ("DEVELOPER", "LAUNCH", "BUSINESS", "SCALE", "ENTERPRISE")
# ZID-COM-001 §6: fee, MAH, auth events, policy decisions, M2M grants, SSO included, environments
SECTION_6 = {
    "DEVELOPER": ("0", 2500, 25_000, 50_000, 0, 0, 1),
    "LAUNCH": ("299", 10_000, 150_000, 500_000, 50_000, 0, 1),
    "BUSINESS": ("1499", 50_000, 1_000_000, 5_000_000, 250_000, 1, 2),
    "SCALE": ("4999", 250_000, 5_000_000, 25_000_000, 1_000_000, 3, 3),
    "ENTERPRISE": ("12500", None, None, None, None, None, None),
}
# ZID-COM-001 §29, per plan in PLANS order. N=not available.
SECTION_29 = {
    "human_iam": ("INCLUDED", "INCLUDED", "INCLUDED", "INCLUDED", "CONTRACT"),
    "organizations": ("INCLUDED", "INCLUDED", "INCLUDED", "INCLUDED", "INCLUDED"),
    "passkeys_mfa": ("INCLUDED", "INCLUDED", "INCLUDED", "INCLUDED", "CONTRACT"),
    "oidc_oauth": ("INCLUDED", "INCLUDED", "INCLUDED", "INCLUDED", "INCLUDED"),
    "saml_sso": ("NONE", "ADD_ON", "INCLUDED", "INCLUDED", "CONTRACT"),
    "scim": ("NONE", "ADD_ON", "ADD_ON", "ADD_ON", "CONTRACT"),
    "policy_engine": ("INCLUDED", "INCLUDED", "INCLUDED", "INCLUDED", "CONTRACT"),
    "consent_receipts": ("SANDBOX", "INCLUDED", "INCLUDED", "INCLUDED", "CONTRACT"),
    "credential_issue_verify": ("SANDBOX", "METERED", "METERED", "METERED", "CONTRACT"),
    "idv_kyb": ("SANDBOX", "QUALIFIED", "QUALIFIED", "QUALIFIED", "QUALIFIED"),
    "audit_export": ("INCLUDED", "INCLUDED", "INCLUDED", "INCLUDED", "CONTRACT"),
    "agent_principal": ("SANDBOX", "METERED", "METERED", "METERED", "CONTRACT"),
    "dedicated_region": ("NONE", "NONE", "ADD_ON", "ADD_ON", "CONTRACT"),
    "operational_sla": ("NONE", "NONE", "QUALIFIED", "QUALIFIED", "CONTRACT"),
}
# §7 overage: sku -> (launch, business, scale, divisor)
SECTION_7 = {
    "ZID-HUM": ("0.025", "0.015", "0.009", 1),
    "ZID-AUTH": ("0.40", "0.25", "0.15", 1000),
    "ZID-PDP": ("0.60", "0.45", "0.25", 10_000),
    "ZID-M2M": ("0.50", "0.35", "0.20", 1000),
    "ZID-FED": ("149", "149", "125", 1),
    "ZID-SCIM": ("149", "149", "125", 1),
    "ZID-VCI": ("0.08", "0.05", "0.03", 1),
    "ZID-VCP": ("0.025", "0.015", "0.010", 1),
}
UNRELEASED = {"idv_kyb", "dedicated_region", "operational_sla"}


def uid() -> str:
    return str(uuid.uuid4())


def settings(rt_url: str, **kw: Any) -> Settings:
    return Settings(
        database_url=rt_url,
        token_issuer=ISS,
        token_audience=AUD,
        public_base_url=AUD,
        environment="test",
        cell_id="uk-1",
        cell_uuid=CELL,
        **kw,
    )


def test_seed_matches_the_proposal_and_nothing_is_priced_until_approved(
    rt_url: str, owner_url: str
) -> None:
    author, approver = uid(), uid()

    async def read() -> dict[str, Any]:
        async with engine(rt_url) as eng, eng.begin() as c:
            plans = {r["plan_code"]: r for r in await store.plans(c)}
            caps = {
                (r.plan_code, r.capability_key): r.mode
                for r in (
                    await c.execute(
                        text(
                            "SELECT plan_code, capability_key, mode FROM commercial.plan_capability"
                        )
                    )
                ).all()
            }
            counts = {
                (r.plan_code, r.capability_key): r.included_count
                for r in (
                    await c.execute(
                        text(
                            "SELECT plan_code, capability_key, included_count FROM commercial.plan_capability "
                            "WHERE included_count IS NOT NULL"
                        )
                    )
                ).all()
            }
            released = {
                r.capability_key: r.released
                for r in (
                    await c.execute(
                        text("SELECT capability_key, released FROM commercial.capability")
                    )
                ).all()
            }
            book = (
                (await c.execute(text("SELECT * FROM commercial.price_book WHERE version = 1")))
                .mappings()
                .one()
            )
            lines = {
                (r["sku_id"], r["plan_code"]): (r["unit_rate"], r["unit_divisor"])
                for r in await store.lines(c, str(book["price_book_id"]))
            }
        return {
            "plans": plans,
            "caps": caps,
            "counts": counts,
            "released": released,
            "book": book,
            "lines": lines,
        }

    seed = asyncio.run(read())
    for plan, (fee, mah, auth, pdp, m2m, sso, envs) in SECTION_6.items():
        p = seed["plans"][plan]
        assert p["platform_fee"] == Decimal(fee)
        assert (
            p["mah_included"],
            p["auth_events_included"],
            p["policy_decisions_included"],
            p["m2m_grants_included"],
            p["sso_included"],
            p["production_environments"],
        ) == (mah, auth, pdp, m2m, sso, envs)
    assert (
        seed["plans"]["ENTERPRISE"]["fee_is_floor"] is True
    )  # "From $12,500": a floor, never a price
    for cap, modes in SECTION_29.items():
        for plan, mode in zip(PLANS, modes, strict=True):
            assert seed["caps"][(plan, cap)] == mode, (plan, cap)
    assert seed["counts"] == {("BUSINESS", "saml_sso"): 1, ("SCALE", "saml_sso"): 3}
    assert {k for k, v in seed["released"].items() if not v} == UNRELEASED
    book = seed["book"]
    assert (
        book["status"] == "DRAFT" and book["approved_by"] is None and book["effective_from"] is None
    )
    for sku, (launch, business, scale, divisor) in SECTION_7.items():
        for plan, rate in zip(
            ("LAUNCH", "BUSINESS", "SCALE"), (launch, business, scale), strict=True
        ):
            assert seed["lines"][(sku, plan)] == (Decimal(rate), divisor), (sku, plan)
    for plan, (fee, *_rest) in SECTION_6.items():
        assert seed["lines"][("ZID-BASE", plan)][0] == Decimal(fee)

    app = create_app(settings(rt_url))
    with TestClient(app) as http:
        r = http.get("/v1/catalog")  # public: no credentials
        assert r.status_code == 200
        cat = r.json()
        # other tests in this database may already have approved a book; the seeded DRAFT (v1) is never shown
        assert cat["pricing"] is None or cat["pricing"]["version"] != 1
        assert cat["status"] == ("UNPRICED" if cat["pricing"] is None else "PRICED")
        assert [p["plan"] for p in cat["plans"]] == list(PLANS)
        shown = {c["capability"] for p in cat["plans"] for c in p["capabilities"]}
        assert shown and not (shown & UNRELEASED), "an unreleased capability must not be advertised"
        if cat["pricing"] is None:
            assert "unit_rate" not in r.text, "a DRAFT price book is never visible"

    async def approval() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            draft = await store.create_draft(
                c, copy_of=str(book["price_book_id"]), created_by=author, note="v2"
            )
            assert draft["version"] >= 2 and draft["status"] == "DRAFT"
            did = draft["price_book_id"]
            assert len(await store.lines(c, did)) == len(
                seed["lines"]
            )  # the copy carries every line
            await store.put_line(
                c,
                did,
                sku_id="ZID-HUM",
                plan_code="BUSINESS",
                unit_rate=Decimal("0.016"),
                unit_divisor=1,
                included_quantity=None,
                minimum_amount=None,
            )
            with pytest.raises(CommercialError) as e:
                await store.approve(
                    c, did, approver=author, effective_from=datetime.now(UTC), now=datetime.now(UTC)
                )
            assert e.value.code == "PRICE_BOOK_SELF_APPROVAL"
        async with engine(rt_url) as eng, eng.begin() as c:
            now = datetime.now(UTC)
            await store.approve(
                c, did, approver=approver, effective_from=now - timedelta(seconds=1), now=now
            )
        # a DRAFT row is editable, an APPROVED one is frozen by the database itself (even for its owner)
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.price_book_line SET unit_rate = 0 WHERE price_book_id = :b",
                (
                    "INSERT INTO commercial.price_book_line (price_book_id, sku_id, plan_code, unit_rate) "
                    "VALUES (:b, 'ZID-VCI', 'DEVELOPER', 1)"
                ),
                "DELETE FROM commercial.price_book_line WHERE price_book_id = :b",
                "UPDATE commercial.price_book SET currency = 'EUR' WHERE price_book_id = :b",
                "DELETE FROM commercial.price_book WHERE price_book_id = :b",
            ):
                with pytest.raises(DBAPIError, match="immutable|cannot be deleted"):
                    async with eng.begin() as c:
                        await c.execute(text(sql), {"b": uuid.UUID(did)})
        async with engine(rt_url) as eng, eng.begin() as c:
            with pytest.raises(CommercialError) as e2:
                await store.approve(
                    c, did, approver=uid(), effective_from=datetime.now(UTC), now=datetime.now(UTC)
                )
            assert e2.value.code == "PRICE_BOOK_NOT_DRAFT"

    asyncio.run(approval())
    with TestClient(app) as http:
        cat = http.get("/v1/catalog").json()
        assert cat["status"] == "PRICED" and cat["pricing"]["currency"] == "USD"
        lines = {(ln["sku"], ln["plan"]): ln for ln in cat["pricing"]["lines"]}
        assert (
            lines[("ZID-HUM", "BUSINESS")]["unit_rate"] == "0.016"
        )  # the approved book, not the draft seed
        assert lines[("ZID-BASE", "BUSINESS")]["unit_rate"] == "1499"
        assert all(ln["released"] for ln in cat["pricing"]["lines"])


def test_entitlements_history_isolation_and_the_gate(rt_url: str, owner_url: str) -> None:
    a, b, operator = uid(), uid(), uid()
    now = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(a)) as c:
                v = await service.entitlement_view(c)
                assert (
                    v["plan"] == "DEVELOPER" and v["source"] == "DEFAULT" and v["state"] == "ACTIVE"
                )
                # the default plan: enterprise SSO is not included
                r = await service.entitled(c, "saml_sso", used=0, enforcement="enforce")
                assert not r.allowed and r.reason == "NOT_IN_PLAN" and r.plan_code == "DEVELOPER"
                shadow = await service.entitled(c, "saml_sso", used=0, enforcement="shadow")
                assert shadow.allowed and shadow.shadow_denied and not shadow.would_allow
                assert (await service.entitled(c, "oidc_oauth", enforcement="enforce")).allowed
                sb = await service.entitled(c, "credential_issue_verify", enforcement="enforce")
                assert sb.allowed and sb.sandbox_only

                first = await store.set_entitlement(
                    c,
                    tenant_id=a,
                    plan_code="BUSINESS",
                    price_book_id=None,
                    actor=operator,
                    now=now,
                )
                assert first["version"] == 1 and first["previous_plan"] is None
            async with tenant_transaction(eng, ctx(a)) as c:
                one = await service.entitled(c, "saml_sso", used=0, enforcement="enforce")
                assert one.allowed and one.limit == 1
                full = await service.entitled(c, "saml_sso", used=1, enforcement="enforce")
                assert not full.allowed and full.reason == "QUOTA_EXHAUSTED" and full.limit == 1
                assert not (
                    await service.entitled(c, "scim", used=0, enforcement="enforce")
                ).allowed  # ADD_ON
                view = await service.entitlement_view(c, used={"saml_sso": 1})
                sso = next(x for x in view["capabilities"] if x["capability"] == "saml_sso")
                assert view["plan"] == "BUSINESS" and (sso["limit"], sso["used"]) == (1, 1)

                # a contracted exception adds room; an unreleased capability stays closed whatever is granted
                await store.add_override(
                    c,
                    tenant_id=a,
                    capability_key="scim",
                    grant_mode=None,
                    extra_count=2,
                    reason="signed order 42",
                    approved_by=uid(),
                    created_by=uid(),
                    expires_at=None,
                )
                await store.add_override(
                    c,
                    tenant_id=a,
                    capability_key="idv_kyb",
                    grant_mode="CONTRACT",
                    extra_count=None,
                    reason="signed order 43",
                    approved_by=uid(),
                    created_by=uid(),
                    expires_at=None,
                )
                assert (await service.entitled(c, "scim", used=1, enforcement="enforce")).allowed
                assert not (
                    await service.entitled(c, "scim", used=2, enforcement="enforce")
                ).allowed
                idv = await service.entitled(c, "idv_kyb", enforcement="enforce")
                assert not idv.allowed and idv.reason == "NOT_RELEASED"

                # moving plan ends the old entitlement and keeps it as history; never backdated
                with pytest.raises(CommercialError) as e:
                    await store.set_entitlement(
                        c,
                        tenant_id=a,
                        plan_code="SCALE",
                        price_book_id=None,
                        actor=operator,
                        now=now - timedelta(days=1),
                    )
                assert e.value.code == "ENTITLEMENT_EFFECTIVE_DATE"
            async with tenant_transaction(eng, ctx(a)) as c:
                second = await store.set_entitlement(
                    c,
                    tenant_id=a,
                    plan_code="SCALE",
                    price_book_id=None,
                    actor=operator,
                    now=now + timedelta(seconds=1),
                )
                assert second["version"] == 2 and second["previous_plan"] == "BUSINESS"
                hist = await store.entitlement_history(c)
                assert [(h["plan_code"], h["state"]) for h in hist] == [
                    ("SCALE", "ACTIVE"),
                    ("BUSINESS", "ENDED"),
                ]
                assert (
                    await service.entitled(c, "saml_sso", used=2, enforcement="enforce")
                ).allowed  # 3 on SCALE

                # one active entitlement per tenant is a database rule, not a convention
                with pytest.raises(IntegrityError):
                    async with c.begin_nested():
                        await c.execute(
                            text(
                                "INSERT INTO commercial.entitlement (entitlement_id, tenant_id, plan_code, state, "
                                "effective_from, source) VALUES (:i, :t, 'LAUNCH', 'ACTIVE', now(), 'OPERATOR')"
                            ),
                            {"i": uuid.uuid4(), "t": uuid.UUID(a)},
                        )
                assert await store.set_state(c, "SUSPENDED")
                s = await service.entitled(c, "saml_sso", used=0, enforcement="enforce")
                assert not s.allowed and s.reason == "SUSPENDED"
                assert await store.set_state(c, "ACTIVE")

        # rows are never rewritten or deleted beyond state and end date
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.entitlement SET plan_code = 'ENTERPRISE' WHERE tenant_id = :t",
                "DELETE FROM commercial.entitlement WHERE tenant_id = :t",
            ):
                with pytest.raises(DBAPIError, match="only change its state|never deleted"):
                    async with eng.begin() as c:
                        await c.execute(text(sql), {"t": uuid.UUID(a)})
            with pytest.raises(DBAPIError, match="final"):
                async with eng.begin() as c:
                    await c.execute(
                        text(
                            "UPDATE commercial.entitlement SET state = 'ACTIVE' WHERE tenant_id = :t AND state = 'ENDED'"
                        ),
                        {"t": uuid.UUID(a)},
                    )

        # tenant isolation: B sees none of A's rows, cannot write for A, and starts on the default plan
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(b)) as c:
            assert await store.entitlement_history(c) == []
            assert (await service.entitlement_view(c))["plan"] == "DEVELOPER"
            assert await store.overrides(c, now) == []
            with pytest.raises(DBAPIError, match="row-level security"):
                await store.set_entitlement(
                    c,
                    tenant_id=a,
                    plan_code="ENTERPRISE",
                    price_book_id=None,
                    actor=None,
                    now=now,
                )

    asyncio.run(run())


def test_the_gate_fails_closed_for_a_paid_extra_only_when_enforcing(rt_url: str) -> None:
    class Broken:
        def begin_nested(self) -> Any:
            raise ConnectionError("entitlement store down")

    async def run() -> None:
        denied = await service.entitled(Broken(), "saml_sso", enforcement="enforce")  # type: ignore[arg-type]
        assert not denied.allowed and denied.reason == "ENTITLEMENT_UNAVAILABLE"
        for mode in ("shadow", "off"):
            soft = await service.entitled(Broken(), "saml_sso", enforcement=mode)  # type: ignore[arg-type]
            assert soft.allowed and soft.reason == "ENTITLEMENT_UNAVAILABLE"

    asyncio.run(run())


def test_plan_limits_over_http_and_security_paths_are_never_gated(rt_url: str) -> None:
    o = onboard(
        rt_url,
        extra_actions=["commercial.read", "provisioning.source.manage"],
        extra_resources=["provisioning_source:*"],
    )
    other = onboard(rt_url)
    nobody = uid()

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await create_principal(
                c, Principal(nobody, o.tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )

    asyncio.run(seed())
    enforcing = create_app(settings(rt_url, commercial_enforcement="enforce"), keyring=o.ring)
    soft = create_app(settings(rt_url), keyring=o.ring)  # the default: shadow
    adm = o.bearer(o.owner)

    with TestClient(enforcing, base_url=ISS) as http:
        r = http.get(f"/v1/tenants/{o.tenant}/entitlements", headers=adm)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["plan"] == "DEVELOPER" and body["source"] == "DEFAULT"
        assert body["limits"]["sso_included"] == 0 and body["limits"]["mah_included"] == 2500
        assert (
            http.get(f"/v1/tenants/{other.tenant}/entitlements", headers=adm).status_code == 404
        )  # reveals nothing
        assert http.get(f"/v1/tenants/{o.tenant}/entitlements").status_code == 401
        assert (
            http.get(f"/v1/tenants/{o.tenant}/entitlements", headers=o.bearer(nobody)).status_code
            == 403
        )  # a member with no commercial.read authority

        # a directory connection is a paid extra: refused on the Developer plan when enforcing
        r = http.post(
            "/provisioning/sources",
            headers=adm,
            json={"client_id": f"hr-{uid()[:8]}", "display_name": "HR"},
        )
        assert r.status_code == 402 and r.json()["error"]["code"] == "ENTITLEMENT_REQUIRED", r.text

        # while identity itself keeps working: the same token reads and acts on security-path endpoints
        assert http.get("/health/live").status_code == 200
        assert http.get(f"/v1/tenants/{o.tenant}/entitlements", headers=adm).status_code == 200

    with TestClient(soft, base_url=ISS) as http:  # shadow: reported, never blocked
        r = http.post(
            "/provisioning/sources",
            headers=adm,
            json={"jwks": {"keys": [KEY]}, "display_name": "HR"},
        )
        assert r.status_code == 201, r.text


def test_the_operator_surface_needs_grants_and_a_second_approver(
    rt_url: str, owner_url: str
) -> None:
    from operator_fixture import activate_grant, operators
    from zid_platform.modules.operator_access.domain.model import OperatorRole

    alice, dave, bob, carol = uid(), uid(), uid(), uid()
    tenant = uid()
    o = operators(
        rt_url,
        {
            alice: {OperatorRole.REQUESTER},
            dave: {OperatorRole.REQUESTER},
            bob: {OperatorRole.APPROVER},
            carol: {OperatorRole.APPROVER},
        },
    )
    customer = onboard(rt_url)
    app = create_app(
        settings(rt_url, operator_tenant_id=o.tenant, operator_client_ids=(o.client,)),
        keyring=o.ring,
    )
    with TestClient(app, base_url=ISS) as api:
        # a customer-tenant identity is not an operator, whatever it holds
        r = api.post("/v1/commercial/price-books", json={}, headers=customer.bearer(customer.owner))
        assert r.status_code in (401, 403)  # not this cell's operator plane
        assert api.post("/v1/commercial/price-books", json={}).status_code == 401

        # an operator without the grant is refused
        r = api.post("/v1/commercial/price-books", json={}, headers=o.bearer(alice))
        assert r.status_code == 403 and r.json()["error"]["code"] == "OPERATOR_CAPABILITY_REQUIRED"

        scope = {
            "capability": "commercial.catalog.manage",
            "resource_scope": "commercial:catalogue",
        }
        activate_grant(api, o, alice, (bob, carol), **scope)
        activate_grant(api, o, dave, (bob, carol), **scope)
        books = api.get("/v1/commercial/price-books", headers=o.bearer(alice)).json()["items"]
        assert books and all(b["status"] in ("DRAFT", "APPROVED", "SUPERSEDED") for b in books)
        r = api.post(
            "/v1/commercial/price-books",
            json={"copy_of": books[-1]["price_book_id"], "note": "operator draft"},
            headers=o.bearer(alice),
        )
        assert r.status_code == 201, r.text
        draft = r.json()["price_book_id"]
        r = api.put(
            f"/v1/commercial/price-books/{draft}/lines",
            json={"sku": "ZID-AUTH", "plan": "BUSINESS", "unit_rate": "0.26", "unit_divisor": 1000},
            headers=o.bearer(alice),
        )
        assert r.status_code == 200, r.text
        bad = api.put(
            f"/v1/commercial/price-books/{draft}/lines",
            json={"sku": "ZID-AUTH", "plan": "BUSINESS", "unit_rate": "-1"},
            headers=o.bearer(alice),
        )
        assert bad.status_code == 422
        r = api.post(
            f"/v1/commercial/price-books/{draft}:approve", json={}, headers=o.bearer(alice)
        )
        assert r.status_code == 403 and r.json()["error"]["code"] == "PRICE_BOOK_SELF_APPROVAL"
        r = api.post(f"/v1/commercial/price-books/{draft}:approve", json={}, headers=o.bearer(dave))
        assert r.status_code == 200 and r.json()["status"] == "APPROVED", r.text
        r = api.put(
            f"/v1/commercial/price-books/{draft}/lines",
            json={"sku": "ZID-AUTH", "plan": "BUSINESS", "unit_rate": "0.27"},
            headers=o.bearer(alice),
        )
        assert r.status_code == 409 and r.json()["error"]["code"] == "PRICE_BOOK_NOT_DRAFT"
        shown = api.get(f"/v1/commercial/price-books/{draft}", headers=o.bearer(alice)).json()
        auth = next(
            ln for ln in shown["lines"] if ln["sku"] == "ZID-AUTH" and ln["plan"] == "BUSINESS"
        )
        assert auth["unit_rate"] == "0.26"

        # select a plan, enter the amount (CR-147): a new draft of the approved book with only that plan's platform fee changed, the same in every region
        r = api.post(
            "/v1/commercial/plan-prices",
            json={"plan": "LAUNCH", "amount": "349.00"},
            headers=o.bearer(alice),
        )
        assert r.status_code == 201, r.text
        priced = r.json()
        assert priced["plan"] == "LAUNCH" and priced["amount"] == "349.00"
        assert priced["previous_amount"] == "299" and priced["currency"] == "USD"
        assert (
            priced["status"] == "DRAFT" and priced["awaiting_approval"] is True
        )  # alice may not approve her own book
        book = api.get(
            f"/v1/commercial/price-books/{priced['price_book_id']}", headers=o.bearer(alice)
        ).json()
        fee = {ln["plan"]: ln["unit_rate"] for ln in book["lines"] if ln["sku"] == "ZID-BASE"}
        assert fee["LAUNCH"] == "349" and fee["BUSINESS"] == "1499" and fee["SCALE"] == "4999"
        kept = next(
            ln for ln in book["lines"] if ln["sku"] == "ZID-AUTH" and ln["plan"] == "BUSINESS"
        )
        assert kept["unit_rate"] == "0.26"  # every other line is carried over unchanged
        for bad_body in (
            {"plan": "LAUNCH", "amount": "0"},
            {"plan": "LAUNCH", "amount": "-5"},
            {"plan": "LAUNCH", "amount": "10.999"},
            {"plan": "DEVELOPER", "amount": "10"},
            {"plan": "LAUNCH", "amount": "10", "region": "eu"},
        ):
            assert (
                api.post(
                    "/v1/commercial/plan-prices", json=bad_body, headers=o.bearer(alice)
                ).status_code
                == 422
            ), bad_body
        assert (
            api.post(
                "/v1/commercial/plan-prices", json={"plan": "LAUNCH", "amount": "1"}
            ).status_code
            == 401
        )

        # moving a tenant to a plan needs a grant for that tenant, and records the change
        body = {"plan": "BUSINESS"}
        url = f"/v1/commercial/tenants/{tenant}/entitlement"
        assert api.put(url, json=body, headers=o.bearer(alice)).status_code == 403
        activate_grant(
            api,
            o,
            alice,
            (bob, carol),
            capability="tenant.config.change",
            resource_scope=f"tenant:{tenant}",
            target_tenant_id=tenant,
        )
        r = api.put(url, json=body, headers=o.bearer(alice))
        assert r.status_code == 200, r.text
        assert r.json()["plan_code"] == "BUSINESS" and r.json()["version"] == 1
        assert api.put(url, json={"plan": "PLATINUM"}, headers=o.bearer(alice)).status_code == 422
        # a grant for one tenant does not move another
        other = f"/v1/commercial/tenants/{uid()}/entitlement"
        assert api.put(other, json=body, headers=o.bearer(alice)).status_code == 403

        ovr = f"/v1/commercial/tenants/{tenant}/overrides"
        payload = {
            "capability": "scim",
            "extra_count": 2,
            "reason": "signed order 7",
            "approver_id": alice,
        }
        assert (
            api.post(ovr, json=payload, headers=o.bearer(alice)).status_code == 403
        )  # self-approval
        payload["approver_id"] = str(uuid.uuid4())
        assert (
            api.post(ovr, json=payload, headers=o.bearer(alice)).status_code == 403
        )  # not an operator
        payload["approver_id"] = bob
        r = api.post(ovr, json=payload, headers=o.bearer(alice))
        assert r.status_code == 201, r.text

    async def check() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT plan_code, state, source FROM commercial.entitlement WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).all()
            ev = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE tenant_id = :t "
                        "AND event_type = 'com.zoiko.id.commercial.entitlement.changed.v1'"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).scalar_one()
            o_rows = (
                await c.execute(
                    text(
                        "SELECT capability_key, extra_count, approved_by <> created_by "
                        "FROM commercial.entitlement_override WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).all()
        assert [tuple(r) for r in rows] == [("BUSINESS", "ACTIVE", "OPERATOR")]
        assert ev == 1
        assert [tuple(r) for r in o_rows] == [("scim", 2, True)]

    asyncio.run(check())

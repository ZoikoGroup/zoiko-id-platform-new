"""Capability bands and usage reservations on a real database (ZID-ENG-DEC-008 P0 tests 01-07, 09-10, 12 at the API and ledger level).

A plan is read from the tenant's active entitlement, never from the request. The customer-facing view says Included, Available as an
add-on or Available with upgrade; an enforced refusal names the band that would include the feature; a plan change comes only from the
operator's order path; a downgrade is disclosed and deletes nothing; and usage reservations are idempotent, serialized per meter and
refuse only the operation that crosses a cap.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_platform.main import create_app
from zid_platform.modules.commercial.application import usage_reservations as usage
from zid_platform.modules.commercial.infrastructure import catalogue_store as store
from zid_platform.modules.commercial.infrastructure.bands import meter_register
from zid_platform.settings import Settings


def uid() -> str:
    return str(uuid.uuid4())


def settings(url: str, **kw: Any) -> Settings:
    return Settings(
        database_url=url,
        token_issuer=ISS,
        token_audience=AUD,
        public_base_url=AUD,
        environment="test",
        cell_id="uk-1",
        **kw,
    )


def move_plan(rt_url: str, tenant: str, plan: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await store.set_entitlement(
                c,
                tenant_id=tenant,
                plan_code=plan,
                price_book_id=None,
                actor=uid(),
                now=datetime.now(UTC),
            )

    asyncio.run(run())


def by_feature(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["feature"]: f for f in view["features"]}


def test_the_band_view_the_refusal_and_the_downgrade_disclosure(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=["commercial.read"])
    app = create_app(settings(rt_url, commercial_enforcement="enforce"), keyring=o.ring)
    adm = o.bearer(o.owner)

    with TestClient(app, base_url=ISS) as http:
        view = http.get("/v1/entitlements/effective", headers=adm)
        assert view.status_code == 200, view.text
        v = view.json()
        assert (v["plan"], v["band"], v["catalog_version"]) == (
            "DEVELOPER",
            "C1",
            "ZID-ENG-DEC-008-v1.0",
        )
        assert (
            len(v["features"]) == 40
            and v["commercial_enforcement"] == "enforce"
            and v["band_matrix_enforcement"] == "REPORT_ONLY"
        )
        f = by_feature(v)
        # acceptance 01: the protected baseline is included on the smallest plan, with no plan reason attached
        base = [x for x in v["features"] if x["section"] == "baseline"]
        assert len(base) == 8 and all(
            x["available"] and x["label"] == "Included" and not x["reasons"] for x in base
        )
        # what the plan lacks says which band would include it
        scim = f["advanced.scim-automated-provisioning"]
        assert (
            not scim["available"]
            and scim["label"] == "Available with upgrade"
            and scim["upgrade_band"] == "C4"
        )
        assert f["standard.custom-domain-hosted-brand-theme"]["upgrade_band"] == "C2"
        assert f["standard.hosted-authentication-oidc-core"]["available"]
        assert http.get("/v1/entitlements/effective").status_code == 401

        # acceptance 02: through the API, a C1 tenant cannot do C2, C3 or C4 work, and is told what would unlock it
        r = http.post(
            "/v1/tenant/domains", headers=adm, json={"domain": f"acme-{uid()[:6]}.example.com"}
        )
        assert r.status_code == 402 and r.json()["error"]["code"] == "ENTITLEMENT_REQUIRED", r.text
        assert {d["issue"] for d in r.json()["error"]["details"]} >= {"NOT_IN_BAND", "C2"}
        r = http.post(
            "/provisioning/sources",
            headers=adm,
            json={"client_id": f"hr-{uid()[:8]}", "display_name": "HR"},
        )
        assert r.status_code == 402, r.text

        # acceptance 04: asking for a plan changes nothing; only the operator's order path does
        assert (
            by_feature(http.get("/v1/entitlements/effective", headers=adm).json())[
                "advanced.enterprise-sso-saml-oidc-federation"
            ]["available"]
            is False
        )
        move_plan(rt_url, o.tenant, "BUSINESS")
        up = http.get("/v1/entitlements/effective", headers=adm).json()
        assert (up["plan"], up["band"], up["source"]) == ("BUSINESS", "C3", "OPERATOR")
        g = by_feature(up)
        assert (
            g["advanced.enterprise-sso-saml-oidc-federation"]["available"]
            and g["standard.custom-domain-hosted-brand-theme"]["available"]
        )
        assert not g["advanced.scim-automated-provisioning"][
            "available"
        ]  # SCIM is an add-on of the Estate and C4 in the record

        # acceptance 05: the downgrade is disclosed first, applies at renewal and deletes nothing
        d = http.get("/v1/entitlements/downgrade-impact?to=C1", headers=adm).json()
        assert (
            (d["from_band"], d["to_band"]) == ("C3", "C1")
            and d["deletes_anything"] is False
            and d["effective"] == "AT_AUTHORIZED_RENEWAL"
        )
        assert "advanced.enterprise-sso-saml-oidc-federation" in {
            x["feature"] for x in d["becomes_inactive"]
        }
        assert not {x["feature"] for x in d["becomes_inactive"]} & {x["feature"] for x in base}
        assert (
            "the evidence ledger and audit history" in d["preserved"]
            and "issued credentials" in d["preserved"]
        )
        assert http.get("/v1/entitlements/downgrade-impact?to=C9", headers=adm).status_code == 422
        # acceptance 10: a plan is not a privilege: a member with no commercial authority sees nothing, whatever the tenant bought
        nobody = uid()
        assert http.get("/v1/entitlements/effective", headers=o.bearer(nobody)).status_code in (
            401,
            403,
        )


def test_shadow_mode_reports_a_commercial_refusal_without_blocking_it(rt_url: str) -> None:
    from zid_platform.modules.commercial.application import entitlement_resolver as resolver

    o = onboard(rt_url)

    async def run() -> list[tuple[bool, bool, str | None]]:
        out = []
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            for mode in ("enforce", "shadow", "off"):
                r = await resolver.decide(
                    c, "advanced.scim-automated-provisioning", enforcement=mode
                )
                out.append((r.allowed, r.shadow_denied, r.decision.reasons[0]))
            # compliance is never shadowed: a missing provider route holds in every mode
            for mode in ("enforce", "shadow", "off"):
                r = await resolver.decide(
                    c,
                    "standard.human-id-proofing-orchestration",
                    enforcement=mode,
                    coverage_ok=False,
                )
                out.append((r.allowed, r.shadow_denied, r.decision.outcome))
            with pytest.raises(ZidError) as hold:
                await resolver.require_feature(
                    c,
                    "standard.human-id-proofing-orchestration",
                    enforcement="shadow",
                    coverage_ok=False,
                )
            out.append((False, False, f"{hold.value.code}:{hold.value.http_status}"))
            with pytest.raises(ZidError) as refused:
                await resolver.require_feature(
                    c, "advanced.scim-automated-provisioning", enforcement="enforce"
                )
            out.append((False, False, f"{refused.value.code}:{refused.value.http_status}"))
            unknown = await resolver.decide(c, "no.such-feature", enforcement="shadow")
            out.append((unknown.allowed, False, unknown.decision.reasons[0]))
        return out

    assert asyncio.run(run()) == [
        (False, False, "NOT_IN_PLAN"), (True, True, "NOT_IN_PLAN"), (True, False, "NOT_IN_PLAN"),
        (False, False, "HOLD"), (False, False, "HOLD"), (False, False, "HOLD"),
        (False, False, "ENTITLEMENT_HOLD:503"), (False, False, "ENTITLEMENT_REQUIRED:402"),
        (False, False, "UNKNOWN_FEATURE"),
    ]  # fmt: skip


# ---- usage reservations -------------------------------------------------------------------------------------------------


def hard(register: dict[str, Any], *meters: str) -> dict[str, Any]:
    return {k: replace(v, hard_quota=k in meters) for k, v in register.items()}


def test_reservations_are_idempotent_serialized_and_refuse_only_the_crossing_operation(
    rt_url: str, owner_url: str
) -> None:
    tenant, other = uid(), uid()
    register = hard(
        meter_register(), "auth.mau", "kyb.check"
    )  # the Developer plan includes 2,500 active humans

    async def run() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with engine(rt_url) as eng:

            async def one(t: str, n: int, key: str, meter: str = "auth.mau") -> Any:
                try:
                    async with tenant_transaction(eng, ctx(t)) as c:
                        return await usage.reserve(c, t, meter, n, key, register=register)
                except ZidError as e:
                    return e

            first = await one(tenant, 2000, "key-aaaaaaaa")
            out["first"] = (
                first["state"],
                first["replayed"],
                first["limit"],
                first["remaining"],
                first["catalog_version"],
            )
            again = await one(
                tenant, 2000, "key-aaaaaaaa"
            )  # a retry or a provider callback: the same reservation
            out["replay"] = (again["replayed"], again["reservation_id"] == first["reservation_id"])
            conflict = await one(tenant, 100, "key-aaaaaaaa")
            out["conflict"] = (conflict.code, conflict.http_status)
            crossing = await one(tenant, 600, "key-bbbbbbbb")  # 2,000 + 600 > 2,500
            out["quota"] = (
                crossing.code,
                crossing.http_status,
                {d.field: d.issue for d in crossing.details or []},
            )
            async with tenant_transaction(eng, ctx(tenant)) as c:
                released = await usage.release(
                    c, first["reservation_id"], outcome="PROVIDER_FAILED"
                )
            out["released"] = (released["state"], released["outcome"])
            fits = await one(tenant, 600, "key-bbbbbbbb")  # the failed work gave its quantity back
            out["fits"] = (fits["state"], fits["remaining"])

            # concurrent requests with distinct keys against one cap: exactly as many as fit, never more
            fresh = uid()
            results = await asyncio.gather(
                *[one(fresh, 200, f"conc-{i:04d}-aaaa") for i in range(20)]
            )
            ok = [r for r in results if isinstance(r, dict)]
            out["concurrent"] = (
                len(ok),
                sum(1 for r in results if isinstance(r, ZidError) and r.code == "QUOTA_REACHED"),
            )
            # concurrent requests with the same key: one reservation
            same = uid()
            dup = await asyncio.gather(*[one(same, 10, "same-key-aaaa") for _ in range(6)])
            out["same_key"] = (
                len({r["reservation_id"] for r in dup}),
                sum(1 for r in dup if r["replayed"]),
            )
            async with tenant_transaction(eng, ctx(same)) as c:
                out["same_key_rows"] = (
                    await c.execute(text("SELECT count(*) FROM commercial.usage_reservation"))
                ).scalar_one()

            # commit and release are one-way and repeatable
            async with tenant_transaction(eng, ctx(tenant)) as c:
                done = await usage.commit(c, fits["reservation_id"])
                done2 = await usage.commit(c, fits["reservation_id"])
                out["commit"] = (done["state"], done["replayed"], done2["replayed"])
            for fn, name in (
                (usage.release, "release_committed"),
                (usage.commit, "commit_released"),
            ):
                try:
                    async with tenant_transaction(eng, ctx(tenant)) as c:
                        await fn(
                            c,
                            fits["reservation_id"]
                            if name == "release_committed"
                            else first["reservation_id"],
                        )
                except ZidError as e:
                    out[name] = (e.code, e.http_status)
            try:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    await usage.commit(c, uid())
            except ZidError as e:
                out["missing"] = e.code

            # a quota with no readable limit is a hold for a human decision, never a free pass or a silent charge
            undefined = await one(tenant, 1, "kyb-key-aaaa", "kyb.check")
            out["undefined"] = (undefined.code, undefined.http_status)
            unknown = await one(tenant, 1, "bogus-key-aaaa", "no.such")
            out["unknown"] = (unknown.code, unknown.http_status)
            uncapped = await one(
                tenant, 900_000_000, "api-key-aaaaaa", "api.request"
            )  # no hard quota: recorded, never refused
            out["uncapped"] = (uncapped["state"], uncapped["limit"], uncapped["remaining"])

            # another tenant sees none of it, and the summary tells the truth
            async with tenant_transaction(eng, ctx(other)) as c:
                out["other_summary"] = sorted(
                    (m["meter"], m["committed"], m["reserved"])
                    for m in (await usage.summary(c, register=register))["meters"]
                    if m["committed"] or m["reserved"]
                )
            async with tenant_transaction(eng, ctx(tenant)) as c:
                s = await usage.summary(c, register=register)
            mau = next(m for m in s["meters"] if m["meter"] == "auth.mau")
            out["summary"] = (
                mau["committed"],
                mau["reserved"],
                mau["released"],
                mau["limit"],
                mau["remaining"],
                mau["priced"],
            )
            out["treatment"] = sorted(s["treatment"])
        return out

    got = asyncio.run(run())
    assert got["first"] == ("RESERVED", False, 2500, 500, "ZID-ENG-DEC-008-v1.0")
    assert got["replay"] == (True, True) and got["conflict"] == ("IDEMPOTENCY_CONFLICT", 409)
    assert got["quota"][:2] == ("QUOTA_REACHED", 429)
    assert got["quota"][2] == {
        "limit": "2500",
        "used": "0",
        "reserved": "2000",
        "resets_at": got["quota"][2]["resets_at"],
    }
    assert got["released"] == ("RELEASED", "PROVIDER_FAILED") and got["fits"] == ("RESERVED", 1900)
    assert got["concurrent"] == (12, 8)  # 12 x 200 = 2,400 fits under 2,500; the 13th would not
    assert got["same_key"] == (1, 5) and got["same_key_rows"] == 1
    assert got["commit"] == ("COMMITTED", False, True)
    assert got["release_committed"] == ("RESERVATION_ALREADY_RESOLVED", 409) and got[
        "commit_released"
    ] == ("RESERVATION_ALREADY_RESOLVED", 409)
    assert got["missing"] == "RESERVATION_NOT_FOUND"
    assert got["undefined"] == ("QUOTA_UNDEFINED", 503) and got["unknown"] == ("METER_UNKNOWN", 422)
    assert got["uncapped"] == ("RESERVED", None, None)
    assert got["other_summary"] == []
    assert got["summary"] == (600, 0, 2000, 2500, 1900, False)
    assert got["treatment"] == ["duplicate", "incomplete", "unpriced"]

    async def guard() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            rid = (
                (
                    await c.execute(
                        text(
                            "SELECT reservation_id FROM commercial.usage_reservation WHERE tenant_id = :t AND state = 'COMMITTED'"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                )
                .scalars()
                .first()
            )
            for sql in (
                "UPDATE commercial.usage_reservation SET quantity = 1 WHERE reservation_id = :i",
                "UPDATE commercial.usage_reservation SET state = 'RELEASED' WHERE reservation_id = :i",
                "DELETE FROM commercial.usage_reservation WHERE reservation_id = :i",
            ):
                with pytest.raises(DBAPIError):
                    async with c.begin_nested():
                        await c.execute(text(sql), {"i": rid})

    asyncio.run(guard())


def test_the_period_resets_and_the_summary_is_a_route(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=["commercial.read"])
    app = create_app(settings(rt_url), keyring=o.ring)
    now = datetime(2026, 12, 31, 23, 0, tzinfo=UTC)
    assert usage.period_of(now) == "2026-12" and usage.reset_at(now) == datetime(
        2027, 1, 1, tzinfo=UTC
    )
    assert usage.reset_at(datetime(2026, 5, 3, tzinfo=UTC)) == datetime(2026, 6, 1, tzinfo=UTC)

    async def reserve(at: datetime, key: str) -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            return await usage.reserve(c, o.tenant, "api.request", 5, key, now=at)

    asyncio.run(reserve(datetime.now(UTC) - timedelta(days=62), "old-period-aaaa"))
    asyncio.run(reserve(datetime.now(UTC), "this-period-aaaa"))
    with TestClient(app, base_url=ISS) as http:
        s = http.get("/v1/usage/summary", headers=o.bearer(o.owner))
        assert s.status_code == 200, s.text
        meters = {m["meter"]: m for m in s.json()["meters"]}
        assert (
            len(meters) == 10 and meters["api.request"]["reserved"] == 5
        )  # the record's eight plus the two CR-070 definitions; last quarter's reservation is not this period's
        assert all(m["priced"] is False for m in meters.values())
        for key in ("credential.check", "credential.sensitive_check"):
            assert meters[key]["committed"] == 0 and meters[key]["reserved"] == 0
        assert http.get("/v1/usage/summary").status_code == 401


# ---- the reservation routes, the events and the billing switch -----------------------------------------------------------


def _events(rt_url: str, tenant: str) -> list[str]:
    async def run() -> list[str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type FROM events.outbox_event WHERE event_type LIKE '%commercial.usage.%' OR event_type LIKE '%commercial.quota.%' ORDER BY occurred_at, event_id"
                    )
                )
            ).scalars()
            return [str(r).split("commercial.")[1].removesuffix(".v1") for r in rows]

    return asyncio.run(run())


def test_reservation_routes_events_and_the_scope_they_need(rt_url: str) -> None:
    o = onboard(rt_url)
    app = create_app(settings(rt_url), keyring=o.ring)
    svc = o.bearer(o.owner, scope="usage:write", client="svc")
    plain = o.bearer(o.owner)

    with TestClient(app, base_url=ISS) as http:
        body = {"meter": "api.request", "quantity": 25, "idempotency_key": "route-key-0001"}
        assert http.post("/v1/usage/reservations", json=body).status_code == 401
        denied = http.post("/v1/usage/reservations", json=body, headers=plain)
        assert denied.status_code == 403 and denied.json()["error"]["code"] == "SCOPE_INSUFFICIENT"
        r = http.post("/v1/usage/reservations", json=body, headers=svc)
        assert r.status_code == 201, r.text
        res = r.json()
        assert (res["state"], res["quantity"], res["replayed"], res["catalog_version"]) == (
            "RESERVED",
            25,
            False,
            "ZID-ENG-DEC-008-v1.0",
        )
        again = http.post("/v1/usage/reservations", json=body, headers=svc).json()
        assert again["replayed"] is True and again["reservation_id"] == res["reservation_id"]
        clash = http.post("/v1/usage/reservations", json={**body, "quantity": 26}, headers=svc)
        assert clash.status_code == 409 and clash.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        for bad in (
            {**body, "meter": "Bad Meter"},
            {**body, "quantity": 0},
            {**body, "idempotency_key": "short"},
            {**body, "extra": 1},
        ):
            assert http.post("/v1/usage/reservations", json=bad, headers=svc).status_code == 422
        assert (
            http.post(
                "/v1/usage/reservations",
                json={**body, "meter": "no.such", "idempotency_key": "route-key-0002"},
                headers=svc,
            ).status_code
            == 422
        )

        rid = res["reservation_id"]
        assert (
            http.post(f"/v1/usage/reservations/{rid}:commit", json={}, headers=plain).status_code
            == 403
        )
        done = http.post(
            f"/v1/usage/reservations/{rid}:commit", json={"outcome": "COMPLETED"}, headers=svc
        )
        assert done.status_code == 200 and (
            done.json()["state"],
            done.json()["replayed"],
            done.json()["outcome"],
        ) == ("COMMITTED", False, "COMPLETED")
        assert (
            http.post(f"/v1/usage/reservations/{rid}:commit", json={}, headers=svc).json()[
                "replayed"
            ]
            is True
        )
        assert (
            http.post(f"/v1/usage/reservations/{rid}:release", json={}, headers=svc).status_code
            == 409
        )
        assert (
            http.post(f"/v1/usage/reservations/{uid()}:commit", json={}, headers=svc).status_code
            == 404
        )
        second = http.post(
            "/v1/usage/reservations",
            json={**body, "idempotency_key": "route-key-0003"},
            headers=svc,
        ).json()
        released = http.post(
            f"/v1/usage/reservations/{second['reservation_id']}:release",
            json={"outcome": "PROVIDER_FAILED"},
            headers=svc,
        )
        assert released.status_code == 200 and released.json()["state"] == "RELEASED"

    # one event per real change, none for a replay or a refusal; ids and quantities only
    assert _events(rt_url, o.tenant) == [
        "usage.reserved",
        "usage.committed",
        "usage.reserved",
        "usage.released",
    ]


def test_a_cap_reached_raises_the_threshold_event_and_billing_on_commit_is_an_owner_switch(
    rt_url: str,
) -> None:
    tenant = uid()
    base = meter_register()
    off = hard(base, "auth.mau")
    on = {
        k: replace(v, bill_on_commit=k in ("auth.mau", "api.request")) for k, v in off.items()
    }  # api.request has no Estate meter: nothing to bill

    async def ledger(c: Any) -> list[tuple[str, int]]:
        rows = (
            await c.execute(
                text(
                    "SELECT meter_id, quantity FROM commercial.billable_event ORDER BY occurred_at"
                )
            )
        ).all()
        return [(str(r[0]), int(r[1])) for r in rows]

    async def run() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                a = await usage.reserve(
                    c, tenant, "auth.mau", 2500, "cap-key-aaaaaaa", register=off
                )  # exactly the Developer cap
                out["reached"] = (a["remaining"], a["limit"])
            async with tenant_transaction(eng, ctx(tenant)) as c:
                try:
                    await usage.reserve(c, tenant, "auth.mau", 1, "cap-key-bbbbbbb", register=off)
                except ZidError as e:
                    out["refused"] = e.code
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await usage.commit(
                    c, a["reservation_id"], register=off
                )  # the switch is off: nothing reaches the ledger
                out["off"] = await ledger(c)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                b = await usage.reserve(c, tenant, "api.request", 7, "bill-key-aaaaaa", register=on)
                await usage.commit(c, b["reservation_id"], register=on)
                out["no_estate_meter"] = await ledger(c)
            other = uid()
            async with tenant_transaction(eng, ctx(other)) as c:
                m = await usage.reserve(c, other, "auth.mau", 40, "bill-key-bbbbbb", register=on)
                await usage.commit(c, m["reservation_id"], register=on)
                await usage.commit(
                    c, m["reservation_id"], register=on
                )  # a retried commit records nothing twice
                f = await usage.reserve(c, other, "auth.mau", 5, "bill-key-cccccc", register=on)
                await usage.release(c, f["reservation_id"])  # a released one is never charged
                out["on"] = await ledger(c)
        return out

    got = asyncio.run(run())
    assert got["reached"] == (0, 2500) and got["refused"] == "QUOTA_REACHED"
    assert got["off"] == [] and got["no_estate_meter"] == []
    assert got["on"] == [("MAH", 40)]
    assert _events(rt_url, tenant)[:2] == ["usage.reserved", "quota.threshold_reached"]


# ---- the signed snapshot cache -------------------------------------------------------------------------------------------


def test_snapshots_are_signed_bounded_and_ended_at_once_by_any_change(
    rt_url: str, owner_url: str
) -> None:
    from dataclasses import replace as rep
    from datetime import datetime as dt

    from zid_crypto.handles import LocalHmacKey
    from zid_platform.modules.commercial.application import entitlement_resolver as resolver
    from zid_platform.modules.commercial.application.entitlement_cache import SnapshotCache
    from zid_platform.modules.commercial.infrastructure.bands import band_matrix

    tenant = uid()
    mac = LocalHmacKey(1, b"k" * 32)  # type: ignore[call-arg]
    cache = SnapshotCache(mac=mac.mac)
    m = band_matrix()
    features = list(m.features)
    base = datetime.now(UTC)
    SCIM, SSO = (
        "advanced.scim-automated-provisioning",
        "advanced.enterprise-sso-saml-oidc-federation",
    )

    async def ask(
        eng: Any, fid: str, *, at: dt | None = None, mode: str = "enforce", **kw: Any
    ) -> Any:
        async with tenant_transaction(eng, ctx(tenant)) as c:
            return await resolver.decide(
                c, fid, enforcement=mode, now=at or base, cache=cache, **kw
            )

    async def run() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with engine(rt_url) as eng:
            # the cached answer is the uncached answer, for every feature and every request fact
            async with tenant_transaction(eng, ctx(tenant)) as c:
                same = True
                for fid in features:
                    for region, cover in (
                        (True, True),
                        (False, True),
                        (None, True),
                        (True, False),
                        (True, None),
                    ):
                        cached = await resolver.decide(
                            c, fid, region_ok=region, coverage_ok=cover, now=base, cache=cache
                        )
                        plain = await resolver.decide(
                            c, fid, region_ok=region, coverage_ok=cover, now=base, cache=None
                        )
                        same = (
                            same
                            and cached.decision == plain.decision
                            and cached.allowed == plain.allowed
                        )
                out["equivalent"] = same
            out["counters"] = (cache.rebuilds, cache.hits > 100)
            snap = cache.peek(tenant)
            out["snap"] = (
                snap.band,
                snap.plan_code,
                len(snap.features),
                len(snap.signed),
                (snap.expires_at - snap.issued_at).total_seconds(),
            )

            # a plan change ends the snapshot on the next call, not at the TTL
            before = await ask(eng, SSO)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await store.set_entitlement(
                    c,
                    tenant_id=tenant,
                    plan_code="BUSINESS",
                    price_book_id=None,
                    actor=uid(),
                    now=base,
                )
            after = await ask(eng, SSO)
            out["upgrade"] = (before.allowed, after.allowed, cache.peek(tenant).band)

            # a suspension denies at once although the snapshot has not expired
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await store.set_state(c, "SUSPENDED")
            suspended = await ask(eng, SSO)
            out["suspended"] = (suspended.allowed, suspended.decision.reasons)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await store.set_state(c, "ACTIVE")
            out["resumed"] = (await ask(eng, SSO)).allowed

            # an override opens a paid feature; revoking it closes it at once
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await store.add_override(
                    c,
                    tenant_id=tenant,
                    capability_key="scim",
                    grant_mode="CONTRACT",
                    extra_count=None,
                    reason="signed order",
                    approved_by=uid(),
                    created_by=uid(),
                    expires_at=None,
                )
            out["override_opens"] = (await ask(eng, SCIM)).allowed
            async with engine(owner_url) as oeng, oeng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE commercial.entitlement_override SET revoked_at = now() + interval '1 second' WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            out["revoked_closes"] = (await ask(eng, SCIM, at=base + timedelta(seconds=2))).allowed

            # an override that expires ends the snapshot at its own expiry, sooner than the TTL
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await store.add_override(
                    c,
                    tenant_id=tenant,
                    capability_key="scim",
                    grant_mode="CONTRACT",
                    extra_count=None,
                    reason="trial",
                    approved_by=uid(),
                    created_by=uid(),
                    expires_at=base + timedelta(seconds=10),
                )
            live = await ask(eng, SCIM, at=base + timedelta(seconds=1))
            out["expiring"] = (
                live.allowed,
                int((cache.peek(tenant).expires_at - base).total_seconds()),
            )
            gone = await ask(eng, SCIM, at=base + timedelta(seconds=11))
            out["expired"] = gone.allowed

            # the TTL bounds an unchanged snapshot
            rebuilt = cache.rebuilds
            await ask(eng, SSO, at=base + timedelta(seconds=12))
            await ask(eng, SSO, at=base + timedelta(seconds=12 + m.snapshot_ttl_seconds + 1))
            out["ttl_rebuilds"] = cache.rebuilds - rebuilt

            # an edited copy fails its signature and is rebuilt, never trusted
            real = cache.peek(tenant)
            forged = rep(
                real,
                features=tuple(
                    (f, "ALLOW", (), s, b, d, u) if f == SCIM else (f, o, r, s, b, d, u)
                    for f, o, r, s, b, d, u in real.features
                ),
            )
            cache.put_raw(forged)
            rejected = cache.rejected
            served = await ask(
                eng, SCIM, at=base + timedelta(seconds=12 + m.snapshot_ttl_seconds + 1)
            )  # the forged copy says ALLOW
            out["forged"] = (cache.rejected - rejected, served.allowed)
        return out

    got = asyncio.run(run())
    assert got["equivalent"] is True and got["counters"] == (1, True)
    assert got["snap"] == ("C1", "DEVELOPER", 40, 64, float(band_matrix().snapshot_ttl_seconds))
    assert got["upgrade"] == (False, True, "C3")
    assert got["suspended"] == (False, ("SUSPENDED",)) and got["resumed"] is True
    assert got["override_opens"] is True and got["revoked_closes"] is False
    assert got["expiring"] == (True, 10) and got["expired"] is False
    assert got["ttl_rebuilds"] == 1
    assert got["forged"] == (1, False)  # rejected once, and the forged allow was never served


# ---- acceptance 08: a premium plan does not buy a transfer ----------------------------------------------------------------


def test_a_premium_plan_never_buys_a_cross_region_transfer(rt_url: str) -> None:
    from zid_platform.modules.evidence.application.runtime import development_runtime
    from zid_platform.modules.evidence.ports import configure_runtime
    from zid_platform.modules.tenant.application import residency

    tenant, admin, second = uid(), uid(), uid()
    from zid_platform.modules.commercial.application import entitlement_resolver as resolver

    SSO = "advanced.enterprise-sso-saml-oidc-federation"
    configure_runtime(development_runtime("uk-1"))

    async def run() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await store.set_entitlement(
                c,
                tenant_id=tenant,
                plan_code="ENTERPRISE",
                price_book_id=None,
                actor=admin,
                now=datetime.now(UTC),
            )
            await store.add_override(
                c,
                tenant_id=tenant,
                capability_key="saml_sso",
                grant_mode="CONTRACT",
                extra_count=None,
                reason="signed order",
                approved_by=second,
                created_by=admin,
                expires_at=None,
            )
            out["plan_alone"] = (
                await resolver.decide(c, SSO, enforcement="enforce", cache=None)
            ).allowed  # the top plan, contracted
            await residency.set_residency(
                c, tenant, profile="SRP-1", home_cell_id="uk-1", recovery_cells=[], prohibited_cells=[], operator_juris=[],
                key_profile="PLATFORM_MANAGED", jurisdiction_pack_id="uk-gdpr", approval_ref="LEGAL-1", actor=admin,
            )  # fmt: skip

            async def ask(cell: str) -> Any:
                return await resolver.decide_in_region(
                    c,
                    tenant,
                    SSO,
                    gate=residency.authorize_transfer,
                    destination_cell_id=cell,
                    data_class="DC2",
                    purpose_code="KYC_REUSE",
                    enforcement="enforce",
                    cache=None,
                )

            home = await ask("uk-1")
            away = await ask("eu-1")  # another zone with no approved rule
            out["home"] = home.allowed
            out["away"] = (away.allowed, away.decision.outcome, away.decision.reasons)
            await residency.add_rule(
                c, tenant, data_class="DC2", destination_cell_id="eu-1", purpose_code="KYC_REUSE", mechanism="uk_idta",
                field_allowlist=["assertion_id"], approval_ref="LEGAL-3", created_by=admin, approved_by=second, valid_to=None,
            )  # fmt: skip
            out["approved"] = (await ask("eu-1")).allowed
        return out

    try:
        got = asyncio.run(run())
    finally:
        configure_runtime(None)
    assert got["plan_alone"] is True and got["home"] is True
    assert got["away"] == (False, "DENY", ("NOT_AVAILABLE_IN_REGION",))
    assert got["approved"] is True  # the residency rule opened it, not the plan


# ---- cross-cell sharing needs the residency rule AND the plan -------------------------------------------------------------


def test_shared_identity_leaves_a_cell_only_if_residency_and_the_plan_both_allow(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zid_platform.modules.commercial.application.transfer_gate import plan_aware_transfer_gate
    from zid_platform.modules.evidence.application.runtime import development_runtime
    from zid_platform.modules.evidence.ports import configure_runtime
    from zid_platform.modules.tenant.application import residency
    from zid_platform.modules.verification.application import service as vservice
    from zid_platform.modules.verification.domain.shared_identity import SharedIdentityError
    from zid_platform.modules.verification.infrastructure import verification_store as vstore

    tenant, admin, second, person = (uid() for _ in range(4))
    feature = "standard.credential-verification-validation"

    async def no_rows(conn: Any, principal_id: str) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(vstore, "assertions_for", no_rows)
    configure_runtime(development_runtime("uk-1"))

    async def run() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:

            async def ask(enforcement: str, cell: str) -> str:
                vservice.configure_transfer_gate(
                    plan_aware_transfer_gate(
                        residency.authorize_transfer, feature_id=feature, enforcement=enforcement
                    )
                )
                try:
                    await vservice.shared_assertions(
                        c,
                        tenant_id=tenant,
                        principal_id=person,
                        home_cell_id="uk-1",
                        relying_cell_id=cell,
                        purpose="KYC_REUSE",
                        requested=["identity.name"],
                        now=datetime.now(UTC),
                    )
                    return "SHARED"
                except SharedIdentityError as e:
                    return str(e.code) if hasattr(e, "code") else str(e)

            await residency.set_residency(
                c, tenant, profile="SRP-1", home_cell_id="uk-1", recovery_cells=[], prohibited_cells=[], operator_juris=[],
                key_profile="PLATFORM_MANAGED", jurisdiction_pack_id="uk-gdpr", approval_ref="LEGAL-1", actor=admin,
            )  # fmt: skip
            out["no_rule"] = await ask("enforce", "eu-1")  # residency says no first
            await residency.add_rule(
                c, tenant, data_class="DC2", destination_cell_id="eu-1", purpose_code="KYC_REUSE", mechanism="uk_idta",
                field_allowlist=["assertion_id"], approval_ref="LEGAL-3", created_by=admin, approved_by=second, valid_to=None,
            )  # fmt: skip
            out["both_allow"] = await ask("enforce", "eu-1")
            await store.set_entitlement(
                c,
                tenant_id=tenant,
                plan_code="DEVELOPER",
                price_book_id=None,
                actor=admin,
                now=datetime.now(UTC),
            )
            await store.set_state(c, "SUSPENDED")
            out["suspended_enforce"] = await ask(
                "enforce", "eu-1"
            )  # residency allows, the plan no longer does
            out["suspended_shadow"] = await ask("shadow", "eu-1")  # reported, not blocked
            out["residency_unmasked"] = await ask(
                "enforce", "us-1"
            )  # no rule for us-1: the residency answer, not the plan's
            await store.set_state(c, "ACTIVE")
            out["resumed"] = await ask("enforce", "eu-1")
        return out

    try:
        got = asyncio.run(run())
    finally:
        vservice.configure_transfer_gate(None)
        configure_runtime(None)
    assert (
        got["both_allow"] == "SHARED"
        and got["resumed"] == "SHARED"
        and got["suspended_shadow"] == "SHARED"
    )
    assert got["suspended_enforce"] == "ENTITLEMENT_REQUIRED"
    assert (
        got["no_rule"] == got["residency_unmasked"] and "ENTITLEMENT" not in got["no_rule"]
    )  # residency answers stand, whatever the plan


# ---- the snapshot is shared across processes ---------------------------------------------------------------------------------


def test_a_second_process_reads_the_shared_snapshot_and_a_change_is_announced_once(
    rt_url: str,
) -> None:
    from zid_crypto.handles import LocalHmacKey
    from zid_platform.modules.commercial.application import entitlement_resolver as resolver
    from zid_platform.modules.commercial.application.entitlement_cache import SnapshotCache
    from zid_platform.modules.commercial.infrastructure.bands import band_matrix

    tenant, other = uid(), uid()
    key1, key2 = LocalHmacKey(1, b"a" * 32), LocalHmacKey(1, b"b" * 32)
    a, b, c3 = SnapshotCache(mac=key1.mac), SnapshotCache(mac=key1.mac), SnapshotCache(mac=key2.mac)
    ttl = band_matrix().snapshot_ttl_seconds
    SSO = "advanced.enterprise-sso-saml-oidc-federation"
    now = datetime.now(UTC)

    def versions() -> list[int]:
        async def run() -> list[int]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                rows = (
                    await c.execute(
                        text(
                            "SELECT aggregate_version FROM events.outbox_event WHERE event_type LIKE '%entitlement.snapshot_published%' ORDER BY aggregate_version"
                        )
                    )
                ).scalars()
                return [int(r) for r in rows]

        return asyncio.run(run())

    async def run() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with engine(rt_url) as eng:

            async def ask(cache: SnapshotCache, at: datetime = now) -> Any:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return await resolver.decide(c, SSO, enforcement="enforce", now=at, cache=cache)

            async def row() -> tuple[int, str] | None:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    r = (
                        await c.execute(
                            text(
                                "SELECT revision, fingerprint FROM commercial.entitlement_snapshot"
                            )
                        )
                    ).first()
                    return None if r is None else (int(r.revision), str(r.fingerprint))

            first = await ask(a)
            out["a_builds"] = (a.rebuilds, a.shared_hits)
            second = await ask(b)  # another process, an empty memory: it reads what A published
            out["b_reads"] = (b.rebuilds, b.shared_hits, second.decision == first.decision)

            stale = a.peek(tenant)  # a snapshot of the old state, held by a slow process
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await store.set_entitlement(
                    c,
                    tenant_id=tenant,
                    plan_code="BUSINESS",
                    price_book_id=None,
                    actor=uid(),
                    now=now,
                )
            upgraded = await ask(b)  # B notices at once, rebuilds and announces
            out["b_after_change"] = (b.rebuilds, upgraded.allowed, (await row())[0])
            seen = await ask(a)  # A's memory is stale: it adopts B's row without building
            out["a_adopts"] = (a.rebuilds, a.shared_hits, seen.allowed)

            # a process that is behind cannot put an older state back
            before = await row()
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await resolver.publish(c, stale)
            out["stale_ignored"] = (await row()) == before

            # a refresh after the lifetime is saved quietly: same revision, no new announcement
            await ask(b, now + timedelta(seconds=ttl + 1))
            out["refresh"] = ((await row())[0], b.rebuilds)

            # another process under a different key cannot be trusted: its read fails verification and it builds its own
            third = await ask(c3)
            out["foreign"] = (c3.rejected, c3.rebuilds, third.decision == upgraded.decision)

            async with tenant_transaction(eng, ctx(other)) as c:
                out["isolated"] = (
                    await c.execute(text("SELECT count(*) FROM commercial.entitlement_snapshot"))
                ).scalar_one()
        return out

    got = asyncio.run(run())
    assert got["a_builds"] == (1, 0) and got["b_reads"] == (0, 1, True)
    assert got["b_after_change"] == (1, True, 2) and got["a_adopts"] == (1, 1, True)
    assert got["stale_ignored"] is True and got["refresh"][0] == 2
    assert got["foreign"] == (1, 1, True) and got["isolated"] == 0
    assert versions() == [
        1,
        2,
    ]  # one announcement per real change: not per refresh, per process or per stale write

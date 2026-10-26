"""The billable metering ledger (ZID-COM-001 §7, §26, §31; CR-025).

One economic event per chargeable operation, however often it is retried or redelivered; none for an
operation that rolled back; insert-only; tenant-isolated; frozen windows replay to the same hash; a write
failure never blocks the operation that was being billed.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.application import recorder
from zid_platform.modules.commercial.domain import billable as b
from zid_platform.modules.commercial.infrastructure import billable_store as store
from zid_platform.settings import Settings

NOW = datetime.now(UTC)
LAST_MONTH = b.window_month(NOW.replace(day=1) - timedelta(days=1))
IN_LAST_MONTH = datetime(LAST_MONTH.year, LAST_MONTH.month, 15, 12, tzinfo=UTC)


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


async def count(conn: Any, meter: str | None = None) -> int:
    sql = "SELECT count(*) FROM commercial.billable_event"
    if meter:
        sql += " WHERE meter_id = :m"
    return int((await conn.execute(text(sql), {"m": meter})).scalar_one())


def test_one_event_per_operation_rollback_leaves_none_and_tenants_are_isolated(
    rt_url: str, owner_url: str
) -> None:
    a, b_ = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(a)) as c:
                assert await recorder.record(c, tenant_id=a, meter="AUTH_EVENT", key="auth:s1")
                # the same operation recorded again, in the same or a later transaction, is one event
                assert not await recorder.record(c, tenant_id=a, meter="AUTH_EVENT", key="auth:s1")
            async with tenant_transaction(eng, ctx(a)) as c:
                assert not await recorder.record(c, tenant_id=a, meter="AUTH_EVENT", key="auth:s1")
                assert await count(c, "AUTH_EVENT") == 1

            # an operation that rolls back leaves no economic event behind
            with pytest.raises(RuntimeError):
                async with tenant_transaction(eng, ctx(a)) as c:
                    assert await recorder.record(
                        c, tenant_id=a, meter="POLICY_DECISION", key="pdp:d1"
                    )
                    raise RuntimeError("the decision transaction failed after billing")
            async with tenant_transaction(eng, ctx(a)) as c:
                assert await count(c, "POLICY_DECISION") == 0
                assert await recorder.record(
                    c, tenant_id=a, meter="POLICY_DECISION", key="pdp:d1"
                )  # retry bills once

            # the same key in another tenant is a different operation
            async with tenant_transaction(eng, ctx(b_)) as c:
                assert await recorder.record(c, tenant_id=b_, meter="AUTH_EVENT", key="auth:s1")
                assert await count(c) == 1  # B sees only its own ledger
            async with tenant_transaction(eng, ctx(a)) as c:
                assert await count(c) == 2

            # a tenant cannot write an event for another tenant: the row is refused and never raised to the caller
            before = recorder.STATS["write_failed"]
            async with tenant_transaction(eng, ctx(a)) as c:
                assert not await recorder.record(
                    c, tenant_id=b_, meter="AUTH_EVENT", key="auth:forged"
                )
                assert await count(c) == 2
            assert recorder.STATS["write_failed"] == before + 1

        # insert-only, even for the table owner
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.billable_event SET quantity = 99 WHERE tenant_id = :t",
                "DELETE FROM commercial.billable_event WHERE tenant_id = :t",
            ):
                with pytest.raises(DBAPIError, match="insert-only"):
                    async with eng.begin() as c:
                        await c.execute(text(sql), {"t": uuid.UUID(a)})

    asyncio.run(run())


def test_a_billing_failure_never_blocks_the_operation_it_was_billing(rt_url: str) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            before = recorder.STATS["write_failed"]
            # invalid input, an unknown meter and a bad quantity are all swallowed and counted
            assert not await recorder.record(c, tenant_id=tenant, meter="LOGINS", key="auth:x1")
            assert not await recorder.record(
                c, tenant_id=tenant, meter="AUTH_EVENT", key="auth:x2", quantity=0
            )
            assert recorder.STATS["write_failed"] == before + 2
            # the operation that follows on the same transaction is unharmed
            await c.execute(text("SELECT 1"))
            assert await recorder.record(c, tenant_id=tenant, meter="AUTH_EVENT", key="auth:ok")
            assert await count(c) == 1

    asyncio.run(run())

    class Broken:
        def begin_nested(self) -> Any:
            raise ConnectionError("ledger unavailable")

    async def broken() -> None:
        before = recorder.STATS["write_failed"]
        assert not await recorder.record(
            Broken(), tenant_id=tenant, meter="AUTH_EVENT", key="auth:y"
        )  # type: ignore[arg-type]
        assert not await recorder.record_active_human(
            Broken(), tenant_id=tenant, principal_id=uid()
        )  # type: ignore[arg-type]
        assert recorder.STATS["write_failed"] == before + 2

    asyncio.run(broken())


def test_a_monthly_active_human_counts_once_per_month(rt_url: str) -> None:
    tenant, alice, bob = uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert await recorder.record_active_human(
                c, tenant_id=tenant, principal_id=alice, at=NOW
            )
            for _ in range(5):  # signing in again the same month changes nothing
                assert not await recorder.record_active_human(
                    c, tenant_id=tenant, principal_id=alice, at=NOW
                )
            assert await recorder.record_active_human(c, tenant_id=tenant, principal_id=bob, at=NOW)
            assert await recorder.record_active_human(
                c, tenant_id=tenant, principal_id=alice, at=NOW + timedelta(days=40)
            )  # a new month counts again
            assert await count(c, "MAH") == 3
            keys = [
                r[0]
                for r in (
                    await c.execute(text("SELECT deduplication_key FROM commercial.billable_event"))
                ).all()
            ]
            assert not any(alice in k or bob in k for k in keys), "the ledger keeps no principal id"
            assert all(
                r[0] == "HUMAN"
                for r in (
                    await c.execute(text("SELECT principal_class FROM commercial.billable_event"))
                ).all()
            )

    asyncio.run(run())


def test_freezing_a_window_replays_to_the_same_hash_and_closes_it(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                for i in range(25):
                    await recorder.record(
                        c, tenant_id=tenant, meter="AUTH_EVENT", key=f"auth:{i}", at=IN_LAST_MONTH
                    )
                for i in range(7):
                    await recorder.record(
                        c,
                        tenant_id=tenant,
                        meter="VC_ISSUED",
                        key=f"vci:{i}",
                        at=IN_LAST_MONTH + timedelta(hours=i),
                    )
                with pytest.raises(b.BillableError) as e:  # the month in progress cannot be closed
                    await recorder.freeze_month(c, tenant, b.window_month(NOW))
                assert e.value.code == "WINDOW_NOT_CLOSED"
                frozen = await recorder.freeze_month(c, tenant, LAST_MONTH)
                assert {f["meter"] for f in frozen} == set(b.METERS)
                auth = next(f for f in frozen if f["meter"] == "AUTH_EVENT")
                assert auth["event_count"] == 25 and auth["quantity"] == Decimal(25)
                assert await recorder.freeze_month(c, tenant, LAST_MONTH) == []  # idempotent

                # replay: recompute the hash from the stored events; it must equal the frozen one
                rows = await store.window_events(c, LAST_MONTH, "AUTH_EVENT")
                assert b.hash_total(r["row_hash"] for r in rows) == auth["hash_total"]
                summary = await recorder.ledger_summary(c, LAST_MONTH)
                m = {x["meter"]: x for x in summary["meters"]}
                assert (
                    m["AUTH_EVENT"]["frozen"]
                    and m["AUTH_EVENT"]["hash_total"] == auth["hash_total"]
                )
                assert m["VC_ISSUED"]["events"] == 7 and m["MAH"]["events"] == 0

            # the database itself refuses a new event in a frozen window (even for the owner)
            async with engine(owner_url) as oeng:
                with pytest.raises(DBAPIError, match="frozen"):
                    async with oeng.begin() as oc:
                        await oc.execute(
                            text(
                                "INSERT INTO commercial.billable_event (event_id, tenant_id, deduplication_key, "
                                "meter_id, quantity, occurred_at, window_month, environment_id, region, row_hash) "
                                "VALUES (gen_random_uuid(), :t, 'auth:sneaky', 'AUTH_EVENT', 1, now(), :w, 'e', 'r', :h)"
                            ),
                            {"t": uuid.UUID(tenant), "w": LAST_MONTH, "h": "0" * 64},
                        )
                for sql in (
                    "UPDATE commercial.meter_window SET event_count = 0 WHERE tenant_id = :t",
                    "DELETE FROM commercial.meter_window WHERE tenant_id = :t",
                ):
                    with pytest.raises(DBAPIError, match="immutable"):
                        async with oeng.begin() as oc:
                            await oc.execute(text(sql), {"t": uuid.UUID(tenant)})

            # a late event for the closed month lands in the next open window, flagged, never in the closed one
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert await recorder.record(
                    c, tenant_id=tenant, meter="AUTH_EVENT", key="auth:late", at=IN_LAST_MONTH
                )
                late = (
                    await c.execute(
                        text(
                            "SELECT window_month, late FROM commercial.billable_event "
                            "WHERE deduplication_key = 'auth:late'"
                        )
                    )
                ).one()
                assert late[0] == b.window_month(NOW) and late[1] is True
                assert (
                    len(await store.window_events(c, LAST_MONTH, "AUTH_EVENT")) == 25
                )  # the closed window is unchanged

    asyncio.run(run())


def test_reconciliation_recovers_only_what_a_source_row_proves(rt_url: str) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await recorder.record(
                c, tenant_id=tenant, meter="AUTH_EVENT", key=b.key_auth("s1"), at=NOW
            )
            sources = [(b.key_auth(f"s{i}"), NOW, "HUMAN") for i in range(1, 6)]
            assert (
                await recorder.reconcile(c, tenant_id=tenant, meter="AUTH_EVENT", sources=sources)
                == 4
            )
            assert (
                await recorder.reconcile(c, tenant_id=tenant, meter="AUTH_EVENT", sources=sources)
                == 0
            )
            rows = (
                await c.execute(
                    text(
                        "SELECT deduplication_key, recovered FROM commercial.billable_event ORDER BY 1"
                    )
                )
            ).all()
            assert [r[0] for r in rows] == [f"auth:s{i}" for i in range(1, 6)]
            assert [r[1] for r in rows] == [
                False,
                True,
                True,
                True,
                True,
            ]  # the original was not touched

    asyncio.run(run())


def test_parallel_recorders_never_double_charge_and_the_hash_is_stable(rt_url: str) -> None:
    """Eight workers record 4,000 distinct operations, each one four times over, in shuffled order."""
    tenant = uid()
    keys = [f"pdp:{i}" for i in range(500)]

    async def worker(eng: Any, offset: int) -> None:
        order = keys[offset:] + keys[:offset]
        for chunk in range(0, len(order), 50):
            async with tenant_transaction(eng, ctx(tenant)) as c:
                for k in order[chunk : chunk + 50]:
                    await recorder.record(
                        c, tenant_id=tenant, meter="POLICY_DECISION", key=k, at=IN_LAST_MONTH
                    )

    async def run() -> None:
        async with engine(rt_url, size=8) as eng:
            await asyncio.gather(*(worker(eng, i * 61) for i in range(8)))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                rows = await store.window_events(c, LAST_MONTH, "POLICY_DECISION")
                assert len(rows) == 500 and len({r["deduplication_key"] for r in rows}) == 500
                one = b.hash_total(r["row_hash"] for r in rows)
                again = b.hash_total(r["row_hash"] for r in reversed(rows))
                assert one == again
                frozen = await recorder.freeze_month(c, tenant, LAST_MONTH)
                assert (
                    next(f for f in frozen if f["meter"] == "POLICY_DECISION")["hash_total"] == one
                )

    asyncio.run(run())


def test_the_tenant_reproduces_its_own_ledger_and_operators_adjust_it_with_a_second_approver(
    rt_url: str, owner_url: str
) -> None:
    from operator_fixture import activate_grant, operators
    from zid_platform.modules.operator_access.domain.model import OperatorRole

    o = onboard(rt_url, extra_actions=["commercial.read"], extra_resources=["billable:*"])
    other = onboard(rt_url, extra_actions=["commercial.read"], extra_resources=["billable:*"])
    alice, bob, carol, dave = uid(), uid(), uid(), uid()
    ops = operators(
        rt_url,
        {
            alice: {OperatorRole.REQUESTER},
            dave: {OperatorRole.REQUESTER},
            bob: {OperatorRole.APPROVER},
            carol: {OperatorRole.APPROVER},
        },
    )

    async def seed() -> list[str]:
        ids = []
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            for i in range(6):
                await recorder.record(
                    c, tenant_id=o.tenant, meter="AUTH_EVENT", key=f"auth:r{i}", at=IN_LAST_MONTH
                )
            ids = [
                str(r["event_id"]) for r in await store.window_events(c, LAST_MONTH, "AUTH_EVENT")
            ]
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(other.tenant)) as c:
            await recorder.record(
                c, tenant_id=other.tenant, meter="AUTH_EVENT", key="auth:other", at=IN_LAST_MONTH
            )
        return ids

    event_ids = asyncio.run(seed())
    month = f"{LAST_MONTH:%Y-%m}"
    tenant_app = create_app(settings(rt_url), keyring=o.ring)
    with TestClient(tenant_app, base_url=ISS) as http:
        adm = o.bearer(o.owner)
        s = http.get("/v1/usage/billable", params={"month": month}, headers=adm)
        assert s.status_code == 200, s.text
        auth = next(m for m in s.json()["meters"] if m["meter"] == "AUTH_EVENT")
        assert (
            auth["events"] == 6
            and auth["quantity"] == "6"
            and auth["net"] == "6"
            and not auth["frozen"]
        )
        listed = http.get(
            "/v1/usage/billable/events", params={"month": month, "meter": "AUTH_EVENT"}, headers=adm
        ).json()
        assert len(listed["items"]) == 6 and not listed["truncated"]
        assert (
            http.get(
                "/v1/usage/billable/events", params={"month": month, "meter": "LOGINS"}, headers=adm
            ).status_code
            == 422
        )
        assert (
            http.get("/v1/usage/billable", params={"month": "2026-13"}, headers=adm).status_code
            == 422
        )
        csv = http.get("/v1/usage/billable/export", params={"month": month}, headers=adm)
        assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
        lines = csv.text.strip().split("\n")
        assert lines[0].startswith("event_id,meter,quantity") and len(lines) == 7
        assert "auth:other" not in csv.text and other.tenant not in csv.text  # only its own ledger
        assert http.get("/v1/usage/billable", params={"month": month}).status_code == 401

    op_app = create_app(
        settings(rt_url, operator_tenant_id=ops.tenant, operator_client_ids=(ops.client,)),
        keyring=ops.ring,
    )
    with TestClient(op_app, base_url=ISS) as api:
        base = f"/v1/commercial/tenants/{o.tenant}"
        assert (
            api.post(f"{base}/meter-windows/{month}:freeze", headers=ops.bearer(alice)).status_code
            == 403
        )
        scope = {
            "capability": "commercial.billing.manage",
            "resource_scope": f"tenant:{o.tenant}",
            "target_tenant_id": o.tenant,
        }
        activate_grant(api, ops, alice, (bob, carol), **scope)
        activate_grant(api, ops, dave, (bob, carol), **scope)
        assert (
            api.post(
                f"{base}/meter-windows/{datetime.now(UTC):%Y-%m}:freeze", headers=ops.bearer(alice)
            ).status_code
            == 409
        )
        r = api.post(f"{base}/meter-windows/{month}:freeze", headers=ops.bearer(alice))
        assert r.status_code == 200, r.text
        assert "AUTH_EVENT" in r.json()["frozen_now"]
        frozen_hash = next(m for m in r.json()["summary"]["meters"] if m["meter"] == "AUTH_EVENT")[
            "hash_total"
        ]

        # a correction: requested by one operator, decided by another, delta lands in the open window
        adj = {"event_id": event_ids[0], "quantity_delta": "-1", "reason_code": "DUPLICATE_SESSION"}
        r = api.post(f"{base}/adjustments", json=adj, headers=ops.bearer(alice))
        assert r.status_code == 201, r.text
        aid, window = r.json()["adjustment_id"], r.json()["window_month"]
        assert window == b.next_month(LAST_MONTH).isoformat()  # the closed month is never reopened
        assert (
            api.post(
                f"{base}/adjustments/{aid}:decide?approve=true", headers=ops.bearer(alice)
            ).status_code
            == 403
        )
        r = api.post(f"{base}/adjustments/{aid}:decide?approve=true", headers=ops.bearer(dave))
        assert r.status_code == 200 and r.json()["state"] == "APPROVED", r.text
        assert (
            api.post(
                f"{base}/adjustments/{aid}:decide?approve=false", headers=ops.bearer(dave)
            ).status_code
            == 409
        )
        bad = {**adj, "event_id": str(uuid.uuid4())}
        assert (
            api.post(f"{base}/adjustments", json=bad, headers=ops.bearer(alice)).status_code == 404
        )
        zero = {**adj, "quantity_delta": "0"}
        assert (
            api.post(f"{base}/adjustments", json=zero, headers=ops.bearer(alice)).status_code == 422
        )

    with TestClient(tenant_app, base_url=ISS) as http:
        adm = o.bearer(o.owner)
        frozen = http.get("/v1/usage/billable", params={"month": month}, headers=adm).json()
        auth = next(m for m in frozen["meters"] if m["meter"] == "AUTH_EVENT")
        assert auth["frozen"] and auth["hash_total"] == frozen_hash and auth["events"] == 6
        current = http.get(
            "/v1/usage/billable", params={"month": f"{datetime.now(UTC):%Y-%m}"}, headers=adm
        ).json()
        cur_auth = next(m for m in current["meters"] if m["meter"] == "AUTH_EVENT")
        assert (
            cur_auth["adjustments"] == "-1"
        )  # the closed month's events did not change; the delta is here

    async def events() -> int:
        async with engine(owner_url) as eng, eng.begin() as c:
            return int(
                (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM events.outbox_event WHERE tenant_id = :t AND event_type IN "
                            "('com.zoiko.id.commercial.meter_window.frozen.v1', "
                            "'com.zoiko.id.commercial.billable_event.adjusted.v1')"
                        ),
                        {"t": uuid.UUID(o.tenant)},
                    )
                ).scalar_one()
            )

    assert asyncio.run(events()) == 2


def test_the_month_helper_matches_the_database_window_constraint() -> None:
    assert b.window_month(datetime(2026, 2, 28, 23, tzinfo=UTC)) == date(2026, 2, 1)


def test_reconciliation_over_http_recovers_sessions_that_were_never_billed(rt_url: str) -> None:
    from operator_fixture import activate_grant, operators
    from zid_platform.modules.operator_access.domain.model import OperatorRole

    o = onboard(
        rt_url
    )  # its two admin sessions were created without going through a login, so unbilled
    alice, bob, carol = uid(), uid(), uid()
    ops = operators(
        rt_url,
        {
            alice: {OperatorRole.REQUESTER},
            bob: {OperatorRole.APPROVER},
            carol: {OperatorRole.APPROVER},
        },
    )
    month = f"{datetime.now(UTC):%Y-%m}"
    app = create_app(
        settings(rt_url, operator_tenant_id=ops.tenant, operator_client_ids=(ops.client,)),
        keyring=ops.ring,
    )
    with TestClient(app, base_url=ISS) as api:
        url = f"/v1/commercial/tenants/{o.tenant}/reconcile/{month}"
        assert api.post(url, headers=ops.bearer(alice)).status_code == 403  # no grant
        activate_grant(
            api,
            ops,
            alice,
            (bob, carol),
            capability="commercial.billing.manage",
            resource_scope=f"tenant:{o.tenant}",
            target_tenant_id=o.tenant,
        )
        r = api.post(url, headers=ops.bearer(alice))
        assert r.status_code == 200, r.text
        assert r.json()["recovered"]["AUTH_EVENT"] == 2
        assert (
            api.post(url, headers=ops.bearer(alice)).json()["recovered"]["AUTH_EVENT"] == 0
        )  # idempotent
        assert (
            api.post(
                f"/v1/commercial/tenants/{o.tenant}/reconcile/2026-13", headers=ops.bearer(alice)
            ).status_code
            == 422
        )

    async def rows() -> list[tuple[str, bool]]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            return [
                (r[0], r[1])
                for r in (
                    await c.execute(
                        text(
                            "SELECT deduplication_key, recovered FROM commercial.billable_event "
                            "WHERE meter_id = 'AUTH_EVENT'"
                        )
                    )
                ).all()
            ]

    got = asyncio.run(rows())
    assert sorted(k for k, _ in got) == sorted(f"auth:{s}" for s in o.sessions.values())
    assert all(rec for _, rec in got)

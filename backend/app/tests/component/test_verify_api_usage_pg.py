"""Verify API usage and the usage ledger (CR-154), against the real database and the real HTTP endpoints.

A sandbox test is reported to the company but never written to the usage ledger. A production session is one usage fact when it starts and one when it reaches a final decision, each exactly once, and the
report shows whether the ledger matches the sessions.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from conftest import engine
from sqlalchemy import text
from test_verify_api_go_live_pg import Company, uid
from zid_platform.modules.verify_api.application import usage_service as usage
from zid_platform.modules.verify_api.infrastructure import session_store as sdb


def ledger(owner_url: str, tenant: str) -> list[Any]:
    async def go() -> list[Any]:
        async with engine(owner_url) as eng, eng.begin() as conn:
            return list(
                (
                    await conn.execute(
                        text(
                            "SELECT meter_id, deduplication_key, billable_result, finality, origin, principal_class FROM commercial.billable_event "
                            "WHERE tenant_id = :t AND meter_id LIKE 'VERIFY_%' ORDER BY meter_id, deduplication_key"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).all()
            )

    return asyncio.run(go())


def test_the_report_keeps_sandbox_apart_and_never_meters_a_sandbox_test(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        server = co.sandbox_server()
        co.run_sandbox(server, ("approved", "declined", "review", "inconclusive", "expired"))
        made = co.http.post(
            "/v1/verify-api/verification-sessions",
            headers={**server, "Idempotency-Key": uid()},
            json={
                "customer_reference": "c-1",
                "country": "US",
                "purpose_code": "customer_onboarding",
            },
        ).json()["session_id"]
        co.http.post(f"/v1/verify-api/verification-sessions/{made}:cancel", headers=server)

        r = co.http.get("/v1/verify-api/usage", headers=co.h)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["billing"]["state"] == "USAGE_FACTS_ONLY" and "never usage" in d["billing"]["note"]
        assert d["totals"]["sandbox"] == {
            "sessions_started": 6,
            "verified": 1,
            "not_verified": 1,
            "inconclusive": 1,
            "review_required": 1,
            "canceled": 1,
            "expired": 1,
        }
        assert set(d["totals"]["production"].values()) == {0}
        assert [
            (i["application_name"], i["environment"], i["sessions_started"]) for i in d["items"]
        ] == [("Checkout", "sandbox", 6)]
        assert d["reconciliation"] == {
            "sessions_started": {"sessions": 0, "ledger": 0},
            "checks_completed": {"sessions": 0, "ledger": 0},
            "reconciled": True,
        }
        assert ledger(owner_url, co.o.tenant) == []  # a sandbox test is never usage

        # another month is empty; a bad month is refused; no sign-in is refused
        assert co.http.get("/v1/verify-api/usage?month=2020-01", headers=co.h).json()["items"] == []
        for bad in ("2026-13", "26-11", "november", "2026-1"):
            assert (
                co.http.get("/v1/verify-api/usage", headers=co.h, params={"month": bad}).status_code
                == 422
            ), bad
        assert co.http.get("/v1/verify-api/usage").status_code == 401
    finally:
        co.close()


def test_a_production_session_is_one_usage_fact_at_start_and_one_at_its_final_decision_never_twice(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        server = co.sandbox_server()
        co.run_sandbox(server, ("approved", "declined", "review", "inconclusive"))

        async def promote() -> None:
            # stand in for the live engine, which is a later gate: treat this company's sessions as production ones
            async with engine(owner_url) as eng, eng.begin() as conn:
                p = {"t": uuid.UUID(co.o.tenant)}
                await conn.execute(
                    text(
                        "UPDATE verify.api_application SET environment = 'PRODUCTION' WHERE tenant_id = :t"
                    ),
                    p,
                )
                await conn.execute(
                    text(
                        "UPDATE verify.api_session SET environment = 'PRODUCTION' WHERE tenant_id = :t"
                    ),
                    p,
                )

        asyncio.run(promote())

        async def rows() -> list[dict[str, Any]]:
            async with co.tx() as c:
                return [
                    r
                    for r in (
                        await sdb.list_sessions(
                            c,
                            app_id,
                            status=None,
                            reference=None,
                            created_from=None,
                            created_to=None,
                            after=None,
                            limit=50,
                        )
                    )
                ]

        async def find_app() -> str:
            async with engine(owner_url) as eng, eng.begin() as conn:
                return str(
                    (
                        await conn.execute(
                            text(
                                "SELECT application_id FROM verify.api_application WHERE tenant_id = :t"
                            ),
                            {"t": uuid.UUID(co.o.tenant)},
                        )
                    ).scalar_one()
                )

        app_id = asyncio.run(find_app())
        sessions = asyncio.run(rows())
        assert len(sessions) == 4

        # before anything is recorded the report shows the gap instead of hiding it
        d = co.http.get("/v1/verify-api/usage", headers=co.h).json()
        assert (
            d["totals"]["production"]["sessions_started"] == 4
            and d["totals"]["sandbox"]["sessions_started"] == 0
        )
        assert d["reconciliation"]["reconciled"] is False
        assert d["reconciliation"]["sessions_started"] == {"sessions": 4, "ledger": 0}
        assert d["reconciliation"]["checks_completed"] == {
            "sessions": 2,
            "ledger": 0,
        }  # verified and not verified; review and inconclusive are not final

        async def meter(final: bool) -> list[bool]:
            out: list[bool] = []
            async with co.tx() as c:
                for s in sessions:
                    out.append(await usage.meter_session_started(c, s))
                    if final:
                        dec = await sdb.latest_decision(c, s["session_id"])
                        assert dec is not None
                        out.append(
                            await usage.meter_check_completed(
                                c, s, dec["outcome"], dec["completed_at"]
                            )
                        )
            return out

        first = asyncio.run(meter(True))
        assert (
            first.count(True) == 6
        )  # 4 starts and 2 final decisions; review and inconclusive write no completion
        again = asyncio.run(meter(True))
        assert not any(again)  # recording the same sessions again writes nothing
        facts = ledger(owner_url, co.o.tenant)
        assert [f.meter_id for f in facts].count("VERIFY_SESSION_START") == 4 and [
            f.meter_id for f in facts
        ].count("VERIFY_CHECK_COMPLETE") == 2
        assert {f.origin for f in facts} == {"API"} and {f.principal_class for f in facts} == {
            "NON_HUMAN"
        }
        assert {f.billable_result for f in facts if f.meter_id == "VERIFY_CHECK_COMPLETE"} == {
            "CHARGEABLE"
        }
        assert all(f.deduplication_key.split(":")[1].startswith("vfs_") for f in facts)

        d2 = co.http.get("/v1/verify-api/usage", headers=co.h).json()
        assert d2["reconciliation"]["reconciled"] is True
        assert d2["reconciliation"]["sessions_started"] == {"sessions": 4, "ledger": 4}
        assert d2["reconciliation"]["checks_completed"] == {"sessions": 2, "ledger": 2}

        # a review resolved later is the session's first FINAL decision: one completion, and a revision of it adds none
        review = next(s for s in sessions if s["status"] == "manual_review")

        async def resolve() -> list[bool]:
            async with co.tx() as c:
                return [
                    await usage.meter_check_completed(
                        c, review, "verified", sessions[0]["created_at"]
                    ),
                    await usage.meter_check_completed(
                        c, review, "not_verified", sessions[0]["created_at"]
                    ),
                    await usage.meter_check_completed(
                        c, review, "review_required", sessions[0]["created_at"]
                    ),
                ]

        assert asyncio.run(resolve()) == [True, False, False]
        assert len(ledger(owner_url, co.o.tenant)) == 7
    finally:
        co.close()

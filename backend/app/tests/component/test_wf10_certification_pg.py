"""Scored sandbox runs against PostgreSQL (ZID-CP-WF10 v2.0, WF-10.12; CR-173): kept with their digest, never a certification, shown beside go-live readiness without blocking it."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_verify_api_sessions_pg import World, uid
from zid_db import tenant_transaction
from zid_platform.modules.verify_api.application import certification_service as svc
from zid_platform.modules.verify_api.application import go_live_service
from zid_platform.modules.verify_api.domain import fixtures as fx
from zid_platform.modules.verify_api.domain.model import PartnerError


def run(rt_url: str, tenant: str, fn: Any) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await fn(c)

    return asyncio.run(go())


def perfect(seed: str = "7") -> list[dict[str, Any]]:
    """What a correct partner server would report for every persona."""
    out = []
    for p in fx.PERSONAS:
        plan = fx.delivery_plan(p.id, seed)
        ex = fx.expectations(p.id)
        refused = sum(
            1
            for d in plan["deliveries"]
            if d["signature_age_s"] > fx.SIGNATURE_TOLERANCE_S
            or d["body"] == "ALTERED_AFTER_SIGNING"
        )
        ids = (
            []
            if p.fault == "STALE_SIGNATURE"
            else list(
                dict.fromkeys(
                    d["event_id"]
                    for d in plan["deliveries"]
                    if d["body"] != "ALTERED_AFTER_SIGNING"
                    and d["signature_age_s"] <= fx.SIGNATURE_TOLERANCE_S
                )
            )
        )
        out.append(
            {
                "persona": p.id, "seed": seed, "plan_digest": plan["plan_digest"], "signatures_refused": refused, "acted_on_event_ids": ids, "final_outcome_seen": None if p.fault == "STALE_SIGNATURE" else p.final_outcome,
                "read_authoritative_state": True, "acted_on_pending": False, "refused_missing_field": ex["must_tolerate_extra_fields"], "accepted_extra_field": ex["must_tolerate_extra_fields"],
            }
        )  # fmt: skip
    return out


def test_a_run_is_scored_kept_with_its_digest_and_shown_beside_go_live_without_blocking_it(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        h = w.server()
        app_id = w.http.get("/v1/verify-api/whoami", headers=h).json()["application_id"]
        by = str(uuid.uuid4())

        none = run(rt_url, w.tenant, lambda c: svc.status(c, app_id))
        assert none["suite"]["status"] == "NOT_RUN" and none["runs"] == []

        partial = run(
            rt_url, w.tenant, lambda c: svc.record_run(c, w.tenant, app_id, by, perfect()[:2])
        )
        assert (
            partial["passed"] is False
            and partial["is_certification"] is False
            and "needs_review" in partial["missing_personas"]
        )
        good = run(rt_url, w.tenant, lambda c: svc.record_run(c, w.tenant, app_id, by, perfect()))
        assert (
            good["passed"] is True
            and good["personas_passed"] == good["personas_total"] == len(fx.PERSONAS)
            and good["report_digest"].startswith("sha-256:")
        )
        again = run(rt_url, w.tenant, lambda c: svc.record_run(c, w.tenant, app_id, by, perfect()))
        assert (
            again["report_digest"] == good["report_digest"] and again["run_id"] != good["run_id"]
        )  # the same behaviour gives the same digest, so a partner QA can reproduce it

        st = run(rt_url, w.tenant, lambda c: svc.status(c, app_id))
        assert (
            st["suite"]["status"] == "PASSED"
            and st["suite"]["met"] is True
            and st["suite"]["approves_go_live"] is False
            and len(st["runs"]) == 3
        )

        # a run with one persona handled wrongly fails and says so; the earlier pass is no longer the latest
        wrong = perfect()
        wrong[5] = {**wrong[5], "acted_on_event_ids": wrong[5]["acted_on_event_ids"] * 2}
        bad = run(rt_url, w.tenant, lambda c: svc.record_run(c, w.tenant, app_id, by, wrong))
        assert bad["passed"] is False and bad["personas_passed"] == len(fx.PERSONAS) - 1
        assert (
            run(rt_url, w.tenant, lambda c: svc.status(c, app_id))["suite"]["status"]
            == "NOT_CURRENT"
        )

        # beside the go-live checks, informational: the existing go-live checks are not changed by it
        status = run(rt_url, w.tenant, lambda c: go_live_service.status_view(c, w.tenant))
        assert status["adversarial_suite"]["approves_go_live"] is False and {
            c["id"] for c in status["certification"]
        } == {"sandbox_approved", "sandbox_declined", "webhook_ready"}

        async def edit() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(text("UPDATE verify.api_certification_run SET passed = true"))

        with pytest.raises(Exception, match="append-only"):
            asyncio.run(edit())

        async def other(c: Any) -> int:
            return int(
                (
                    await c.execute(text("SELECT count(*) FROM verify.api_certification_run"))
                ).scalar_one()
            )

        assert run(rt_url, uid(), other) == 0  # another company sees none of it
    finally:
        w.close()


def test_only_a_sandbox_application_can_be_tested_and_an_unknown_one_has_nothing(
    rt_url: str,
) -> None:
    w = World(rt_url)
    try:

        async def go(c: Any) -> None:
            with pytest.raises(PartnerError) as e:
                await svc.record_run(c, w.tenant, str(uuid.uuid4()), str(uuid.uuid4()), perfect())
            assert e.value.code == "NOT_FOUND"
            with pytest.raises(PartnerError):
                await svc.status(c, str(uuid.uuid4()))

        run(rt_url, w.tenant, go)
    finally:
        w.close()

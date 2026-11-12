"""The team's own labelled equivalents (ZID-UAT-005, CR-074): developer runs, the draft policy proposal and the register runner. None of them is independent evidence."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from typing import Any

import pytest
from conftest import ctx, engine
from test_assurance_plane_pg import BUILD, CASES, CONFIG, UNIT, sha
from test_v2_privacy_pg import runtime  # noqa: F401  (runtime is an autouse fixture)
from zid_db import tenant_transaction
from zid_platform.modules.assurance.application import registry_source as rs
from zid_platform.modules.assurance.application import service as svc
from zid_platform.modules.assurance.domain.rules import RuleError


def test_a_developer_run_is_labelled_and_never_counts(rt_url: str) -> None:
    tenant = str(uuid.uuid4())
    admin, author, tester = (str(uuid.uuid4()) for _ in range(3))

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await svc.assign_role(c, tenant, admin, role="TESTER", principal=tester)
            await svc.import_baseline(c, tenant, admin, version="1", cases=CASES)
            fx = await svc.create_fixture(c, tenant, author, name="fx", content_sha256=sha("fx"))
            await svc.review_fixture(
                c, tester, fixture_id=fx["fixture_id"], revision=1, approve=True, rationale="ok"
            )
            args: dict[str, Any] = {
                "release_unit": UNIT, "environment_id": "env-1", "build_sha256": BUILD, "config_digest": CONFIG,
                "case_ids": ["GOV-01"], "fixture_hashes": [sha("fx")], "commit_authors": [author],
            }  # fmt: skip
            # an author may not run an independent test of their own build, but may run a labelled drill
            with pytest.raises(RuleError, match="TESTER_ROLE_REQUIRED"):
                await svc.create_run(c, tenant, author, **args)
            run = await svc.create_run(c, tenant, author, kind="DEVELOPER", **args)
            assert run["kind"] == "DEVELOPER"
            await svc.sign_manifest(
                c, tenant, author, run_id=run["run_id"], case_id="GOV-01", result="PASS", requirement_ids=["R"], trace_refs=["t"],
                provider_receipt_refs=[], source_snapshot_refs=[], oracle_version="v1", defect_id=None, human_approval_refs=[],
            )  # fmt: skip
            req = await svc.requirements(c)
            row = next(r for r in req["cases"] if r["case_id"] == "GOV-01")
            assert row["status"] == "NOT_RUN", "a developer PASS never becomes a verdict"
            assert (
                row["developer_evidence"]["verdict"] == "PASS"
                and row["developer_evidence"]["counts_toward_release"] is False
            )
            with pytest.raises(RuleError, match="RUN_KIND_UNKNOWN"):
                await svc.create_run(c, tenant, author, kind="OTHER", **args)

    asyncio.run(go())


def test_the_draft_policy_carries_only_the_numbers_a_document_proposes(rt_url: str) -> None:
    tenant = str(uuid.uuid4())
    admin, qa = str(uuid.uuid4()), str(uuid.uuid4())
    draft = svc.draft_proposal()
    assert set(draft["thresholds"]) == {"p95_ms", "availability_percent"}
    assert {"rpo_minutes", "rto_minutes"} <= set(draft["no_number_proposed"])

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await svc.assign_role(c, tenant, admin, role="QA_LEAD", principal=qa)
            out = await svc.propose_policy_from_draft(c, tenant, qa, release_unit=UNIT)
            assert out["signed"] is False and out["from_draft"] is True

    asyncio.run(go())


def test_the_register_runner_baselines_then_closes_drift_and_refuses_when_off(rt_url: str) -> None:
    tenant = str(uuid.uuid4())
    today = date(2026, 11, 12)
    one = [
        {
            "provider_id": "p1",
            "service_code": "KYC",
            "status": "ACTIVE",
            "valid_until": "2027-06-01",
            "scheme_version": "1",
        }
    ]
    gone: list[dict[str, Any]] = []
    on = {"stand-in": {"enabled": True, "simulated": True}, "off": {"enabled": False}}

    def fetcher(items: list[dict[str, Any]]) -> Any:
        async def fetch(_cfg: Any) -> list[dict[str, Any]]:
            return items

        return fetch

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(RuleError, match="SOURCE_UNKNOWN"):
                await rs.run_source(c, tenant, "nope", config=on, fetch=fetcher(one), today=today)
            with pytest.raises(RuleError, match="SOURCE_DISABLED"):
                await rs.run_source(c, tenant, "off", config=on, fetch=fetcher(one), today=today)
            rs.register_registry_fetcher(None)
            with pytest.raises(RuleError, match="NO_FETCHER_REGISTERED"):
                await rs.run_source(c, tenant, "stand-in", config=on, today=today)
            first = await rs.run_source(
                c, tenant, "stand-in", config=on, fetch=fetcher(one), today=today
            )
            assert (
                first["first_snapshot"] is True
                and first["applied"] == []
                and first["simulated"] is True
            )
            same = await rs.run_source(
                c, tenant, "stand-in", config=on, fetch=fetcher(one), today=today
            )
            assert (
                same["first_snapshot"] is False
                and same["unchanged"] is True
                and same["changes"] == []
            )
            drift = await rs.run_source(
                c, tenant, "stand-in", config=on, fetch=fetcher(gone), today=today
            )
            assert drift["unchanged"] is False and drift["changes"], "a removed service is drift"

    asyncio.run(go())

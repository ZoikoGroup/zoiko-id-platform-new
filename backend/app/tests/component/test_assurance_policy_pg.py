"""The signed SLO and assurance policy (ZID-UAT-005 section 05): read from the signed record, never taken from the caller, and the only judge of a number."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from conftest import ctx, engine
from test_assurance_plane_pg import BUILD, CASES, CONFIG, UNIT, sha
from test_v2_privacy_pg import runtime  # noqa: F401  (runtime is an autouse fixture)
from zid_db import tenant_transaction
from zid_platform.modules.assurance.application import service as svc
from zid_platform.modules.assurance.domain.rules import RuleError

THRESHOLDS = {"availability_percent": 99.9, "p95_ms": 400, "rto_minutes": 60}


def test_the_policy_is_read_from_its_three_signatures_and_judges_every_number(rt_url: str) -> None:
    tenant = str(uuid.uuid4())
    admin, tester, author = (str(uuid.uuid4()) for _ in range(3))
    qa, cto, sec = (str(uuid.uuid4()) for _ in range(3))

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for role, who in (
                ("QA_LEAD", qa),
                ("CTO", cto),
                ("SECURITY_GRC", sec),
                ("TESTER", tester),
            ):
                await svc.assign_role(c, tenant, admin, role=role, principal=who)
            await svc.import_baseline(c, tenant, admin, version="1", cases=CASES)
            fx = await svc.create_fixture(c, tenant, author, name="fx", content_sha256=sha("fx"))
            await svc.review_fixture(
                c,
                tester,
                fixture_id=fx["fixture_id"],
                revision=1,
                approve=True,
                rationale="checked",
            )
            run = await svc.create_run(
                c, tenant, tester, release_unit=UNIT, environment_id="env-1", build_sha256=BUILD, config_digest=CONFIG,
                case_ids=["GOV-01"], fixture_hashes=[sha("fx")], commit_authors=[author],
            )  # fmt: skip

            async def manifest(result: str = "PASS", **kw: Any) -> dict[str, Any]:
                return await svc.sign_manifest(
                    c, tenant, tester, run_id=run["run_id"], case_id="GOV-01", result=result, requirement_ids=["R"], trace_refs=["t"],
                    provider_receipt_refs=[], source_snapshot_refs=[], oracle_version="v1", defect_id=None, human_approval_refs=[], **kw,
                )  # fmt: skip

            async def candidate() -> dict[str, Any]:
                # the caller claims the policy is signed; the record decides
                return await svc.create_candidate(
                    c, tenant, admin, release_unit=UNIT, build_sha256=BUILD, policy_pack="US-CA@1", facts={"thresholds_policy_signed": True},
                    engineering_owner=author, commit_authors=[author], privacy_gate=None,
                )  # fmt: skip

            # no policy: nothing can be judged, and the caller's claim is ignored
            with pytest.raises(RuleError, match="POLICY_NOT_SIGNED"):
                await manifest(measurements={"p95_ms": 100})
            none = await candidate()
            g6 = next(g for g in none["decision"]["gates"] if g["gate"] == "UAT-G6")
            assert "THRESHOLDS_POLICY_NOT_SIGNED" in g6["reasons"], (
                "a claimed policy is not a signed one"
            )

            # only the QA lead or the CTO proposes, and every number must be a person's, in a known key
            with pytest.raises(RuleError, match="ROLE_NOT_ASSIGNED_TO_THE_CALLER"):
                await svc.propose_policy(c, tenant, sec, release_unit=UNIT, thresholds=THRESHOLDS)
            with pytest.raises(RuleError, match="POLICY_INVALID"):
                await svc.propose_policy(
                    c, tenant, qa, release_unit=UNIT, thresholds={"vibes_ms": 5}
                )
            pol = await svc.propose_policy(c, tenant, qa, release_unit=UNIT, thresholds=THRESHOLDS)
            assert pol["signed"] is False

            # three different role holders, each once
            await svc.sign_policy(
                c,
                tenant,
                qa,
                policy_id=pol["policy_id"],
                role="QA_LEAD",
                decision="APPROVE",
                rationale="numbers reviewed",
            )
            with pytest.raises(RuleError, match="ROLE_NOT_ASSIGNED_TO_THE_CALLER"):
                await svc.sign_policy(
                    c,
                    tenant,
                    qa,
                    policy_id=pol["policy_id"],
                    role="CTO",
                    decision="APPROVE",
                    rationale="the QA lead is not the CTO",
                )
            with pytest.raises(RuleError, match="SIGNATURE_REFUSED"):
                await svc.sign_policy(
                    c,
                    tenant,
                    qa,
                    policy_id=pol["policy_id"],
                    role="QA_LEAD",
                    decision="APPROVE",
                    rationale="signing twice",
                )
            with pytest.raises(RuleError, match="ROLE_NOT_ASSIGNED_TO_THE_CALLER"):
                await svc.sign_policy(
                    c,
                    tenant,
                    admin,
                    policy_id=pol["policy_id"],
                    role="CTO",
                    decision="APPROVE",
                    rationale="not the CTO",
                )
            half = await svc.sign_policy(
                c,
                tenant,
                cto,
                policy_id=pol["policy_id"],
                role="CTO",
                decision="APPROVE",
                rationale="agreed",
            )
            assert half["signed"] is False and half["needs"] == ["SECURITY_GRC"]
            with pytest.raises(RuleError, match="POLICY_NOT_SIGNED"):
                await manifest(measurements={"p95_ms": 100})
            done = await svc.sign_policy(
                c,
                tenant,
                sec,
                policy_id=pol["policy_id"],
                role="SECURITY_GRC",
                decision="APPROVE",
                rationale="agreed",
            )
            assert done["signed"] is True and done["needs"] == []

            signed = await candidate()
            g6 = next(g for g in signed["decision"]["gates"] if g["gate"] == "UAT-G6")
            assert "THRESHOLDS_POLICY_NOT_SIGNED" not in g6["reasons"]

            # numbers are judged against it: a breach cannot be signed as PASS, a missing measurement is a breach, a clean one is recorded
            good = {"availability_percent": 99.95, "p95_ms": 380, "rto_minutes": 45}
            with pytest.raises(RuleError, match="MEASUREMENTS_BREACH_THE_POLICY"):
                await manifest(measurements={**good, "p95_ms": 900})
            with pytest.raises(RuleError, match="RTO_MINUTES_NOT_MEASURED"):
                await manifest(measurements={"availability_percent": 99.95, "p95_ms": 380})
            ok = await manifest(measurements=good)
            assert ok["verdict"] == "PASS"
            stored = (await svc.evidence_view(c, run_id=run["run_id"], audience="auditor"))[
                "manifests"
            ][-1]["manifest"]
            assert (
                stored["policy_check"] == {"policy_id": pol["policy_id"], "breaches": []}
                and stored["measurements"] == good
            )

    asyncio.run(go())

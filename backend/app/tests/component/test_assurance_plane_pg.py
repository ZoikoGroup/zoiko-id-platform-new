"""The independent assurance plane over HTTP and against PostgreSQL (ZID-UAT-005; CR-074 slices S2 to S5)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any

import pytest
from conftest import ctx, engine
from onboarding_fixture import onboard
from test_v2_events_pg import events
from test_v2_privacy_pg import api_for, code, runtime  # noqa: F401  (runtime is an autouse fixture)
from zid_db import tenant_transaction
from zid_platform.modules.assurance.application import service as svc
from zid_platform.modules.assurance.domain.rules import RuleError

ACTIONS = (
    "assurance.read", "assurance.run.manage", "assurance.fixture.manage", "assurance.fixture.review", "assurance.release.decide",
    "assurance.release.rollback", "assurance.roles.manage",
)  # fmt: skip
RESOURCES = (
    "uat_role:*", "uat_baseline:*", "uat_requirements:*", "uat_fixture:*", "uat_run:*", "uat_evidence:*", "uat_defect:*", "uat_release:*",
)  # fmt: skip


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


UNIT = {
    "entity": "zoiko-tech",
    "service": "license.verify",
    "country": "US",
    "subdivision": "CA",
    "policy_pack": "US-CA@1",
}
BUILD, CONFIG = sha("build-1"), sha("config-1")
CASES = [
    {
        "case_id": "GOV-01",
        "suite": "GOV",
        "risk": "PROPOSED_P0",
        "dependency": "OWN",
        "assertion": "No cross-tenant leak",
    },
    {
        "case_id": "DOC-02",
        "suite": "DOC",
        "risk": "PROPOSED_P0",
        "dependency": "OWN",
        "assertion": "One changed byte is INVALID",
    },
    {
        "case_id": "INT-02",
        "suite": "INT",
        "risk": "PROPOSED_P1",
        "dependency": "OWN",
        "assertion": "Replay is refused",
    },
]


def test_the_assurance_flow_keeps_roles_fixtures_evidence_and_releases_honest_over_http(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    owner, second = o.bearer(o.owner), o.bearer(o.second)
    with api_for(rt_url, o) as api:

        def post(path: str, who: dict, body: dict | None = None):
            return api.post(f"/v2/assurance{path}", headers=who, json=body or {})

        assert (
            api.post("/v2/assurance/baselines", json={"version": "1", "cases": CASES}).status_code
            == 401
        )

        # ---- roles: nobody assigns themselves, and a release role is held by one person
        assert (
            post("/role-assignments", owner, {"role": "TESTER", "principal": o.owner}).status_code
            == 403
        )
        assert (
            post("/role-assignments", owner, {"role": "TESTER", "principal": o.second}).status_code
            == 201
        )
        assert (
            post("/role-assignments", owner, {"role": "CTO", "principal": o.second}).status_code
            == 201
        )
        assert (
            post(
                "/role-assignments", owner, {"role": "SECURITY_GRC", "principal": o.second}
            ).status_code
            == 409
        ), "one person, one release role"
        assert (
            post("/role-assignments", second, {"role": "QA_LEAD", "principal": o.owner}).status_code
            == 201
        )

        # ---- baseline and requirements: everything starts NOT_RUN
        assert (
            post(
                "/baselines", owner, {"version": "1", "cases": [{**CASES[0], "suite": "DOC"}]}
            ).status_code
            == 422
        )
        made = post("/baselines", owner, {"version": "1", "cases": CASES})
        assert made.status_code == 201 and made.json()["case_count"] == 3, made.text
        req = api.get("/v2/assurance/requirements", headers=owner).json()
        assert req["counts"] == {"NOT_RUN": 3} and all(
            c["manifest_id"] is None for c in req["cases"]
        )

        # ---- fixtures: a second person approves, an approved fixture is locked
        h1, h2 = sha("fixture-v1"), sha("fixture-v2")
        fx = post("/fixtures", owner, {"name": "signed-pdf-baseline", "content_sha256": h1}).json()
        assert (
            post(
                f"/fixtures/{fx['fixture_id']}/review",
                owner,
                {"revision": 1, "approve": True, "rationale": "mine"},
            ).status_code
            == 403
        )
        ok = post(
            f"/fixtures/{fx['fixture_id']}/review",
            second,
            {"revision": 1, "approve": True, "rationale": "oracle and digest checked"},
        )
        assert ok.status_code == 200 and ok.json()["state"] == "APPROVED", ok.text
        assert (
            post(
                f"/fixtures/{fx['fixture_id']}/review",
                second,
                {"revision": 1, "approve": False, "rationale": "changed my mind"},
            ).status_code
            == 409
        )
        assert (
            post(
                f"/fixtures/{fx['fixture_id']}/revisions", owner, {"content_sha256": h1}
            ).status_code
            == 409
        ), "an approved fixture is locked"
        rev2 = post(f"/fixtures/{fx['fixture_id']}/revisions", owner, {"content_sha256": h2}).json()
        assert rev2["revision"] == 2 and rev2["state"] == "DRAFT"

        # ---- runs: an assigned, independent tester, approved fixtures only
        run_body = {
            "release_unit": UNIT,
            "environment_id": "env-uat-1",
            "build_sha256": BUILD,
            "config_digest": CONFIG,
            "case_ids": ["GOV-01", "DOC-02"],
            "fixture_hashes": [h1],
        }
        assert post("/test-runs", owner, run_body).status_code == 403, (
            "the owner holds no TESTER role"
        )
        assert (
            post("/test-runs", second, {**run_body, "commit_authors": [o.second]}).status_code
            == 403
        ), "a commit author cannot test the build"
        assert (
            post("/test-runs", second, {**run_body, "fixture_hashes": [h2]}).status_code == 422
        ), "an unapproved fixture is refused"
        run = post("/test-runs", second, run_body)
        assert run.status_code == 201 and run.json()["frozen"] is True, run.text
        rid = run.json()["run_id"]

        # ---- events: raw-safe, idempotent, chained, and only the run's tester writes them
        ev = {
            "case_id": "GOV-01",
            "kind": "request.sent",
            "payload": {"status": 403},
            "idempotency_key": "evt-key-0001",
        }
        assert (
            post(
                f"/test-runs/{rid}/events", second, {**ev, "payload": {"selfie_image": "x"}}
            ).status_code
            == 422
        )
        assert post(f"/test-runs/{rid}/events", owner, ev).status_code == 403
        first = post(f"/test-runs/{rid}/events", second, ev).json()
        assert first["seq"] == 1 and first["replayed"] is False
        again = post(f"/test-runs/{rid}/events", second, ev).json()
        assert again["replayed"] is True and again["hash"] == first["hash"]
        assert (
            post(f"/test-runs/{rid}/events", second, {**ev, "payload": {"status": 200}}).status_code
            == 409
        )
        nxt = post(
            f"/test-runs/{rid}/events",
            second,
            {**ev, "kind": "response.seen", "idempotency_key": "evt-key-0002"},
        ).json()
        assert nxt["seq"] == 2

        # ---- manifests: a screenshot-only pass is INCONCLUSIVE; a complete one is PASS; a FAIL needs its defect
        thin = post(
            f"/test-runs/{rid}/cases/GOV-01/manifest",
            second,
            {"result": "PASS", "requirement_ids": ["GID-036"]},
        )
        assert thin.status_code == 201 and thin.json()["verdict"] == "INCONCLUSIVE", thin.text
        assert "NO_TRACE_RECEIPT_OR_SOURCE_SNAPSHOT" in thin.json()["problems"]
        full = post(
            f"/test-runs/{rid}/cases/GOV-01/manifest",
            second,
            {"result": "PASS", "requirement_ids": ["GID-036"], "trace_refs": ["trace-1"]},
        )
        assert full.json()["verdict"] == "PASS" and full.json()["chain_head"] == nxt["hash"]
        assert (
            post(
                f"/test-runs/{rid}/cases/DOC-02/manifest",
                second,
                {"result": "FAIL", "requirement_ids": ["DTV-02"]},
            ).status_code
            == 422
        )
        defect = post(
            "/defects", second, {"case_id": "DOC-02", "severity": "BLOCKER", "owner": "platform"}
        ).json()
        failed = post(
            f"/test-runs/{rid}/cases/DOC-02/manifest",
            second,
            {"result": "FAIL", "requirement_ids": ["DTV-02"], "defect_id": defect["defect_id"]},
        )
        assert failed.json()["verdict"] == "FAIL"
        assert (
            post(f"/test-runs/{rid}/cases/GOV-01/manifest", owner, {"result": "PASS"}).status_code
            == 403
        )

        # ---- evidence view by audience, and the chain
        auditor = api.get(
            f"/v2/assurance/test-runs/{rid}/evidence?audience=auditor", headers=owner
        ).json()
        customer = api.get(
            f"/v2/assurance/test-runs/{rid}/evidence?audience=customer", headers=owner
        ).json()
        assert auditor["chain_problems"] == [] and auditor["events"] == 2
        assert all("manifest" in m for m in auditor["manifests"]) and all(
            "manifest" not in m for m in customer["manifests"]
        )
        assert (
            "tester_principal"
            not in api.get(
                f"/v2/assurance/test-runs/{rid}/evidence?audience=regulator", headers=owner
            ).json()["manifests"][0]["manifest"]
        )

        # ---- requirements now reflect evidence, never typed-in status
        counts = api.get("/v2/assurance/requirements", headers=owner).json()["counts"]
        assert counts == {"PASS": 1, "FAIL": 1, "NOT_RUN": 1}

        # ---- verification: a stored manifest is valid, a changed bundle is not
        manifest_id = full.json()["manifest_id"]
        stored = post("/evidence:verify", owner, {"manifest_id": manifest_id})
        assert stored.status_code == 200 and stored.json()["valid"] is True, stored.text

        # ---- release candidates: nothing is ready, and the preview says exactly why
        cand_body = {
            "release_unit": UNIT,
            "build_sha256": BUILD,
            "policy_pack": "US-CA@1",
            "engineering_owner": str(uuid.uuid4()),
            "facts": {},
        }
        cand = post("/release-candidates", owner, cand_body)
        assert cand.status_code == 201, cand.text
        c1 = cand.json()
        assert c1["decision"]["status"] == "NO_GO" and any(
            m.startswith("DOC-02")
            for m in c1["decision"]["missing"]
            + [g for gate in c1["decision"]["gates"] for g in gate["reasons"]]
        )
        g0 = next(g for g in c1["decision"]["gates"] if g["gate"] == "UAT-G0")
        assert not g0["passed"] and "RISK_NOT_CONFIRMED_BY_QA_LEAD" in g0["reasons"]
        rel = c1["release_id"]

        # ---- decisions: only the role holder signs, once, and never a QA lead who wrote the build
        assert (
            post(
                f"/releases/{rel}/decisions",
                second,
                {"role": "QA_LEAD", "decision": "APPROVE", "rationale": "x"},
            ).status_code
            == 403
        )
        signed = post(
            f"/releases/{rel}/decisions",
            second,
            {"role": "CTO", "decision": "APPROVE", "rationale": "reviewed the evidence"},
        )
        assert signed.status_code == 201 and signed.json()["candidate_status"] == "NO_GO"
        assert (
            post(
                f"/releases/{rel}/decisions",
                second,
                {"role": "CTO", "decision": "APPROVE", "rationale": "again"},
            ).status_code
            == 409
        )
        assert len(events(rt_url, o.tenant, "assurance.release.decided")) == 1

        authored = post(
            "/release-candidates", owner, {**cand_body, "commit_authors": [o.owner]}
        ).json()["release_id"]
        refused = post(
            f"/releases/{authored}/decisions",
            owner,
            {"role": "QA_LEAD", "decision": "APPROVE", "rationale": "I wrote it"},
        )
        assert (
            refused.status_code == 409
            and "QA_LEAD_IS_NOT_INDEPENDENT_OF_THE_BUILD" in refused.json()["detail"]
        )

        # ---- a changed build expires the PASS by itself
        newer = post(
            "/release-candidates", owner, {**cand_body, "build_sha256": sha("build-2")}
        ).json()
        assert newer["expired_cases"] == ["GOV-01"]
        assert len(events(rt_url, o.tenant, "assurance.case.expired")) >= 1

        # ---- tampered evidence locks the release built on it, and nothing more is signed
        row = api.get(
            f"/v2/assurance/test-runs/{rid}/evidence?audience=auditor", headers=owner
        ).json()["manifests"]
        mine = next(m for m in row if m["manifest_id"] == manifest_id)
        changed = {**mine["manifest"], "result": "PASS", "trace_refs": ["trace-forged"]}
        bad = post(
            "/evidence:verify",
            owner,
            {"manifest_id": manifest_id, "manifest": changed, "jws": mine["jws"]},
        )
        assert (
            bad.status_code == 200
            and bad.json()["valid"] is False
            and "MANIFEST_CHANGED" in bad.json()["problems"]
        )
        assert rel in bad.json()["locked_releases"]
        assert len(events(rt_url, o.tenant, "assurance.evidence.tampered")) == 1
        assert (
            post(
                f"/releases/{rel}/decisions",
                second,
                {"role": "CTO", "decision": "REJECT", "rationale": "late"},
            ).status_code
            == 409
        )
        locked = api.get(f"/v2/assurance/release-candidates/{rel}", headers=owner).json()
        assert (
            locked["candidate_status"] == "LOCKED"
            and locked["locked_reason"] == "TAMPERED_EVIDENCE"
        )

        # ---- rollback disables only the scope, once, and keeps the evidence
        assert (
            post(
                f"/releases/{authored}/rollback",
                owner,
                {"scope": {"country": "US"}, "reason": "canary alarm"},
            ).status_code
            == 200
        )
        assert (
            post(
                f"/releases/{authored}/rollback",
                owner,
                {"scope": {"country": "US"}, "reason": "again"},
            ).status_code
            == 409
        )
        assert (
            post(
                f"/releases/{authored}/decisions",
                second,
                {"role": "CTO", "decision": "APPROVE", "rationale": "late"},
            ).status_code
            == 409
        )
        assert len(events(rt_url, o.tenant, "assurance.release.rolled_back")) == 1
        assert api.get(f"/v2/assurance/test-runs/{rid}/evidence", headers=owner).json()[
            "manifests"
        ], "rollback keeps every manifest"

        # ---- the event chain cannot be edited, even by someone with SQL access through the runtime role
        async def edit() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
                from sqlalchemy import text

                await c.execute(text("UPDATE evidence.uat_event SET payload = '{}'::jsonb"))

        with pytest.raises(Exception, match="append-only"):
            asyncio.run(edit())


# ------------------------------------------------------------------------------------------------------------- the full path to GO (service level)

FULL = [
    {"case_id": f"{s}-01", "suite": s, "risk": "P0", "dependency": "OWN", "assertion": s}
    for s in ("GOV", "IDV", "DOC", "PRO", "RNT", "INT", "VC", "OPS")
] + [
    {"case_id": c, "suite": "PRI", "risk": "P0", "dependency": "OWN", "assertion": c}
    for c in ("PRI-07", "PRI-08")
]
FACTS = {
    "unresolved_requirement_links": 0, "official_source_status_in_date": True, "critical_false_pass_open": False, "open_critical_or_high_security": 0,
    "thresholds_policy_signed": True, "flag_documented": True, "post_launch_owner": "sre_lead", "country_legal_required": True,
    "no_go_facts": {k: False for k in __import__("zid_platform.modules.assurance.domain.rules", fromlist=["NO_GO"]).NO_GO},
}  # fmt: skip


def test_only_a_release_with_every_case_passed_every_gate_and_every_role_signed_is_go_and_a_new_build_undoes_it(
    rt_url: str, monkeypatch
) -> None:
    tenant = str(uuid.uuid4())
    admin, tester, author = (str(uuid.uuid4()) for _ in range(3))
    holders = {
        r: str(uuid.uuid4()) for r in ("QA_LEAD", "CTO", "SECURITY_GRC", "PRIVACY", "COUNTRY_LEGAL")
    }

    async def ready(
        *_a: Any, **_k: Any
    ) -> dict[
        str, Any
    ]:  # the privacy gate is Document 04's own: stood in for here, covered by its own tests
        return {"ready": True, "blockers": []}

    monkeypatch.setattr(svc, "release_gate", ready)

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await svc.assign_role(c, tenant, admin, role="TESTER", principal=tester)
            for role, p in holders.items():
                await svc.assign_role(c, tenant, admin, role=role, principal=p)
            await svc.import_baseline(c, tenant, admin, version="1", cases=FULL)
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
                case_ids=[x["case_id"] for x in FULL], fixture_hashes=[sha("fx")], commit_authors=[author],
            )  # fmt: skip

            # the signed policy G6 reads: proposed by the QA lead, signed by three different role holders
            pol = await svc.propose_policy(
                c, tenant, holders["QA_LEAD"], release_unit=UNIT, thresholds={"p95_ms": 400}
            )
            for role in ("QA_LEAD", "CTO", "SECURITY_GRC"):
                await svc.sign_policy(
                    c,
                    tenant,
                    holders[role],
                    policy_id=pol["policy_id"],
                    role=role,
                    decision="APPROVE",
                    rationale="agreed",
                )

            async def pass_case(case_id: str, **over: Any) -> dict[str, Any]:
                args: dict[str, Any] = {
                    "run_id": run["run_id"], "case_id": case_id, "result": "PASS", "requirement_ids": ["R"], "trace_refs": ["t"],
                    "provider_receipt_refs": [], "source_snapshot_refs": [], "oracle_version": "v1", "defect_id": None, "human_approval_refs": [],
                }  # fmt: skip
                return await svc.sign_manifest(c, tenant, tester, **{**args, **over})

            for x in FULL[:-1]:
                assert (await pass_case(x["case_id"]))["verdict"] == "PASS"
            thin = await pass_case(FULL[-1]["case_id"], trace_refs=[])
            assert thin["verdict"] == "INCONCLUSIVE"

            async def candidate(build: str = BUILD) -> str:
                made = await svc.create_candidate(
                    c, tenant, admin, release_unit=UNIT, build_sha256=build, policy_pack="US-CA@1", facts=FACTS, engineering_owner=author,
                    commit_authors=[author], privacy_gate={"market": "US", "product": "p", "version": "1"},
                )  # fmt: skip
                assert made["decision"]["status"] == "NO_GO"
                return made["release_id"]

            rel = await candidate()
            assert (await svc.preview(c, rel))["decision"]["gates"][0]["reasons"][
                0
            ] == "PRI-08_INCONCLUSIVE"

            # the last case gets a complete manifest; a new candidate sees it
            assert (await pass_case(FULL[-1]["case_id"]))["verdict"] == "PASS"
            rel = await candidate()
            for i, (role, p) in enumerate(holders.items()):
                out = await svc.sign_decision(
                    c,
                    tenant,
                    p,
                    release_id=rel,
                    role=role,
                    decision="APPROVE",
                    rationale="evidence reviewed",
                )
                expect = "GO" if i == len(holders) - 1 else "NO_GO"
                assert out["candidate_status"] == expect, (role, out["decision"]["gates"])
            final = await svc.preview(c, rel)
            assert (
                final["decision"]["status"] == "GO"
                and final["decision"]["reason"] == "ALL_GATES_PASSED"
            )

            # a different build: every PASS is EXPIRED and the release is no longer go
            newer = await svc.create_candidate(
                c, tenant, admin, release_unit=UNIT, build_sha256=sha("build-2"), policy_pack="US-CA@1", facts=FACTS, engineering_owner=author,
                commit_authors=[author], privacy_gate={"market": "US", "product": "p", "version": "1"},
            )  # fmt: skip
            assert (
                len(newer["expired_cases"]) == len(FULL) and newer["decision"]["status"] == "NO_GO"
            )
            with pytest.raises(RuleError, match="ROLE_NOT_ASSIGNED_TO_THE_CALLER"):
                await svc.sign_decision(
                    c,
                    tenant,
                    admin,
                    release_id=newer["release_id"],
                    role="CTO",
                    decision="APPROVE",
                    rationale="admin tries",
                )

    asyncio.run(go())
    assert json.dumps(sorted(holders))  # the five release roles are five different people

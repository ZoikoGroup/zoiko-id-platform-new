"""ZID-AG-WF-001 (the DAARA reference agent) against PostgreSQL. Synthetic data only; the rows AT-01..AT-24 these exercise are in each docstring.

The runner is driven through its service so time and failures can be injected, and a short HTTP pass checks the routes. Every case that
should change nothing also proves it: the mandates and grants are read back before and after.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from gateway_fixture import ISS, READ
from sqlalchemy.exc import DBAPIError
from test_agent_governance_pg import REGION, Gov
from zid_db import tenant_transaction
from zid_platform.modules.machine.application import daara as svc
from zid_platform.modules.machine.application import governance as gov
from zid_platform.modules.machine.domain import daara as d
from zid_platform.modules.machine.infrastructure import governance_store as gstore

DAARA_TOOLS = sorted(d.TOOLS)


@pytest.fixture(autouse=True)
def _enforce() -> Any:
    before = gov.mode()
    gov.configure("enforce")
    yield
    gov.configure(before)
    svc.clear_explainers()


class Daara(Gov):
    """A governed tenant whose agent `root` holds the DAARA tools, with mandates that have a known answer."""

    def __init__(self, rt_url: str, owner_url: str) -> None:
        super().__init__(rt_url, owner_url)
        self.w.owner(
            "UPDATE agent.agent_profile SET allowed_tools = allowed_tools || CAST(:tools AS text[]) WHERE agent_principal_id = :a AND tenant_id = :t",
            tools=DAARA_TOOLS,
            a=uuid.UUID(self.agent),
        )

    def baseline(self, ai: tuple[str, ...] = ()) -> None:
        self.run(
            lambda c: gov.set_baseline(
                c, self.tenant, self.p("own"), allowed_tools=["docs.read", *DAARA_TOOLS], allowed_regions=[REGION],
                ai_providers=list(ai), quotas=None, schedules_allowed=False,
            )
        )  # fmt: skip

    def setup(self, agent: str | None = None, **kw: Any) -> dict[str, Any]:
        args = {
            "label": "Access Readiness: Finance",
            "rationale": None,
            "warn_days": 14,
            "region": REGION,
            "ai_explain": False,
        }
        args.update(kw)
        aid = agent or self.agent
        return self.run(lambda c: svc.save_setup(c, self.tenant, aid, self.p("dev"), **args))  # type: ignore[no-any-return]

    def validate(self, agent: str | None = None) -> gov.Outcome:
        aid = agent or self.agent
        return self.run(lambda c: svc.validate(c, self.tenant, aid, self.p("dev")))  # type: ignore[no-any-return]

    def dry(self, **kw: Any) -> gov.Outcome:
        return self.run(lambda c: svc.dry_run(c, self.tenant, self.agent, self.p("dev"), **kw))  # type: ignore[no-any-return]

    def publish_request(self) -> gov.Outcome:
        return self.run(lambda c: svc.request_publish(c, self.tenant, self.agent, self.p("dev")))  # type: ignore[no-any-return]

    def go_live(self, signers: tuple[str, ...] = ("sec", "auth"), **setup: Any) -> dict[str, Any]:
        """Setup, validate, sandbox, request, sign, apply: the whole of the wizard."""
        self.setup(**setup)
        self.baseline(ai=("provider-a",) if setup.get("ai_explain") else ())
        assert self.validate().ok
        assert self.dry().ok
        req = self.publish_request()
        assert req.ok, req.codes
        view = req.data
        self.approve(view, list(signers))
        assert self.apply(view["request_id"]).ok
        return view

    def start(self, key: str) -> gov.Outcome:
        return self.run(
            lambda c: svc.run(c, self.tenant, self.agent, self.p("dev"), idempotency_key=key)
        )  # type: ignore[no-any-return]

    # -- mandates with a known answer

    def mandate(self, status: str, days: int, actions: list[str], grant: str | None) -> str:
        mid = str(uuid.uuid4())
        self.w.owner(
            "INSERT INTO agent.mandate (mandate_id, tenant_id, authority_grant_id, represented_principal_id, executor_principal_id, purpose_id, "
            "resource_scope_id, allowed_actions, home_cell_id, status, valid_from, valid_to, created_by_principal_id) VALUES "
            "(:id, :t, :g, :rep, :exe, :p, :rs, CAST(:acts AS text[]), :cell, CAST(:st AS agent.mandate_status), :vf, :vt, :by)",
            id=uuid.UUID(mid), g=uuid.UUID(grant or str(uuid.uuid4())), rep=uuid.UUID(self.w.sponsor), exe=uuid.UUID(self.agent),
            p=uuid.uuid4(), rs=uuid.uuid4(), acts=actions, cell=uuid.uuid4(), st=status,
            vf=datetime.now(UTC) - timedelta(days=400), vt=datetime.now(UTC) + timedelta(days=days), by=uuid.UUID(self.p("own")),
        )  # fmt: skip
        return mid

    def seed(self) -> dict[str, str]:
        grant = self.w.approver_grant
        return {
            "expired": self.mandate("ACTIVE", -2, [READ], grant),
            "soon": self.mandate("ACTIVE", 5, [READ], grant),
            "ok": self.mandate("ACTIVE", 90, [READ], grant),
            "variance": self.mandate("ACTIVE", 90, [READ, "docs.sign"], grant),
            "nogrant": self.mandate("ACTIVE", 90, [READ], None),
            "closed": self.mandate("REVOKED", -30, [READ], grant),
        }

    def snapshot(self) -> list[Any]:
        """Everything an access change would touch: mandates, grants, roles. DAARA must leave it byte-for-byte alike."""
        return [
            self.w.owner(
                "SELECT mandate_id, status, valid_to, allowed_actions FROM agent.mandate WHERE tenant_id = :t ORDER BY mandate_id"
            ),
            self.w.owner(
                "SELECT agent_principal_id, status, allowed_tools, sponsor_principal_id FROM agent.agent_profile WHERE tenant_id = :t ORDER BY agent_principal_id"
            ),
        ]


# ====================================================================== setup, sandbox, publish


def test_setup_is_gated_by_validation_a_passed_sandbox_and_two_independent_approvals(
    rt_url: str, owner_url: str
) -> None:
    """AT-02, AT-03, AT-04, AT-05, AT-06, AT-24."""
    dd = Daara(rt_url, owner_url)
    # AT-04: an agent that does not list the three template tools cannot be validated; any other tool is simply not part of the template
    bad = dd.setup(agent=dd.w.outsider)
    assert bad["stage"] == "DRAFT" and any(
        b.startswith("AGENT_TOOLS_MISSING") for b in bad["blockers"]
    )
    assert any(c.startswith("AGENT_TOOLS_MISSING") for c in dd.validate(dd.w.outsider).codes)
    # AT-24: a region the company has not allowed fails closed, with the reason named
    dd.baseline()
    mars = dd.setup(region="mars")
    assert "REGION_DENIED" in mars["blockers"] and dd.validate().codes == ("REGION_DENIED",)
    # a missing company baseline fails closed too
    assert dd.setup()["stage"] == "DRAFT"
    assert (
        "REGION_DENIED" not in dd.run(lambda c: svc.get_setup(c, dd.tenant, dd.agent))["blockers"]
    )
    assert dd.validate().ok
    now = dd.run(lambda c: svc.get_setup(c, dd.tenant, dd.agent))
    assert (
        now["stage"] == "READY_TO_RUN_SANDBOX"
        and now["setup"]["validated"]
        and not now["setup"]["sandbox_passed"]
    )

    # AT-05: publishing is disabled until the sandbox passed for THIS configuration
    assert dd.publish_request().codes == ("SANDBOX_NOT_PASSED",)

    # AT-06: a dry run reads and writes nothing live (no run row, no proposal, no reservation) and leaves one evidence row
    before = [
        dd.w.owner(f"SELECT count(*) FROM agent.{t} WHERE tenant_id = :t")[0][0]
        for t in ("run_ledger", "review_proposal", "budget_reservation")
    ]
    ok = dd.dry()
    after = [
        dd.w.owner(f"SELECT count(*) FROM agent.{t} WHERE tenant_id = :t")[0][0]
        for t in ("run_ledger", "review_proposal", "budget_reservation")
    ]
    assert (
        ok.ok
        and ok.data["passed"]
        and ok.data["synthetic"]
        and ok.data["wrote"] == 0
        and before == after == [0, 0, 0]
    )
    assert all("synth" in f for f in ok.data["findings"])

    # a dry run that reaches for a real write is a security incident and a failed test, never a quiet no-op
    def reach() -> None:
        raise d.DryRunWriteAttempt

    attempt = dd.dry(writer=reach)
    assert attempt.codes == ("DRY_RUN_WRITE_ATTEMPT",)
    assert "DRY_RUN_WRITE_ATTEMPT" in {
        e["code"] for e in dd.events() if e["action"] == "daara.dry_run"
    }

    # AT-05: any change to the setup voids both the validation and the sandbox pass
    changed = dd.setup(warn_days=30)
    assert (
        changed["stage"] == "DRAFT"
        and not changed["setup"]["validated"]
        and not changed["setup"]["sandbox_passed"]
    )
    assert dd.publish_request().codes == ("NOT_VALIDATED", "SANDBOX_NOT_PASSED")
    assert dd.validate().ok and dd.dry().ok

    # the request is the governance plane's: two independent signers, never the requester, and approved is not live
    req = dd.publish_request()
    assert (
        req.ok
        and req.data["kind"] == "PUBLISH"
        and req.data["required_slots"] == ["SECURITY", "AUTHORITY"]
    )
    assert (
        dd.publish_request().data["request_id"] == req.data["request_id"]
    )  # asking again opens nothing new
    assert (
        "SEPARATION_OF_DUTIES"
        in dd.decide(req.data["request_id"], "dev", req.data["policy_hash"]).codes
    )
    assert dd.start("run-before-0001").codes[0] in ("APPROVAL_REQUIRED", "POLICY_UNAVAILABLE")
    dd.approve(req.data, ["sec", "auth"])
    assert dd.start("run-before-0002").codes[0] in (
        "APPROVAL_REQUIRED",
        "AGENT_INACTIVE",
        "POLICY_UNAVAILABLE",
    )  # approved, not applied: still no run
    assert dd.apply(req.data["request_id"]).ok
    live = dd.run(lambda c: svc.get_setup(c, dd.tenant, dd.agent))
    assert live["stage"] == "ACTIVE" and live["policy"]["version"] == 1


def test_a_sponsorless_agent_cannot_be_validated(rt_url: str, owner_url: str) -> None:
    """AT-02."""
    dd = Daara(rt_url, owner_url)
    dd.setup()  # registers the template tools, which the company baseline may then list
    dd.baseline()
    dd.w.disable_sponsor()
    try:
        assert "SPONSOR_INVALID" in dd.validate().codes
        assert dd.publish_request().codes[0] == "SPONSOR_INVALID"
    finally:
        dd.w.owner("UPDATE agent.agent_profile SET status = 'SUSPENDED' WHERE tenant_id = :t")


# ====================================================================== runs


def test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access(
    rt_url: str, owner_url: str
) -> None:
    """AT-07, AT-08, AT-09, AT-10, AT-11, AT-12, AT-13, AT-14, AT-20, AT-23."""
    dd = Daara(rt_url, owner_url)
    ids = dd.seed()
    dd.go_live()
    before = dd.snapshot()

    first = dd.start("run-daara-0001")
    assert first.ok, first.codes
    r = first.data
    assert r["state"] == "NEEDS_REVIEW" and not r["incomplete"] and r["privileges_changed"] == 0
    assert r["issues"] == 4 and r["proposals"] == 4 and r["summary"]["new_proposals"] == 4
    assert r["summary"]["by_rule"] == {
        "R-01": 1,
        "R-02": 1,
        "R-04": 1,
        "R-05": 1,
    }  # the closed and healthy mandates raise nothing (AT-08)
    assert r["explanation_source"] == "FIXED" and r["explanation"].endswith(
        "No access was changed."
    )
    assert dd.snapshot() == before  # AT-10: nothing about any mandate changed

    # AT-11: the same key is the same run; a new run over the same facts proposes nothing new, and the sighting is counted
    assert dd.start("run-daara-0001").data["run_id"] == r["run_id"]
    second = dd.start("run-daara-0002")
    assert second.ok and second.data["issues"] == 4 and second.data["summary"]["new_proposals"] == 0
    alerts = dd.w.owner(
        "SELECT count(*), max(occurrences), bool_and(is_task) FROM platform.notification_request WHERE tenant_id = :t AND kind = 'agent.review.proposals'"
    )
    assert alerts == [
        (1, 2, True)
    ]  # the company was told once, as a task, and the second run folded into the same open alert
    queue = dd.run(lambda c: svc.list_proposals(c, status="OPEN", agent_id=None))
    assert len(queue["items"]) == 4 and all(
        p["seen_count"] == 2 and p["submitted_by"] == "AGENT" for p in queue["items"]
    )
    # the reviewer sees masked targets and the notice, never a full identifier
    assert all(re.fullmatch(r"(mandate|agent):[0-9a-f]{8}…", p["target"]) for p in queue["items"])
    assert all("No access permissions changed" in p["notice"] for p in queue["items"])

    # AT-13: the same facts at the same instant give the same findings (replay)
    facts = dd.run(lambda c: svc._facts(c, 100))
    now = datetime.now(UTC)
    one = d.evaluate_rules(facts[0], facts[1], now=now)
    two = d.evaluate_rules(list(reversed(facts[0])), list(reversed(facts[1])), now=now)
    assert one == two and {f.target_ref for f in one.findings} == {
        f"mandate:{ids[k]}" for k in ("expired", "soon", "variance", "nogrant")
    }

    # AT-12 and AT-10: a proposal is decided by a person only; an agent cannot, and a hand-off changes no access
    target = queue["items"][0]["proposal_id"]
    assert dd.run(
        lambda c: svc.decide_proposal(
            c, dd.tenant, dd.agent, target, action="dismiss", reason="NOT_A_RISK"
        )
    ).codes == ("AGENT_CANNOT_REVIEW",)
    with pytest.raises(gov.GovernanceError, match="REASON_REQUIRED"):
        dd.run(
            lambda c: svc.decide_proposal(
                c, dd.tenant, dd.p("own"), target, action="dismiss", reason="because"
            )
        )
    handed = dd.run(
        lambda c: svc.decide_proposal(c, dd.tenant, dd.p("own"), target, action="handoff")
    )
    assert (
        handed.ok
        and handed.data["status"] == "HANDED_OFF"
        and handed.data["handoff_digest"].startswith("sha256:")
    )
    assert "No access permissions changed" in handed.data["notice"]
    assert dd.run(
        lambda c: svc.decide_proposal(
            c, dd.tenant, dd.p("own"), target, action="dismiss", reason="NOT_A_RISK"
        )
    ).codes == ("PROPOSAL_FINAL",)
    other = queue["items"][1]["proposal_id"]
    assert (
        dd.run(
            lambda c: svc.decide_proposal(
                c, dd.tenant, dd.p("own"), other, action="assign", assignee=dd.p("sec")
            )
        ).data["status"]
        == "ASSIGNED"
    )
    with pytest.raises(DBAPIError):  # the finding itself cannot be edited by anyone
        dd.w.owner(
            "UPDATE agent.review_proposal SET rule_id = 'R-99' WHERE proposal_id = :p",
            p=uuid.UUID(target),
        )
    assert dd.snapshot() == before  # AT-12: still nothing changed

    # AT-20: the run's evidence is complete, in order, and the chain verifies
    detail = dd.run(lambda c: svc.run_detail(c, dd.tenant, dd.agent, r["run_id"], detail=True))
    steps = [e["step"] for e in detail["timeline"]]
    assert steps[:1] == ["run.start"] and steps.index("daara.authorized") < steps.index(
        "daara.metadata_read"
    ) < steps.index("daara.rules_evaluated") < steps.index("daara.proposals_written") < steps.index(
        "daara.completed"
    )
    assert detail["evidence"]["complete"] and detail["policy_decisions"] and not detail["redacted"]
    assert dd.run(lambda c: gstore.verify_chain(c))["intact"] is True
    assert (
        dd.run(lambda c: svc.run_detail(c, dd.tenant, dd.agent, r["run_id"], detail=False))[
            "policy_decisions"
        ]
        == []
    )  # redacted by role

    # AT-19 and AT-08: a freeze refuses the next run, and nothing queued is run after it
    dd.run(
        lambda c: gov.pause(
            c,
            dd.tenant,
            dd.p("sec"),
            scope="AGENT",
            agent_id=dd.agent,
            reason="drill",
            severity="P2",
        )
    )
    assert dd.start("run-daara-0003").codes[0] in ("EMERGENCY_FROZEN", "AGENT_INACTIVE")


def test_unknown_state_makes_the_run_incomplete_never_healthy(rt_url: str, owner_url: str) -> None:
    """AT-14: a mandate with no usable end date cannot be judged, so the run is NEEDS_REVIEW and incomplete, with no invented pass."""
    dd = Daara(rt_url, owner_url)
    dd.go_live()
    mid = dd.mandate("ACTIVE", 90, [READ], dd.w.approver_grant)
    dd.w.owner(
        "UPDATE agent.mandate SET status = 'PAUSED' WHERE mandate_id = :m", m=uuid.UUID(mid)
    )  # a known, quiet state: nothing to review
    clean = dd.start("run-clean-0001")
    assert (
        clean.data["state"] == "COMPLETED"
        and not clean.data["incomplete"]
        and clean.data["issues"] == 0
    )
    assert clean.data["explanation"].startswith("Checked ")
    # now an adapter-level unknown: a status the rules do not know
    out = d.evaluate_rules(
        [d.MandateFact("m", "???", None, frozenset(), None, True, "ACTIVE")],
        [],
        now=datetime.now(UTC),
    )
    assert d.classify_run(out.findings, out.unknown) == ("NEEDS_REVIEW", True)


def test_the_record_cap_fails_the_run_instead_of_returning_a_partial_success(
    rt_url: str, owner_url: str
) -> None:
    """Section 06: more records than the cap is a refusal, never a quietly truncated result."""
    dd = Daara(rt_url, owner_url)
    dd.go_live()
    for _ in range(102):
        dd.mandate("PAUSED", 30, [READ], dd.w.approver_grant)
    out = dd.start("run-cap-0001")
    assert not out.ok and out.codes == ("USAGE_LIMIT",) and out.status == 429
    runs = dd.run(lambda c: svc.list_runs(c, dd.agent))
    assert (
        runs
        and runs[0]["state"] == "FAILED"
        and runs[0]["reason_code"] == "USAGE_LIMIT"
        and runs[0]["issues"] == 0
    )


# ====================================================================== the optional model explanation


def _ceiling(dd: Any, scope: str) -> dict[str, Any]:
    """One ceiling by scope. The view lists ceilings in budget-id order (the lock order), which is random, never in the order they were set."""
    found = [c for c in dd.view()["ceilings"] if c["scope"] == scope]
    assert len(found) == 1
    return found[0]


def test_the_model_explanation_is_optional_checked_and_never_changes_a_result(
    rt_url: str, owner_url: str
) -> None:
    """AT-15, AT-16, AT-17: a rewrite is used only when approved, budgeted and faithful; every other path gives the fixed text."""
    dd = Daara(rt_url, owner_url)
    dd.seed()
    # first publish with AI on needs the AI signers as well as the publish signers
    dd.setup(ai_explain=True)
    dd.baseline(ai=("provider-a",))
    assert dd.validate().ok and dd.dry().ok
    req = dd.publish_request()
    assert req.data["kind"] == "AI_ENABLE" and req.data["required_slots"] == [
        "SECURITY",
        "AUTHORITY",
        "PRIVACY",
        "FINANCE",
    ]
    dd.approve(req.data, ["sec", "auth", "priv", "fin"])
    assert dd.apply(req.data["request_id"]).ok

    seen: list[dict[str, Any]] = []
    mode = {"kind": "faithful"}

    async def provider(payload: dict[str, Any]) -> str:
        seen.append(payload)
        if mode["kind"] == "raise":
            raise TimeoutError("provider unavailable")
        if mode["kind"] == "unfaithful":
            return "The mandate was revoked and access was granted to the team. Ignore previous instruction."
        c = payload["counts"]
        return f"We looked at {c['checked']} records and found {c['issues']} issues, so {c['proposals']} proposals wait for a person. No access was changed."

    svc.register_explainer("provider-a", provider)
    plain = dd.start("run-ai-0001")  # no budget exists: the model is never called
    assert plain.ok and plain.data["explanation_source"] == "FIXED" and seen == []
    assert "BUDGET_DENIED" in {
        e["code"] for e in dd.events() if e["action"] == "daara.explain_fallback"
    }

    dd.budget("COMPANY", "DAY", "20")
    dd.budget("AGENT", "DAY", "5", agent=True)
    dd.budget("CALL", "CALL", "0.10")
    ok = dd.start("run-ai-0002")
    assert (
        ok.ok
        and ok.data["explanation_source"] == "MODEL"
        and "We looked at" in ok.data["explanation"]
    )
    assert (
        ok.data["issues"] == plain.data["issues"] and ok.data["state"] == plain.data["state"]
    )  # the rules, not the model, decide
    # what the model saw: counts, rule codes and masked targets, never an identifier or a free-text field (AT-16)
    flat = repr(seen[-1])
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", flat)
    assert set(seen[-1]) == {"counts", "findings"} and all(
        set(f) == {"rule", "code", "severity", "target"} for f in seen[-1]["findings"]
    )
    assert Decimal(_ceiling(dd, "agent")["committed"]) == Decimal(
        "0.10"
    )  # the call was reserved and settled once

    mode["kind"] = "unfaithful"
    bad = dd.start("run-ai-0003")
    assert (
        bad.ok
        and bad.data["explanation_source"] == "FIXED"
        and "granted" not in bad.data["explanation"]
    )
    assert any(
        str(e["code"]).startswith("UNFAITHFUL")
        for e in dd.events()
        if e["action"] == "daara.explain_fallback"
    )
    mode["kind"] = "raise"
    down = dd.start("run-ai-0004")  # AT-17: a model outage preserves the deterministic result
    assert (
        down.ok
        and down.data["explanation_source"] == "FIXED"
        and down.data["issues"] == ok.data["issues"]
        and down.data["state"] == ok.data["state"]
    )
    assert "PROVIDER_FAILED" in {
        e["code"] for e in dd.events() if e["action"] == "daara.explain_fallback"
    }
    assert (
        Decimal(_ceiling(dd, "agent")["reserved"]) == 0
    )  # every failed rewrite gave its reservation back


# ====================================================================== HTTP


def test_daara_http_routes_and_the_authority_they_need(rt_url: str, owner_url: str) -> None:
    """AT-01: without the action the setup and the run routes refuse; with it they work."""
    dd = Daara(rt_url, owner_url)
    dd.seed()
    dd.go_live()

    async def grant() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(dd.tenant)) as c:
            await make_admin(
                c,
                dd.tenant,
                dd.w.human,
                [
                    "agent.daara.read",
                    "agent.daara.manage",
                    "agent.daara.run",
                    "agent.review.read",
                    "agent.review.decide",
                    "agent.audit.read",
                ],
                ["agent:*"],
                release=False,
            )
            await make_admin(
                c,
                dd.tenant,
                dd.w.approver,
                ["agent.daara.read", "agent.review.read"],
                ["agent:*"],
                release=False,
            )

    dd.w.run(grant())
    with TestClient(dd.w.app, base_url=ISS) as http:
        person = dd.w.bearer(dd.w.human, scope=frozenset({"admin"}))
        viewer = dd.w.bearer(dd.w.approver, scope=frozenset({"admin"}))
        body = {
            "label": "Access Readiness: Finance",
            "warn_days": 14,
            "region": REGION,
            "ai_explain": False,
        }
        assert (
            http.put(f"/v1/machine/agents/{dd.agent}/daara", headers=viewer, json=body).status_code
            == 403
        )  # AT-01: no authority, no setup
        assert http.get(f"/v1/machine/agents/{dd.agent}/daara", headers=viewer).status_code == 200
        assert (
            http.get("/v1/machine/daara/template", headers=viewer).json()["tools"][0]["tool_id"]
            in d.TOOLS
        )
        no_key = http.post(f"/v1/machine/agents/{dd.agent}/daara/runs", headers=person)
        assert no_key.status_code == 422  # an Idempotency-Key is required
        run = http.post(
            f"/v1/machine/agents/{dd.agent}/daara/runs",
            headers={**person, "Idempotency-Key": "http-run-0001"},
        )
        assert run.status_code == 201, run.text
        assert run.json()["state"] == "NEEDS_REVIEW" and run.json()["privileges_changed"] == 0
        again = http.post(
            f"/v1/machine/agents/{dd.agent}/daara/runs",
            headers={**person, "Idempotency-Key": "http-run-0001"},
        )
        assert again.json()["run_id"] == run.json()["run_id"]
        detail = http.get(
            f"/v1/machine/agents/{dd.agent}/daara/runs/{run.json()['run_id']}", headers=person
        )
        assert (
            detail.status_code == 200
            and detail.json()["redacted"] is True
            and detail.json()["policy_decisions"] == []
        )
        queue = http.get("/v1/machine/agent-review?status=OPEN", headers=viewer).json()
        assert queue["open"] >= 1 and "No access permissions changed" in queue["notice"]
        first = queue["items"][0]["proposal_id"]
        assert (
            http.post(
                f"/v1/machine/agent-review/{first}:dismiss",
                headers=viewer,
                json={"reason": "NOT_A_RISK"},
            ).status_code
            == 403
        )
        done = http.post(f"/v1/machine/agent-review/{first}:handoff", headers=person, json={})
        assert done.status_code == 200, done.text  # this person holds review.decide
        assert (
            done.json()["status"] == "HANDED_OFF"
            and "No access permissions changed" in done.json()["notice"]
        )
        assert http.get(
            "/v1/machine/agent-review/00000000-0000-0000-0000-000000000000", headers=viewer
        ).status_code in (404, 422)


# ====================================================================== queued and scheduled runs


def test_a_queued_run_is_counted_now_rechecked_when_claimed_and_run_by_a_worker(
    rt_url: str, owner_url: str
) -> None:
    """AT-19 (queued runs), AT-07 in spirit: what changed while a run waited is checked again when a worker takes it."""
    dd = Daara(rt_url, owner_url)
    dd.seed()
    dd.go_live()

    def queue(key: str) -> gov.Outcome:
        return dd.run(
            lambda c: svc.enqueue(c, dd.tenant, dd.agent, dd.p("dev"), idempotency_key=key)
        )  # type: ignore[no-any-return]

    def work() -> list[dict[str, Any]]:
        return dd.run(lambda c: svc.work_queue(c, datetime.now(UTC)))  # type: ignore[no-any-return]

    first = queue("queue-key-0001")
    assert first.ok and first.data["state"] == "QUEUED"
    assert (
        queue("queue-key-0001").data["run_id"] == first.data["run_id"]
    )  # a replay is the same queued run
    waiting = queue("queue-key-0002")  # a queued run counts as the agent's one active run
    assert waiting.codes == ("USAGE_LIMIT",) and waiting.data["waiting_capacity"] is True
    assert dd.run(lambda c: svc.list_runs(c, dd.agent)) == []  # nothing has run yet

    done = work()
    assert [x["ok"] for x in done] == [True] and done[0]["run_id"] == first.data["run_id"]
    runs = dd.run(lambda c: svc.list_runs(c, dd.agent))
    assert (
        len(runs) == 1 and runs[0]["state"] == "NEEDS_REVIEW" and runs[0]["privileges_changed"] == 0
    )
    assert work() == []  # the run is claimed once: a second pass finds nothing

    # an agent that stopped being active while the run waited is not run, and the failure names why
    second = queue("queue-key-0003")
    assert second.ok
    dd.w.owner(
        "UPDATE agent.agent_profile SET status = 'SUSPENDED' WHERE agent_principal_id = :a AND tenant_id = :t",
        a=uuid.UUID(dd.agent),
    )
    stopped = work()
    assert stopped and stopped[0]["ok"] is False and stopped[0]["codes"] == ["AGENT_INACTIVE"]
    assert dd.w.owner(
        "SELECT state, reason_code FROM agent.run_ledger WHERE run_id = :r AND tenant_id = :t",
        r=uuid.UUID(second.data["run_id"]),
    ) == [("FAILED", "AGENT_INACTIVE")]
    dd.w.owner(
        "UPDATE agent.agent_profile SET status = 'ACTIVE' WHERE agent_principal_id = :a AND tenant_id = :t",
        a=uuid.UUID(dd.agent),
    )

    # a freeze cancels the queued run; a worker never starts it
    third = queue("queue-key-0004")
    assert third.ok
    dd.run(
        lambda c: gov.pause(
            c,
            dd.tenant,
            dd.p("sec"),
            scope="AGENT",
            agent_id=dd.agent,
            reason="drill",
            severity="P2",
        )
    )
    assert work() == []
    assert dd.w.owner(
        "SELECT state, reason_code FROM agent.run_ledger WHERE run_id = :r AND tenant_id = :t",
        r=uuid.UUID(third.data["run_id"]),
    ) == [("CANCELED", "EMERGENCY_FROZEN")]
    assert dd.run(lambda c: gstore.verify_chain(c))["intact"] is True


def test_a_schedule_needs_the_agent_policy_the_company_baseline_and_the_platform_ceiling(
    rt_url: str, owner_url: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T37: a schedule cannot be switched on by one of the three alone, and the shipped platform ceiling keeps every schedule off."""
    import yaml
    from zid_platform.modules.machine.infrastructure import governance_config as cfg

    dd = Daara(rt_url, owner_url)
    dd.seed()
    dd.setup(schedule="DAILY")
    # the company baseline cannot allow schedules while the platform ceiling says no, so the setup cannot be validated
    dd.run(
        lambda c: gov.set_baseline(
            c,
            dd.tenant,
            dd.p("own"),
            allowed_tools=["docs.read", *DAARA_TOOLS],
            allowed_regions=[REGION],
            ai_providers=[],
            quotas=None,
            schedules_allowed=True,
        )
    )
    assert dd.run(lambda c: gov.get_baseline(c, dd.tenant))["schedules_allowed"] is False
    assert "SCHEDULE_NOT_PERMITTED" in dd.validate().codes

    # with the platform ceiling raised (a file an owner edits) the company can allow it, and the agent's own policy must too
    raw = yaml.safe_load(cfg.FILE.read_text(encoding="utf-8"))
    raw["platform_quota_ceiling"]["schedules_enabled"] = True
    allowed = tmp_path / "governance.yaml"
    allowed.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setattr(cfg, "FILE", allowed)
    cfg.config.cache_clear()
    try:
        dd.run(
            lambda c: gov.set_baseline(
                c,
                dd.tenant,
                dd.p("own"),
                allowed_tools=["docs.read", *DAARA_TOOLS],
                allowed_regions=[REGION],
                ai_providers=[],
                quotas=None,
                schedules_allowed=True,
            )
        )
        assert dd.run(lambda c: gov.get_baseline(c, dd.tenant))["schedules_allowed"] is True
        assert dd.validate().ok and dd.dry().ok
        req = dd.publish_request()
        assert req.ok, req.codes
        # nothing is queued before the approved policy is live
        assert dd.run(lambda c: svc.schedule_due(c, datetime.now(UTC))) == []
        dd.approve(req.data, ["sec", "auth"])
        assert dd.apply(req.data["request_id"]).ok
        monday = datetime.now(UTC)
        queued = dd.run(lambda c: svc.schedule_due(c, monday))
        assert len(queued) == 1 and queued[0]["ok"] and not queued[0]["replayed"]
        assert (
            dd.run(lambda c: svc.schedule_due(c, monday))[0]["replayed"] is True
        )  # once a day, by key
        done = dd.run(lambda c: svc.work_queue(c, monday))
        assert [x["ok"] for x in done] == [True]
        assert dd.w.owner("SELECT mode, state FROM agent.run_ledger WHERE tenant_id = :t") == [
            ("SCHEDULED", "COMPLETED")
        ]
        tuesday = monday + timedelta(days=1)
        assert len(dd.run(lambda c: svc.schedule_due(c, tuesday))) == 1  # a new day, a new run
        dd.run(lambda c: svc.work_queue(c, tuesday))
    finally:
        monkeypatch.undo()
        cfg.config.cache_clear()
    # with the shipped ceiling back, the same agent queues nothing, whatever it asks for
    assert dd.run(lambda c: svc.schedule_due(c, datetime.now(UTC) + timedelta(days=2))) == []

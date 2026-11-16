"""Delegated Authority & Access Readiness agent over HTTP and against PostgreSQL with RLS (ZID-AG-WF-001; CR-091).

Acceptance cases: AT-01 AT-02 AT-03 AT-04 AT-05 AT-06 AT-07 AT-08 AT-09 AT-10 AT-11 AT-12 AT-13 AT-14 AT-18 AT-19 AT-20 AT-21 AT-23 AT-24.
(The run's credential, the sponsor notification, the evidence export and AT-15 to AT-17, the optional model, are in
test_access_readiness_runtime_pg.py; AT-22 is the console's readiness-a11y Playwright spec.) Every record is synthetic.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from access_readiness_fixture import ALLOWED, BASE, REGION, World, runtime, uid
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from zid_platform.modules.access_readiness.application import runs, setup
from zid_platform.modules.access_readiness.domain.policy import ReadinessError
from zid_platform.modules.access_readiness.infrastructure import store
from zid_platform.modules.machine.domain.agents import AgentStatus

KEY = "idem-key-0001"
AUDIT = "(event_type LIKE 'access_readiness.%' OR event_type LIKE 'agent.run.%' OR event_type = 'agent.review.proposal_created')"


class Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def body(**kw: Any) -> dict[str, Any]:
    return {
        "agent_principal_id": kw.pop("agent", ""),
        "display_name": "Access Readiness - Finance",
        **kw,
    }


def release(http: TestClient, w: World, agent: str) -> None:
    """Create, dry-run, request and approve: the whole gate sequence."""
    adm, apr = w.bearer(w.admin), w.bearer(w.approver)
    assert http.post(f"{BASE}/agents", headers=adm, json=body(agent=agent)).status_code == 201
    dry = http.post(f"{BASE}/agents/{agent}/dry-runs", headers=adm, json={})
    assert dry.status_code == 200 and dry.json()["passed"], dry.text
    assert http.post(f"{BASE}/agents/{agent}/publish-requests", headers=adm).status_code == 201
    done = http.post(f"{BASE}/agents/{agent}/publish-requests:approve", headers=apr)
    assert done.status_code == 200 and done.json()["status"] == "APPROVED", done.text


def count(w: World, table: str) -> int:
    return int(w.owner(f"SELECT count(*) FROM agent.{table} WHERE tenant_id = :t")[0][0])


def rights_snapshot(w: World) -> list[Any]:
    """Everything an agent could change if it could change a right: grants, mandates, principals, agent profiles."""
    return [
        w.owner("SELECT authority_grant_id, status, actions, valid_to, revision FROM authz.authority_grant WHERE tenant_id = :t ORDER BY 1"),
        w.owner("SELECT mandate_id, status, allowed_actions, valid_to, row_version FROM agent.mandate WHERE tenant_id = :t ORDER BY 1"),
        w.owner("SELECT agent_principal_id, status, allowed_tools, security_revision FROM agent.agent_profile WHERE tenant_id = :t ORDER BY 1"),
        w.owner("SELECT principal_id, status FROM core.principal WHERE tenant_id = :t ORDER BY 1"),
    ]  # fmt: skip


# --------------------------------------------------------------------------------------------------------------- the switch


def test_feature_off_by_default_every_route_is_not_found_and_no_run_executes(
    rt_url: str, owner_url: str
) -> None:
    """AT-19 (the switch): default OFF answers 404 for everyone, authenticated or not, and nothing runs."""
    w = World(rt_url, owner_url).start()
    with TestClient(w.app(), base_url="https://id.zoiko.test") as http:
        for method, path in (
            ("GET", f"{BASE}/agents"),
            ("POST", f"{BASE}/agents"),
            ("GET", f"{BASE}/review-proposals"),
            ("POST", f"{BASE}/agents/{w.daara}/runs"),
            ("POST", f"{BASE}/agents/{w.daara}:pause"),
        ):
            for headers in ({}, w.bearer(w.admin)):
                r = http.request(
                    method, path, headers=headers, json={} if method == "POST" else None
                )
                assert r.status_code == 404, (method, path)
    assert count(w, "access_review_setup") == count(w, "access_review_run") == 0

    # the service refuses too, so a caller that skips the route cannot start a run while it is off
    async def attempt(c: Any) -> None:
        with pytest.raises(ReadinessError) as e:
            await runs.start_run(
                c, runtime(enabled=False), w.tenant, actor=w.admin, agent_id=w.daara, idempotency_key=KEY,
                purpose="access_posture_review", selector={}, mode="manual", trace_id="t",
            )  # fmt: skip
        assert e.value.code == "FEATURE_DISABLED"

    w.db(attempt)


# --------------------------------------------------------------------------------------------------------------- setup gates


def test_setup_authorization_sponsor_blueprint_tools_region_and_digest(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url, owner_url).start()
    adm, apr, nob = w.bearer(w.admin), w.bearer(w.approver), w.bearer(w.nobody)
    with TestClient(w.on(), base_url="https://id.zoiko.test") as http:
        # AT-01: without an authorized tenant role nothing is visible or creatable
        assert http.get(f"{BASE}/agents", headers=nob).status_code == 403
        assert http.post(f"{BASE}/agents", headers=nob, json=body(agent=w.daara)).status_code == 403
        assert http.get(f"{BASE}/agents").status_code == 401

        # the tenant and region are server-set: a body that names them is refused
        assert (
            http.post(
                f"{BASE}/agents", headers=adm, json={**body(agent=w.daara), "tenant_id": uid()}
            ).status_code
            == 422
        )
        assert (
            http.post(
                f"{BASE}/agents", headers=adm, json={**body(agent=w.daara), "home_region": "eu-1"}
            ).status_code
            == 422
        )

        # AT-02: no setup for an agent whose accountable sponsor is not active
        orphan = http.post(f"{BASE}/agents", headers=adm, json=body(agent=w.exec_orphan))
        assert orphan.status_code == 422 and orphan.json()["error"]["code"] in (
            "AGENT_SPONSOR_REQUIRED",
        ), orphan.text
        # AT-04: an unapproved blueprint (not the DAARA template) and any tool beyond the allowlist are refused
        other = http.post(f"{BASE}/agents", headers=adm, json=body(agent=w.exec_ok))
        assert other.status_code == 422 and other.json()["error"]["code"] in (
            "BLUEPRINT_NOT_ALLOWED",
            "TOOL_NOT_ALLOWED",
        )
        assert http.post(f"{BASE}/agents", headers=adm, json=body(agent=uid())).status_code == 404
        # an unknown data source is refused
        assert (
            http.post(
                f"{BASE}/agents",
                headers=adm,
                json=body(agent=w.daara, resource_kinds=["credentials"]),
            ).status_code
            == 422
        )

        created = http.post(f"{BASE}/agents", headers=adm, json=body(agent=w.daara))
        assert created.status_code == 201, created.text
        s = created.json()
        assert (
            s["status"] == "DRAFT"
            and s["scope"]["home_region"] == REGION
            and "grant_role" in s["blocked"]
        )
        assert set(s["permitted_tools"]) == ALLOWED
        assert (
            http.post(f"{BASE}/agents", headers=adm, json=body(agent=w.daara)).status_code == 409
        )  # one setup per agent

        # a publish request needs a dry run on this exact scope first
        assert (
            http.post(f"{BASE}/agents/{w.daara}/publish-requests", headers=adm).status_code == 409
        )

        # AT-06: the dry run uses synthetic records and writes nothing live
        before = [
            count(w, t)
            for t in (
                "access_review_run",
                "access_review_proposal",
                "access_review_evidence",
                "access_review_policy_decision",
            )
        ]
        dry = http.post(
            f"{BASE}/agents/{w.daara}/dry-runs", headers=adm, json={"fixture": "baseline"}
        )
        assert dry.status_code == 200
        d = dry.json()
        assert (
            d["synthetic"] is True
            and d["live_writes"] == 0
            and d["passed"]
            and d["setup_status"] == "SANDBOX_READY"
        )
        assert set(d["rules_covered"]) == {"R-01", "R-02", "R-03", "R-04", "R-05", "R-06"}
        assert all(f["target_ref"].startswith("mandate:synthetic-") for f in d["findings"])
        assert [
            count(w, t)
            for t in (
                "access_review_run",
                "access_review_proposal",
                "access_review_evidence",
                "access_review_policy_decision",
            )
        ] == before
        assert (
            http.post(
                f"{BASE}/agents/{w.daara}/dry-runs", headers=adm, json={"fixture": "live"}
            ).status_code
            == 422
        )
        assert (
            http.post(
                f"{BASE}/agents/{w.daara}/dry-runs", headers=adm, json={"policy_version": "x/9"}
            ).status_code
            == 422
        )

        # AT-05: the requester cannot approve; a draft change after the request invalidates the signed digest
        req = http.post(f"{BASE}/agents/{w.daara}/publish-requests", headers=adm)
        assert req.status_code == 201 and req.json()["status"] == "PENDING_APPROVAL"
        digest_then = req.json()["approval"]["scope_digest"]
        own = http.post(f"{BASE}/agents/{w.daara}/publish-requests:approve", headers=adm)
        assert own.status_code == 403 and own.json()["error"]["code"] == "APPROVAL_NOT_INDEPENDENT"
        changed = http.patch(f"{BASE}/agents/{w.daara}", headers=adm, json={"max_records": 50})
        assert changed.status_code == 200
        c = changed.json()
        assert (
            c["status"] == "DRAFT"
            and c["scope_digest"] != digest_then
            and c["validated"] is False
            and c["approval"] is None
        )
        stale = http.post(f"{BASE}/agents/{w.daara}/publish-requests:approve", headers=apr)
        assert stale.status_code == 409  # nothing waits for approval any more
        assert (
            http.patch(
                f"{BASE}/agents/{w.daara}",
                headers=adm,
                json={"max_records": 40, "expected_revision": 1},
            ).status_code
            == 409
        )  # stale draft
        assert (
            http.post(f"{BASE}/agents/{w.daara}/publish-requests", headers=adm).status_code == 409
        )  # must be validated again

        # the full sequence, then a change AFTER approval takes the approval away again
        assert http.post(f"{BASE}/agents/{w.daara}/dry-runs", headers=adm, json={}).json()["passed"]
        assert (
            http.post(f"{BASE}/agents/{w.daara}/publish-requests", headers=adm).status_code == 201
        )
        ok = http.post(f"{BASE}/agents/{w.daara}/publish-requests:approve", headers=apr)
        assert (
            ok.status_code == 200
            and ok.json()["status"] == "APPROVED"
            and ok.json()["approval"]["approved_by"] == w.approver
        )
        again = http.patch(
            f"{BASE}/agents/{w.daara}", headers=adm, json={"warning_window_days": 30}
        )
        assert again.json()["status"] == "DRAFT" and again.json()["approval"] is None
        run = http.post(
            f"{BASE}/agents/{w.daara}/runs",
            headers={**adm, "Idempotency-Key": "idem-after-change"},
            json={},
        )
        assert (
            run.status_code == 202
            and run.json()["state"] == "DENIED"
            and "SETUP_NOT_APPROVED" in run.json()["denial_codes"]
        )

    # the database repeats the rule: nobody approves their own request, and an approval is set once
    async def own_approval(c: Any) -> None:
        setup_row = await store.load_setup(c, w.daara)
        assert setup_row
        approval = await store.insert_approval_request(
            c, w.tenant, setup_id=setup_row["setup_id"], agent_id=w.daara, scope_digest=setup_row["scope_digest"],
            requested_by=w.admin, expires_at=datetime.now(UTC) + timedelta(days=1),
        )  # fmt: skip
        await store.record_approval(c, approval["approval_id"], w.admin, datetime.now(UTC))

    with pytest.raises(DBAPIError):
        w.db(own_approval)


# --------------------------------------------------------------------------------------------------------------- the whole run


def test_run_proposals_idempotency_evidence_handoff_and_kill_switches(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url, owner_url).start()
    adm, rev = w.bearer(w.admin), w.bearer(w.reviewer)
    log = Records()
    logging.getLogger().addHandler(log)
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        with TestClient(w.on(), base_url="https://id.zoiko.test") as http:
            release(http, w, w.daara)
            rights_before = rights_snapshot(w)
            run_headers = {**adm, "Idempotency-Key": KEY}

            # AT-11: one run, one proposal per finding, under a replayed request
            r1 = http.post(
                f"{BASE}/agents/{w.daara}/runs",
                headers=run_headers,
                json={"resource_selector": {"kind": "mandates", "max_records": 100}},
            )
            assert r1.status_code == 202, r1.text
            run = r1.json()
            assert run["state"] == "NEEDS_REVIEW" and run["denial_codes"] == []
            assert run["summary"]["records_read"] == 7 and run["summary"]["findings"] == 6
            assert (
                run["summary"]["proposals_created"] == 6
                and run["summary"]["privileges_changed"] == 0
            )
            assert run[
                "worker_attestation_id"
            ]  # the machine module's verified attestation, not a claim
            replay = http.post(
                f"{BASE}/agents/{w.daara}/runs",
                headers=run_headers,
                json={"resource_selector": {"kind": "mandates", "max_records": 100}},
            )
            assert replay.status_code == 202 and replay.json()["run_id"] == run["run_id"]
            assert count(w, "access_review_run") == 1 and count(w, "access_review_proposal") == 6
            clash = http.post(
                f"{BASE}/agents/{w.daara}/runs",
                headers=run_headers,
                json={"resource_selector": {"kind": "mandates", "max_records": 5}},
            )
            assert (
                clash.status_code == 409
                and clash.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
            )
            assert (
                http.post(f"{BASE}/agents/{w.daara}/runs", headers=adm, json={}).status_code == 422
            )  # no key, no run
            assert (
                http.post(
                    f"{BASE}/agents/{w.daara}/runs", headers=run_headers, json={"mode": "scheduled"}
                ).status_code
                == 422
            )
            assert (
                http.post(
                    f"{BASE}/agents/{w.daara}/runs",
                    headers={**adm, "Idempotency-Key": "idem-other-purpose"},
                    json={"purpose": "payments"},
                ).status_code
                == 422
            )

            # each rule found its mandate, and only those
            detail = http.get(f"{BASE}/agents/{w.daara}/runs/{run['run_id']}", headers=adm).json()
            by_rule = {p["rule_id"]: p for p in detail["proposals"]}
            assert sorted(p["rule_id"] for p in detail["proposals"]) == [
                "R-01",
                "R-02",
                "R-03",
                "R-04",
                "R-05",
                "R-05",
            ]
            assert (
                by_rule["R-01"]["finding_code"] == "EXPIRED_MANDATE"
                and by_rule["R-01"]["confidence"] == "DETERMINISTIC"
            )
            flagged = {p["target"] for p in detail["proposals"]}
            assert (
                f"mandate:{w.mandates['clean'][:8]}..." not in flagged
            )  # a healthy mandate is not reported
            assert f"mandate:{w.mandates['expired'][:8]}..." in flagged
            assert all(
                p["notice"].startswith("No access permissions will change")
                and p["submitted_by"]["type"] == "agent"
                for p in detail["proposals"]
            )

            # AT-09: every read was decided, on tenant, actor, purpose, resource and the policy hash
            decisions = http.get(
                f"{BASE}/agents/{w.daara}/runs/{run['run_id']}/decisions", headers=adm
            ).json()["items"]
            reads = [
                d
                for d in decisions
                if d["action"] in ("read_mandate_metadata", "read_agent_identity_status")
            ]
            assert {d["action"] for d in reads} == {
                "read_mandate_metadata",
                "read_agent_identity_status",
            }
            assert all(
                d["outcome"] == "ALLOW"
                and d["actor"] == w.daara
                and d["purpose"] == "access_posture_review"
                and d["policy_hash"] == run["policy_hash"]
                for d in reads
            )
            assert (
                sum(
                    1
                    for d in decisions
                    if d["action"] == "create_access_review_proposal"
                    and d["reason_code"] == "PROPOSAL_CREATED"
                )
                == 6
            )

            # AT-20: the evidence chain verifies, every proposal points at an envelope that exists, and a tampered chain is reported
            ev = http.get(
                f"{BASE}/agents/{w.daara}/runs/{run['run_id']}/evidence", headers=adm
            ).json()
            assert ev["verified"] is True and ev["integrity_exceptions"] == []
            kinds = [e["event_type"] for e in ev["items"]]
            assert kinds[:5] == [
                "preflight_passed",
                "credential_issued",
                "metadata_read",
                "rules_evaluated",
                "proposal_created",
            ] and kinds[-3:] == ["credential_revoked", "sponsor_notified", "run_needs_review"]
            assert all(
                e["payload_digest"].startswith("sha256:")
                and e["classification"] == "METADATA"
                and e["region"] == REGION
                for e in ev["items"]
            )
            event_ids = {e["event_id"] for e in ev["items"]}
            for p in detail["proposals"]:
                assert any(
                    ref.removeprefix("evidence:") in event_ids
                    for ref in p["evidence_refs"]
                    if ref.startswith("evidence:")
                )
            broken = [dict(e) for e in ev["items"]]
            broken[1]["payload_digest"] = "sha256:" + "f" * 64
            assert store.verify_chain(broken) != []
            del broken[2]
            assert any(p.startswith("SEQUENCE_GAP") for p in store.verify_chain(broken))

            # AT-23: pause, re-release and run again: the same findings, no second proposal
            assert (
                http.post(
                    f"{BASE}/agents/{w.daara}:pause", headers=adm, json={"reason": "ROLLBACK_DRILL"}
                ).json()["status"]
                == "PAUSED"
            )
            blocked = http.post(
                f"{BASE}/agents/{w.daara}/runs",
                headers={**adm, "Idempotency-Key": "idem-while-paused"},
                json={},
            )
            assert (
                blocked.json()["state"] == "DENIED"
                and "SETUP_NOT_APPROVED" in blocked.json()["denial_codes"]
            )  # AT-19
            assert (
                http.patch(
                    f"{BASE}/agents/{w.daara}", headers=adm, json={"max_records": 90}
                ).status_code
                == 409
            )  # paused: resume first
            assert (
                http.post(f"{BASE}/agents/{w.daara}:resume", headers=adm).json()["status"]
                == "DRAFT"
            )
            assert http.post(f"{BASE}/agents/{w.daara}/dry-runs", headers=adm, json={}).json()[
                "passed"
            ]
            assert (
                http.post(f"{BASE}/agents/{w.daara}/publish-requests", headers=adm).status_code
                == 201
            )
            assert (
                http.post(
                    f"{BASE}/agents/{w.daara}/publish-requests:approve",
                    headers=w.bearer(w.approver),
                ).status_code
                == 200
            )
            r2 = http.post(
                f"{BASE}/agents/{w.daara}/runs",
                headers={**adm, "Idempotency-Key": "idem-second-run"},
                json={},
            ).json()
            assert (
                r2["state"] == "NEEDS_REVIEW"
                and r2["summary"]["proposals_created"] == 0
                and r2["summary"]["proposals_existing"] == 6
            )
            assert count(w, "access_review_proposal") == 6

            # AT-12 / AT-10: dismissing, assigning and handing off change no right
            items = http.get(f"{BASE}/review-proposals", headers=rev).json()["items"]
            assert len(items) == 6 and all(i["status"] == "OPEN" for i in items)
            first, second, third = (
                items[0]["proposal_id"],
                items[1]["proposal_id"],
                items[2]["proposal_id"],
            )
            assert (
                http.post(
                    f"{BASE}/review-proposals/{first}:dismiss", headers=w.bearer(w.nobody)
                ).status_code
                == 403
            )  # no review role, no review
            dismissed = http.post(f"{BASE}/review-proposals/{first}:dismiss", headers=rev)
            assert dismissed.status_code == 200 and dismissed.json()["status"] == "DISMISSED"
            assert (
                http.post(f"{BASE}/review-proposals/{first}:handoff", headers=rev).status_code
                == 409
            )  # a dismissed finding is final
            assigned = http.post(
                f"{BASE}/review-proposals/{second}:assign",
                headers=rev,
                json={"assignee": w.approver},
            )
            assert (
                assigned.json()["status"] == "ASSIGNED"
                and assigned.json()["assignee"] == w.approver
            )
            ho = http.post(f"{BASE}/review-proposals/{third}:handoff", headers=rev)
            assert ho.status_code == 200, ho.text
            h = ho.json()
            assert (
                h["status"] == "HANDED_OFF"
                and h["handoff"]["executes"] is False
                and h["handoff"]["target_workflow"] == "governed_access_workflow"
            )
            assert (
                h["handoff"]["handoff_digest"].startswith("sha256:") and h["handoff"]["signature"]
            )
            assert (
                http.post(f"{BASE}/review-proposals/{third}:handoff", headers=rev).json()[
                    "handoff"
                ]["handoff_id"]
                == h["handoff"]["handoff_id"]
            )
            assert rights_snapshot(w) == rights_before

            # the database refuses to make a proposal say something else, or an `executes` handoff
            with pytest.raises(DBAPIError):
                w.owner(
                    "UPDATE agent.access_review_proposal SET finding_code = 'X' WHERE tenant_id = :t"
                )
            with pytest.raises(DBAPIError):
                w.owner(
                    "UPDATE agent.access_review_proposal SET status = 'OPEN' WHERE proposal_id = :p",
                    p=uuid.UUID(first),
                )
            with pytest.raises(DBAPIError):
                w.owner(
                    "UPDATE agent.access_review_handoff SET executes = true WHERE tenant_id = :t"
                )

            # AT-19: suspending the agent in the machine registry stops new runs at the preflight
            w.owner(
                "UPDATE agent.agent_profile SET status = 'SUSPENDED' WHERE agent_principal_id = :a",
                a=uuid.UUID(w.daara),
            )
            sus = http.post(
                f"{BASE}/agents/{w.daara}/runs",
                headers={**adm, "Idempotency-Key": "idem-suspended"},
                json={},
            ).json()
            assert sus["state"] == "DENIED" and "AGENT_NOT_ACTIVE" in sus["denial_codes"]
            assert count(w, "access_review_proposal") == 6
    finally:
        logging.getLogger().removeHandler(log)

    # AT-21: no token, document, biometric or raw personal field in a log line, an evidence row, a decision or an audit record
    token = w.bearer(w.admin)["Authorization"].split()[1]
    haystack = "\n".join(log.lines)
    for table in (
        "access_review_evidence",
        "access_review_policy_decision",
        "access_review_run",
        "access_review_proposal",
        "access_review_handoff",
    ):
        haystack += "\n".join(
            str(row) for row in w.owner(f"SELECT * FROM agent.{table} WHERE tenant_id = :t")
        )
    haystack += "\n".join(
        str(row)
        for row in w.owner(
            f"SELECT body FROM evidence.audit_record WHERE tenant_id = :t AND {AUDIT}"
        )
    )
    assert token not in haystack and "Bearer" not in haystack and "Authorization" not in haystack
    assert "Acme Ltd" not in haystack and "@" not in haystack
    audits = {
        r[0]
        for r in w.owner(
            f"SELECT event_type FROM evidence.audit_record WHERE tenant_id = :t AND {AUDIT}"
        )
    }
    assert {
        "access_readiness.setup.created",
        "access_readiness.publish.approved",
        "agent.run.completed",
        "agent.review.proposal_created",
        "access_readiness.handoff.signed",
    } <= audits
    assert (
        w.owner(
            "SELECT count(*) FROM events.outbox_event WHERE tenant_id = :t AND event_type = 'com.zoiko.id.agent.review.proposal_created.v1'"
        )[0][0]
        == 6
    )


# --------------------------------------------------------------------------------------------------------------- preflight and failure cases


def make_run(
    w: World, key: str, selector: dict[str, Any] | None = None, **rt: Any
) -> dict[str, Any]:
    async def go(c: Any) -> dict[str, Any]:
        run, _ = await runs.start_run(
            c, runtime(**rt), w.tenant, actor=w.admin, agent_id=w.daara, idempotency_key=key,
            purpose="access_posture_review", selector=selector if selector is not None else {"kind": "mandates"}, mode="manual", trace_id="trace-1",
        )  # fmt: skip
        return run  # type: ignore[no-any-return]

    return w.db(go)  # type: ignore[no-any-return]


def execute(w: World, run_id: str, **kw: Any) -> dict[str, Any]:
    rt = kw.pop("rt", runtime())

    async def go(c: Any) -> dict[str, Any]:
        return await runs.execute_run(c, rt, w.tenant, run_id, **kw)

    return w.db(go)  # type: ignore[no-any-return]


def approved_world(rt_url: str, owner_url: str) -> World:
    w = World(rt_url, owner_url).start()
    with TestClient(w.on(), base_url="https://id.zoiko.test") as http:
        release(http, w, w.daara)
    return w


def test_selector_region_policy_adapter_and_attestation_failures_fail_closed(
    rt_url: str, owner_url: str
) -> None:
    w = approved_world(rt_url, owner_url)

    # AT-03: another tenant's or another region's selector is a recorded denial, and reads nothing
    cross = execute(
        w, make_run(w, "idem-cross-tenant", {"kind": "mandates", "tenant_id": uid()})["run_id"]
    )
    assert (
        cross["state"] == "DENIED"
        and "SELECTOR_TENANT_MISMATCH" in cross["denial_codes"]
        and cross["summary"]["records_read"] == 0
    )
    wrong = execute(
        w, make_run(w, "idem-wrong-region", {"kind": "mandates", "region": "eu-1"})["run_id"]
    )
    assert wrong["state"] == "DENIED" and "SELECTOR_REGION_MISMATCH" in wrong["denial_codes"]
    source = execute(w, make_run(w, "idem-bad-source", {"kind": "credentials"})["run_id"])
    assert (
        source["state"] == "DENIED" and "SOURCE_NOT_APPROVED" in source["denial_codes"]
    )  # AT-04: unapproved connector
    assert count(w, "access_review_proposal") == 0

    # AT-24: a cell outside the approved regions fails closed with a truthful code
    far = execute(w, make_run(w, "idem-region")["run_id"], rt=runtime(regions=frozenset({"eu-9"})))
    assert far["state"] == "DENIED" and "REGION_UNSUPPORTED" in far["denial_codes"]
    moved = execute(
        w,
        make_run(w, "idem-moved")["run_id"],
        rt=runtime(region="eu-1", regions=frozenset({"eu-1"})),
    )
    assert moved["state"] == "DENIED" and "REGION_MISMATCH" in moved["denial_codes"]

    # AT-18: the policy service is down -> denied, no broad fallback, nothing read, nothing written
    async def down(_c: Any, _t: str, _a: str) -> bool:
        raise ConnectionError("policy service unavailable")

    outage = execute(w, make_run(w, "idem-outage")["run_id"], gate=down)
    assert (
        outage["state"] == "DENIED"
        and outage["denial_codes"] == ["DEPENDENCY_UNAVAILABLE"]
        and outage["summary"]["records_read"] == 0
    )

    async def refuse(_c: Any, _t: str, _a: str) -> bool:
        return False

    assert (
        "POLICY_DENIED"
        in execute(w, make_run(w, "idem-refuse")["run_id"], gate=refuse)["denial_codes"]
    )

    # the adapter is down mid-run: FAILED with nothing written; a retry revalidates and then succeeds (idempotent)
    class Broken:
        async def read_mandates(self, *a: Any, **k: Any) -> list[Any]:
            raise ConnectionError("projection unavailable")

    failed = execute(w, make_run(w, "idem-adapter")["run_id"], adapter=Broken())
    assert failed["state"] == "FAILED" and failed["denial_codes"] == ["DEPENDENCY_UNAVAILABLE"]
    assert count(w, "access_review_proposal") == 0

    async def retry(c: Any) -> dict[str, Any]:
        return await runs.retry_run(c, runtime(), w.tenant, failed["run_id"])

    retried = w.db(retry)
    assert (
        retried["state"] == "NEEDS_REVIEW"
        and retried["attempt"] == 2
        and retried["summary"]["proposals_created"] == 6
    )
    with pytest.raises(ReadinessError) as e:
        w.db(retry)
    assert e.value.code == "RUN_NOT_RETRYABLE"  # a finished run is final

    # retry after the agent was stopped is a denial, not a blind replay
    again = execute(w, make_run(w, "idem-adapter-2")["run_id"], adapter=Broken())
    assert again["state"] == "FAILED"
    w.owner(
        "UPDATE agent.agent_profile SET status = 'REVOKED' WHERE agent_principal_id = :a",
        a=uuid.UUID(w.daara),
    )

    async def retry_again(c: Any) -> dict[str, Any]:
        return await runs.retry_run(c, runtime(), w.tenant, again["run_id"])

    denied = w.db(retry_again)
    assert denied["state"] == "DENIED" and "AGENT_NOT_ACTIVE" in denied["denial_codes"]

    # every outcome above is a recorded decision, and a denial writes evidence of its own
    assert (
        w.owner(
            "SELECT count(*) FROM agent.access_review_policy_decision WHERE tenant_id = :t AND outcome = 'DENY'"
        )[0][0]
        >= 8
    )
    assert (
        w.owner(
            "SELECT count(*) FROM agent.access_review_evidence WHERE tenant_id = :t AND event_type = 'preflight_denied'"
        )[0][0]
        >= 6
    )


def test_unattested_expired_unsponsored_and_unapproved_blueprint_never_run(
    rt_url: str, owner_url: str
) -> None:
    w = approved_world(rt_url, owner_url)
    agent = uuid.UUID(w.daara)

    def denial(key: str) -> list[str]:
        out = execute(w, make_run(w, key)["run_id"])
        assert out["state"] == "DENIED" and out["summary"]["records_read"] == 0
        return list(out["denial_codes"])

    # AT-07: no valid attestation for the workload, no run
    w.owner(
        "UPDATE agent.workload_attestation SET valid_until = now() - interval '1 minute' WHERE tenant_id = :t"
    )
    assert "AGENT_NOT_ATTESTED" in denial("idem-unattested")
    w.owner(
        "UPDATE agent.workload_attestation SET valid_until = now() + interval '1 day', status = 'VALID' WHERE tenant_id = :t"
    )
    w.owner(
        "UPDATE agent.workload_binding SET attestation_required = 3 WHERE tenant_id = :t"
    )  # the attestation is below the level the binding needs
    assert "AGENT_NOT_ATTESTED" in denial("idem-low-level")
    w.owner("UPDATE agent.workload_binding SET attestation_required = 1 WHERE tenant_id = :t")
    w.owner("UPDATE agent.workload_binding SET status = 'SUSPENDED' WHERE tenant_id = :t")
    assert "AGENT_NOT_ATTESTED" in denial("idem-no-binding")
    w.owner("UPDATE agent.workload_binding SET status = 'ACTIVE' WHERE tenant_id = :t")

    # AT-08: an agent past its expiry, and a revoked blueprint, stop further work
    w.owner(
        "UPDATE agent.agent_profile SET expires_at = now() - interval '1 hour' WHERE agent_principal_id = :a",
        a=agent,
    )
    assert "AGENT_EXPIRED" in denial("idem-expired")
    w.owner(
        "UPDATE agent.agent_profile SET expires_at = NULL WHERE agent_principal_id = :a", a=agent
    )
    w.owner(
        "UPDATE agent.blueprint_release SET review_status = 'REVOKED' WHERE tenant_id = :t AND blueprint_id = 'daara'"
    )
    assert "BLUEPRINT_NOT_APPROVED" in denial("idem-blueprint")
    w.owner(
        "UPDATE agent.blueprint_release SET review_status = 'APPROVED' WHERE tenant_id = :t AND blueprint_id = 'daara'"
    )

    # the accountable sponsor leaves: no run (and the registry's own sweep would suspend the agent)
    async def disable(c: Any) -> None:
        from zid_platform.modules.principal.domain.principal import PrincipalStatus
        from zid_platform.modules.principal.infrastructure.principal_store import (
            set_status as set_principal_status,
        )

        await set_principal_status(c, w.sponsor, PrincipalStatus.DISABLED)

    w.db(disable)
    assert "SPONSOR_NOT_ACTIVE" in denial("idem-sponsor")

    # AT-05 at run time: a tool added to the agent after approval is a new digest, so the approval no longer applies
    w.owner(
        "UPDATE agent.agent_profile SET allowed_tools = ARRAY['read_mandate_metadata'] WHERE agent_principal_id = :a",
        a=agent,
    )
    codes = denial("idem-tools")
    assert "APPROVAL_DIGEST_MISMATCH" in codes
    w.owner(
        "UPDATE agent.agent_profile SET allowed_tools = ARRAY['read_mandate_metadata','grant_role'] WHERE agent_principal_id = :a",
        a=agent,
    )
    assert "TOOL_NOT_ALLOWED" in denial("idem-bad-tool")


def test_tools_queue_kill_switch_isolation_and_append_only_records(
    rt_url: str, owner_url: str
) -> None:
    w = approved_world(rt_url, owner_url)

    # AT-04: any tool name outside the allowlist is denied, and the denial is recorded
    run = make_run(w, "idem-tools-1")

    async def ask(c: Any) -> list[str]:
        out = []
        for tool in ("grant_role", "revoke_credential", "update_mandate", "run_sql"):
            try:
                await runs.invoke_tool(c, w.tenant, run["run_id"], tool, "mandates:page")
            except ReadinessError as e:
                out.append(e.code)
        await runs.invoke_tool(
            c, w.tenant, run["run_id"], "read_mandate_metadata", "mandates:page"
        )  # an allowlisted read is permitted
        return out

    assert w.db(ask) == ["TOOL_NOT_ALLOWED"] * 4
    rows = w.owner(
        "SELECT action, outcome FROM agent.access_review_policy_decision WHERE run_id = :r ORDER BY decided_at",
        r=uuid.UUID(run["run_id"]),
    )
    assert [r[1] for r in rows] == ["DENY"] * 4 + ["ALLOW"]

    # AT-19: queued work is discarded when the agent is paused, never run later under withdrawn authority
    queued = [make_run(w, f"idem-queued-{n}") for n in range(3)]
    assert all(q["state"] == "QUEUED" for q in queued)

    async def pause(c: Any) -> dict[str, Any]:
        return await setup.pause_setup(
            c, w.tenant, actor=w.admin, agent_id=w.daara, reason="SECURITY_PAUSE"
        )

    assert w.db(pause)["status"] == "PAUSED"
    states = {
        r[0]: r[1]
        for r in w.owner(
            "SELECT run_id::text, state FROM agent.access_review_run WHERE tenant_id = :t"
        )
    }
    assert all(states[q["run_id"]] == "CANCELED" for q in queued)
    assert (
        w.owner(
            "SELECT count(*) FROM agent.access_review_approval WHERE tenant_id = :t AND invalidated_at IS NULL"
        )[0][0]
        == 0
    )

    # the worker's pass over queued runs: each is preflighted again, so one queued before the pause is denied now
    w.owner(
        "UPDATE agent.access_review_setup SET status = 'DRAFT' WHERE tenant_id = :t"
    )  # simulate a run queued while approved
    later = make_run(w, "idem-late")

    async def drain(c: Any) -> list[dict[str, Any]]:
        return await runs.execute_queued(c, runtime(), w.tenant)

    done = w.db(drain)
    assert [d["run_id"] for d in done] == [later["run_id"]] and done[0]["state"] == "DENIED"

    # tenant isolation: another tenant sees none of this, and gets the same 404 whether the run exists or not
    other = World(rt_url, owner_url).start()
    with TestClient(other.on(), base_url="https://id.zoiko.test") as http:
        h = other.bearer(other.admin)
        assert (
            http.get(f"{BASE}/agents/{w.daara}/runs/{later['run_id']}", headers=h).status_code
            == 404
        )
        assert http.get(f"{BASE}/agents/{w.daara}/runs/{uid()}", headers=h).status_code == 404
        assert http.get(f"{BASE}/agents/{w.daara}", headers=h).status_code == 404
        assert http.get(f"{BASE}/review-proposals", headers=h).json()["items"] == []

    # decisions, evidence and handoffs are append-only for everyone, the table owner included
    for sql in (
        "UPDATE agent.access_review_policy_decision SET outcome = 'ALLOW' WHERE tenant_id = :t",
        "DELETE FROM agent.access_review_policy_decision WHERE tenant_id = :t",
        "UPDATE agent.access_review_evidence SET payload_digest = 'sha256:' || repeat('0', 64) WHERE tenant_id = :t",
        "DELETE FROM agent.access_review_evidence WHERE tenant_id = :t",
        "UPDATE agent.access_review_run SET state = 'COMPLETED' WHERE tenant_id = :t AND state = 'DENIED'",
        "DELETE FROM agent.access_review_run WHERE tenant_id = :t",
        "DELETE FROM agent.access_review_setup WHERE tenant_id = :t",
        "DELETE FROM agent.access_review_approval WHERE tenant_id = :t",
    ):
        with pytest.raises(DBAPIError):
            w.owner(sql)


def test_agent_registry_status_is_not_changed_by_a_run(rt_url: str, owner_url: str) -> None:
    """AT-10: a run reads the registry and the mandates and leaves every right where it was (the agent included)."""
    w = approved_world(rt_url, owner_url)
    before = rights_snapshot(w)
    out = execute(w, make_run(w, "idem-readonly")["run_id"])
    assert out["state"] == "NEEDS_REVIEW"
    assert rights_snapshot(w) == before

    async def status(c: Any) -> str | None:
        from zid_platform.modules.machine.ports import agent_status

        return await agent_status(c, w.daara)

    assert w.db(status) == AgentStatus.ACTIVE.value


def test_replay_of_the_same_stored_metadata_gives_the_same_digest(
    rt_url: str, owner_url: str
) -> None:
    """AT-13 end to end: two runs over unchanged data and the same trusted clock produce the same result digest; a different clock does not."""
    w = approved_world(rt_url, owner_url)
    at = datetime.now(UTC)
    a = execute(w, make_run(w, "idem-replay-a")["run_id"], now=at)
    b = execute(w, make_run(w, "idem-replay-b")["run_id"], now=at)
    c = execute(w, make_run(w, "idem-replay-c")["run_id"], now=at + timedelta(days=5))
    assert a["summary"]["result_digest"] == b["summary"]["result_digest"] != ""
    assert c["summary"]["result_digest"] != a["summary"]["result_digest"]


def test_an_untrusted_clock_or_unreadable_state_is_incomplete_never_healthy(
    rt_url: str, owner_url: str
) -> None:
    """AT-14 end to end: a clock the engine cannot trust blocks the judgement; the run says so instead of reporting a healthy tenant."""
    w = approved_world(rt_url, owner_url)
    out = execute(w, make_run(w, "idem-clock")["run_id"], now=datetime(2026, 11, 13))
    assert out["state"] == "NEEDS_REVIEW"
    assert (
        out["summary"]["complete"] is False
        and out["summary"]["unknown"] == 7
        and out["summary"]["proposals_created"] == 0
    )
    assert out["state"] != "COMPLETED"


def test_an_emergency_stop_of_agent_governance_stops_the_readiness_agent_too(
    rt_url: str, owner_url: str
) -> None:
    """The company-wide and per-agent stop of ZID-AG-WF-002 binds every agent, this one included: a stopped agent reads nothing."""
    from zid_platform.modules.machine.application import governance

    w = World(rt_url, owner_url).start()
    adm = w.bearer(w.admin)
    with TestClient(w.on(), base_url="https://id.zoiko.test") as http:
        release(http, w, w.daara)
        ok = http.post(
            f"{BASE}/agents/{w.daara}/runs",
            headers={**adm, "Idempotency-Key": "freeze-ok-01"},
            json={},
        )
        assert ok.status_code == 202 and ok.json()["state"] == "NEEDS_REVIEW", ok.text
        assert count(w, "access_review_proposal") > 0

        # a company-wide stop, with no approval asked for (it is the kill switch)
        w.db(
            lambda c: governance.pause(
                c, w.tenant, w.admin, scope="TENANT", agent_id=None, reason="suspected misuse"
            )
        )
        before = count(w, "access_review_proposal")
        stopped = http.post(
            f"{BASE}/agents/{w.daara}/runs",
            headers={**adm, "Idempotency-Key": "freeze-no-01"},
            json={},
        )
        assert stopped.status_code == 202, stopped.text
        out = stopped.json()
        assert out["state"] == "DENIED" and "EMERGENCY_FROZEN" in out["denial_codes"], out
        assert (
            count(w, "access_review_proposal") == before
        )  # nothing was read and nothing was proposed
        assert out["summary"].get("records_read", 0) == 0

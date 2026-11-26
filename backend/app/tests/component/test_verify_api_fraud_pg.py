"""The fraud graph (CR-168; ZID-CP-WF05), against the real database and the real HTTP routes.

What these hold: observations are idempotent and carry no customer reference; links are typed and sourced; a lead is made by a deterministic rule and is not a finding; an excluded link leaves every current analysis and a
restored one comes back; an erasure removes the graph's copy; a source that is not enabled never makes a link; a finding needs a final review case and someone who took no part in it; an action proposal needs a confirmed
finding, exact targets and a different approver, and changes no session; one company never sees another's graph; the audit trail records who looked and cannot be edited.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from test_verify_api_go_live_pg import code
from test_verify_api_review_pg import Crew, owner_sql
from zid_platform.modules.verify_api.application import fraud_service as fs
from zid_platform.modules.verify_api.domain import cases as case_domain
from zid_platform.modules.verify_api.infrastructure import case_store

CHECKLIST = {"evidence_reviewed": True, "reason_matches_evidence": True, "no_conflict_of_interest": True}


class Graph(Crew):
    """A company whose three administrators hold every fraud duty, so the tests can name who does what."""

    def __init__(self, rt_url: str) -> None:
        super().__init__(rt_url, fraud=True)
        self.server = self.sandbox_server()

    def session(self, ref: str, fixture: str = "declined") -> str:
        made = self.http.post(
            f"/v1/verify-api/sandbox/fixtures/{fixture}/sessions",
            headers={**self.server, "Idempotency-Key": f"k-{ref}-{fixture}-{datetime.now(UTC).timestamp()}-{len(ref)}"},
            json={"customer_reference": ref},
        )
        assert made.status_code == 201, made.text
        return str(made.json()["session"]["session_id"])

    def refused(self, ref: str, n: int = 3) -> list[str]:
        return [self.session(ref) for _ in range(n)]

    def refresh(self, who: dict[str, str] | None = None) -> dict[str, Any]:
        r = self.http.post("/v1/verify-api/fraud/graph:refresh", headers=who or self.a)
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    def leads(self, **q: str) -> list[dict[str, Any]]:
        return self.http.get("/v1/verify-api/fraud/leads", headers=self.a, params=q).json()["items"]  # type: ignore[no-any-return]

    def lead(self, lead_id: str, who: dict[str, str] | None = None) -> dict[str, Any]:
        r = self.http.get(f"/v1/verify-api/fraud/leads/{lead_id}", headers=who or self.a)
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    def fx(self, method: str, path: str, who: dict[str, str] | None = None, **body: Any) -> Any:
        return self.http.request(method, f"/v1/verify-api/fraud/{path}", headers=who or self.a, json=body or None)

    def finalize_case(self, session: str) -> str:
        """Reviewer a proposes verified and reviewer b gives the independent second review."""
        case, proposed = self.ready_to_second(session)
        done = self.post(
            self.b, case, "second-review", expected_case_version=proposed["case_version"], decision="approve", challenge_answers=CHECKLIST
        )
        assert done.status_code == 200, done.text
        assert self.get(case)["status"] == "APPROVED_FINAL"
        return case


def test_a_refresh_is_idempotent_makes_typed_sourced_links_and_a_lead_that_is_not_a_finding(rt_url: str, owner_url: str) -> None:
    co = Graph(rt_url)
    try:
        ids = co.refused("secret-ref-A", 3)
        ok = [co.session("secret-ref-B", "approved") for _ in range(2)]  # a repeat of good results is ordinary
        one = co.session("secret-ref-C")
        first = co.refresh()
        assert first["new_observations"] == 6 and first["groups"] == 2
        again = co.refresh()
        assert again["new_observations"] == 0
        assert len(co.leads()) == 1  # the same lead, not a second
        lead = co.leads()[0]
        assert lead["rule_id"] == "REPEAT_AFTER_REFUSAL" and lead["risk_class"] == "MODERATE" and lead["status"] == "OPEN"
        assert lead["finding"] == "UNREVIEWED" and lead["sessions"] == 3 and lead["uncertain_facts"]
        detail = co.lead(lead["lead_id"])
        assert detail["snapshot_current"] is True and len(detail["edges"]) == 2
        for e in detail["edges"]:
            assert e["type"] == "EXACT_REFERENCE" and e["strength"] == "corroborating" and e["evidence_ref"] and e["fraud_finding"] is False
            assert e["rule_version"] and e["method"]
        # the graph around any of the three shows the same picture, masked, and never the reference
        g = co.http.get(f"/v1/verify-api/fraud/graphs/{ids[1]}", headers=co.a).json()
        assert {n["id"] for n in g["nodes"]} == set(ids) and g["masked"] is True and g["has_more"] is False and g["tenant_scope"] == "derived_from_principal"
        assert all(e["fraud_finding"] is False for e in g["edges"]) and g["graph_snapshot_id"].startswith("sha256:")
        # the sessions of ordinary repeats and the single one made no lead and no link
        assert not ({ok[0], ok[1], one} & {n for e in detail["edges"] for n in e["nodes"]})
        everything = json.dumps([detail, g, co.leads()])
        assert "secret-ref" not in everything
        stored = owner_sql(owner_url, "SELECT token, value FROM verify.api_fraud_observation")
        assert stored and all("secret-ref" not in str(r) for r in stored)
    finally:
        co.close()


def test_a_link_can_be_disputed_with_a_reason_and_leaves_every_current_analysis_and_comes_back_on_restore(rt_url: str, owner_url: str) -> None:
    co = Graph(rt_url)
    try:
        ids = co.refused("dispute-ref", 3)
        co.refresh()
        lead = co.leads()[0]
        d = co.lead(lead["lead_id"])
        target = d["edges"][0]
        # a stale view changes nothing; a missing reason is refused
        stale = co.fx("POST", f"edges/{target['edge_id']}:challenge", action="exclude", reason="Shared household.", expected_version=target["edge_version"] + 5)
        assert stale.status_code == 409 and code(stale) == "GRAPH_SNAPSHOT_STALE"
        assert code(co.fx("POST", f"edges/{target['edge_id']}:challenge", action="exclude", reason=" ", expected_version=1)) == "NOTE_REQUIRED"
        done = co.fx("POST", f"edges/{target['edge_id']}:challenge", action="exclude", reason="Shared household.", expected_version=target["edge_version"])
        assert done.status_code == 200 and done.json()["status"] == "EXCLUDED"
        # it is out of the current analysis at once, and the lead says its snapshot is no longer current
        g = co.http.get(f"/v1/verify-api/fraud/graphs/{ids[0]}", headers=co.a).json()
        assert target["edge_id"] not in {e["edge_id"] for e in g["edges"]}
        assert co.lead(lead["lead_id"])["snapshot_current"] is False
        # a refresh never re-activates what a person excluded, and the lead (now too small) expires
        co.refresh()
        assert co.fx("GET", f"edges/{target['edge_id']}").json()["status"] == "EXCLUDED"
        assert co.lead(lead["lead_id"])["status"] == "EXPIRED"
        # the history is kept, with the reason, and restoring brings the lead back
        edge = co.fx("GET", f"edges/{target['edge_id']}").json()
        restored = co.fx("POST", f"edges/{target['edge_id']}:challenge", action="restore", reason="Checked: not a household.", expected_version=edge["edge_version"])
        assert restored.status_code == 200 and restored.json()["status"] == "ACTIVE"
        co.refresh()
        assert co.lead(lead["lead_id"])["status"] == "OPEN"
        trail = co.fx("GET", f"audit/edge:{target['edge_id']}").json()
        assert trail["chain_intact"] and [e["type"] for e in trail["events"] if e["type"] != "edge_viewed"] == ["edge_excluded", "edge_restored"]
        # an exculpatory fact needs its source; a note on a link outside the lead is refused
        assert code(co.fx("POST", f"leads/{lead['lead_id']}/notes", kind="EXCULPATORY", body="They share a flat.")) == "NOTE_REQUIRED"
        ok = co.fx("POST", f"leads/{lead['lead_id']}/notes", kind="EXCULPATORY", body="They share a flat.", source_ref="tenancy letter 14 Nov")
        assert ok.status_code == 201 and ok.json()["notes"][0]["kind"] == "EXCULPATORY"
        assert code(co.fx("POST", f"leads/{lead['lead_id']}/notes", kind="NOTE", body="Hmm.", edge_id="edg_" + "0" * 24)) == "VALIDATION_FAILED"
    finally:
        co.close()


def test_an_erasure_removes_the_graphs_copy_and_a_source_that_is_not_enabled_makes_no_link(rt_url: str, owner_url: str) -> None:
    co = Graph(rt_url)
    try:
        ids = co.refused("erase-me", 3)
        co.refresh()
        lead = co.leads()[0]
        assert co.http.get(f"/v1/verify-api/fraud/graphs/{ids[0]}", headers=co.a).json()["edges"]
        tenant = str(co.o.tenant)
        h = case_domain.reference_hash(tenant, "erase-me")

        async def erase() -> dict[str, int]:
            async with co.tx() as c:
                await case_store.erase_reference(c, tenant, h)
                return await fs.purge_reference(c, tenant, h)

        gone = asyncio.run(erase())
        assert gone["observations"] == 3 and gone["edges"] == 2 and gone["leads_expired"] >= 1
        assert owner_sql(owner_url, "SELECT count(*) FROM verify.api_fraud_observation WHERE token IS NOT NULL AND tenant_id = :t", t=co.o.tenant)[0][0] == 0
        assert owner_sql(owner_url, "SELECT count(*) FROM verify.api_fraud_edge WHERE token_group IS NOT NULL AND status <> 'RETRACTED' AND tenant_id = :t", t=co.o.tenant)[0][0] == 0
        g = co.http.get(f"/v1/verify-api/fraud/graphs/{ids[0]}", headers=co.a).json()
        assert g["edges"] == [] and g["source_gap"] is True  # missing data is shown as missing
        assert co.lead(lead["lead_id"])["status"] == "EXPIRED"
        assert co.refresh()["new_observations"] == 0  # an erased reference makes no new observation
        assert co.fx("GET", f"edges/{owner_sql(owner_url, 'SELECT edge_id FROM verify.api_fraud_edge WHERE tenant_id = :t LIMIT 1', t=co.o.tenant)[0][0]}").json()["status"] == "RETRACTED"

        async def quarantine() -> str:
            async with co.tx() as c:
                return await fs.record_observation(
                    c, tenant, source="ip_network", kind="CUSTOMER_REFERENCE", session_id=ids[0], source_event_id="ip-1", token="a" * 64,
                    observed_at=datetime.now(UTC), environment="SANDBOX", region="UK", purpose="fraud_investigation", now=datetime.now(UTC),
                )  # fmt: skip

        assert asyncio.run(quarantine()) != "known"
        rows = owner_sql(owner_url, "SELECT status, quarantine_reason, token FROM verify.api_fraud_observation WHERE source = 'ip_network' AND tenant_id = :t", t=co.o.tenant)
        assert rows == [("QUARANTINED", "SOURCE_NOT_ENABLED", None)]  # kept as a record, never a token, never a link
        reg = co.fx("GET", "sources").json()
        assert {s["source"] for s in reg["sources"] if s["enabled"]} == {"verify_sessions"}
        assert {g["feature"] for g in reg["gates"] if g["state"] == "OFF"} == {"biometric_1n", "face_blocklist", "cross_tenant"}
        assert any(t["type"] == "IP_SHARED" and t["strength"] == "weak" and not t["active"] for t in reg["edge_types"])
        # a read beyond the limits is refused, not trimmed quietly
        assert code(co.http.get(f"/v1/verify-api/fraud/graphs/{ids[0]}", headers=co.a, params={"depth": 4})) == "QUERY_LIMIT_EXCEEDED"
        assert code(co.http.get(f"/v1/verify-api/fraud/graphs/{ids[0]}", headers=co.a, params={"limit": 500})) == "QUERY_LIMIT_EXCEEDED"
    finally:
        co.close()


def test_one_company_never_sees_another_and_the_company_comes_from_the_person_not_the_request(rt_url: str, owner_url: str) -> None:
    mine, other = Graph(rt_url), Graph(rt_url)
    try:
        ids = mine.refused("shared-looking-ref", 3)
        other.refused("shared-looking-ref", 3)  # the same reference text in another company is another token and another graph
        mine.refresh()
        other.refresh()
        lead_id = mine.leads()[0]["lead_id"]
        theirs = other.leads()[0]["lead_id"]
        assert lead_id != theirs
        # the other company gets the same answer for a thing that exists elsewhere as for a thing that does not exist
        assert other.fx("GET", f"leads/{lead_id}").status_code == 404 and code(other.fx("GET", f"leads/{lead_id}")) == "GRAPH_ACCESS_DENIED"
        assert other.fx("GET", f"graphs/{ids[0]}").status_code == 404 and code(other.fx("GET", f"graphs/{ids[0]}")) == "GRAPH_ACCESS_DENIED"
        assert other.fx("GET", f"edges/{mine.lead(lead_id)['edges'][0]['edge_id']}").status_code == 404
        assert other.fx("GET", f"audit/{lead_id}").json()["events"] == []  # not even the other company's trail
        assert other.fx("POST", f"leads/{lead_id}:triage", status="CLEARED", note="Not mine.", expected_version=1).status_code == 404
        # a tenant named in a request is not accepted
        bad = mine.fx("POST", f"leads/{lead_id}:triage", status="IN_ANALYSIS", tenant_id=str(other.o.tenant))
        assert bad.status_code == 422
        assert mine.http.get("/v1/verify-api/fraud/leads").status_code == 401  # nobody signed in
        assert owner_sql(owner_url, "SELECT count(DISTINCT tenant_id) FROM verify.api_fraud_edge WHERE tenant_id IN (:a, :b)", a=mine.o.tenant, b=other.o.tenant)[0][0] == 2
        assert other.fx("GET", "overview").json()["leads"]["raised"] == 1  # only its own
    finally:
        mine.close()
        other.close()


def test_a_lead_goes_to_the_review_queue_once_and_a_finding_needs_the_final_case_and_someone_who_took_no_part(rt_url: str, owner_url: str) -> None:
    co = Graph(rt_url)
    try:
        review = co.session("loop-ref", "review")  # waiting for a person; the cell queued its case
        co.refused("loop-ref", 2)
        co.refresh()
        lead = next(x for x in co.leads() if x["sessions"] == 3)
        assert code(co.fx("POST", f"leads/{lead['lead_id']}:finding", finding="CONFIRMED", note="I looked.", expected_version=lead["lead_version"])) == "LEAD_STATE_INVALID"
        # an escalation with a stale version changes nothing; the right one hands the lead to the existing case, and a retry is the same case
        assert code(co.fx("POST", f"leads/{lead['lead_id']}:escalate", expected_version=lead["lead_version"] + 3)) == "GRAPH_SNAPSHOT_STALE"
        first = co.fx("POST", f"leads/{lead['lead_id']}:escalate", expected_version=lead["lead_version"])
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "ESCALATED"
        again = co.fx("POST", f"leads/{lead['lead_id']}:escalate", expected_version=lead["lead_version"])
        assert again.status_code == 200, again.text
        assert again.json()["review_case_id"] == first.json()["review_case_id"]
        cases = [c for c in co.http.get("/v1/verify-api/review-cases", headers=co.a).json()["items"] if c["session_id"] == review]
        assert len(cases) == 1 and cases[0]["case_id"] == first.json()["review_case_id"] and cases[0]["status"] != "APPROVED_FINAL"
        evidence = co.get(cases[0]["case_id"])["evidence"]
        item = next(e for e in evidence if e["kind"] == "fraud_lead")
        assert item["summary"]["is_a_finding"] is False and item["classification"] == "STANDARD"
        # the lead alone decides nothing about the session
        assert owner_sql(owner_url, "SELECT status FROM verify.api_session WHERE session_id = :s", s=review)[0][0] == "manual_review"
        # no finding until the case is final
        late = co.fx("POST", f"leads/{lead['lead_id']}:finding", who=None, finding="CONFIRMED", note="Seems right.", expected_version=co.lead(lead["lead_id"])["lead_version"])
        assert late.status_code == 409 and code(late) == "CASE_NOT_FINAL"
        # a proposal needs a confirmed finding, so there is none yet
        none = co.fx(
            "POST", "actions:propose", who=co.c, lead_id=lead["lead_id"], proposal_type="temporary_hold", target_ids=[review], basis_ref="Risk policy v3",
            impact_assessment_ref="Impact 19", expires_at=(datetime.now(UTC) + timedelta(days=3)).isoformat(),
        )  # fmt: skip
        assert none.status_code in (409, 422) and code(none) in {"NO_CONFIRMED_FINDING", "VALIDATION_FAILED"}
        co.finalize_case(review)
        version = co.lead(lead["lead_id"])["lead_version"]
        for taker in (co.a, co.b):  # both took part in the case
            r = co.fx("POST", f"leads/{lead['lead_id']}:finding", who=taker, finding="CONFIRMED", note="I looked.", expected_version=version)
            assert r.status_code == 403 and code(r) == "REVIEWER_NOT_INDEPENDENT"
        done = co.fx("POST", f"leads/{lead['lead_id']}:finding", who=co.c, finding="CONFIRMED", note="Independent look at the adjudicated case.", expected_version=version)
        assert done.status_code == 200 and done.json()["finding"] == "CONFIRMED"
        again = co.fx("POST", f"leads/{lead['lead_id']}:finding", who=co.c, finding="NO_FINDING", note="Changed my mind.", expected_version=done.json()["lead_version"])
        assert again.status_code == 409  # the earlier record stays; a correction is a new lead
        ov = co.fx("GET", "overview").json()
        assert ov["leads"]["confirmed_findings"] == 1 and ov["leads"]["no_outcome_yet"] == 0
        assert {"raised", "confirmed_findings", "no_outcome_yet"} <= set(ov["definitions"])
    finally:
        co.close()


def test_an_action_proposal_names_exact_targets_needs_a_different_approver_and_changes_no_session(rt_url: str, owner_url: str) -> None:
    co = Graph(rt_url)
    try:
        review = co.session("act-ref", "review")
        others = co.refused("act-ref", 2)
        co.refresh()
        lead = next(x for x in co.leads() if x["sessions"] == 3)
        esc = co.fx("POST", f"leads/{lead['lead_id']}:escalate", expected_version=lead["lead_version"])
        assert esc.status_code == 200, esc.text
        co.finalize_case(review)
        cur = co.lead(lead["lead_id"])
        fin = co.fx("POST", f"leads/{lead['lead_id']}:finding", who=co.c, finding="CONFIRMED", note="Independent look.", expected_version=cur["lead_version"])
        assert fin.status_code == 200, fin.text
        cur = co.lead(lead["lead_id"])
        before = owner_sql(owner_url, "SELECT session_id, status FROM verify.api_session WHERE tenant_id = :t ORDER BY session_id", t=co.o.tenant)
        soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()

        def propose(who: dict[str, str], key: str = "idem-key-0001", **over: Any) -> Any:
            body = {
                "lead_id": lead["lead_id"], "proposal_type": "temporary_hold", "target_ids": others, "basis_ref": "Risk policy v3, section 4", "impact_assessment_ref": "Impact assessment 19",
                "expires_at": soon, "expected_lead_version": cur["lead_version"], **over,
            }  # fmt: skip
            return co.http.post("/v1/verify-api/fraud/actions:propose", headers={**who, "Idempotency-Key": key}, json=body)

        # blank legal basis, a target outside the lead, no key and too long a life are each refused
        assert code(propose(co.c, basis_ref=" ")) == "LEGAL_BASIS_REQUIRED"
        assert code(propose(co.c, target_ids=[others[0], "vfs_" + "0" * 32])) == "TARGET_NOT_IN_LEAD"
        assert code(propose(co.c, key="short")) == "VALIDATION_FAILED"
        assert code(propose(co.c, expires_at=(datetime.now(UTC) + timedelta(days=90)).isoformat())) == "BAD_EXPIRY"
        made = propose(co.c)
        assert made.status_code == 201, made.text
        action = made.json()
        assert action["status"] == "PROPOSED" and action["executes_changes"] is False and action["targets"] == others
        assert action["impact"]["by_strength"]["corroborating"] == 2 and action["impact"]["reversible"] is True
        assert [r["target"] for r in action["receipt"]] == others and action["manifest_checksum"].startswith("sha256:")
        assert propose(co.c).json()["action_id"] == action["action_id"]  # a retry is the same proposal
        aid, cs = action["action_id"], action["manifest_checksum"]
        # the maker cannot approve, and an approval must name the manifest the approver looked at
        mine = co.fx("POST", f"actions/{aid}:approve", who=co.c, manifest_checksum=cs)
        assert mine.status_code == 403 and code(mine) == "ACTION_APPROVAL_REQUIRED"
        wrong = co.fx("POST", f"actions/{aid}:approve", who=co.a, manifest_checksum="sha256:" + "0" * 64)
        assert wrong.status_code == 409 and code(wrong) == "ACTION_TARGET_SET_CHANGED"
        ok = co.fx("POST", f"actions/{aid}:approve", who=co.a, manifest_checksum=cs, note="Checked each target.")
        assert ok.status_code == 200 and ok.json()["status"] == "APPROVED" and ok.json()["decided_by"] == str(co.ids["a"])
        assert {r["state"] for r in ok.json()["receipt"]} == {"APPROVED"}
        assert code(co.fx("POST", f"actions/{aid}:approve", who=co.b, manifest_checksum=cs)) == "ACTION_NOT_PROPOSED"
        # nothing about any session changed
        assert owner_sql(owner_url, "SELECT session_id, status FROM verify.api_session WHERE tenant_id = :t ORDER BY session_id", t=co.o.tenant) == before
        # an approval can be taken back; the earlier approval stays in the record
        gone = co.fx("POST", f"actions/{aid}:revoke", who=co.b, note="The company withdrew the instruction.")
        assert gone.status_code == 200 and gone.json()["status"] == "REVOKED"
        trail = co.fx("GET", f"audit/{aid}").json()
        assert trail["chain_intact"] and [e["type"] for e in trail["events"]] == ["action_proposed", "action_approved", "action_revoked"]
        # the database itself refuses the same person as maker and approver, and any edit of the trail
        with pytest.raises(Exception, match="decided_by|check"):
            owner_sql(owner_url, "UPDATE verify.api_fraud_action SET decided_by = maker WHERE action_id = :a", a=aid)
        with pytest.raises(Exception, match="append-only"):
            owner_sql(owner_url, "UPDATE verify.api_fraud_event SET actor = 'someone-else' WHERE scope_id = :s", s=aid)
    finally:
        co.close()


def test_every_look_at_the_graph_is_recorded_with_who_looked(rt_url: str, owner_url: str) -> None:
    co = Graph(rt_url)
    try:
        ids = co.refused("looked-at", 3)
        co.refresh()
        co.http.get(f"/v1/verify-api/fraud/graphs/{ids[0]}", headers=co.b)
        co.http.get(f"/v1/verify-api/fraud/graphs/{ids[0]}", headers=co.c, params={"depth": 1})
        trail = co.fx("GET", f"audit/seed:{ids[0]}").json()
        assert trail["chain_intact"] and [e["type"] for e in trail["events"]] == ["graph_viewed", "graph_viewed"]
        assert [e["actor"] for e in trail["events"]] == [str(co.ids["b"]), str(co.ids["c"])]
        assert trail["events"][1]["data"]["depth"] == 1
        # the refresh itself is on the record too
        assert co.fx("GET", f"audit/refresh:{co.o.tenant}").json()["events"][0]["type"] == "graph_refreshed"
    finally:
        co.close()

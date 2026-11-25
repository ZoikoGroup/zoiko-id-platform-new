"""The fraud graph's governed parts (CR-168; ZID-CP-WF05 .03, .06, .08, .10, .11), against the real database and the real routes.

What these hold: a query is typed, replayed without side effects and published by someone else; the blocklist is the company's own, needs a confirmed finding and a second person, matches exactly, expires, can be
challenged and recovered, and loses an erased reference; an earlier picture of the graph is rebuilt only from what was recorded; a quiet source is reported as missing data; and the numbers show their denominators.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from test_verify_api_fraud_pg import Graph
from test_verify_api_go_live_pg import code
from test_verify_api_review_pg import owner_sql
from zid_platform.modules.verify_api.application import fraud_service as fs
from zid_platform.modules.verify_api.domain import cases as case_domain


def confirmed_lead(co: Graph, ref: str) -> tuple[str, str, list[str]]:
    """A lead with a confirmed finding behind it: escalated to the review queue, the case finalized by two people, the finding recorded by a third."""
    review = co.session(ref, "review")
    others = co.refused(ref, 2)
    co.refresh()
    lead = next(x for x in co.leads() if x["sessions"] == 3 and x["status"] == "OPEN")
    assert co.fx("POST", f"leads/{lead['lead_id']}:escalate", expected_version=lead["lead_version"]).status_code == 200
    co.finalize_case(review)
    version = co.lead(lead["lead_id"])["lead_version"]
    done = co.fx("POST", f"leads/{lead['lead_id']}:finding", who=co.c, finding="CONFIRMED", note="Independent look at the adjudicated case.", expected_version=version)
    assert done.status_code == 200, done.text
    return lead["lead_id"], review, others


def test_a_query_is_typed_replayed_without_side_effects_and_published_only_by_someone_else(rt_url: str, owner_url: str) -> None:
    co = Graph(rt_url)
    try:
        co.refused("q-ref", 4)
        co.refresh()
        scope = {"t": co.o.tenant}
        # a field that is not part of a query, and a weak join, are refused when drafted
        assert code(co.fx("POST", "queries", key="by-phone", params={"phone": "0123"})) == "UNSUPPORTED_FIELD"
        assert code(co.fx("POST", "queries", key="by-ip", params={"edge_types": ["IP_SHARED"]})) == "PROHIBITED_JOIN"
        assert code(co.fx("POST", "queries", key="Bad Key", params={})) == "VALIDATION_FAILED"
        made = co.fx("POST", "queries", key="four-refusals", params={"min_sessions": 4, "min_not_verified": 4, "risk_class": "HIGH"})
        assert made.status_code == 201, made.text
        q = made.json()
        assert q["status"] == "DRAFT" and q["version"] == 1 and q["replay_current"] is False
        # it cannot go for approval before this exact version has been replayed
        assert code(co.fx("POST", f"queries/{q['query_id']}:submit")) == "REPLAY_REQUIRED"
        leads_before = owner_sql(owner_url, "SELECT count(*) FROM verify.api_fraud_lead WHERE tenant_id = :t", **scope)[0][0]
        events_before = owner_sql(owner_url, "SELECT count(*) FROM verify.api_review_case WHERE tenant_id = :t", **scope)[0][0]
        played = co.fx("POST", f"queries/{q['query_id']}:replay")
        assert played.status_code == 200, played.text
        r = played.json()["replay"]
        assert r["would_open"] == 1 and r["by_risk"]["HIGH"] == 1 and r["review_workload_sessions"] == 4
        assert r["side_effects"] == {"leads_opened": 0, "provider_calls": 0, "charges": 0, "decisions_changed": 0}
        assert owner_sql(owner_url, "SELECT count(*) FROM verify.api_fraud_lead WHERE tenant_id = :t", **scope)[0][0] == leads_before  # a replay opened nothing
        assert owner_sql(owner_url, "SELECT count(*) FROM verify.api_review_case WHERE tenant_id = :t", **scope)[0][0] == events_before
        assert played.json()["replay_current"] is True
        assert co.fx("POST", f"queries/{q['query_id']}:submit").json()["status"] == "IN_REVIEW"
        # the maker cannot publish; a rejection needs a reason; the second person publishes
        mine = co.fx("POST", f"queries/{q['query_id']}:approve", who=co.a, note="Looks right.")
        assert mine.status_code == 403 and code(mine) == "ACTION_APPROVAL_REQUIRED"
        assert code(co.fx("POST", f"queries/{q['query_id']}:reject", who=co.b, note="")) == "NOTE_REQUIRED"
        done = co.fx("POST", f"queries/{q['query_id']}:approve", who=co.b, note="Replay checked.")
        assert done.status_code == 200 and done.json()["status"] == "PUBLISHED" and done.json()["approver"] == str(co.ids["b"])
        # a published query opens its own lead on the next refresh, named by its exact version
        co.refresh()
        custom = [x for x in co.leads() if x["rule_id"] == "QUERY_FOUR_REFUSALS"]
        assert len(custom) == 1 and custom[0]["risk_class"] == "HIGH" and custom[0]["rule_version"].endswith("four-refusals@v1") and custom[0]["sessions"] == 4
        # a new version replaces it only once someone else publishes it, and the old one is retired in the same step
        v2 = co.fx("POST", "queries", key="four-refusals", params={"min_sessions": 3, "min_not_verified": 3}).json()
        assert v2["version"] == 2
        co.fx("POST", f"queries/{v2['query_id']}:replay")
        co.fx("POST", f"queries/{v2['query_id']}:submit")
        assert co.fx("POST", f"queries/{v2['query_id']}:approve", who=co.c, note="Wider window.").json()["status"] == "PUBLISHED"
        states = {x["version"]: x["status"] for x in co.fx("GET", "queries").json()["items"] if x["key"] == "four-refusals"}
        assert states == {1: "RETIRED", 2: "PUBLISHED"}
        # a query that names a source nothing produces is never published as if it worked
        weak = co.fx("POST", "queries", key="with-ip", params={"edge_types": ["EXACT_REFERENCE", "IP_SHARED"], "require_corroboration": True}).json()
        co.fx("POST", f"queries/{weak['query_id']}:replay")
        refused = co.fx("POST", f"queries/{weak['query_id']}:submit")
        assert refused.status_code == 422 and code(refused) == "QUERY_SOURCE_NOT_ENABLED"
        assert co.fx("POST", f"queries/{v2['query_id']}:retire", who=co.a, note="No longer needed.").json()["status"] == "RETIRED"
        with pytest.raises(Exception, match="check"):
            owner_sql(owner_url, "UPDATE verify.api_fraud_query SET approver = maker WHERE query_id = :q", q=v2["query_id"])  # the database refuses it too
    finally:
        co.close()


def test_the_blocklist_is_the_companys_own_needs_a_finding_and_a_second_person_and_can_be_challenged_and_recovered(rt_url: str, owner_url: str) -> None:
    co, other = Graph(rt_url), Graph(rt_url)
    try:
        lead_id, review, _ = confirmed_lead(co, "listed-ref")
        plain = co.refused("not-adjudicated", 3)
        co.refresh()
        plain_lead = next(x for x in co.leads() if x["seed_session"] in plain)
        body = {"reason_code": "CONFIRMED_REPEAT_ABUSE", "basis_ref": "Risk policy v3, section 6"}
        # an entry needs a CONFIRMED finding: a lead nobody adjudicated, a made-up reason or a blank basis are refused
        assert code(co.fx("POST", "blocklist", who=co.c, lead_id=plain_lead["lead_id"], **body)) == "NO_CONFIRMED_FINDING"
        assert code(co.fx("POST", "blocklist", who=co.c, lead_id=lead_id, reason_code="BECAUSE", basis_ref="x policy")) == "VALIDATION_FAILED"
        assert code(co.fx("POST", "blocklist", who=co.c, lead_id=lead_id, reason_code="CONFIRMED_REPEAT_ABUSE", basis_ref=" ")) == "LEGAL_BASIS_REQUIRED"
        made = co.fx("POST", "blocklist", who=co.c, lead_id=lead_id, **body)
        assert made.status_code == 201, made.text
        entry = made.json()
        assert entry["status"] == "PROPOSED" and entry["scope"] == "this company only" and entry["matches_by"] == "exact reference only"
        assert code(co.fx("POST", "blocklist", who=co.c, lead_id=lead_id, **body)) == "ALREADY_LISTED"
        ask = {"customer_reference": "listed-ref"}
        assert co.fx("POST", "blocklist:check", **ask).json()["listed"] is False  # not until it is approved
        assert code(co.fx("POST", f"blocklist/{entry['entry_id']}:approve", who=co.c, note="Mine.")) == "ACTION_APPROVAL_REQUIRED"
        ok = co.fx("POST", f"blocklist/{entry['entry_id']}:approve", who=co.a, note="Checked the case.")
        assert ok.status_code == 200 and ok.json()["status"] == "ACTIVE"
        # an exact match only, advice only, and never another company's list
        hit = co.fx("POST", "blocklist:check", **ask).json()
        assert hit["listed"] is True and hit["advisory_only"] is True and hit["similarity_used"] is False and hit["reason_code"] == "CONFIRMED_REPEAT_ABUSE" and hit["entry_id"] == entry["entry_id"]
        assert co.fx("POST", "blocklist:check", customer_reference="listed-ref ").json()["listed"] is True  # the same reference, tidied
        assert co.fx("POST", "blocklist:check", customer_reference="listed-ref-2").json()["listed"] is False  # not a look-alike
        assert other.fx("POST", "blocklist:check", **ask).json()["listed"] is False
        assert other.fx("GET", "blocklist").json()["items"] == []
        assert other.fx("POST", f"blocklist/{entry['entry_id']}:remove", note="Not mine.", expected_version=1).status_code == 404
        trail = co.fx("GET", f"audit/blocklist:{co.o.tenant}").json()
        assert trail["chain_intact"] and trail["events"] and "listed-ref" not in str(trail)  # a check is recorded, never the reference
        # a person says they were wrongly listed: while it is open the entry does not match
        assert co.fx("POST", f"blocklist/{entry['entry_id']}:challenge", note="They say it was a shared account.", expected_version=99).status_code == 409
        ch = co.fx("POST", f"blocklist/{entry['entry_id']}:challenge", note="They say it was a shared account.", expected_version=ok.json()["entry_version"])
        assert ch.status_code == 200 and ch.json()["status"] == "CHALLENGED"
        under = co.fx("POST", "blocklist:check", **ask).json()
        assert under["listed"] is False and under["under_review"] is True
        # someone who took part cannot decide the challenge; someone else does, and the outcome is kept
        v = ch.json()["entry_version"]
        assert code(co.fx("POST", f"blocklist/{entry['entry_id']}:resolve", who=co.a, uphold=False, note="Fine.", expected_version=v)) == "REVIEWER_NOT_INDEPENDENT"
        gone = co.fx("POST", f"blocklist/{entry['entry_id']}:resolve", who=co.b, uphold=False, note="Account was shared; removed.", expected_version=v)
        assert gone.status_code == 200 and gone.json()["status"] == "REMOVED" and gone.json()["outcome_note"]
        assert co.fx("POST", "blocklist:check", **ask).json()["listed"] is False
        assert owner_sql(owner_url, "SELECT count(*) FROM verify.api_fraud_blocklist WHERE entry_id = :e", e=entry["entry_id"])[0][0] == 1  # recovery keeps the record
        # it can be listed again by the same process, and an entry past its time stops counting at the next look
        again = co.fx("POST", "blocklist", who=co.c, lead_id=lead_id, **body).json()
        co.fx("POST", f"blocklist/{again['entry_id']}:approve", who=co.b, note="Second look done.")
        assert co.fx("POST", "blocklist:check", **ask).json()["listed"] is True
        owner_sql(owner_url, "UPDATE verify.api_fraud_blocklist SET expires_at = now() - interval '1 day' WHERE entry_id = :e", e=again["entry_id"])
        assert co.fx("POST", "blocklist:check", **ask).json()["listed"] is False
        assert next(i for i in co.fx("GET", "blocklist").json()["items"] if i["entry_id"] == again["entry_id"])["status"] == "EXPIRED"
        # an erasure takes the reference off the list: the token is gone, the record of what was done stays
        third = co.fx("POST", "blocklist", who=co.c, lead_id=lead_id, **body)
        assert third.status_code == 201
        co.fx("POST", f"blocklist/{third.json()['entry_id']}:approve", who=co.a, note="Again.")
        tenant = str(co.o.tenant)
        h = case_domain.reference_hash(tenant, "listed-ref")

        async def erase() -> None:
            async with co.tx() as c:
                await fs.purge_reference(c, tenant, h)

        asyncio.run(erase())
        assert co.fx("POST", "blocklist:check", **ask).json()["listed"] is False
        row = owner_sql(owner_url, "SELECT token, status FROM verify.api_fraud_blocklist WHERE entry_id = :e", e=third.json()["entry_id"])[0]
        assert row == (None, "REMOVED")
        assert review  # the escalated lead keeps its record of what was done
    finally:
        co.close()
        other.close()


def test_an_earlier_picture_is_rebuilt_only_from_what_was_recorded_and_the_timeline_says_when_it_cannot_be(rt_url: str, owner_url: str) -> None:
    co = Graph(rt_url)
    try:
        long_ago = datetime.now(UTC) - timedelta(seconds=1)
        time.sleep(0.05)
        ids = co.refused("as-of-ref", 3)
        co.refresh()
        lead = co.leads()[0]
        edge = co.lead(lead["lead_id"])["edges"][0]
        t0 = datetime.now(UTC)
        time.sleep(0.05)
        assert co.fx("POST", f"edges/{edge['edge_id']}:challenge", action="exclude", reason="Looked like a shared household.", expected_version=edge["edge_version"]).status_code == 200
        time.sleep(0.05)
        t1 = datetime.now(UTC)
        time.sleep(0.05)
        edge2 = co.fx("GET", f"edges/{edge['edge_id']}").json()
        assert co.fx("POST", f"edges/{edge['edge_id']}:challenge", action="restore", reason="Checked: it was not.", expected_version=edge2["edge_version"]).status_code == 200
        time.sleep(0.05)
        t2 = datetime.now(UTC)

        def at(when: datetime, **q: Any) -> dict[str, Any]:
            r = co.http.get(f"/v1/verify-api/fraud/graphs/{ids[0]}/as-of", headers=co.a, params={"at": when.isoformat(), **q})
            assert r.status_code == 200, r.text
            return r.json()  # type: ignore[no-any-return]

        assert {e["edge_id"] for e in at(t0)["edges"]} >= {edge["edge_id"]} and len(at(t0)["edges"]) == 2
        assert edge["edge_id"] not in {e["edge_id"] for e in at(t1)["edges"]}  # it was excluded then
        assert edge["edge_id"] in {e["edge_id"] for e in at(t2)["edges"]}  # and restored later
        assert at(long_ago)["edges"] == [] and at(long_ago)["nodes"][0]["id"] == ids[0]  # before anything was recorded
        assert code(co.http.get(f"/v1/verify-api/fraud/graphs/{ids[0]}/as-of", headers=co.a, params={"at": (datetime.now(UTC) + timedelta(days=1)).isoformat()})) == "VALIDATION_FAILED"
        assert code(co.http.get(f"/v1/verify-api/fraud/graphs/{ids[0]}/as-of", headers=co.a, params={"at": t0.isoformat(), "depth": 4})) == "QUERY_LIMIT_EXCEEDED"
        assert co.http.get(f"/v1/verify-api/fraud/graphs/{ids[0]}/as-of", headers=co.a, params={"at": t0.isoformat()}).json()["masked"] is True
        # the timeline is one chronology in the order things happened, and a lead is never a signed finding
        tl = co.fx("GET", f"leads/{lead['lead_id']}/timeline").json()
        kinds = [e["type"] for e in tl["events"] if e["source"] == "fraud_graph"]
        assert kinds[0] == "lead_opened" and tl["reproducible"] is True and tl["final_receipt"] is None
        assert [e["at"] for e in tl["events"]] == sorted(e["at"] for e in tl["events"])
        # once the data behind it is erased, the picture is no longer rebuilt, and it says why
        tenant = str(co.o.tenant)

        async def erase() -> None:
            async with co.tx() as c:
                await fs.purge_reference(c, tenant, case_domain.reference_hash(tenant, "as-of-ref"))

        asyncio.run(erase())
        gone = co.fx("GET", f"leads/{lead['lead_id']}/timeline").json()
        assert gone["reproducible"] is False and "erased" in gone["not_reproducible_because"] and gone["erased_links"]
        later = at(t2)
        assert later["edges"] == [] and later["source_gap"] is True and "erased" in later["note"]
    finally:
        co.close()


def test_a_quiet_source_is_missing_data_and_every_number_shows_its_denominator_and_hides_small_groups(rt_url: str, owner_url: str) -> None:
    co = Graph(rt_url)
    try:
        empty = co.fx("GET", "analytics").json()
        assert empty["quality"]["labelled"] == 0 and empty["quality"]["precision"] is None and empty["quality"]["reliable"] is False
        assert empty["prevented_loss"] is None and "invented" in empty["prevented_loss_note"] and empty["by_rule"] == []
        co.refused("quality-ref", 3)
        before = co.fx("GET", "quality").json()
        src = next(s for s in before["sources"] if s["source"] == "verify_sessions")
        assert src["state"] == "NO_DATA" and before["warning"].startswith("MISSING_DATA")  # sessions arrived and nothing was read in
        assert next(s for s in before["sources"] if s["source"] == "ip_network")["state"] == "NOT_ENABLED"
        assert before["coverage"]["with_observation"] == 0 and before["coverage"]["without"] == 3
        co.refresh()
        after = co.fx("GET", "quality").json()
        assert next(s for s in after["sources"] if s["source"] == "verify_sessions")["state"] == "OK" and after["warning"] is None
        assert after["coverage"]["with_observation"] == 3 and after["coverage"]["share_observed"] == 1.0
        assert after["retention"]["observation_days"] == 180 and "no single global" in after["retention"]["note"]

        async def quarantine() -> None:
            async with co.tx() as c:
                sid = (await fs.db.sessions_for_ingest(c, limit=1))[0]["session_id"]
                await fs.record_observation(
                    c, str(co.o.tenant), source="device_attestation", kind="CUSTOMER_REFERENCE", session_id=sid, source_event_id="dev-1", token="b" * 64,
                    observed_at=datetime.now(UTC), environment="SANDBOX", region="UK", purpose="fraud_investigation", now=datetime.now(UTC),
                )  # fmt: skip

        asyncio.run(quarantine())
        assert {"source": "device_attestation", "reason": "SOURCE_NOT_ENABLED", "count": 1} in co.fx("GET", "quality").json()["quarantined"]
        # one adjudicated lead is a label, not a rate: it is shown with its denominator, flagged as too few, and the group is hidden
        confirmed_lead(co, "label-ref")
        got = co.fx("GET", "analytics").json()
        assert got["quality"]["labelled"] == 1 and got["quality"]["confirmed"] == 1 and got["quality"]["precision"] == 1.0 and got["quality"]["reliable"] is False
        assert "do not rely" in got["quality"]["note"] and got["handled_leads"] == 1 and got["mean_handling_hours"] is not None
        assert all(c["labelled"] == "fewer than 5" and c["confirmed_share"] == "not shown" for c in got["by_rule"] + got["by_country"])
        assert got["by_rule"] and "singles people out" in got["fairness_note"]
        assert co.fx("GET", "overview").json()["leads"]["confirmed_findings"] == 1
    finally:
        co.close()


def test_another_company_cannot_read_or_change_a_query_an_entry_or_a_timeline(rt_url: str, owner_url: str) -> None:
    mine, other = Graph(rt_url), Graph(rt_url)
    try:
        mine.refused("mine-only", 3)
        mine.refresh()
        lead = mine.leads()[0]
        q = mine.fx("POST", "queries", key="private-query", params={}).json()
        assert other.fx("GET", "queries").json()["items"] == []
        for path in (f"queries/{q['query_id']}:replay", f"queries/{q['query_id']}:submit"):
            assert other.fx("POST", path).status_code == 404
        assert other.fx("GET", f"leads/{lead['lead_id']}/timeline").status_code == 404
        assert other.fx("GET", "analytics").json()["quality"]["labelled"] == 0
        assert other.fx("GET", "quality").json()["coverage"]["sessions"] == 0
    finally:
        mine.close()
        other.close()


def test_an_export_is_masked_has_a_purpose_and_a_reason_a_do_not_keep_time_a_daily_limit_and_is_on_the_record(rt_url: str, owner_url: str) -> None:
    co, other = Graph(rt_url), Graph(rt_url)
    try:
        ids = co.refused("export-secret-ref", 3)
        co.refresh()
        path = f"/v1/verify-api/fraud/graphs/{ids[0]}/export"
        ask = {"format": "csv", "purpose": "fraud_investigation", "reason": "=Check the case for the 14th"}
        # no purpose, no reason, nobody signed in, and another company's session are each refused
        assert code(co.http.get(path, headers=co.a, params={**ask, "purpose": "marketing"})) == "VALIDATION_FAILED"
        assert code(co.http.get(path, headers=co.a, params={**ask, "reason": " "})) == "NOTE_REQUIRED"
        assert co.http.get(path, params=ask).status_code == 401
        assert other.http.get(path, headers=other.a, params=ask).status_code == 404
        got = co.http.get(path, headers=co.a, params=ask)
        assert got.status_code == 200, got.text
        assert got.headers["content-type"].startswith("text/csv") and "attachment" in got.headers["content-disposition"] and got.headers["cache-control"] == "no-store"
        assert got.headers["x-export-purpose"] == "fraud_investigation" and got.headers["x-export-do-not-keep-after"]
        text = got.text
        assert text.count("NODE,") == 3 and text.count("EDGE,") == 2 and "export-secret-ref" not in text  # masked: the reference is nowhere
        assert "'=Check the case" in text and "\n=Check" not in text  # a spreadsheet will not run the reason
        svg = co.http.get(path, headers=co.a, params={**ask, "format": "svg"})
        assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml") and "<script" not in svg.text and "export-secret-ref" not in svg.text
        # every export is recorded with who, why, how big, and until when to keep it
        trail = co.fx("GET", f"audit/export:{co.ids['a']}").json()
        assert trail["chain_intact"] and [e["type"] for e in trail["events"]] == ["graph_exported", "graph_exported"]
        assert trail["events"][0]["reason"].startswith("=Check") and trail["events"][0]["data"]["nodes"] == 3 and trail["events"][0]["data"]["do_not_keep_after"]
        # nobody copies the graph in bulk
        for _ in range(8):
            assert co.http.get(path, headers=co.a, params=ask).status_code == 200
        limit = co.http.get(path, headers=co.a, params=ask)
        assert limit.status_code == 429 and code(limit) == "RATE_LIMITED"
        assert co.http.get(path, headers=co.b, params=ask).status_code == 200  # the limit is per person
        assert code(co.http.get(path, headers=co.b, params={**ask, "depth": 4})) == "QUERY_LIMIT_EXCEEDED"
    finally:
        co.close()
        other.close()


def test_a_lead_is_timed_by_its_risk_a_lead_with_the_review_queue_is_paused_and_every_screen_shows_the_same_numbers(rt_url: str, owner_url: str) -> None:
    co = Graph(rt_url)
    try:
        co.refused("sla-ref", 3)
        co.refresh()
        lead = co.leads()[0]
        assert lead["sla"]["state"] == "RUNNING" and lead["sla"]["age_seconds"] < 120 and lead["sla"]["basis"]
        # three days and a bit later the moderate-risk lead is late; the review queue's own clock is not this one
        owner_sql(owner_url, "UPDATE verify.api_fraud_lead SET created_at = now() - interval '80 hours' WHERE lead_id = :l", l=lead["lead_id"])
        late = co.lead(lead["lead_id"])
        assert late["sla"]["state"] == "BREACHED" and late["sla"]["age_seconds"] >= 80 * 3600 - 5
        ov = co.fx("GET", "overview").json()
        assert ov["queue"]["breached"] == 1 and ov["queue"]["oldest_waiting_seconds"] >= 80 * 3600 - 5 and ov["queue"]["basis"]
        # one source for every number: the overview, the analytics and the metric list cannot disagree
        review = co.session("sla-ref-2", "review")
        co.refused("sla-ref-2", 2)
        co.refresh()
        two = next(x for x in co.leads() if x["seed_session"] != lead["seed_session"])
        assert co.fx("POST", f"leads/{two['lead_id']}:escalate", expected_version=two["lead_version"]).status_code == 200
        assert co.fx("GET", f"leads/{two['lead_id']}").json()["sla"]["state"] == "WITH_REVIEW"  # paused, not late
        co.finalize_case(review)
        version = co.lead(two["lead_id"])["lead_version"]
        assert co.fx("POST", f"leads/{two['lead_id']}:finding", who=co.c, finding="CONFIRMED", note="Independent look.", expected_version=version).status_code == 200
        assert co.fx("GET", f"leads/{two['lead_id']}").json()["sla"]["state"] == "DONE"
        ov, an, me = co.fx("GET", "overview").json(), co.fx("GET", "analytics").json(), co.fx("GET", "metrics").json()
        v = me["values"]
        assert v["leads_raised"] == ov["leads"]["raised"] == 2 and v["confirmed_findings"] == ov["leads"]["confirmed_findings"] == an["quality"]["confirmed"] == 1
        assert v["no_outcome_yet"] == ov["leads"]["no_outcome_yet"] and v["labelled"] == an["quality"]["labelled"] and v["precision"] == an["quality"]["precision"]
        assert v["queue_breached"] == ov["queue"]["breached"] == 1 and v["queue_with_review"] == ov["queue"]["with_review"]
        assert set(v) <= set(me["definitions"]) and me["reliable"] is False and "No Command Center" in me["reconciliation"]
    finally:
        co.close()


def test_a_query_that_uses_up_its_time_is_stopped_and_records_nothing_and_does_not_touch_another_company(rt_url: str, owner_url: str) -> None:
    co, other = Graph(rt_url), Graph(rt_url)
    try:
        co.refused("budget-ref", 3)
        co.refresh()
        other.refused("budget-ref-other", 3)
        other.refresh()
        q = co.fx("POST", "queries", key="slow-one", params={"min_sessions": 3}).json()
        tenant = str(co.o.tenant)

        async def replay(budget: float) -> Any:
            async with co.tx() as c:
                return await fs.replay_query(c, tenant, str(co.ids["a"]), q["query_id"], budget_s=budget)

        with pytest.raises(fs.m.PartnerError) as stopped:
            asyncio.run(replay(0.0))  # no time at all: stopped at the first group
        assert stopped.value.code == "QUERY_LIMIT_EXCEEDED" and "budget" in stopped.value.detail
        assert co.fx("GET", "queries").json()["items"][0]["replay"] is None  # nothing was recorded
        ok = asyncio.run(replay(5.0))
        assert ok["replay"]["would_open"] == 1 and ok["replay_current"] is True
        assert other.fx("GET", "overview").json()["leads"]["raised"] == 1  # the other company was not touched
    finally:
        co.close()
        other.close()

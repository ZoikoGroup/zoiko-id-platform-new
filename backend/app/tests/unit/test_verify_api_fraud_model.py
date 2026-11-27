"""The pure rules of the fraud graph (CR-168; ZID-CP-WF05): link strengths, the signal registry, the lead rules, the bounded read, the finding and the action manifest."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.verify_api.domain import fraud as f
from zid_platform.modules.verify_api.domain.model import PartnerError

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)


def code(fn: Any, *a: Any, **k: Any) -> str:
    with pytest.raises(PartnerError) as e:
        fn(*a, **k)
    return e.value.code


def facts(*outcomes: str | None, apps: tuple[str, ...] = ("app_1",)) -> list[f.SessionFact]:
    return [
        f.SessionFact(f"s{i}", apps[i % len(apps)], NOW + timedelta(minutes=i), o)
        for i, o in enumerate(outcomes)
    ]


def test_a_weak_or_similarity_link_never_supports_a_step_and_a_person_with_no_links_is_not_called_weak() -> (
    None
):
    assert f.only_weak(["weak"]) and f.only_weak(["weak", "similarity"])
    assert not f.only_weak(["weak", "corroborating"]) and not f.only_weak(["strong"])
    assert not f.only_weak([])
    assert (
        f.best_strength(["weak", "strong", "similarity"]) is f.Strength.STRONG
        and f.best_strength([]) is None
    )
    assert f.EDGE_KINDS["IP_SHARED"].strength is f.Strength.WEAK
    assert (
        f.EDGE_KINDS["NEAR_IMAGE"].strength is f.Strength.SIMILARITY
    )  # shown with a method, never as a probability
    assert all(k.note for k in f.EDGE_KINDS.values())  # every kind says what it does NOT show


def test_only_the_enabled_source_makes_links_and_everything_else_is_quarantined_with_a_reason() -> (
    None
):
    assert f.source_problem("verify_sessions", "CUSTOMER_REFERENCE") is None
    assert f.source_problem("verify_sessions", "DEVICE_ID") == "KIND_NOT_FROM_SOURCE"
    assert f.source_problem("device_attestation", "CUSTOMER_REFERENCE") == "SOURCE_NOT_ENABLED"
    assert f.source_problem("ip_network", "CUSTOMER_REFERENCE") == "SOURCE_NOT_ENABLED"
    assert f.source_problem("a_made_up_vendor", "CUSTOMER_REFERENCE") == "UNKNOWN_SOURCE"
    assert [k for k, s in f.SOURCES.items() if s.enabled] == ["verify_sessions"]


def test_the_biometric_and_cross_company_features_are_deny_only_whatever_is_asked() -> None:
    for name in ("biometric_1n", "face_blocklist", "cross_tenant", "anything_else"):
        assert code(f.require_feature, name) == "SENSITIVE_FEATURE_DISABLED"
    assert {g["feature"] for g in f.gates()} == {"biometric_1n", "face_blocklist", "cross_tenant"}
    assert all(g["state"] == "OFF" and g["reason"] for g in f.gates())


def test_a_reference_token_comes_from_the_hash_the_erasure_request_keeps_and_never_from_the_reference() -> (
    None
):
    a, b = f.reference_token(b"\x01" * 32), f.reference_token(b"\x02" * 32)
    assert a == f.reference_token(b"\x01" * 32) and a != b and len(a) == 64
    assert "customer" not in a


def test_the_lead_rules_are_deterministic_and_the_most_serious_applicable_rule_wins() -> None:
    assert f.evaluate_group(facts("verified")) is None  # one session is no link
    assert (
        f.evaluate_group(facts("verified", "verified")) is None
    )  # a repeat of a good result is ordinary
    assert f.evaluate_group(facts("not_verified", "verified", "verified")) is None
    cand = f.evaluate_group(facts("not_verified", "not_verified", "verified"))
    assert cand and cand.rule_id == "REPEAT_AFTER_REFUSAL" and cand.risk_class == "MODERATE"
    assert (
        f.evaluate_group(
            facts("not_verified", "not_verified", "not_verified", "not_verified", None)
        ).risk_class
        == "HIGH"
    )  # type: ignore[union-attr]
    again = f.evaluate_group(facts("not_verified", "not_verified", "verified"))
    assert again == cand  # identical facts, identical lead
    multi = f.evaluate_group(facts("verified", "verified", apps=("app_1", "app_2")))
    assert multi and multi.rule_id == "MULTI_APPLICATION" and multi.risk_class == "LOW"
    both = f.evaluate_group(
        facts("not_verified", "not_verified", "verified", apps=("app_1", "app_2"))
    )
    assert both and both.rule_id == "REPEAT_AFTER_REFUSAL"
    assert any("company chose" in u for u in cand.uncertain)  # what is not known is on the lead


def test_a_lead_carries_its_uncertain_facts_and_says_when_it_was_cut() -> None:
    cand = f.evaluate_group(facts("not_verified", "not_verified", None), truncated=True)
    assert (
        cand
        and any("no result yet" in u for u in cand.uncertain)
        and any(str(f.GROUP_MAX) in u for u in cand.uncertain)
    )


def test_a_lead_cannot_skip_a_step_and_a_finished_one_does_not_move() -> None:
    L = f.LeadStatus
    assert f.check_lead_transition(L.OPEN, L.IN_ANALYSIS) is L.IN_ANALYSIS
    assert f.check_lead_transition(L.IN_ANALYSIS, L.ESCALATED) is L.ESCALATED
    for src in (L.ESCALATED, L.CLEARED, L.EXPIRED):
        for dst in L:
            assert code(f.check_lead_transition, src, dst) == "LEAD_STATE_INVALID"
    assert set(f.LEAD_TRANSITIONS) == set(L)


def test_the_read_is_bounded_and_breadth_first_from_the_seed_and_says_when_more_exists() -> None:
    assert f.check_read_limits(2, 200) == (2, 200)
    for depth, limit in ((0, 10), (4, 10), (2, 0), (2, 201)):
        assert code(f.check_read_limits, depth, limit) == "QUERY_LIMIT_EXCEEDED"
    edges = [("e1", "a", "b"), ("e2", "b", "c"), ("e3", "c", "d"), ("e4", "a", "x")]
    nodes, kept, more = f.expand("a", edges, depth=2, limit=10)
    assert (
        nodes == ["a", "b", "x", "c"] and kept == ["e1", "e2", "e4"] and more
    )  # d is three steps away
    nodes, kept, more = f.expand("a", edges, depth=3, limit=10)
    assert "d" in nodes and not more
    nodes, kept, more = f.expand("a", edges, depth=3, limit=2)
    assert (
        len(nodes) == 2 and more and all(k in {"e1", "e4"} for k in kept)
    )  # cut at the limit, never past it
    assert f.expand("a", edges, depth=3, limit=2) == (nodes, kept, more)  # deterministic


def test_a_challenge_needs_a_reason_and_a_stale_screen_changes_nothing() -> None:
    assert f.check_challenge("exclude", "  A shared   household. ") == (
        "exclude",
        "A shared household.",
    )
    assert code(f.check_challenge, "delete", "Because") == "VALIDATION_FAILED"
    assert code(f.check_challenge, "exclude", " ") == "NOTE_REQUIRED"
    f.check_version(3, 3)
    assert (
        code(f.check_version, 3, 2) == "GRAPH_SNAPSHOT_STALE"
        and code(f.check_version, 3, None) == "GRAPH_SNAPSHOT_STALE"
    )


def test_an_edge_explanation_is_computed_from_the_edge_and_never_claims_more() -> None:
    text = f.explain_edge("EXACT_REFERENCE", first_seen=NOW, last_seen=NOW + timedelta(days=2), n=2)
    assert "exact equality" in text and "2 observations" in text and "does not show" in text.lower()
    assert "fraud" not in text.lower().replace("not show", "")  # no verdict


def test_a_finding_needs_an_escalated_lead_a_final_case_and_a_person_who_took_no_part() -> None:
    ok = {
        "lead_status": f.LeadStatus.ESCALATED,
        "case_final": True,
        "person": "c",
        "case_people": {"a", "b"},
    }
    assert f.finding_problems(**ok) == []
    assert f.finding_problems(**{**ok, "lead_status": f.LeadStatus.OPEN}) == ["LEAD_NOT_ESCALATED"]
    assert f.finding_problems(**{**ok, "case_final": False}) == ["CASE_NOT_FINAL"]
    assert f.finding_problems(**{**ok, "person": "a"}) == ["REVIEWER_NOT_INDEPENDENT"]
    assert f.check_finding("CONFIRMED") is f.Finding.CONFIRMED
    assert (
        code(f.check_finding, "UNREVIEWED") == "VALIDATION_FAILED"
        and code(f.check_finding, "guilty") == "VALIDATION_FAILED"
    )


def proposal(**over: Any) -> list[str]:
    base: dict[str, Any] = {
        "finding": f.Finding.CONFIRMED, "targets": ["s0", "s1"], "proposal_type": "temporary_hold", "expires_at": NOW + timedelta(days=2), "now": NOW,
        "facts": {"s0": f.TargetFacts("s0", True, ("corroborating",)), "s1": f.TargetFacts("s1", True, ("corroborating", "weak")), "s9": f.TargetFacts("s9", True, ("weak",))},
    }  # fmt: skip
    return f.proposal_problems(**{**base, **over})


def test_an_action_proposal_names_exact_targets_each_supported_by_a_link_that_can_support_a_step() -> (
    None
):
    assert proposal() == []
    assert proposal(finding=f.Finding.NO_FINDING) == ["NO_CONFIRMED_FINDING"]
    assert proposal(finding=f.Finding.UNREVIEWED) == ["NO_CONFIRMED_FINDING"]
    assert proposal(targets=["s0", "s9"]) == [
        "TARGET_WEAK_ONLY:s9"
    ]  # two strong leads are not 200 weak ones
    assert proposal(targets=["s0", "zz"]) == ["TARGET_NOT_IN_LEAD:zz"]
    assert proposal(targets=[]) == ["NO_TARGETS"] and proposal(targets=["s0", "s0"]) == [
        "DUPLICATE_TARGET"
    ]
    assert proposal(proposal_type="ban_everyone") == ["UNKNOWN_ACTION_TYPE"]
    assert proposal(expires_at=NOW - timedelta(days=1)) == ["BAD_EXPIRY"] and proposal(
        expires_at=NOW + timedelta(days=31)
    ) == ["BAD_EXPIRY"]
    many = [f"t{i}" for i in range(f.ACTION_MAX_TARGETS + 1)]
    got = proposal(targets=many, facts={t: f.TargetFacts(t, True, ("strong",)) for t in many})
    assert got == ["TOO_MANY_TARGETS"]
    assert proposal(
        facts={"s0": f.TargetFacts("s0", True, ()), "s1": f.TargetFacts("s1", True, ("strong",))}
    ) == ["TARGET_UNLINKED:s0"]
    assert (
        code(f.check_basis, " ", "legal basis") == "LEGAL_BASIS_REQUIRED"
        and f.check_basis(" policy  v3 ", "x") == "policy v3"
    )


def checksum(**over: Any) -> str:
    base: dict[str, Any] = {"tenant_id": "t", "lead_id": "led_1", "proposal_type": "temporary_hold", "targets": ["s0", "s1"], "basis_ref": "policy v3", "impact_ref": "impact 19", "snapshot": "sha256:aa", "expires_at": NOW + timedelta(days=1)}  # fmt: skip
    return f.manifest_checksum(**{**base, **over})


def test_a_manifest_names_the_exact_targets_in_order_so_any_change_needs_a_new_approval() -> None:
    assert checksum() == checksum() and checksum().startswith("sha256:")
    assert checksum(targets=["s1", "s0"]) != checksum()  # reordered
    assert checksum(targets=["s0", "s2"]) != checksum()  # a changed target
    assert checksum(targets=["s0"]) != checksum()
    assert (
        checksum(snapshot="sha256:bb") != checksum() and checksum(basis_ref="other") != checksum()
    )


def approval(**over: Any) -> list[str]:
    base: dict[str, Any] = {"status": "PROPOSED", "maker": "m", "approver": "z", "expires_at": NOW + timedelta(days=1), "now": NOW, "supplied": "sha256:x", "stored": "sha256:x", "still_linked": True}  # fmt: skip
    return f.approval_problems(**{**base, **over})


def test_nobody_approves_their_own_proposal_a_stale_manifest_or_an_expired_one() -> None:
    assert approval() == []
    assert approval(approver="m") == ["ACTION_APPROVAL_REQUIRED"]
    assert approval(supplied="sha256:y") == ["ACTION_TARGET_SET_CHANGED"] and approval(
        supplied=None
    ) == ["ACTION_TARGET_SET_CHANGED"]
    assert approval(still_linked=False) == ["ACTION_TARGET_SET_CHANGED"]
    assert approval(expires_at=NOW) == ["ACTION_EXPIRED"]
    assert approval(status="APPROVED") == ["ACTION_NOT_PROPOSED"]
    assert set(approval(approver="m", supplied="x", status="REJECTED")) == {
        "ACTION_APPROVAL_REQUIRED",
        "ACTION_TARGET_SET_CHANGED",
        "ACTION_NOT_PROPOSED",
    }


# ------------------------------------------------------------------------------- the query builder


def test_a_query_is_typed_fields_only_and_never_joins_by_a_weak_signal_alone() -> None:
    ok = f.compile_query({})
    assert (
        ok["edge_types"] == ["EXACT_REFERENCE"]
        and ok["min_sessions"] == 3
        and ok["window_days"] == 30
        and ok["require_corroboration"] is False
    )
    for field in ("ip", "phone", "payment_bin", "name", "free_text", "cypher"):
        assert (
            code(f.compile_query, {field: "x"}) == "UNSUPPORTED_FIELD"
        )  # no search by a person's details, no free text
    assert code(f.compile_query, {"edge_types": ["IP_SHARED"]}) == "PROHIBITED_JOIN"
    assert (
        code(
            f.compile_query,
            {"edge_types": ["IP_SHARED", "NEAR_IMAGE"], "require_corroboration": True},
        )
        == "PROHIBITED_JOIN"
    )  # all weak, even when it asks for both
    assert (
        code(f.compile_query, {"edge_types": ["EXACT_REFERENCE", "IP_SHARED"]}) == "PROHIBITED_JOIN"
    )  # an OR that lets the weak one decide
    assert f.compile_query(
        {"edge_types": ["EXACT_REFERENCE", "IP_SHARED"], "require_corroboration": True}
    )["edge_types"] == ["EXACT_REFERENCE", "IP_SHARED"]
    assert (
        code(f.compile_query, {"edge_types": ["MADE_UP"]}) == "VALIDATION_FAILED"
        and code(f.compile_query, {"edge_types": []}) == "VALIDATION_FAILED"
    )
    assert code(f.compile_query, {"min_strength": "weak"}) == "PROHIBITED_JOIN"
    for bad in (
        {"min_sessions": 1},
        {"min_sessions": f.GROUP_MAX + 1},
        {"window_days": 0},
        {"window_days": 400},
        {"min_not_verified": -1},
        {"min_sessions": True},
        {"risk_class": "SEVERE"},
        {"multi_application": "yes"},
    ):
        assert code(f.compile_query, bad) == "VALIDATION_FAILED", bad
    assert code(f.compile_query, "not a mapping") == "VALIDATION_FAILED"  # type: ignore[arg-type]
    assert f.query_checksum(ok) == f.query_checksum(
        dict(reversed(list(ok.items())))
    ) and f.query_checksum(ok) != f.query_checksum({**ok, "min_sessions": 4})
    assert f.re_key("repeat-refusals") and not f.re_key("Bad Key") and not f.re_key("a")


def test_a_query_naming_a_source_nothing_produces_is_not_published_and_a_replay_changes_nothing() -> (
    None
):
    assert f.publish_problems(f.compile_query({})) == []
    weak = f.compile_query(
        {"edge_types": ["EXACT_REFERENCE", "IP_SHARED"], "require_corroboration": True}
    )
    assert f.publish_problems(weak) == ["SOURCE_NOT_ENABLED:ip_network"]
    p = f.compile_query({"min_sessions": 3, "min_not_verified": 2, "window_days": 7})
    fresh = [
        f.SessionFact(f"s{i}", "app", NOW - timedelta(days=i), "not_verified") for i in range(3)
    ]
    cand = f.evaluate_query(p, "repeat-refusals", fresh, f.Strength.CORROBORATING, now=NOW)
    assert (
        cand
        and cand.rule_id == "QUERY_REPEAT_REFUSALS"
        and len(cand.nodes) == 3
        and any("custom query" in u for u in cand.uncertain)
    )
    old = [
        f.SessionFact(f"o{i}", "app", NOW - timedelta(days=30 + i), "not_verified")
        for i in range(3)
    ]
    assert (
        f.evaluate_query(p, "k", old, f.Strength.CORROBORATING, now=NOW) is None
    )  # outside the window
    assert f.evaluate_query(p, "k", fresh, None, now=NOW) is None
    assert (
        f.evaluate_query(
            {**p, "min_strength": "strong"}, "k", fresh, f.Strength.CORROBORATING, now=NOW
        )
        is None
    )
    assert (
        f.evaluate_query({**p, "multi_application": True}, "k", fresh, f.Strength.STRONG, now=NOW)
        is None
    )
    summary = f.replay_summary([cand, None, None], 3)
    assert (
        summary["would_open"] == 1
        and summary["groups_scanned"] == 3
        and summary["review_workload_sessions"] == 3
    )
    assert summary["side_effects"] == {
        "leads_opened": 0,
        "provider_calls": 0,
        "charges": 0,
        "decisions_changed": 0,
    }


# ------------------------------------------------------------------------------- the as-of picture


def test_a_past_picture_uses_only_what_was_recorded_then_and_never_rebuilds_erased_data() -> None:
    base = {
        "status": "ACTIVE",
        "status_reason": None,
        "created_at": NOW,
        "expires_at": NOW + timedelta(days=100),
    }
    hist = [(NOW + timedelta(days=2), "edge_excluded"), (NOW + timedelta(days=4), "edge_restored")]
    assert not f.edge_active_at(base, hist, NOW - timedelta(minutes=1))  # not yet known
    assert f.edge_active_at(base, hist, NOW + timedelta(days=1))
    assert not f.edge_active_at(base, hist, NOW + timedelta(days=3))  # excluded then
    assert f.edge_active_at(base, hist, NOW + timedelta(days=5))  # restored
    assert not f.edge_active_at(base, [], NOW + timedelta(days=101))  # expired
    assert not f.edge_active_at(
        {**base, "status": "RETRACTED", "status_reason": "ERASED"}, [], NOW + timedelta(days=1)
    )  # an erasure is final: never rebuilt


# ------------------------------------------------------------------------------- the blocklist, data quality and honest numbers


def test_a_blocklist_entry_needs_a_confirmed_finding_a_link_that_supports_a_step_and_a_reference_still_held() -> (
    None
):
    ok = {
        "finding": f.Finding.CONFIRMED,
        "lead_status": f.LeadStatus.ESCALATED,
        "has_token": True,
        "strengths": ["corroborating"],
        "already_listed": False,
    }
    assert f.admission_problems(**ok) == []
    assert f.admission_problems(**{**ok, "finding": f.Finding.UNREVIEWED}) == [
        "NO_CONFIRMED_FINDING"
    ]
    assert f.admission_problems(**{**ok, "has_token": False}) == ["SOURCE_ERASED"]
    assert f.admission_problems(**{**ok, "strengths": ["similarity"]}) == [
        "SIMILARITY_ONLY"
    ]  # a look-alike never makes an entry
    assert f.admission_problems(**{**ok, "strengths": ["weak", "weak"]}) == [
        "SIMILARITY_ONLY"
    ] and f.admission_problems(**{**ok, "strengths": []}) == ["SIMILARITY_ONLY"]
    assert f.admission_problems(**{**ok, "already_listed": True}) == ["ALREADY_LISTED"]
    assert code(f.check_block_reason, "because") == "VALIDATION_FAILED" and f.check_block_reason(
        "CONFIRMED_REPEAT_ABUSE"
    )
    soon = NOW + timedelta(days=1)
    assert (
        f.block_effective("ACTIVE", soon, NOW) == "ACTIVE"
        and f.block_effective("ACTIVE", NOW, NOW) == "EXPIRED"
    )  # past its time it stops counting
    assert (
        f.block_effective("CHALLENGED", NOW - timedelta(days=1), NOW) == "EXPIRED"
        and f.block_effective("REMOVED", NOW - timedelta(days=1), NOW) == "REMOVED"
    )


def test_a_quiet_source_is_missing_data_never_less_risk_and_small_or_unlabelled_numbers_are_not_trusted() -> (
    None
):
    assert f.freshness(None, NOW, sessions_recent=5) == "NO_DATA"
    assert f.freshness(NOW - timedelta(hours=1), NOW, sessions_recent=5) == "OK"
    assert (
        f.freshness(NOW - timedelta(days=3), NOW, sessions_recent=5) == "STALE"
    )  # sessions arrived, nothing was read in
    assert (
        f.freshness(NOW - timedelta(days=3), NOW, sessions_recent=0) == "OK"
    )  # a genuinely quiet day
    assert f.shown(4) == "fewer than 5" and f.shown(5) == 5
    none = f.label_quality(0, 0)
    assert none["precision"] is None and none["reliable"] is False
    few = f.label_quality(3, 1)
    assert few["precision"] == 0.75 and few["reliable"] is False and "do not rely" in few["note"]
    many = f.label_quality(24, 8)
    assert many["precision"] == 0.75 and many["reliable"] is True and many["labelled"] == 32


# ------------------------------------------------------------------------------- how long a lead may wait, the export, the budget


def sla(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "OPEN",
        "finding": "UNREVIEWED",
        "risk": "MODERATE",
        "created_at": NOW,
        "now": NOW + timedelta(hours=1),
    }
    return f.lead_sla(**{**base, **over})


def test_a_lead_has_a_time_by_risk_and_a_lead_with_the_review_queue_is_paused_not_late() -> None:
    assert sla()["state"] == "RUNNING" and sla()["age_seconds"] == 3600 and sla()["basis"]
    assert sla(now=NOW + timedelta(hours=60))["state"] == "DUE_SOON"  # the last quarter of 72 hours
    assert sla(now=NOW + timedelta(hours=72))["state"] == "BREACHED"
    assert (
        sla(risk="HIGH", now=NOW + timedelta(hours=25))["state"] == "BREACHED"
        and sla(risk="LOW", now=NOW + timedelta(days=6))["state"] == "DUE_SOON"
    )
    late = {"now": NOW + timedelta(days=30)}
    assert (
        sla(status="ESCALATED", **late)["state"] == "WITH_REVIEW"
    )  # the review case's clock applies now
    assert (
        sla(status="CLEARED", **late)["state"] == "DONE"
        and sla(status="EXPIRED", **late)["state"] == "DONE"
    )
    assert sla(status="ESCALATED", finding="CONFIRMED", **late)["state"] == "DONE"
    assert (
        sla(data_missing=True, **late)["state"] == "UNKNOWN"
    )  # a quiet queue proves nothing when the feed is missing
    assert sla(data_missing=True, status="CLEARED", **late)["state"] == "DONE"


GRAPH = {
    "nodes": [
        {"id": "s1", "is_seed": True, "outcome": "not_verified", "country": "GB"},
        {"id": "s<2>", "outcome": "=cmd", "country": "GB"},
    ],
    "edges": [
        {
            "edge_id": "edg_1",
            "nodes": ["s1", "s<2>"],
            "type": "EXACT_REFERENCE",
            "strength": "corroborating",
            "evidence_ref": ["obs_1", "obs_2"],
            "first_seen": "2026-11-01",
            "last_seen": "2026-11-02",
        }
    ],
}
META = {
    "purpose": "fraud_investigation",
    "reason": '=HYPERLINK("http://x")',
    "graph_snapshot_id": "sha256:" + "a" * 64,
    "do_not_keep_after": "2026-11-26T10:00:00+00:00",
}


def test_an_export_needs_a_purpose_and_a_reason_and_a_spreadsheet_cannot_run_what_is_in_it() -> (
    None
):
    assert f.check_export("csv", "fraud_investigation", " Case review 14 ") == (
        "csv",
        "fraud_investigation",
        "Case review 14",
    )
    assert code(f.check_export, "pdf", "fraud_investigation", "reason here") == "VALIDATION_FAILED"
    assert code(f.check_export, "csv", "marketing", "reason here") == "VALIDATION_FAILED"
    assert code(f.check_export, "csv", "audit_review", " ") == "NOTE_REQUIRED"
    assert (
        f.csv_cell("=1+1") == "'=1+1"
        and f.csv_cell("@x") == "'@x"
        and f.csv_cell("-3") == "'-3"
        and f.csv_cell(None) == ""
        and f.csv_cell("plain") == "plain"
    )
    text = f.render_csv(GRAPH, META)
    assert (
        "'=HYPERLINK" in text and "\n=" not in text and ",=" not in text
    )  # nothing starts a formula
    assert (
        text.splitlines()[0].startswith("record,")
        and "NODE,s1" in text
        and "EDGE,edg_1,s1" in text
        and "obs_1 obs_2" in text
    )
    assert "customer" not in text.lower() and "do_not_keep_after" in text


def test_an_svg_export_escapes_everything_and_marks_weak_links_and_says_it_is_not_a_finding() -> (
    None
):
    svg = f.render_svg(GRAPH, META)
    assert (
        svg.startswith("<svg")
        and "<script" not in svg.lower()
        and "s&lt;2&gt;" in svg
        and "s<2>" not in svg
    )
    assert "not a finding" in svg and "Keep until" in svg and "stroke-dasharray" not in svg
    weak = {**GRAPH, "edges": [{**GRAPH["edges"][0], "strength": "weak"}]}
    assert "stroke-dasharray" in f.render_svg(weak, META)  # a weak link looks weak


def test_a_query_that_runs_too_long_or_reads_too_much_is_stopped() -> None:
    assert f.budget_exceeded(0.0, 1.0, 5.0, 10) is None
    assert f.budget_exceeded(0.0, 6.0, 5.0, 10) == "TIME"
    assert f.budget_exceeded(0.0, 1.0, 5.0, f.QUERY_EDGE_SCAN_MAX + 1) == "FAN_OUT"
    assert set(f.METRIC_DEFINITIONS) >= {
        "leads_raised",
        "confirmed_findings",
        "precision",
        "queue_breached",
        "oldest_waiting_seconds",
    }
    assert all(
        d["definition"] and d["denominator"] and d["window"] and d["source"]
        for d in f.METRIC_DEFINITIONS.values()
    )

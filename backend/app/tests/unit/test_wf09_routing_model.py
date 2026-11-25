"""Deterministic provider routing, the attempt state machine, coverage claims, release governance and cost reconciliation (ZID-CP-WF09 v2.0; CR-171): pure rules."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.verification.domain import routing as r

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=200)


def cand(pid: str = "prov-a", **over: Any) -> r.Candidate:
    base: dict[str, Any] = {
        "provider_id": pid, "method": "DOCUMENT_FACE", "assurance": 3, "countries": frozenset({"GB", "DE"}), "channels": frozenset({"web", "ios"}), "min_sdk_version": (2, 0),
        "processing_regions": frozenset({"uk"}), "subprocessor_regions": frozenset({"uk"}), "legal_determination_ref": "LD-1", "legal_determination_valid_until": LATER, "state": "APPROVED",
        "qualified_until": LATER, "contract_until": LATER, "tenant_ids": None, "health": "UP", "quota_remaining": 1000, "unit_cost_minor": 100, "review_cost_minor": 50,
        "valid_outcome_rate_bp": 9000, "p95_latency_ms": 800, "measured": True,
    }  # fmt: skip
    return r.Candidate(**{**base, **over})


def inp(**over: Any) -> r.RouteInput:
    base: dict[str, Any] = {
        "tenant_id": "t1", "session_id": "s1", "workflow_version": "wf03-v21", "purpose": "kyc", "issuer_country": "GB", "residence_country": "GB", "method": "DOCUMENT_FACE",
        "assurance_floor": 2, "channel": "web", "sdk_version": "2.1.0", "allowed_regions": frozenset({"uk"}), "now": NOW, "tenant_entitled_methods": frozenset({"DOCUMENT_FACE", "NFC_CHIP"}),
        "workflow_approved": True, "market_open": True,
    }  # fmt: skip
    return r.RouteInput(**{**base, **over})


def reasons_of(d: r.RouteDecision, pid: str) -> set[str]:
    return set(next(x for x in d.rejected if x.provider_id == pid).reasons)


# ------------------------------------------------------------------------------------------------------------------ hard gates first


def test_a_cheaper_provider_that_fails_a_gate_is_never_chosen_and_every_failing_reason_is_named() -> None:
    cheap_bad = cand("cheap", unit_cost_minor=1, processing_regions=frozenset({"us"}), legal_determination_ref=None)
    dear_ok = cand("dear", unit_cost_minor=900)
    d = r.resolve(inp(), [cheap_bad, dear_ok])
    assert d.selected == "dear" and [x.provider_id for x in d.ranked] == ["dear"]
    assert reasons_of(d, "cheap") >= {"PROCESSING_REGION_NOT_ALLOWED", "NO_LEGAL_DETERMINATION"}
    assert next(x for x in d.rejected if x.provider_id == "cheap").gates == ("G4_DATA_PATH",)


def test_each_gate_has_a_reason_and_an_unknown_fact_is_a_failure_not_a_pass() -> None:
    cases = {
        "SUBPROCESSOR_REGION_NOT_ALLOWED": cand(subprocessor_regions=frozenset({"us"})),
        "LEGAL_DETERMINATION_EXPIRED": cand(legal_determination_valid_until=NOW - timedelta(days=1)),
        "METHOD_NOT_OFFERED": cand(method="DOCUMENT"),
        "ASSURANCE_BELOW_FLOOR": cand(assurance=1),
        "ISSUER_COUNTRY_NOT_COVERED": cand(countries=frozenset({"FR"})),
        "PROVIDER_SUSPENDED": cand(state="SUSPENDED"),
        "PROVIDER_NOT_APPROVED": cand(state="UNDER_REVIEW"),
        "QUALIFICATION_EXPIRED": cand(qualified_until=NOW - timedelta(seconds=1)),
        "CONTRACT_EXPIRED": cand(contract_until=None),
        "TENANT_NOT_ENTITLED_TO_PROVIDER": cand(tenant_ids=frozenset({"other"})),
        "CHANNEL_NOT_SUPPORTED": cand(channels=frozenset({"ios"})),
        "SDK_VERSION_NOT_SUPPORTED": cand(min_sdk_version=(3, 0)),
        "PROVIDER_HEALTH_DOWN": cand(health="DOWN"),
        "PROVIDER_HEALTH_UNKNOWN": cand(health="UNKNOWN"),
        "QUOTA_EXHAUSTED": cand(quota_remaining=0),
    }
    for code, c in cases.items():
        d = r.resolve(inp(), [c])
        assert d.selected is None and code in reasons_of(d, c.provider_id) and code in d.no_route_reasons, code
    assert r.resolve(inp(sdk_version=None), [cand()]).selected is None  # an SDK that did not say what it is
    assert r.resolve(inp(), [cand()]).selected == "prov-a"


def test_a_document_only_route_never_satisfies_a_chip_requirement() -> None:
    doc = cand("doc", method="DOCUMENT_FACE")
    chip = cand("chip", method="NFC_CHIP", assurance=4)
    d = r.resolve(inp(method="DOCUMENT_FACE", chip_required=True), [doc, chip])
    assert d.selected is None and "CHIP_REQUIRED_BUT_METHOD_IS_WEAKER" in reasons_of(d, "doc")
    assert r.resolve(inp(method="NFC_CHIP", chip_required=True, assurance_floor=3), [doc, chip]).selected == "chip"


def test_a_request_level_failure_is_no_route_for_everyone_with_its_own_reason() -> None:
    for over, code in (({"workflow_approved": False}, "WORKFLOW_NOT_APPROVED"), ({"market_open": False}, "MARKET_NOT_RELEASED"), ({"context_verified": False}, "CONTEXT_NOT_VERIFIED"), ({"tenant_entitled_methods": frozenset()}, "METHOD_NOT_ENTITLED_FOR_TENANT")):
        d = r.resolve(inp(**over), [cand()])
        assert d.selected is None and code in d.no_route_reasons and d.to_dict()["status"] == "NO_ROUTE", code
    assert r.resolve(inp(), []).no_route_reasons == ("NO_PROVIDER_REGISTERED",)  # a missing route is not a failed applicant


# ------------------------------------------------------------------------------------------------------------------- soft optimisation


def test_among_lawful_providers_the_risk_adjusted_cost_decides_not_the_sticker_price() -> None:
    cheap_weak = cand("cheap", unit_cost_minor=60, valid_outcome_rate_bp=4000)  # (60+50)/0.4 = 275
    dear_strong = cand("dear", unit_cost_minor=120, valid_outcome_rate_bp=9500)  # (120+50)/0.95 = 179
    d = r.resolve(inp(), [cheap_weak, dear_strong])
    assert d.selected == "dear" and [x.rank for x in d.ranked] == [1, 2] and d.ranked[0].risk_adjusted_cost_minor < d.ranked[1].risk_adjusted_cost_minor


def test_a_vendors_own_claim_is_not_a_measurement_and_is_ranked_at_half() -> None:
    claimed = cand("claimed", unit_cost_minor=100, valid_outcome_rate_bp=9900, measured=False)
    proven = cand("proven", unit_cost_minor=100, valid_outcome_rate_bp=6000, measured=True)
    assert r.risk_adjusted_cost_minor(claimed) > r.risk_adjusted_cost_minor(cand("x", valid_outcome_rate_bp=9900))
    d = r.resolve(inp(), [claimed, proven])
    assert d.selected == "proven" and any(n.startswith("RANKED_ON_CLAIMED_NOT_MEASURED:claimed") for n in d.notes)


def test_the_same_snapshot_gives_the_same_answer_byte_for_byte_and_a_change_changes_the_digest() -> None:
    pool = [cand("b", unit_cost_minor=100), cand("a", unit_cost_minor=100), cand("c", unit_cost_minor=300)]
    first = r.resolve(inp(), pool)
    again = r.resolve(inp(), list(reversed(pool)))  # the order candidates arrive in never matters
    assert first.selected == "a" and first.decision_digest == again.decision_digest and r.canonical(first.to_dict()) == r.canonical(again.to_dict())  # ties break on provider id
    moved = r.resolve(inp(), [replace(pool[0], unit_cost_minor=101), *pool[1:]])
    assert moved.snapshot_digest != first.snapshot_digest and moved.decision_digest != first.decision_digest
    assert r.resolve(inp(session_id="s2"), pool).input_digest != first.input_digest
    assert first.algorithm_version == r.ALGORITHM_VERSION


def test_excluding_a_provider_reevaluates_every_gate_for_the_rest() -> None:
    a, b = cand("a"), cand("b", processing_regions=frozenset({"us"}))  # b's secondary path fails the data guard
    d = r.resolve(inp(), [a, b], exclude=["a"])
    assert d.selected is None and "PROCESSING_REGION_NOT_ALLOWED" in d.no_route_reasons and any(n.startswith("EXCLUDED_BEFORE_RESOLVING") for n in d.notes)


# ---------------------------------------------------------------------------------------------------------------------- attempt machine


def test_the_attempt_follows_the_documented_states_and_nothing_skips_one() -> None:
    S = r.AttemptState
    path = [S.ROUTE_REQUESTED, S.ELIGIBLE, S.ATTEMPT_RESERVED, S.DISPATCHED, S.RESULT_RECEIVED, S.NORMALIZED, S.FINALIZED]
    for a, b in zip(path, path[1:], strict=False):
        assert r.transition(a, b, actor="VERIFIER" if b is S.FINALIZED else "SYSTEM") == b
    for a, b in ((S.ROUTE_REQUESTED, S.DISPATCHED), (S.ELIGIBLE, S.DISPATCHED), (S.DISPATCHED, S.FINALIZED), (S.RESULT_RECEIVED, S.FINALIZED), (S.NORMALIZED, S.ELIGIBLE)):
        with pytest.raises(r.TransitionError) as e:
            r.transition(a, b, actor="VERIFIER")
        assert e.value.code == "ATTEMPT_TRANSITION_FORBIDDEN"
    with pytest.raises(r.TransitionError) as e:
        r.transition(S.FINALIZED, S.NORMALIZED)
    assert e.value.code == "ATTEMPT_ALREADY_TERMINAL"
    with pytest.raises(r.TransitionError) as e:
        r.transition(S.NORMALIZED, S.FINALIZED, actor="PROVIDER")  # a vendor "success" is not an identity decision
    assert e.value.code == "ONLY_THE_OWNING_VERIFIER_FINALISES"


def test_an_unknown_outcome_is_reconciled_and_never_sent_to_another_vendor() -> None:
    S = r.AttemptState
    assert r.classify_failure(dispatched=False) == "PRE_DISPATCH" and r.FAILURE_NEXT["PRE_DISPATCH"] is S.FAILED_BEFORE_DISPATCH
    assert r.classify_failure(dispatched=True, transport_timeout=True) == "OUTCOME_UNKNOWN"  # a timeout is not "not processed"
    assert r.classify_failure(dispatched=True, provider_confirmed_not_processed=True) == "DEFINITELY_NOT_PROCESSED"
    assert r.classify_failure(dispatched=True, bad_result=True) == "BAD_RESULT" and r.classify_failure(dispatched=True, late_result=True) == "LATE_RESULT"
    assert r.classify_failure(dispatched=True, provider_outage=True) == "VENDOR_OUTAGE_AFTER_DISPATCH_UNKNOWN"
    for s in (S.DISPATCHED, S.PENDING, S.OUTCOME_UNKNOWN, S.RECONCILING, S.RESULT_RECEIVED, S.FINALIZED):
        assert not r.may_ask_another_provider(s), s
    for s in (S.FAILED_BEFORE_DISPATCH, S.CONFIRMED_NOT_PROCESSED, S.CANCELLED, S.NO_ROUTE):
        assert r.may_ask_another_provider(s), s
    assert r.failover_plan(S.OUTCOME_UNKNOWN) == r.Handoff("RECONCILE", ("OUTCOME_NOT_KNOWN",))
    assert r.transition(S.OUTCOME_UNKNOWN, S.RECONCILING) is S.RECONCILING
    with pytest.raises(r.TransitionError):
        r.transition(S.OUTCOME_UNKNOWN, S.ATTEMPT_RESERVED)  # no blind second attempt


def test_a_switch_before_dispatch_is_free_and_carrying_evidence_over_needs_four_approvals() -> None:
    S = r.AttemptState
    assert r.failover_plan(S.FAILED_BEFORE_DISPATCH).kind == "SAME_SESSION_SWITCH"
    assert r.failover_plan(S.CONFIRMED_NOT_PROCESSED, contract_allows_transfer=True, consent_covers_transfer=True, data_flow_approved=True, format_portable=True).kind == "EVIDENCE_TRANSFER"
    h = r.failover_plan(S.CONFIRMED_NOT_PROCESSED, contract_allows_transfer=True)
    assert h.kind == "RECAPTURE" and set(h.reasons) == {"CONSENT_DOES_NOT_COVER_TRANSFER", "DATA_FLOW_NOT_APPROVED", "FORMAT_NOT_PORTABLE"}  # never a false seamless promise


# --------------------------------------------------------------------------------------------------------------- coverage and claims


def cell(cid: str, country: str, spec: str, tier: str = "ENABLED", **over: Any) -> r.Cell:
    base: dict[str, Any] = {"cell_id": cid, "issuer_country": country, "specimen_id": spec, "method": "DOCUMENT_FACE", "tier": tier, "evidence_ref": "EV-1", "approved": True, "expires_at": LATER}
    return r.Cell(**{**base, **over})


def test_counts_are_deduplicated_and_never_include_unapproved_or_expired_cells_as_available() -> None:
    cells = [cell("1", "GB", "passport-2019"), cell("2", "GB", "passport-2019"), cell("3", "GB", "licence-2020"), cell("4", "DE", "id-2021"), cell("5", "FR", "id-2022", approved=False), cell("6", "ES", "id", expires_at=NOW - timedelta(days=1)), cell("7", "IT", "id", tier="LABORATORY", evidence_ref=None)]
    c = r.coverage_counts(cells, NOW)
    assert c["tiers"]["ENABLED"] == {"countries": 2, "specimens": 3} and c["unknown_cells"] == 1  # the unapproved ENABLED cell is UNKNOWN, not counted
    assert c["tiers"]["RESTRICTED"]["countries"] == 1 and c["tiers"]["LABORATORY"]["countries"] == 1  # expired evidence is restricted, a lab cell stays a lab cell
    assert r.cell_status(cell("x", "GB", "s", tier="MADE_UP"), NOW) == "UNKNOWN"
    assert r.coverage_counts(cells, NOW, method="NFC_CHIP")["tiers"]["ENABLED"]["countries"] == 0


def test_a_public_number_comes_only_from_released_cells_and_fails_when_one_lacks_evidence() -> None:
    good = [cell("1", "GB", "a"), cell("2", "DE", "b")]
    assert r.public_claim(good, NOW)["countries"] == 2
    with pytest.raises(ValueError, match="COUNTED_CELL_LACKS_APPROVED_EVIDENCE:CELL_NOT_PUBLISHABLE:3"):
        r.public_claim([*good, cell("3", "FR", "c", evidence_ref=None)], NOW)
    with pytest.raises(ValueError):
        r.public_claim([*good, cell("4", "IT", "d", tier="CONTRACTED")], NOW)  # a contract is not a launch


# ----------------------------------------------------------------------------------------------------------------- release governance


def test_a_release_needs_independent_approvals_of_the_exact_dependency_set() -> None:
    d = r.ReleaseDraft("rel-1", "maker", "HIGH", "sha-256:aa", {"tenant": "t1", "region": "uk", "method": "DOCUMENT_FACE"})
    sig = lambda who, role, dig="sha-256:aa": {"approver": who, "role": role, "dependency_digest": dig}  # noqa: E731
    full = [sig("p", "PRODUCT"), sig("s", "SECURITY"), sig("v", "PRIVACY"), sig("l", "LEGAL")]
    assert r.publish_problems(d, full, current_dependency_digest="sha-256:aa") == []
    assert r.publish_problems(d, [sig("maker", "PRODUCT"), *full[1:]], current_dependency_digest="sha-256:aa") == ["MAKER_CANNOT_APPROVE_OWN_RELEASE", "MISSING_APPROVAL:PRODUCT"]
    assert "ONE_PERSON_CANNOT_HOLD_TWO_ROLES" in r.publish_problems(d, [sig("p", "PRODUCT"), sig("p", "SECURITY"), *full[2:]], current_dependency_digest="sha-256:aa")
    stale = r.publish_problems(d, full, current_dependency_digest="sha-256:bb")  # a provider manifest changed after the approvals
    assert "APPROVAL_FOR_A_DIFFERENT_DEPENDENCY_SET" in stale and "DEPENDENCIES_CHANGED_SINCE_PREFLIGHT" in stale and "MISSING_APPROVAL:LEGAL" in stale
    low = r.ReleaseDraft("rel-2", "maker", "LOW", "sha-256:aa", {})
    assert r.publish_problems(low, full[:2], current_dependency_digest="sha-256:aa") == [] and r.required_approvers("LOW") < r.required_approvers("HIGH")


def test_a_session_keeps_the_route_version_it_started_with() -> None:
    assert r.pinned_version("v20", "v21") == "v20" and r.pinned_version(None, "v21") == "v21"  # a rollback or release never moves an in-flight session


# --------------------------------------------------------------------------------------------------------------------------- costs


def test_vendor_invoice_lines_are_matched_to_attempts_and_anything_odd_is_held_not_adjusted() -> None:
    attempts = [
        {"external_operation_id": "op1", "state": "FINALIZED", "expected_cost_minor": 100, "currency": "EUR"},
        {"external_operation_id": "op2", "state": "OUTCOME_UNKNOWN", "expected_cost_minor": 100, "currency": "EUR"},
        {"external_operation_id": "op3", "state": "FINALIZED", "expected_cost_minor": 100, "currency": "EUR"},
        {"external_operation_id": "op4", "state": "FINALIZED", "expected_cost_minor": 100, "currency": "EUR"},
    ]
    lines = [r.VendorLine("p", "op1", 100, "EUR"), r.VendorLine("p", "op1", 100, "EUR"), r.VendorLine("p", "op2", 100, "EUR"), r.VendorLine("p", "op3", 150, "EUR"), r.VendorLine("p", "op9", 100, "EUR")]
    out = r.reconcile_costs(attempts, lines)
    reasons = {(h["operation"], h["reason"]) for h in out["held"]}
    assert out["matched"] == 1 and out["adjusts_anything"] is False and out["attempts_without_vendor_line"] == ["op4"]
    assert reasons == {("op1", "DUPLICATE_VENDOR_LINE"), ("op2", "ATTEMPT_OUTCOME_UNKNOWN"), ("op3", "AMOUNT_DIFFERS_FROM_LEDGER"), ("op9", "VENDOR_LINE_WITHOUT_ATTEMPT")}


def test_observed_contractual_and_marketed_service_quality_are_three_different_fields() -> None:
    v = r.slo_view(None, 1500, 6000)
    assert v["observed_available"] is False and v["observed_p95_ms"] is None and v["vendor_claim_ms"] == 6000 and "vendor's claim" in v["note"]
    assert r.slo_view(900, 1500, 6000)["observed_p95_ms"] == 900


# ------------------------------------------------------------------------------------------------------ data flow, breaker, applicant


HOPS = [
    {"kind": "INGRESS", "party": "prov", "region": "uk", "data_class": "MEDIA"},
    {"kind": "STORAGE", "party": "prov", "region": "uk", "data_class": "MEDIA"},
    {"kind": "REMOTE_SUPPORT", "party": "prov-support", "region": "uk", "data_class": "METADATA"},
]
UK = frozenset({"uk"})


def test_a_data_flow_graph_is_checked_hop_by_hop_and_a_change_stops_the_route_until_legal_looks_again() -> None:
    digest = r.flow_digest(HOPS)
    assert r.flow_digest(list(reversed(HOPS))) == digest  # order never matters
    assert r.flow_problems(HOPS, UK, determination_flow_digest=digest) == []
    assert r.flow_problems(None, UK, determination_flow_digest=digest) == ["NO_DATA_FLOW_GRAPH"]
    assert r.flow_problems([], UK, determination_flow_digest=digest) == ["DATA_FLOW_EMPTY"]
    assert r.flow_problems(HOPS[:1], UK, determination_flow_digest=digest) == ["DATA_FLOW_MISSING_STORAGE"]  # a path nobody described cannot be approved
    abroad = [*HOPS, {"kind": "ANALYTICS", "party": "stats", "region": "us", "data_class": "DERIVED"}]
    assert r.flow_problems(abroad, UK, determination_flow_digest=r.flow_digest(abroad)) == ["DATA_FLOW_REGION_NOT_ALLOWED:ANALYTICS"]  # even telemetry and analytics hops count
    quiet = [*HOPS, {"kind": "TELEMETRY", "party": "metrics", "region": "uk", "data_class": "METADATA"}]  # a new party in an allowed region still needs a fresh determination
    assert r.flow_problems(quiet, UK, determination_flow_digest=digest) == ["DATA_FLOW_CHANGED_SINCE_DETERMINATION"]
    assert r.flow_problems(HOPS, UK, determination_flow_digest=None) == ["DATA_FLOW_CHANGED_SINCE_DETERMINATION"]
    bad = [{"kind": "WIRE", "party": "", "region": "uk", "data_class": "SECRETS"}]
    assert {"DATA_FLOW_HOP_KIND_UNKNOWN", "DATA_FLOW_DATA_CLASS_UNKNOWN", "DATA_FLOW_HOP_INCOMPLETE"} <= set(r.flow_shape_problems(bad))
    drift = r.flow_drift(HOPS, abroad)
    assert drift["new_regions"] == ["us"] and drift["added"] == ["ANALYTICS|stats|us|DERIVED"] and drift["removed"] == []


def test_a_provider_whose_data_flow_fails_is_not_routed_to_and_the_reason_is_named() -> None:
    bad = cand("flow", data_flow_problems=("DATA_FLOW_REGION_NOT_ALLOWED:REMOTE_SUPPORT",))
    d = r.resolve(inp(), [bad, cand("ok")])
    assert d.selected == "ok" and "DATA_FLOW_REGION_NOT_ALLOWED:REMOTE_SUPPORT" in reasons_of(d, "flow")
    ent = r.resolve(inp(), [cand("noent", entitlement_refusals=("NO_ENTITLEMENT",))])
    assert ent.selected is None and "ENTITLEMENT:NO_ENTITLEMENT" in ent.no_route_reasons


def test_the_breaker_opens_on_measured_failures_only_after_enough_traffic_and_lets_one_look_through_after_the_cooldown() -> None:
    def run(failures: int, ok: int, last_failure_ago: int = 10) -> list[tuple[datetime, bool]]:
        out = [(NOW - timedelta(seconds=last_failure_ago + i), False) for i in range(failures)]
        out += [(NOW - timedelta(seconds=200 + i), True) for i in range(ok)]
        return out

    assert r.breaker_state(run(5, 5), NOW) == "CLOSED"  # ten attempts is too few to open anything
    assert r.breaker_state(run(12, 18), NOW) == "OPEN"  # 40 percent of thirty
    assert r.breaker_state(run(3, 27), NOW) == "CLOSED"  # 10 percent is within tolerance
    assert r.breaker_state(run(12, 18, last_failure_ago=400), NOW) == "HALF_OPEN"  # quiet for the cooldown: look again
    assert r.breaker_state([(NOW - timedelta(hours=3), False)] * 40, NOW) == "CLOSED"  # old failures are outside the window
    assert r.risk_adjusted_cost_minor(cand("x")) > 0
    assert r.resolve(inp(), [cand("open", breaker="OPEN")]).no_route_reasons == ("BREAKER_OPEN",)
    assert r.resolve(inp(), [cand("half", breaker="HALF_OPEN")]).selected == "half"


def test_a_method_is_quarantined_for_quality_however_up_the_provider_is() -> None:
    assert r.quality_quarantined(30, 60) is True and r.quality_quarantined(45, 60) is False  # 50 percent against a 70 percent floor
    assert r.quality_quarantined(0, 10) is False  # a few attempts never quarantine anything
    d = r.resolve(inp(), [cand("q", quality_quarantined=True, health="UP")])
    assert d.selected is None and "QUALITY_QUARANTINED" in d.no_route_reasons


def test_a_provider_with_a_per_minute_cap_takes_no_more_than_that() -> None:
    assert r.throttled(5, 5) is True and r.throttled(4, 5) is False and r.throttled(999, None) is False


def test_the_applicant_is_never_told_they_failed_for_something_that_was_not_theirs_and_always_has_one_next_step() -> None:
    S = r.AttemptState
    states = [s.value for s in S]
    seen = {s: r.applicant_message(s) for s in states}
    for s, m in seen.items():
        assert m["counts_against_applicant"] is False and m["next"] and m["title"] and "failed" not in (m["title"] + m["body"]).lower() and "prov" not in m["body"].lower(), s
    assert seen["OUTCOME_UNKNOWN"]["next"] == "WAIT" and "should not start again" in seen["OUTCOME_UNKNOWN"]["body"]  # no second attempt by the applicant either
    assert seen["FAILED_BEFORE_DISPATCH"]["next"] == "RESUME" and seen["FAILED_BEFORE_DISPATCH"]["keeps_evidence"] is True
    assert seen["EXPLICIT_RECAPTURE_APPROVED"]["next"] == "RESTART_CAPTURE" and seen["EXPLICIT_RECAPTURE_APPROVED"]["keeps_evidence"] is False  # told plainly when photos must be retaken
    assert seen["NO_ROUTE"]["code"] == "NOT_AVAILABLE_HERE" and seen["REVIEW_REQUIRED"]["code"] == "A_PERSON_WILL_LOOK"
    assert seen["FINALIZED"]["code"] == "RESULT_OWNED_BY_VERIFIER"

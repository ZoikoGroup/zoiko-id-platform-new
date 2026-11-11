"""ZID-INT-002 section 06.1: the v2 status vocabulary can only restate or lower what a check decided."""

from __future__ import annotations

import pytest
from zid_errors import problem_document
from zid_platform.modules.credential.application import v2_view as vw
from zid_platform.modules.credential.domain.claims import ClaimType, Decision

POSITIVE = {vw.Outcome.VERIFIED, vw.Outcome.VERIFIED_LIMITED}


@pytest.mark.parametrize("decision", [d.value for d in Decision])
def test_only_a_verified_check_can_become_a_positive_outcome(decision: str) -> None:
    outcome, _ = vw.outcome_of({"decision": decision, "credential_status": "ACTIVE"})
    assert (outcome in POSITIVE) == (decision == "VERIFIED")


def test_a_source_that_could_not_be_asked_is_unavailable_never_failed() -> None:
    outcome, limits = vw.outcome_of(
        {"decision": "SOURCE_UNAVAILABLE", "credential_status": "UNKNOWN"}
    )
    assert outcome is vw.Outcome.UNAVAILABLE and "FAILED" not in outcome.value and limits


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("REVOKED", vw.Outcome.REVOKED),
        ("EXPIRED", vw.Outcome.EXPIRED),
        ("SUSPENDED", vw.Outcome.NOT_VERIFIED),
    ],
)
def test_a_changed_status_is_reported_as_what_it_is(status: str, expected: vw.Outcome) -> None:
    assert vw.outcome_of({"decision": "NOT_VERIFIED", "credential_status": status})[0] is expected


def test_limitations_make_a_verified_check_limited() -> None:
    out, limits = vw.outcome_of(
        {
            "decision": "VERIFIED",
            "credential_status": "ACTIVE",
            "limitations": ["activity_scope_restricted"],
        }
    )
    assert out is vw.Outcome.VERIFIED_LIMITED and limits == ["activity_scope_restricted"]


def test_review_and_unverifiable_ask_for_a_person_and_never_blame_the_holder() -> None:
    for d in ("REVIEW", "UNVERIFIABLE", "INDETERMINATE"):
        outcome, _ = vw.outcome_of({"decision": d, "credential_status": "UNKNOWN"})
        assert outcome is vw.Outcome.INDETERMINATE
        dim = vw.dimension_result({"decision": d}, ClaimType.PROFESSIONAL_LICENSE, check_id="c1")
        assert dim["human_review_required"] is True


def test_the_dimension_carries_the_section_06_1_fields() -> None:
    contract = {
        "decision": "VERIFIED", "credential_status": "ACTIVE", "assurance_tier": "PRIMARY_SOURCE_CONFIRMED",
        "source_id": "reg-1", "source_checked_at": "2026-11-10T12:00:00+00:00", "expires_at": "2026-12-10T00:00:00+00:00",
        "policy_ref": {"id": "US-CA-LIC", "version": 3},
    }  # fmt: skip
    d = vw.dimension_result(contract, ClaimType.PROFESSIONAL_LICENSE, check_id="abc")
    assert d["dimension"] == "professional_license" and d["claim_ref"] == "claim_abc"
    assert (
        d["evidence_manifest_ref"] == "manifest_abc" and d["policy_pack_version"] == "US-CA-LIC/3"
    )
    assert (
        d["valid_until"] == "2026-12-10T00:00:00+00:00"
        and d["checked_at"] == "2026-11-10T12:00:00+00:00"
    )


def test_the_envelope_has_every_section_02_1_field() -> None:
    r = vw.credential_response(
        {"decision": "VERIFIED", "policy_ref": {"id": "P", "version": 1}}, ClaimType.ACADEMIC_AWARD,
        check_id="x", request_id="req_1", correlation_id="cor_1",
    )  # fmt: skip
    for k in ("request_id", "correlation_id", "policy_pack_id", "policy_pack_version", "provenance_manifest_ref",
              "checked_at", "valid_until", "dimensions"):  # fmt: skip
        assert k in r
    assert r["policy_pack_version"] == "1" and r["credential_ref"] == "cred_x"


@pytest.mark.parametrize(
    ("eligibility", "engagement", "expected"),
    [
        ("ELIGIBLE", "READY", vw.EngagementEligibility.ELIGIBLE),
        ("ELIGIBLE", "HOLD", vw.EngagementEligibility.REVIEW_REQUIRED),
        ("ELIGIBLE", "BLOCKED", vw.EngagementEligibility.NOT_ELIGIBLE),
        ("BLOCKED", None, vw.EngagementEligibility.NOT_ELIGIBLE),
        ("REVIEW", "READY", vw.EngagementEligibility.REVIEW_REQUIRED),
        ("NOT_EVALUATED", None, vw.EngagementEligibility.UNKNOWN),
    ],
)
def test_engagement_is_eligible_only_when_everything_says_so(
    eligibility: str, engagement: str | None, expected: vw.EngagementEligibility
) -> None:
    assert vw.engagement_eligibility(eligibility, engagement) is expected


def test_the_two_vocabularies_never_overlap() -> None:
    assert {o.value for o in vw.Outcome}.isdisjoint({e.value for e in vw.EngagementEligibility})


def test_a_v2_claim_keeps_issuer_and_award_apart_and_the_callers_own_field_wins() -> None:
    merged = vw.claim_from_v2(
        ClaimType.ACADEMIC_AWARD,
        {"name": "State University"},
        {"program": "BSc", "award_status": "CONFERRED"},
    )
    assert merged["institution"] == "State University"
    own = vw.claim_from_v2(
        ClaimType.ACADEMIC_AWARD,
        {"name": "Other"},
        {"institution": "Mine", "program": "p", "award_status": "a"},
    )
    assert own["institution"] == "Mine"


@pytest.mark.parametrize(
    ("code", "status", "public"),
    [
        ("IDEMPOTENCY_KEY_REUSED", 409, "idempotency_conflict"),
        ("VALIDATION_FAILED", 422, "invalid_request"),
        (
            "PACK_UNAVAILABLE",
            409,
            "unsupported_market",
        ),  # always 422, whatever the internal status was
        ("DEPENDENCY_UNAVAILABLE", 503, "provider_unavailable"),
    ],
)
def test_problem_documents_follow_rfc_9457_and_section_14(
    code: str, status: int, public: str
) -> None:
    st, doc = problem_document(code, status, "detail", trace_id="req_9")
    assert doc["code"] == public and doc["trace_id"] == "req_9" and doc["status"] == st
    assert (
        doc["type"].startswith("https://api.zoikoid.com/problems/")
        and "_" not in doc["type"].rsplit("/", 1)[1]
    )
    assert (st == 422) if public == "unsupported_market" else st == status

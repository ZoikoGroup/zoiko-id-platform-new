"""The face-match route (CR-129): an owner-acknowledged automatic route that approves on a live face against the document's own photograph.

It runs no liveness test and does not check that the document is genuine, so what it can say is narrow and what it leaves is the lowest grade. These tests keep it that way: a match is
the only thing that approves, a failure to find a face is a retake and never a rejection, nothing upgrades the claim, and the policy cannot describe the route by accident.
"""

from __future__ import annotations

import pytest
from zid_platform.modules.verification.application.idv_claims import FACE_MATCH, grade_of
from zid_platform.modules.verification.domain.idv_decision import (
    AlternateRoute,
    Decision,
    FaceMatch,
    Liveness,
    Optical,
    Policy,
    Requirement,
    Signals,
    assertions,
    decide,
    face_signal,
)
from zid_platform.modules.verification.domain.idv_policy import PolicyError, parse

FACE = AlternateRoute("WEB_FACE", True, 0, True, True, 0.4)
POLICY = Policy(
    version="t1",
    chip=Requirement.NOT_USED,
    face=Requirement.NOT_USED,
    clone_check=Requirement.NOT_USED,
    capture_integrity=Requirement.NOT_USED,
    expired_document="REFUSE",
    alternates=(FACE,),
)


def _signals(**over) -> Signals:
    base = dict(route="WEB_FACE", optical=Optical.PASS, face=FaceMatch.MATCH, reference_source="OPTICAL", web_only_capture=True)
    base.update(over)
    return Signals(**base)


def test_a_match_with_a_readable_document_approves_without_a_machine_readable_zone_or_typed_details() -> None:
    r = decide(_signals(), POLICY)
    assert r.decision is Decision.APPROVED, r.reasons  # an Aadhaar card has no machine-readable lines; none are needed


@pytest.mark.parametrize(
    ("face", "reason"),
    [
        (FaceMatch.NON_MATCH, "FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO"),
        (FaceMatch.INCONCLUSIVE, "FACE_NOT_FOUND_OR_UNCLEAR"),
        (FaceMatch.NOT_RUN, "FACE_MATCH_NOT_RUN"),
    ],
)
def test_anything_but_a_match_asks_for_a_retake_and_is_never_an_automatic_rejection(face: FaceMatch, reason: str) -> None:
    r = decide(_signals(face=face), POLICY)
    assert r.decision is Decision.INDETERMINATE and reason in r.reasons


def test_a_match_against_anything_but_the_document_photograph_does_not_approve() -> None:
    r = decide(_signals(reference_source="NONE"), POLICY)
    assert r.decision is Decision.INDETERMINATE


def test_an_unreadable_document_picture_still_asks_for_a_retake() -> None:
    r = decide(_signals(optical=Optical.REVIEW), POLICY)
    assert r.decision is Decision.INDETERMINATE and "OPTICAL_REVIEW" in r.reasons


def test_an_expired_document_is_still_refused_when_the_date_is_known() -> None:
    assert decide(_signals(document_expired=True), POLICY).decision is Decision.REJECTED


def test_the_claim_says_what_it_is_and_what_it_is_not() -> None:
    a = assertions(_signals())
    assert "FACE_MATCHES_DOCUMENT_PHOTO_NO_LIVENESS" in a
    assert "LIVE_PRESENCE" not in a and not any(x.startswith("HOLDER_MATCHES") for x in a)  # no holder claim: the photograph was never authenticated
    assert grade_of(a, automatic=True) == FACE_MATCH == "FACE_MATCH_NO_LIVENESS"


def test_a_real_liveness_pass_is_not_confused_with_the_face_route() -> None:
    a = assertions(_signals(liveness=Liveness.PASS))
    assert "FACE_MATCHES_DOCUMENT_PHOTO_NO_LIVENESS" not in a and "HOLDER_MATCHES_DOCUMENT_PORTRAIT" in a


def test_an_injected_capture_does_not_earn_the_claim() -> None:
    assert "FACE_MATCHES_DOCUMENT_PHOTO_NO_LIVENESS" not in assertions(_signals(injection_risk=True))


def test_the_signal_follows_the_threshold_and_no_similarity_is_a_retake() -> None:
    assert face_signal(0.405, 0.363) is FaceMatch.MATCH
    assert face_signal(0.363, 0.363) is FaceMatch.MATCH
    assert face_signal(0.36, 0.363) is FaceMatch.NON_MATCH
    assert face_signal(None, 0.363) is FaceMatch.INCONCLUSIVE


# ---- the policy
def _doc(alt: dict) -> dict:
    return {"policy_version": "p", "purpose": "KYC", "alternates": [alt]}


def test_the_policy_reads_a_face_route() -> None:
    rules = parse({"markets": {"GB": _doc({"route_id": "WEB_FACE", "may_approve": True, "automatic": True, "face_match": True, "face_threshold": 0.45})}})["GB"]
    alt = rules.policy.alternates[0]
    assert alt.face_match and alt.face_threshold == 0.45 and alt.automatic and alt.reviewers_needed == 0


def test_a_face_route_with_no_threshold_gets_a_cautious_default() -> None:
    rules = parse({"markets": {"GB": _doc({"route_id": "WEB_FACE", "may_approve": True, "automatic": True, "face_match": True})}})["GB"]
    assert rules.policy.alternates[0].face_threshold == 0.5


@pytest.mark.parametrize(
    "alt",
    [
        {"route_id": "X", "may_approve": True, "face_match": True},  # a face route is automatic
        {"route_id": "X", "may_approve": True, "automatic": True, "face_threshold": 0.4},  # a threshold belongs to a face route
        {"route_id": "X", "may_approve": True, "automatic": True, "face_match": True, "face_threshold": 0.1},
        {"route_id": "X", "may_approve": True, "automatic": True, "face_match": True, "face_threshold": 1.0},
        {"route_id": "X", "may_approve": True, "automatic": True, "face_match": True, "face_threshold": "high"},
        {"route_id": "X", "may_approve": True, "automatic": True, "face_match": "yes"},
        {"route_id": "X", "may_approve": True, "reviewers_needed": 2, "face_match": True},
    ],
)
def test_the_policy_refuses_a_face_route_that_is_not_described_properly(alt: dict) -> None:
    with pytest.raises(PolicyError):
        parse({"markets": {"GB": _doc(alt)}})

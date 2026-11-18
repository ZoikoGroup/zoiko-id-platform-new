"""WF06 result dimensions (CR-113): coverage, match, disposition and review are four separate answers (QA-012, QA-013, QA-018, QA-021, QA-022, QA-024, QA-029, QA-030, QA-045)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.screening.domain import coverage as c

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
FRESH = NOW - timedelta(hours=3)
POLICY = c.CoveragePolicy(frozenset({c.Klass.SANCTIONS}), timedelta(hours=48))
FULL = c.CoveragePolicy(
    frozenset({c.Klass.SANCTIONS, c.Klass.PEP_RCA, c.Klass.ADVERSE_MEDIA}), timedelta(hours=48)
)


def sanctions(
    source="OFAC_SDN",
    *,
    candidates=0,
    available=True,
    validated=FRESH,
    tier=c.Tier.OFFICIAL,
    snapshot="sha256:aa",
):
    return c.SourceResult(
        source,
        c.Klass.SANCTIONS,
        tier,
        snapshot if available else None,
        NOW if available else None,
        available,
        candidates,
        validated,
    )


def licensed(klass, *, candidates=0, validated=FRESH):
    return c.SourceResult(
        f"vendor-{klass.value}",
        klass,
        c.Tier.LICENSED,
        "sha256:bb",
        NOW,
        True,
        candidates,
        validated,
    )


def model(results, policy=POLICY, **kw):
    return c.result_model(case_id="scr_1", results=results, policy=policy, now=NOW, **kw)


def test_an_official_sanctions_check_alone_is_official_only_and_other_classes_read_not_configured() -> (
    None
):
    m = model([sanctions()])
    assert m["coverage_status"] == "OFFICIAL_ONLY"
    assert m["pep_rca"] == {
        "coverage": "NOT_CONFIGURED",
        "result": None,
    }  # QA-021: never PASS, never "no PEP"
    assert m["adverse_media"] == {"coverage": "NOT_CONFIGURED", "result": None}  # QA-024
    assert m["sanctions"]["result"] == "NO_CANDIDATE"  # only for the named source and snapshot
    assert m["source_results"][0]["snapshot"] == "sha256:aa"


def test_the_example_in_the_document_does_not_clear_while_pep_and_media_are_unconfigured() -> None:
    m = model([sanctions()], FULL)
    assert m["coverage_status"] == "PARTIAL"
    decision = m["decision"]
    assert decision["status"] == "UNRESOLVED"
    assert "UNSCREENED_REQUIRED_CLASSES" in decision["reason_codes"]
    assert {"NOT_SCREENED:PEP_RCA", "NOT_SCREENED:ADVERSE_MEDIA"} <= set(decision["reason_codes"])


def test_a_case_clears_only_when_every_required_class_was_checked_fresh() -> None:
    m = model([sanctions()])
    assert m["decision"]["status"] == "CLEARED_FOR_PURPOSE"
    assert m["decision"]["reason_codes"] == ["NO_CANDIDATE_IN_CHECKED_SOURCES"]
    full = model([sanctions(), licensed(c.Klass.PEP_RCA), licensed(c.Klass.ADVERSE_MEDIA)], FULL)
    assert full["decision"]["status"] == "CLEARED_FOR_PURPOSE"


def test_a_stale_or_unavailable_required_source_never_clears() -> None:
    old = NOW - timedelta(hours=49)
    stale = model([sanctions(validated=old)])
    assert (
        stale["coverage_status"] == "SOURCE_STALE" and stale["decision"]["status"] == "UNRESOLVED"
    )
    assert stale["decision"]["reason_codes"] == ["REQUIRED_SOURCE_STALE"]
    down = model([sanctions(available=False)])
    assert (
        down["coverage_status"] == "UNAVAILABLE" and down["decision"]["status"] == "UNRESOLVED"
    )  # QA-045
    assert down["sanctions"]["result"] is None  # a fabricated clean outcome is not possible
    never = model([sanctions(validated=None)])
    assert never["coverage_status"] == "SOURCE_STALE"  # a corpus nobody released is not current


def test_no_configured_source_at_all_is_not_configured_and_never_clear() -> None:
    m = model([])
    assert m["coverage_status"] == "NOT_CONFIGURED" and m["decision"]["status"] == "UNRESOLVED"
    assert (
        model([], c.CoveragePolicy(frozenset(), timedelta(hours=1)))["decision"]["status"]
        != "CLEARED_FOR_PURPOSE"
    )


def test_a_candidate_is_a_hold_and_a_name_alone_is_never_a_confirmed_match() -> None:
    m = model([sanctions(candidates=1)])
    assert m["match_status"] == "POSSIBLE_MATCH"  # QA-012, QA-013
    assert m["decision"]["status"] == "HOLD_PENDING_REVIEW"
    assert m["review"] == "AUTOMATED"


def test_missing_identifiers_make_a_candidate_unresolved_not_a_false_positive() -> None:
    m = model([sanctions(candidates=1)], insufficient_identifiers=True)
    assert (
        m["match_status"] == "INSUFFICIENT_INFORMATION" and m["decision"]["status"] == "UNRESOLVED"
    )  # QA-018
    clear = model([sanctions()], insufficient_identifiers=True)
    assert clear["match_status"] == "NO_CANDIDATE"  # no candidate: nothing to be insufficient about


def test_a_reviewers_finding_stands_and_a_confirmed_match_is_restricted_until_legal_signs() -> None:
    fp = model(
        [sanctions(candidates=1)], reviewed_match=c.Match.FALSE_POSITIVE, approvers=["a", "b"]
    )
    assert fp["decision"]["status"] == "CLEARED_FOR_PURPOSE" and fp["review"] == "DUAL_REVIEWED"
    hit = model([sanctions(candidates=1)], reviewed_match=c.Match.CONFIRMED_MATCH, approvers=["a"])
    assert hit["decision"]["status"] == "RESTRICTED" and hit["review"] == "REVIEWER_CONFIRMED"
    signed = model(
        [sanctions(candidates=1)],
        reviewed_match=c.Match.CONFIRMED_MATCH,
        approvers=["a", "b"],
        legal_signed=True,
    )
    assert signed["decision"]["status"] == "PROHIBITED" and signed["review"] == "LEGAL_SIGNED"


def test_a_false_positive_does_not_clear_a_case_that_is_otherwise_not_screened() -> None:
    m = model(
        [sanctions(candidates=1)], FULL, reviewed_match=c.Match.FALSE_POSITIVE, approvers=["a", "b"]
    )
    assert m["decision"]["status"] == "UNRESOLVED"  # PEP and media were never run


def test_pep_and_media_candidates_route_to_edd_never_to_a_refusal() -> None:
    pep = model(
        [sanctions(), licensed(c.Klass.PEP_RCA, candidates=1), licensed(c.Klass.ADVERSE_MEDIA)],
        FULL,
    )
    assert (
        pep["decision"]["status"] == "EDD_REQUIRED"
    )  # a licensed hit, found from the results alone
    assert pep["decision"]["reason_codes"] == ["PEP_OR_RCA_CANDIDATE"]
    media = model(
        [sanctions(), licensed(c.Klass.PEP_RCA), licensed(c.Klass.ADVERSE_MEDIA, candidates=2)],
        FULL,
    )
    assert media["decision"]["status"] == "EDD_REQUIRED"
    assert media["decision"]["reason_codes"] == ["ADVERSE_MEDIA_RELEVANCE_REVIEW"]
    both = model(
        [
            sanctions(),
            licensed(c.Klass.PEP_RCA, candidates=1),
            licensed(c.Klass.ADVERSE_MEDIA, candidates=1),
        ],
        FULL,
    )
    assert both["decision"]["reason_codes"] == [
        "PEP_OR_RCA_CANDIDATE",
        "ADVERSE_MEDIA_RELEVANCE_REVIEW",
    ]
    for m in (pep, media, both):
        assert (
            m["match_status"] == "NO_CANDIDATE"
        )  # a PEP or media candidate is not a sanctions candidate
        assert m["decision"]["status"] not in ("RESTRICTED", "PROHIBITED")


def test_an_expired_review_reads_expired_whatever_was_decided() -> None:
    assert (
        c.review_state(["a", "b"], legal_signed=True, expired=True, human=True) is c.Review.EXPIRED
    )


def test_the_customer_summary_never_says_aml_approved_clean_or_no_risk() -> None:
    now = NOW
    for results, policy, kw in (
        ([sanctions()], POLICY, {}),
        ([sanctions(candidates=1)], POLICY, {}),
        (
            [sanctions(candidates=1)],
            POLICY,
            {
                "reviewed_match": c.Match.CONFIRMED_MATCH,
                "legal_signed": True,
                "approvers": ["a", "b"],
            },
        ),
        ([], POLICY, {}),
        ([sanctions(available=False)], POLICY, {}),
        ([sanctions()], FULL, {}),
    ):
        summary = c.customer_summary(model(results, policy, **kw), checked_at=now)
        assert c.unsafe_wording(summary) == [], summary
    clear = c.customer_summary(model([sanctions()]), checked_at=now)
    assert clear["finding"] == "No candidate was found in the checked sources"
    assert clear["pep_rca"] == "Not enabled" and clear["adverse_media"] == "Not enabled"
    assert "says nothing about the person or company beyond that" in str(clear["note"])


def test_a_restricted_or_prohibited_case_reads_like_a_hold_to_the_customer() -> None:
    hold = c.customer_summary(model([sanctions(candidates=1)]), checked_at=NOW)
    restricted = c.customer_summary(
        model([sanctions(candidates=1)], reviewed_match=c.Match.CONFIRMED_MATCH, approvers=["a"]),
        checked_at=NOW,
    )
    assert restricted["finding"] == "Restricted for this use"
    assert "list" not in str(restricted).casefold() or "lists" in str(restricted).casefold()
    assert "OFAC" not in str(restricted) and "OFAC" not in str(hold)


@pytest.mark.parametrize("phrase", ["AML approved", "clean", "no risk", "PEP"])
def test_the_wording_guard_catches_a_forbidden_phrase(phrase: str) -> None:
    assert c.unsafe_wording({"finding": f"This is {phrase}"}) != []

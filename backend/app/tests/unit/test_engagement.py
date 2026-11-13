"""ZID-RCG-003 section 10.1: the Zoikorum engagement decision keeps qualification and practice eligibility apart."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.credential.domain import engagement as eg

NOW = datetime(2026, 11, 13, 9, tzinfo=UTC)
ENG = eg.Engagement(
    "pro-1", "advisor", ("legal.advice",), "US", "US-NY", "lawyer", NOW, NOW + timedelta(days=90)
)
PACK = eg.Pack(True, "APPLICABLE", "us-ny-law:v1", ("license", "insurance"), 30)


def _t(check: str, outcome: str | None) -> eg.Task:
    return eg.Task(check, outcome, f"cred_{check}")


def test_the_mandatory_counterexample_genuine_degree_but_no_practice_right() -> None:
    d = eg.decide(
        ENG,
        [_t("degree", "VERIFIED"), _t("license", "NOT_VERIFIED")],
        PACK,
        case_expires_at=NOW + timedelta(days=60),
        now=NOW,
    )
    assert d.qualification == "QUALIFICATION_VERIFIED"
    assert d.practice_eligibility == "PRACTICE_ELIGIBILITY_NOT_ESTABLISHED"
    assert "LICENSE_NOT_VERIFIED" in d.reasons and "EVIDENCE_MISSING:insurance" in d.reasons
    assert "VERIFIED" not in (d.qualification, d.practice_eligibility)  # never a composite VERIFIED


def test_practice_is_established_only_with_a_license_an_approved_pack_and_its_evidence_minimum() -> (
    None
):
    tasks = [
        _t("degree", "VERIFIED"),
        _t("license", "VERIFIED"),
        _t("insurance", "VERIFIED_LIMITED"),
    ]
    d = eg.decide(ENG, tasks, PACK, case_expires_at=NOW + timedelta(days=60), now=NOW)
    assert d.practice_eligibility == "PRACTICE_ELIGIBILITY_ESTABLISHED" and d.reasons == ()
    assert d.expires_at == NOW + timedelta(
        days=30
    )  # the pack's validity is shorter than the case's
    assert d.refresh_by == NOW + timedelta(days=29)
    assert set(d.evidence_refs) == {"cred_degree", "cred_license", "cred_insurance"}

    no_pack = eg.Pack(False, "NO_APPROVED_PACK")
    d = eg.decide(ENG, tasks, no_pack, case_expires_at=NOW + timedelta(days=60), now=NOW)
    assert d.practice_eligibility == "PRACTICE_ELIGIBILITY_NOT_ESTABLISHED"
    assert "NO_APPROVED_PACK:NO_APPROVED_PACK" in d.reasons
    assert d.expires_at == NOW + timedelta(days=60)


def test_every_check_keeps_its_own_result_and_only_positive_ones_are_cited() -> None:
    tasks = [
        _t("identity", "VERIFIED"),
        _t("license", "PENDING"),
        _t("employment", "INDETERMINATE"),
    ]
    d = eg.decide(ENG, tasks, PACK, case_expires_at=NOW + timedelta(days=5), now=NOW)
    assert d.results == {
        "identity": "VERIFIED",
        "license": "PENDING",
        "employment": "INDETERMINATE",
    }
    assert d.evidence_refs == ("cred_identity",)
    assert d.qualification == "QUALIFICATION_NOT_ESTABLISHED"


def test_an_ended_engagement_period_is_not_eligible() -> None:
    ended = eg.Engagement(
        "pro-1",
        "advisor",
        (),
        "US",
        None,
        "lawyer",
        NOW - timedelta(days=10),
        NOW - timedelta(days=1),
    )
    tasks = [_t("license", "VERIFIED"), _t("insurance", "VERIFIED")]
    d = eg.decide(ended, tasks, PACK, case_expires_at=NOW + timedelta(days=5), now=NOW)
    assert "ENGAGEMENT_PERIOD_ENDED" in d.reasons

"""WF-05 proof of address review: a person decides, an association needs a second person (A07, A10, §21; CR-115). Synthetic only."""

from __future__ import annotations

import pytest
from zid_platform.modules.verification.domain import address as ad
from zid_platform.modules.verification.domain import address_review as ar
from zid_platform.modules.verification.domain.assertions import Assertion
from zid_platform.modules.verification.domain.check_cases import Verdict


def assessment(support: ad.Support, *codes: str) -> ad.Assessment:
    return ad.Assessment(support, tuple(ad.Finding(c) for c in codes), None, "")


def maker(
    a: ad.Assessment,
    finding: ar.Finding,
    *reasons: str,
    status: ar.Status = ar.Status.AWAITING_REVIEW,
) -> ar.Result:
    return ar.maker(a, finding, tuple(reasons), status=status, policy_status="SAMPLE")


def test_a_confirmed_document_is_reviewed_at_once_and_waits_for_a_checker():
    r = maker(assessment(ad.Support.SUPPORTS), ar.Finding.CONFIRMED)
    assert (r.status, r.assertion, r.verdict) == (
        ar.Status.AWAITING_CHECKER,
        Assertion.ADDRESS_DOCUMENT_REVIEWED,
        Verdict.PASS,
    )


def test_the_rules_never_decide_but_a_document_that_does_not_support_cannot_be_confirmed():
    for a, code in (
        (assessment(ad.Support.DOES_NOT_SUPPORT, "DOCUMENT_TOO_OLD"), "DOCUMENT_DOES_NOT_SUPPORT"),
        (assessment(ad.Support.REVIEW, "NO_POLICY_FOR_MARKET"), "NO_POLICY_FOR_MARKET"),
    ):
        with pytest.raises(ar.ReviewError) as e:
            maker(a, ar.Finding.CONFIRMED)
        assert e.value.code == code
    assert (
        maker(assessment(ad.Support.REVIEW, "NAME_PARTIAL_MATCH"), ar.Finding.CONFIRMED).verdict
        is Verdict.PASS
    )  # a person resolved it


def test_other_findings_need_a_reason_and_never_say_fraud():
    a = assessment(ad.Support.REVIEW)
    assert maker(a, ar.Finding.NOT_SUPPORTED, "ADDRESS_MISMATCH").verdict is Verdict.FAIL
    assert maker(a, ar.Finding.REQUIRES_MORE_EVIDENCE, "DOCUMENT_UNCLEAR").verdict is Verdict.REVIEW
    for reasons, code in (
        ((), "REASON_REQUIRED"),
        (("SUSPECTED_FRAUD",), "REASON_CODE_NOT_ALLOWED"),
        (("bad code",), "REASON_CODE_NOT_ALLOWED"),
    ):
        with pytest.raises(ar.ReviewError) as e:
            maker(a, ar.Finding.NOT_SUPPORTED, *reasons)
        assert e.value.code == code
    with pytest.raises(ar.ReviewError):
        maker(a, ar.Finding.CONFIRMED, status=ar.Status.DONE)


def test_the_checker_is_a_different_person_and_only_agreement_confirms_the_association():
    with pytest.raises(ar.ReviewError) as e:
        ar.checker(
            status=ar.Status.AWAITING_CHECKER,
            maker_by="ana",
            checker_by="ana",
            agrees=True,
            reasons=(),
        )
    assert e.value.code == "CHECKER_IS_MAKER"
    yes = ar.checker(
        status=ar.Status.AWAITING_CHECKER, maker_by="ana", checker_by="ben", agrees=True, reasons=()
    )
    assert (yes.assertion, yes.verdict, yes.status) == (
        Assertion.ADDRESS_ASSOCIATION_CONFIRMED,
        Verdict.PASS,
        ar.Status.DONE,
    )
    no = ar.checker(
        status=ar.Status.AWAITING_CHECKER,
        maker_by="ana",
        checker_by="ben",
        agrees=False,
        reasons=("NAME_UNCLEAR",),
    )
    assert no.verdict is Verdict.REVIEW
    with pytest.raises(ar.ReviewError):
        ar.checker(status=ar.Status.DONE, maker_by="ana", checker_by="ben", agrees=True, reasons=())

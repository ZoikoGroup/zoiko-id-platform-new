"""A company's WF-05 verification policy (A11; CR-118). Synthetic only."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain import check_policy as cp

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
PLATFORM = {"UTILITY_BILL": 90, "BANK_STATEMENT": 90}
EMAIL, DOC = cm.CheckMethod.EMAIL_CHALLENGE, cm.CheckMethod.ADDRESS_DOCUMENT


def draft(**over: object) -> cp.Policy:
    base: dict[str, object] = {
        "policy_id": "p-1", "market": "gb", "purpose": "ONBOARDING", "required": [EMAIL], "document_types": {"UTILITY_BILL": 60},
        "platform_types": PLATFORM, "author": "ana", "now": NOW,
    }  # fmt: skip
    base.update(over)
    return cp.draft(**base)  # type: ignore[arg-type]


def test_a_policy_only_narrows_the_platform():
    assert draft().market == "GB"
    for over, code in (
        ({"document_types": {"MOBILE_BILL": 30}}, "DOCUMENT_TYPE_NOT_ACCEPTED_BY_PLATFORM"),
        ({"document_types": {"UTILITY_BILL": 120}}, "DOCUMENT_AGE_ABOVE_PLATFORM"),
        ({"platform_types": None}, "DOCUMENT_TYPE_NOT_ACCEPTED_BY_PLATFORM"),
        ({"required": [EMAIL, EMAIL]}, "METHODS_DUPLICATED"),
        ({"market": "GBR"}, "MARKET_INVALID"),
    ):
        with pytest.raises(cp.PolicyError) as e:
            draft(**over)
        assert e.value.code == code


def test_the_author_submits_and_someone_else_decides():
    p = cp.submit(draft(), by="ana")
    with pytest.raises(cp.PolicyError) as e:
        cp.decide(p, approve=True, by="ana", reason="approving my own policy", now=NOW)
    assert e.value.code == "APPROVER_IS_AUTHOR"
    with pytest.raises(cp.PolicyError):
        cp.submit(draft(), by="ben")
    ok = cp.decide(p, approve=True, by="ben", reason="checked against the market rules", now=NOW)
    assert ok.status is cp.Status.APPROVED and ok.approver == "ben"
    with pytest.raises(cp.PolicyError):
        cp.decide(ok, approve=True, by="ben", reason="approve it a second time", now=NOW)


def test_only_an_approved_policy_has_effect():
    pending = cp.submit(draft(required=[EMAIL, DOC]), by="ana")
    assert cp.requested_with_policy([EMAIL], pending) == [EMAIL]
    assert cp.effective_types(PLATFORM, pending) == PLATFORM
    approved = cp.decide(
        pending, approve=True, by="ben", reason="checked against the market rules", now=NOW
    )
    assert cp.requested_with_policy([EMAIL], approved) == [EMAIL, DOC]
    assert cp.effective_types(PLATFORM, approved) == {"UTILITY_BILL": 60}
    assert cp.effective_types(PLATFORM, None) == PLATFORM

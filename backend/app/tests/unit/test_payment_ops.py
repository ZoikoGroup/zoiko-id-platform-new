"""Pure rules of refunds, disputes and reconciliation (ZID-UX-COM-009 F03/F04; WF09-OP-05..10, CL04-C15)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from zid_platform.modules.commercial.domain import payment_ops as ops
from zid_platform.modules.commercial.domain.payment_ops import DisputeState as D
from zid_platform.modules.commercial.domain.payment_ops import Item, Leg, MatchState, OpsError
from zid_platform.modules.commercial.domain.payment_ops import RefundState as R


def _ok(**kw):
    base = {
        "amount_minor": 100,
        "refundable": 500,
        "disputed": False,
        "category": "GOODWILL",
        "evidence_ref": None,
        "captured": True,
    }
    ops.check_refund_request(**{**base, **kw})


def test_refund_limited_to_overpayment_and_prior_refunds() -> None:
    assert ops.overpayment_minor(paid_minor=1000, gross_minor=800, credited_minor=0) == 200
    assert (
        ops.overpayment_minor(paid_minor=1000, gross_minor=800, credited_minor=300) == 500
    )  # a credit note lowers what is owed
    assert ops.overpayment_minor(paid_minor=500, gross_minor=800, credited_minor=0) == 0
    assert (
        ops.refundable_minor(paid_minor=1000, gross_minor=800, credited_minor=0, refunded_minor=150)
        == 50
    )
    assert (
        ops.refundable_minor(paid_minor=1000, gross_minor=800, credited_minor=0, refunded_minor=999)
        == 0
    )


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"amount_minor": 600}, "REFUND_EXCEEDS_OVERPAYMENT"),
        ({"amount_minor": 0}, "AMOUNT_INVALID"),
        ({"disputed": True}, "REFUND_BLOCKED_BY_DISPUTE"),
        ({"captured": False}, "PAYMENT_NOT_CAPTURED"),
        ({"category": "CORRECTION"}, "EVIDENCE_REQUIRED"),
        ({"category": "NOPE"}, "CATEGORY_INVALID"),
    ],
)
def test_refund_request_refusals(kw, code) -> None:
    with pytest.raises(OpsError) as e:
        _ok(**kw)
    assert e.value.code == code


def test_refund_request_accepts_evidence_and_goodwill() -> None:
    _ok()
    _ok(category="CORRECTION", evidence_ref="CASE-1")


@pytest.mark.parametrize(
    ("status", "amount", "state"),
    [
        ("succeeded", 100, R.SUCCEEDED),
        ("succeeded", 40, R.PARTIAL),
        ("succeeded", 150, R.UNKNOWN),
        ("succeeded", None, R.UNKNOWN),
        ("pending", None, R.PROCESSOR_PENDING),
        ("failed", None, R.FAILED),
        ("weird", None, R.UNKNOWN),
        (None, None, R.UNKNOWN),
    ],
)
def test_provider_outcome_is_never_a_guess(status, amount, state) -> None:
    assert ops.provider_refund_outcome(status, amount, 100)[0] is state


def test_resend_only_after_provider_holds_none() -> None:
    assert not ops.may_send_again(R.UNKNOWN, provider_holds_none=False)
    assert ops.may_send_again(R.UNKNOWN, provider_holds_none=True)
    assert not ops.may_send_again(R.SUCCEEDED, provider_holds_none=True)


def test_refund_transitions() -> None:
    assert ops.refund_transition(R.REQUESTED, R.APPROVED) is R.APPROVED
    with pytest.raises(OpsError):
        ops.refund_transition(R.REQUESTED, R.SUCCEEDED)
    with pytest.raises(OpsError):
        ops.refund_transition(R.SUCCEEDED, R.FAILED)


def test_refund_words_never_overclaim() -> None:
    assert set(ops.REFUND_WORDS) == set(R)
    assert "completed" not in ops.REFUND_WORDS[R.SUCCEEDED][0].lower()


def test_dispute_close_comes_only_from_the_processor() -> None:
    assert ops.provider_dispute_close("won", accepted_by_us=False) is D.CLOSED_WON
    assert ops.provider_dispute_close("lost", accepted_by_us=False) is D.CLOSED_LOST
    assert ops.provider_dispute_close("lost", accepted_by_us=True) is D.CLOSED_ACCEPTED
    assert ops.provider_dispute_close("needs_response", accepted_by_us=False) is None
    for closed in ops.DISPUTE_FINAL:
        with pytest.raises(OpsError):
            ops.dispute_transition(D.OPEN, closed)  # a person cannot close
    with pytest.raises(OpsError):
        ops.dispute_transition(D.SUBMISSION_RECORDED, D.CLOSED_WON)


def test_submission_recorded_is_not_verified() -> None:
    assert ops.dispute_transition(D.CONTESTING, D.SUBMISSION_RECORDED) is D.SUBMISSION_RECORDED
    with pytest.raises(OpsError):
        ops.dispute_transition(D.CONTESTING, D.SUBMISSION_VERIFIED)


def test_deadline_is_the_processors_and_never_invented() -> None:
    now = datetime(2026, 11, 4, tzinfo=UTC)
    assert ops.deadline_view(None, now)["status"] == "NO_DEADLINE_FROM_PROCESSOR"
    assert ops.deadline_view(now + timedelta(days=1), now)["status"] == "NEAR"
    assert ops.deadline_view(now + timedelta(days=10), now)["status"] == "OK"
    assert ops.deadline_view(now - timedelta(hours=1), now)["status"] == "PASSED"


def test_evidence_checklist_needs_every_item() -> None:
    full = {k: {"ref": "r"} for k in ops.EVIDENCE_ITEMS}
    assert ops.checklist_complete(full)
    full.pop("usage_proof")
    assert not ops.checklist_complete(full)


ITEM = Item("PAYMENT", "i1", "pi_1", 1000, "USD", "ZOIKO", date(2026, 10, 3), "INV-1")


def _legs(*, payout=1000, bank=True, gl=True):
    out = [Leg("PAYOUT_LINE", "po_1", "pi_1", payout, "USD")]
    if bank:
        out.append(Leg("BANK_LINE", "po_1", "", payout, "USD"))
    if gl:
        out.append(Leg("GL_JOURNAL", "po_1", "", payout, "USD"))
    return out


def test_match_states_keep_each_leg_separate() -> None:
    assert ops.match_item(ITEM, []).state is MatchState.UNMATCHED
    assert ops.match_item(ITEM, _legs(payout=900)).difference_minor == -100
    assert (
        ops.match_item(ITEM, _legs(bank=False, gl=False)).state is MatchState.PROVISIONALLY_MATCHED
    )
    assert "bank" in (ops.match_item(ITEM, _legs(bank=False)).reason or "")
    assert "ledger" in (ops.match_item(ITEM, _legs(gl=False)).reason or "")
    assert ops.match_item(ITEM, _legs()).state is MatchState.MATCHED


def test_close_blockers_and_materiality() -> None:
    matched = ops.match_item(ITEM, _legs())
    missing = ops.match_item(ITEM, [])
    assert (
        ops.close_blockers([matched], materiality_minor=100, open_refunds=0, open_disputes=0) == []
    )
    assert ops.close_blockers([missing], materiality_minor=100, open_refunds=0, open_disputes=0)
    assert (
        len(ops.close_blockers([matched], materiality_minor=100, open_refunds=1, open_disputes=2))
        == 2
    )
    small = ops.match_item(ITEM, _legs(payout=990))  # 10 apart, legs in place, below the line
    assert ops.close_blockers([small], materiality_minor=100, open_refunds=0, open_disputes=0) == []
    assert (
        ops.close_blockers(
            [missing],
            materiality_minor=100,
            open_refunds=0,
            open_disputes=0,
            resolved=frozenset({("PAYMENT", "i1")}),
        )
        == []
    )


def test_board_hash_changes_with_any_leg_and_ignores_order() -> None:
    a = ops.match_item(ITEM, _legs())
    other = Item("REFUND", "i2", "re_1", -50, "USD", "ZOIKO", date(2026, 10, 4))
    b = ops.match_item(other, [])
    assert ops.board_hash([a, b]) == ops.board_hash([b, a])
    assert ops.board_hash([a]) != ops.board_hash([ops.match_item(ITEM, _legs(gl=False))])


def test_close_signature_rules() -> None:
    allowed = ("CONTROLLER", "CFO")
    kw = {"allowed": allowed, "presented": "h", "current": "h", "maker_roles": ("CONTROLLER",)}
    ops.check_close_signature(role="CONTROLLER", signer="a", already={}, **kw)
    with pytest.raises(OpsError) as e:
        ops.check_close_signature(role="CFO", signer="a", already={"CONTROLLER": "a"}, **kw)
    assert e.value.code == "ONE_PERSON_TWO_SLOTS"
    with pytest.raises(OpsError) as e:
        ops.check_close_signature(role="CONTROLLER", signer="b", already={"CONTROLLER": "a"}, **kw)
    assert e.value.code == "ROLE_ALREADY_SIGNED"
    with pytest.raises(OpsError) as e:
        ops.check_close_signature(role="OTHER", signer="b", already={}, **kw)
    assert e.value.code == "ROLE_NOT_ALLOWED"
    with pytest.raises(OpsError) as e:
        ops.check_close_signature(role="CFO", signer="b", already={}, **{**kw, "current": "x"})
    assert e.value.code == "SNAPSHOT_CHANGED"

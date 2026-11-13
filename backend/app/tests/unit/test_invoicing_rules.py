"""Invoices, credit notes and the billing state machine (ZID-COM-001 §11, §30; CR-027). Pure rules."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from itertools import product

import pytest
from zid_platform.modules.commercial.domain import invoicing as inv

T0 = datetime(2026, 11, 1, 9, tzinfo=UTC)


def test_every_listed_move_is_allowed_with_its_reasons_and_every_other_move_is_refused() -> None:
    for (a, b), reasons in inv.TRANSITIONS.items():
        for r in reasons:
            inv.check_transition(a, b, r)
        with pytest.raises(inv.InvoicingError) as e:
            inv.check_transition(a, b, "MADE_UP")
        assert e.value.code == "BILLING_REASON_INVALID"
    for a, b in product(inv.STATES, inv.STATES):
        if (a, b) in inv.TRANSITIONS:
            continue
        with pytest.raises(inv.InvoicingError) as e:
            inv.check_transition(a, b, "PAID")
        assert e.value.code == "BILLING_TRANSITION_INVALID", (a, b)


def test_closed_is_terminal_and_paying_reactivates_only_from_a_dunning_state() -> None:
    assert not [t for (a, t) in inv.TRANSITIONS if a == "CLOSED"]
    paid_from = {a for (a, b), rs in inv.TRANSITIONS.items() if b == "ACTIVE" and "PAID" in rs}
    assert paid_from == {"PAST_DUE", "RESTRICTED", "SUSPENDED"}


def test_no_state_ever_restricts_a_security_path() -> None:
    security = set(inv.NEVER_RESTRICTED)
    for state in inv.STATES:
        for hold in (False, True):
            assert not (inv.restricted(state, hold) & security), (state, hold)
    assert inv.restricted("PAST_DUE") == frozenset()  # notification only
    assert inv.restricted("RESTRICTED") == {inv.NEW_PAID_EXTRAS}
    assert inv.NEW_ENTITIES in inv.restricted("SUSPENDED")
    assert "data_export" in inv.NEVER_RESTRICTED and "paying_an_invoice" in inv.NEVER_RESTRICTED
    assert (
        "audit_evidence_access" in inv.NEVER_RESTRICTED
        and "data_rights_requests" in inv.NEVER_RESTRICTED
    )  # P50: exit never closes access to a company's own evidence or its rights


def test_a_safety_hold_restricts_like_suspension_whatever_the_billing_state() -> None:
    assert inv.restricted("ACTIVE", safety_hold=True) == inv.restricted("SUSPENDED")


def test_dunning_follows_only_the_configured_policy_and_never_moves_back() -> None:
    policy = [(1, "PAST_DUE"), (15, "RESTRICTED"), (30, "SUSPENDED")]
    assert inv.dunning_target("ACTIVE", 0, policy) is None
    assert inv.dunning_target("ACTIVE", 1, policy) == "PAST_DUE"
    assert inv.dunning_target("ACTIVE", 20, policy) == "RESTRICTED"
    assert inv.dunning_target("PAST_DUE", 45, policy) == "SUSPENDED"
    assert inv.dunning_target("SUSPENDED", 2, policy) is None  # never back up the ladder
    assert inv.dunning_target("TERMINATING", 99, policy) is None
    assert inv.dunning_target("ACTIVE", 999, []) is None  # no policy, no movement
    assert inv.next_dunning_step("ACTIVE", "SUSPENDED") == "PAST_DUE"


def doc(**kw: object) -> str:
    base: dict[str, object] = {
        "issuing_entity": "ZID",
        "sequence_number": 7,
        "tenant_alias": "T-ABC",
        "period": "2026-10",
        "currency": "USD",
        "issued_at": T0,
        "due_at": T0,
        "lines": [
            inv.DocLine(2, "OVERAGE", "18,000 x $0.015", Decimal(270), Decimal(0), None),
            inv.DocLine(1, "FEE", "$1,499 Business platform fee", Decimal(1499), Decimal(0), None),
        ],
        "subtotal": Decimal(1769),
        "tax_total": Decimal(0),
        "gross": Decimal(1769),
        "tax_status": "ZERO_TAX_OVERRIDE",
    }
    return inv.render(**{**base, **kw})  # type: ignore[arg-type]


def test_the_document_is_deterministic_and_every_fact_changes_its_digest() -> None:
    a = doc()
    assert a == doc() and inv.digest(a) == inv.digest(doc())
    assert a.index("001 FEE") < a.index("002 OVERAGE")  # ordered by line, not by input order
    assert "ZID-00000007" in a and "Total: 1769.00 USD" in a
    for change in (
        {"sequence_number": 8},
        {"tenant_alias": "T-XYZ"},
        {"gross": Decimal("1769.01")},
        {"tax_status": "CONFIGURED"},
    ):
        assert inv.digest(doc(**change)) != inv.digest(a), change


def test_the_chain_detects_an_edit_a_deletion_and_a_reorder() -> None:
    entries: list[tuple[int, str, str]] = []
    prev = None
    for n in range(1, 6):
        d = inv.digest(f"invoice {n}")
        prev = inv.chain(prev, n, d)
        entries.append((n, d, prev))
    assert inv.verify_chain(entries) is None
    edited = [*entries]
    edited[2] = (3, inv.digest("tampered"), entries[2][2])
    assert inv.verify_chain(edited) == 3
    assert inv.verify_chain(entries[:2] + entries[3:]) == 4  # a gap
    assert inv.verify_chain([entries[1], entries[0], *entries[2:]]) == 2
    assert inv.verify_chain([]) is None


def test_credit_notes_are_positive_and_never_exceed_the_invoice() -> None:
    inv.creditable(Decimal(100), Decimal(0), Decimal(100))
    inv.creditable(Decimal(100), Decimal(60), Decimal(40))
    for credited, amount, code in (
        (Decimal(0), Decimal(0), "CREDIT_NOTE_INVALID"),
        (Decimal(0), Decimal(-1), "CREDIT_NOTE_INVALID"),
        (Decimal(60), Decimal("40.01"), "CREDIT_NOTE_EXCEEDS_INVOICE"),
    ):
        with pytest.raises(inv.InvoicingError) as e:
            inv.creditable(Decimal(100), credited, amount)
        assert e.value.code == code

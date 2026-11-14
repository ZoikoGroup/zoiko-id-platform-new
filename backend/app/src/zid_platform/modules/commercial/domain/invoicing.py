"""Invoices, credit notes and the billing state machine (ZID-COM-001 §11, §13, §30, §31; CR-027). Pure domain.

- An invoice is built only from a COMPUTED rating run. Once ISSUED it never changes: a dispute is a credit
  note against it. Its document is a deterministic text rendering whose digest is stored, and every issued
  invoice of an issuing entity is linked into a hash chain, so a missing or altered invoice is detectable.
- The billing state machine is explicit, and the restriction function says exactly what each state removes.
  It never removes a security path (sign-in, token refresh, revocation, recovery, signal intake, decisions on
  existing authority) or the ability to export data and to pay (§11, §15, App. C).
- A safety hold (abuse, acceptable-use) is a separate flag, not a billing state, so paying never clears it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

STATES = (
    "DRAFT",
    "CONFIRMED",
    "PROVISIONING",
    "ACTIVE",
    "PAST_DUE",
    "RESTRICTED",
    "SUSPENDED",
    "TERMINATING",
    "CLOSED",
)
DEFAULT_STATE = "ACTIVE"  # a tenant with no billing row is in good standing

# (from, to) -> the reason codes allowed for that move
TRANSITIONS: dict[tuple[str, str], frozenset[str]] = {
    ("DRAFT", "CONFIRMED"): frozenset({"ORDER_CONFIRMED"}),
    ("CONFIRMED", "PROVISIONING"): frozenset({"PROVISIONING_STARTED"}),
    ("PROVISIONING", "ACTIVE"): frozenset({"PROVISIONED"}),
    ("ACTIVE", "PAST_DUE"): frozenset({"PAYMENT_OVERDUE", "PAYMENT_FAILED"}),
    ("PAST_DUE", "ACTIVE"): frozenset({"PAID"}),
    ("PAST_DUE", "RESTRICTED"): frozenset({"DUNNING"}),
    ("RESTRICTED", "ACTIVE"): frozenset({"PAID"}),
    ("RESTRICTED", "SUSPENDED"): frozenset({"DUNNING"}),
    ("SUSPENDED", "ACTIVE"): frozenset({"PAID", "RESUMED"}),
    ("ACTIVE", "TERMINATING"): frozenset({"CANCELLED_AT_TERM", "CUSTOMER_EXIT"}),
    ("PAST_DUE", "TERMINATING"): frozenset({"CANCELLED_AT_TERM", "CUSTOMER_EXIT"}),
    ("RESTRICTED", "TERMINATING"): frozenset({"CANCELLED_AT_TERM", "CUSTOMER_EXIT"}),
    ("SUSPENDED", "TERMINATING"): frozenset({"CANCELLED_AT_TERM", "CUSTOMER_EXIT", "NON_PAYMENT"}),
    ("TERMINATING", "CLOSED"): frozenset({"OFFBOARDED"}),
}
# dunning only ever moves a tenant this way, one step at a time
DUNNING_ORDER = ("ACTIVE", "PAST_DUE", "RESTRICTED", "SUSPENDED")

# Categories a state can restrict. A security path is never one of them.
NEW_PAID_EXTRAS = "new_paid_extras"  # connections above plan, plan upgrades, new paid capabilities
NEW_ENTITIES = "new_entities"  # new applications, connectors, agents
METERED_EXTRAS = "metered_extras"  # new credential issuance, verification checks
CONFIGURATION_WRITES = "configuration_writes"
RESTRICTIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset(),
    "CONFIRMED": frozenset(),
    "PROVISIONING": frozenset(),
    "ACTIVE": frozenset(),
    "PAST_DUE": frozenset(),  # notification only
    "RESTRICTED": frozenset({NEW_PAID_EXTRAS}),
    "SUSPENDED": frozenset({NEW_PAID_EXTRAS, NEW_ENTITIES, METERED_EXTRAS}),
    "TERMINATING": frozenset({NEW_PAID_EXTRAS, NEW_ENTITIES, METERED_EXTRAS, CONFIGURATION_WRITES}),
    "CLOSED": frozenset({NEW_PAID_EXTRAS, NEW_ENTITIES, METERED_EXTRAS, CONFIGURATION_WRITES}),
}
NEVER_RESTRICTED = (
    "sign_in",
    "token_refresh",
    "session_and_credential_revocation",
    "account_recovery",
    "security_signal_intake",
    "decisions_on_existing_authority",
    "data_export",
    "audit_evidence_access",  # a company's own audit evidence stays readable whatever its plan or billing state (ZID-UX-PRICE-004 P50)
    "data_rights_requests",
    "paying_an_invoice",
)


class InvoicingError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def check_transition(current: str, target: str, reason: str) -> None:
    if current not in STATES or target not in STATES:
        raise InvoicingError("BILLING_STATE_UNKNOWN")
    allowed = TRANSITIONS.get((current, target))
    if allowed is None:
        raise InvoicingError(
            "BILLING_TRANSITION_INVALID", f"{current} -> {target} is not a billing move"
        )
    if reason not in allowed:
        raise InvoicingError(
            "BILLING_REASON_INVALID", f"{current} -> {target} needs one of {sorted(allowed)}"
        )


def restricted(state: str, safety_hold: bool = False) -> frozenset[str]:
    """What this state removes. A safety hold removes what suspension removes, whatever the billing state."""
    base = RESTRICTIONS.get(state, frozenset())
    return base | RESTRICTIONS["SUSPENDED"] if safety_hold else base


def dunning_target(
    current: str, days_overdue: int, policy: Sequence[tuple[int, str]]
) -> str | None:
    """The state the dunning policy says an account `days_overdue` past due should be in, or None to stay.

    `policy` is (days after due, state) rows from the configured dunning table; with no rows nothing moves.
    Dunning never moves backwards and never leaves the DUNNING_ORDER ladder."""
    if current not in DUNNING_ORDER:
        return None
    target = current
    for days, state in sorted(policy):
        if (
            days_overdue >= days
            and state in DUNNING_ORDER
            and DUNNING_ORDER.index(state) > DUNNING_ORDER.index(target)
        ):
            target = state
    return None if target == current else target


def next_dunning_step(current: str, target: str) -> str:
    """One rung at a time, so every state change has its own reason and record."""
    return DUNNING_ORDER[DUNNING_ORDER.index(current) + 1] if current != target else current


# ------------------------------------------------------------------------------- documents


@dataclass(frozen=True, slots=True)
class DocLine:
    seq: int
    kind: str
    description: str
    net: Decimal
    tax: Decimal
    tax_code: str | None


def _amt(v: Decimal) -> str:
    return f"{v:.2f}" if v == v.quantize(Decimal("0.01")) else format(v.normalize(), "f")


def render(
    *,
    issuing_entity: str,
    sequence_number: int,
    tenant_alias: str,
    period: str,
    currency: str,
    issued_at: datetime,
    due_at: datetime,
    lines: Sequence[DocLine],
    subtotal: Decimal,
    tax_total: Decimal,
    gross: Decimal,
    tax_status: str,
) -> str:
    """The invoice document: fixed ordering, fixed formatting, UTC dates, LF line ends. The same rows always
    render to the same bytes, so the stored digest proves the document."""
    out = [
        "ZOIKO iD INVOICE",
        f"Issuer: {issuing_entity}",
        f"Invoice: {issuing_entity}-{sequence_number:08d}",
        f"Customer: {tenant_alias}",
        f"Period: {period}",
        f"Issued: {issued_at.date().isoformat()}",
        f"Due: {due_at.date().isoformat()}",
        f"Currency: {currency}",
        "",
    ]
    for ln in sorted(lines, key=lambda x: x.seq):
        tax = f" tax {_amt(ln.tax)} {ln.tax_code}" if ln.tax_code else ""
        out.append(f"{ln.seq:03d} {ln.kind:<10} {_amt(ln.net):>14}{tax}  {ln.description}")
    out += [
        "",
        f"Subtotal: {_amt(subtotal)}",
        f"Tax: {_amt(tax_total)} ({tax_status})",
        f"Total: {_amt(gross)} {currency}",
        "",
    ]
    return "\n".join(out)


def digest(document: str) -> str:
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def chain(previous: str | None, sequence_number: int, document_digest: str) -> str:
    """Each issued invoice commits to the one before it, so deleting or editing one breaks every later link."""
    return hashlib.sha256(
        f"{previous or '0' * 64}|{sequence_number}|{document_digest}".encode()
    ).hexdigest()


def verify_chain(entries: Sequence[tuple[int, str, str]]) -> int | None:
    """`entries` are (sequence, document digest, chain hash) in order. Returns the first broken sequence, or
    None when the chain is whole and gap-free."""
    previous: str | None = None
    expected = 1
    for seq, doc, link in entries:
        if seq != expected or chain(previous, seq, doc) != link:
            return seq
        previous, expected = link, seq + 1
    return None


def creditable(gross: Decimal, credited: Decimal, amount: Decimal) -> None:
    """A credit note is positive and never exceeds what remains of the invoice it reverses."""
    if amount <= 0:
        raise InvoicingError("CREDIT_NOTE_INVALID", "a credit note is a positive amount")
    if credited + amount > gross:
        raise InvoicingError("CREDIT_NOTE_EXCEEDS_INVOICE", "credits cannot exceed the invoice")

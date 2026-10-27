"""Revenue assurance, journals and run-rate KPIs (ZID-COM-001 §11, §22, §24, §30; CR-030). Pure domain.

- The commercial change log is an append-only hash chain: every price book, entitlement, order, invoice,
  credit note, billing-state and vendor-rate change commits to the one before it.
- Journal entries are derived from issued documents, never typed in: an invoice debits receivables and credits
  revenue and tax payable; a credit note reverses; a captured payment moves receivables to cash. Every entry
  balances. They are exported for the ledger system; the platform posts nothing itself.
- KPIs are computed views with their definitions: MRR is recurring subscription fees normalised monthly,
  ARR is MRR x 12, both excluding one-time services and intercompany (Zoiko Group) accounts (§18, §22).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

EXCEPTION_KINDS = (
    "ENTITLEMENT_WITHOUT_ORDER",  # §24 unapproved entitlements
    "ENTITLEMENT_BOOK_DRIFT",  # §24 contract or SKU drift
    "METER_LOSS",  # §24 meter loss: a source row with no billable event
    "UNRATED_MONTH",  # §24 revenue leakage: a frozen month never rated
    "UNINVOICED_RUN",  # §24 revenue leakage: a computed rating never invoiced
    "ORDER_BELOW_FLOOR",  # §24 quote below cost
    "SUPPLIER_DISPUTE_OPEN",  # §24 supplier invoice mismatch
)

KPI_DEFINITIONS = {
    "mrr": "Recurring committed subscription fees of activated orders, normalised monthly; one-time services, "
    "pass-through checks and intercompany accounts excluded (ZID-COM-001 §22).",
    "arr": "MRR x 12, under the same exclusions.",
    "consumption_revenue": "Issued invoice net amounts of usage lines in the period, after credits.",
}


class AssuranceError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def digest(obj: object) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def link(previous: str | None, seq: int, entry_digest: str) -> str:
    return hashlib.sha256(f"{previous or '0' * 64}|{seq}|{entry_digest}".encode()).hexdigest()


def verify(entries: Sequence[tuple[int, str, str]]) -> int | None:
    """(seq, entry digest, chain hash) in order -> first broken seq, or None when whole and gap-free."""
    prev: str | None = None
    for expected, (seq, d, h) in enumerate(entries, start=1):
        if seq != expected or link(prev, seq, d) != h:
            return seq
        prev = h
    return None


@dataclass(frozen=True, slots=True)
class JournalLine:
    account: str  # RECEIVABLES | REVENUE_SUBSCRIPTION | REVENUE_USAGE | TAX_PAYABLE | CASH
    debit: Decimal
    credit: Decimal
    reference: str


def _usage(kind: str) -> bool:
    return kind in ("OVERAGE", "CONNECTION")


def invoice_journal(ref: str, lines: Iterable[tuple[str, Decimal, Decimal]]) -> list[JournalLine]:
    """`lines` are (kind, net, tax) of an issued invoice. Credits inside the invoice reduce revenue."""
    sub = use = tax = Decimal(0)
    for kind, net, t in lines:
        tax += t
        if _usage(kind):
            use += net
        else:
            sub += net  # FEE, MINIMUM and in-invoice CREDIT lines (negative) adjust subscription revenue
    gross = sub + use + tax
    out = [JournalLine("RECEIVABLES", gross, Decimal(0), ref)]
    if sub:
        out.append(JournalLine("REVENUE_SUBSCRIPTION", Decimal(0), sub, ref))
    if use:
        out.append(JournalLine("REVENUE_USAGE", Decimal(0), use, ref))
    if tax:
        out.append(JournalLine("TAX_PAYABLE", Decimal(0), tax, ref))
    return out


def credit_note_journal(ref: str, amount: Decimal) -> list[JournalLine]:
    return [
        JournalLine("REVENUE_SUBSCRIPTION", amount, Decimal(0), ref),
        JournalLine("RECEIVABLES", Decimal(0), amount, ref),
    ]


def payment_journal(ref: str, amount: Decimal) -> list[JournalLine]:
    return [
        JournalLine("CASH", amount, Decimal(0), ref),
        JournalLine("RECEIVABLES", Decimal(0), amount, ref),
    ]


def balanced(entries: Sequence[JournalLine]) -> bool:
    return sum((e.debit for e in entries), Decimal(0)) == sum(
        (e.credit for e in entries), Decimal(0)
    )


def run_rate(
    subscriptions: Iterable[tuple[str, int, bool]], monthly_fee: Mapping[str, Decimal]
) -> dict[str, Decimal]:
    """`subscriptions` are (plan, count, intercompany). MRR excludes intercompany accounts (§18)."""
    mrr = Decimal(0)
    for plan, count, intercompany in subscriptions:
        if intercompany:
            continue
        fee = monthly_fee.get(plan)
        if fee is None:
            raise AssuranceError("PLAN_FEE_UNKNOWN", plan)
        mrr += fee * count
    return {"mrr": mrr, "arr": mrr * 12}


def gross_profit(mrr: Decimal, margin: Decimal) -> Decimal:
    """Illustrative only (§22): monthly gross profit at a blended gross margin."""
    return (mrr * margin).quantize(Decimal("0.01"))

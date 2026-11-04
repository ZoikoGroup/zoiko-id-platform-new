"""Reading the evidence a Controller reconciles against (ZID-UX-COM-009 F04), in the formats that already exist.

- The processor's own payout detail: the balance transactions that make up one Stripe payout, read as PAYOUT_LINE legs. Each line is the gross amount of one payment or refund (fees are not part of the
  leg), keyed to the processor reference the platform holds: the PaymentIntent for a payment, the refund id for a refund.
- (A bank statement in ISO 20022 camt.053 is read by `infrastructure/bank_statement.py`, which owns the XML parsing.)

Pure functions: nothing here reads a database or the network, refuses what it cannot read, and never fills one leg in from another. A line the format does not let us read is counted as skipped and said so.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

_ZERO_EXPONENT = frozenset({"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "XAF", "XOF"})
_THREE_EXPONENT = frozenset({"KWD", "BHD", "OMR", "JOD", "TND"})


class ImportError_(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code


def minor(amount: Decimal, currency: str) -> int:
    exp = 0 if currency in _ZERO_EXPONENT else 3 if currency in _THREE_EXPONENT else 2
    scaled = amount * (10**exp)
    if scaled != scaled.to_integral_value():
        raise ImportError_(
            "AMOUNT_NOT_WHOLE", f"{amount} {currency} has more decimals than the currency"
        )
    return int(scaled)


def _day(ts: int | None, fallback: datetime) -> str:
    return (datetime.fromtimestamp(ts, UTC) if isinstance(ts, int) else fallback).date().isoformat()


def legs_from_payout(
    payout: dict[str, Any], transactions: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """Stripe payout -> (PAYOUT_LINE legs, how many transactions were skipped). `transactions` are the payout's balance transactions with `source` expanded."""
    payout_id = str(payout.get("id") or "")
    if not payout_id.startswith("po_"):
        raise ImportError_("PAYOUT_INVALID", "a payout names its id")
    arrival = payout.get("arrival_date")
    legs: list[dict[str, Any]] = []
    skipped = 0
    for t in transactions:
        kind, source = str(t.get("type") or ""), t.get("source")
        amount, currency = t.get("amount"), str(t.get("currency") or "").upper()
        if not isinstance(amount, int) or not currency or not isinstance(source, dict):
            skipped += 1
            continue
        if kind in ("charge", "payment"):
            link = source.get("payment_intent")
        elif kind in ("refund", "payment_refund"):
            link = source.get("id")
        elif kind in ("adjustment", "dispute", "payment_failure_refund"):
            link = source.get("id") if str(source.get("object")) == "dispute" else None
        else:
            link = None  # a payout, a fee, a transfer: not an item the platform reconciles
        if not isinstance(link, str) or not link:
            skipped += 1
            continue
        legs.append(
            {
                "leg_type": "PAYOUT_LINE",
                "ref": payout_id,
                "link_ref": link,
                "amount_minor": amount,
                "currency": currency,
                "occurred_on": _day(
                    arrival if isinstance(arrival, int) else t.get("available_on"),
                    datetime.now(UTC),
                ),
            }
        )
    return legs, skipped

"""Reading the evidence a Controller reconciles against (ZID-UX-COM-009 F04), in the formats that already exist.

- The processor's own payout detail: the balance transactions that make up one Stripe payout, read as PAYOUT_LINE legs. Each line is the gross amount of one payment or refund (fees are not part of the
  leg), keyed to the processor reference the platform holds: the PaymentIntent for a payment, the refund id for a refund.
- A bank statement in ISO 20022 camt.053 (the standard account statement): each entry becomes a BANK_LINE keyed by the payout id named in its remittance text. An entry that names no payout is kept under its own
  bank reference, so it stays unmatched rather than being guessed onto a payout.

Pure functions: nothing here reads a database or the network, refuses what it cannot read, and never fills one leg in from another. A line the format does not let us read is counted as skipped and said so.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from defusedxml import ElementTree as ET  # the project's gate for every XML document

_PAYOUT_ID = re.compile(r"\bpo_[A-Za-z0-9]{6,}\b")
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


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node: Any, *path: str) -> Any | None:
    cur: Any | None = node
    for name in path:
        cur = next((c for c in (cur if cur is not None else []) if _local(c.tag) == name), None)
        if cur is None:
            return None
    return cur


def _text(node: Any | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def legs_from_camt053(xml: bytes) -> tuple[list[dict[str, Any]], int]:
    """ISO 20022 camt.053 -> (BANK_LINE legs, entries skipped). Credits are positive and debits negative, as the platform's own amounts are."""
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ImportError_("XML_REFUSED", "a statement with a DOCTYPE or entities is not read")
    if len(xml) > 5_000_000:
        raise ImportError_("STATEMENT_TOO_LARGE", "at most 5 MB")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ImportError_("XML_INVALID", "the statement is not well-formed XML") from e
    entries = [n for n in root.iter() if _local(n.tag) == "Ntry"]
    if not entries and not any(_local(n.tag) == "BkToCstmrStmt" for n in root.iter()):
        raise ImportError_("NOT_CAMT053", "no bank-to-customer statement found")
    legs: list[dict[str, Any]] = []
    skipped = 0
    for e in entries:
        amt = _child(e, "Amt")
        status = _text(_child(e, "Sts", "Cd")) or _text(
            _child(e, "Sts")
        )  # camt.053.001.08 wraps the code; older versions carry it as the text
        if amt is None or (
            status and status not in ("BOOK", "BOOKED")
        ):  # a pending entry is not bank settlement
            skipped += 1
            continue
        currency = str(amt.attrib.get("Ccy", "")).upper()
        day = _text(_child(e, "BookgDt", "Dt")) or _text(_child(e, "ValDt", "Dt"))
        try:
            value = Decimal(_text(amt))
            cents = minor(value, currency)
        except (InvalidOperation, ImportError_):
            skipped += 1
            continue
        if not currency or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            skipped += 1
            continue
        sign = -1 if _text(_child(e, "CdtDbtInd")) == "DBIT" else 1
        blob = " ".join(
            _text(n)
            for n in e.iter()
            if _local(n.tag)
            in ("Ustrd", "AddtlNtryInf", "EndToEndId", "NtryRef", "AcctSvcrRef", "Ref")
        )
        found = _PAYOUT_ID.search(blob)
        own = (
            _text(_child(e, "AcctSvcrRef")) or _text(_child(e, "NtryRef")) or f"bank-{day}-{cents}"
        )
        legs.append(
            {
                "leg_type": "BANK_LINE",
                "ref": found.group(0) if found else own,
                "link_ref": "",
                "amount_minor": sign * cents,
                "currency": currency,
                "occurred_on": day,
            }
        )
    return legs, skipped

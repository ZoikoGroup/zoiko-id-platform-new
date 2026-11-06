"""A bank statement in ISO 20022 camt.053 (the standard account statement) read as BANK_LINE legs (ZID-UX-COM-009 F04).

Each booked entry becomes a leg keyed by the payout id named in its remittance text. An entry that names no payout is kept under its own bank reference, so it stays unmatched rather than being
guessed onto a payout. Credits are positive and debits negative, as the platform's own amounts are. XML is read only through `defusedxml` and a statement with a DOCTYPE or entities is refused."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from defusedxml import ElementTree as ET  # the project's gate for every XML document

from zid_platform.modules.commercial.domain.recon_import import ImportError_, minor

_PAYOUT_ID = re.compile(r"\bpo_[A-Za-z0-9]{6,}\b")


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
    except ET.ParseError as exc:
        raise ImportError_("XML_INVALID", "the statement is not well-formed XML") from exc
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

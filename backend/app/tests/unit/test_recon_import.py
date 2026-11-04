"""The processor's payout detail and a camt.053 bank statement, read as reconciliation legs (ZID-UX-COM-009 F04; WF09-OP-09)."""

from __future__ import annotations

from datetime import date

import pytest
from zid_platform.modules.commercial.domain import payment_ops as ops
from zid_platform.modules.commercial.domain import recon_import as ri
from zid_platform.modules.commercial.domain.payment_ops import Item, Leg, MatchState

ARRIVAL = 1_773_273_600  # 2026-03-12 00:00:00 UTC

PAYOUT = {"id": "po_1ABCdefGHI", "arrival_date": ARRIVAL, "amount": 7_000, "currency": "usd"}
TRANSACTIONS = [
    {
        "id": "txn_1",
        "type": "charge",
        "amount": 5000,
        "fee": 175,
        "net": 4825,
        "currency": "usd",
        "source": {"object": "charge", "id": "ch_1", "payment_intent": "pi_1"},
    },
    {
        "id": "txn_2",
        "type": "refund",
        "amount": -1000,
        "currency": "usd",
        "source": {"object": "refund", "id": "re_1"},
    },
    {
        "id": "txn_3",
        "type": "stripe_fee",
        "amount": -50,
        "currency": "usd",
        "source": {"object": "fee", "id": "fee_1"},
    },
    {"id": "txn_4", "type": "charge", "amount": 3000, "currency": "usd", "source": "ch_unexpanded"},
]

CAMT = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt><Stmt>
  <Ntry><NtryRef>N1</NtryRef><Amt Ccy="USD">40.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts><BookgDt><Dt>2026-03-13</Dt></BookgDt>
    <NtryDtls><TxDtls><RmtInf><Ustrd>STRIPE PAYOUT po_1ABCdefGHI</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
  <Ntry><NtryRef>N2</NtryRef><Amt Ccy="USD">12.50</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts><BookgDt><Dt>2026-03-14</Dt></BookgDt>
    <NtryDtls><TxDtls><RmtInf><Ustrd>bank charges</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
  <Ntry><NtryRef>N3</NtryRef><Amt Ccy="USD">99.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts><Cd>PDNG</Cd></Sts><BookgDt><Dt>2026-03-14</Dt></BookgDt></Ntry>
</Stmt></BkToCstmrStmt></Document>"""


def test_a_payout_becomes_one_gross_leg_per_payment_and_refund_and_says_what_it_skipped() -> None:
    legs, skipped = ri.legs_from_payout(PAYOUT, TRANSACTIONS)
    assert [(x["link_ref"], x["amount_minor"]) for x in legs] == [
        ("pi_1", 5000),
        ("re_1", -1000),
    ]  # gross, not net of the fee
    assert all(
        x["ref"] == "po_1ABCdefGHI" and x["occurred_on"] == "2026-03-12" and x["currency"] == "USD"
        for x in legs
    )
    assert skipped == 2  # a fee, and a charge whose source was not expanded: counted, never guessed
    with pytest.raises(ri.ImportError_):
        ri.legs_from_payout({"id": "x"}, [])


def test_a_statement_becomes_signed_bank_lines_keyed_by_the_payout_it_names_and_skips_what_is_not_booked() -> (
    None
):
    legs, skipped = ri.legs_from_camt053(CAMT)
    assert skipped == 1  # the pending entry is not settlement
    assert [(x["ref"], x["amount_minor"]) for x in legs] == [
        ("po_1ABCdefGHI", 4000),
        ("N2", -1250),
    ]  # the entry that names no payout keeps its own reference
    assert legs[0]["occurred_on"] == "2026-03-13"


def test_a_statement_with_a_doctype_or_that_is_not_a_statement_is_refused() -> None:
    for bad in (
        b'<!DOCTYPE x [<!ENTITY a "b">]><Document/>',
        b"<not xml",
        b"<Document><Other/></Document>",
    ):
        with pytest.raises(ri.ImportError_):
            ri.legs_from_camt053(bad)


def test_amounts_follow_the_currency_exponent_and_refuse_extra_decimals() -> None:
    from decimal import Decimal

    assert (
        ri.minor(Decimal("40.00"), "USD") == 4000
        and ri.minor(Decimal(500), "JPY") == 500
        and ri.minor(Decimal("1.234"), "KWD") == 1234
    )
    with pytest.raises(ri.ImportError_):
        ri.minor(Decimal("1.005"), "USD")


def test_the_processor_and_the_bank_together_match_the_platforms_payment_but_the_bank_alone_never_does() -> (
    None
):
    payout_legs, _ = ri.legs_from_payout(PAYOUT, TRANSACTIONS)
    bank_legs, _ = ri.legs_from_camt053(CAMT)

    def as_legs(rows: list[dict]) -> list[Leg]:
        return [
            Leg(r["leg_type"], r["ref"], r["link_ref"], r["amount_minor"], r["currency"])
            for r in rows
        ]

    item = Item("PAYMENT", "i1", "pi_1", 5000, "USD", "ZOIKO", date(2026, 3, 10), "INV-1")
    both = ops.match_item(item, as_legs(payout_legs + bank_legs))
    assert both.state is MatchState.PROVISIONALLY_MATCHED and "ledger journal" in (
        both.reason or ""
    )  # processor and bank agree; the ledger journal is still the Controller's to import
    bank_only = ops.match_item(item, as_legs(bank_legs))
    assert (
        bank_only.state is MatchState.UNMATCHED
    )  # a bank line is never read as the processor's payout line

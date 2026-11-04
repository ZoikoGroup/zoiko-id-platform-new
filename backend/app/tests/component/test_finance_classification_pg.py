"""A credit note, a pilot benefit and a refund are three different things (ZID-COM-CL-004 C09).

Each is its own request kind with its own required facts; one cannot be opened or executed as another; and there is no refund kind at all, because a refund
moves money and is its own decision (Closure 05)."""

from __future__ import annotations

import asyncio
import typing
from datetime import timedelta
from decimal import Decimal
from typing import Any

from conftest import engine
from test_finance_authority_pg import NOW, billed, cast, finance, issued_invoice, refused, sign, uid
from zid_platform.modules.commercial.domain.finance import Kind
from zid_platform.modules.operator_access.api import commercial_finance as routes

D = Decimal


def test_the_request_kinds_are_credit_note_and_pilot_credit_and_never_a_refund() -> None:
    kinds = set(typing.get_args(routes.RequestBody.model_fields["kind"].annotation))
    assert kinds == {"CREDIT_NOTE", "PILOT_CREDIT"}
    assert {k.value for k in Kind} == {"CREDIT_NOTE", "PILOT_CREDIT"}
    assert set(typing.get_args(routes.RequestBody.model_fields["category"].annotation)) == {
        "CORRECTION",
        "GOODWILL",
        "PILOT",
    }


def test_one_kind_cannot_be_opened_or_executed_as_another(rt_url: str) -> None:
    tenant = uid()

    async def run() -> None:
        invoice = await issued_invoice(rt_url, tenant)
        async with engine(rt_url) as eng:
            p = await cast(eng, "REVOPS", "FINANCE_OPS_LEAD", "CCO", "FINANCE_EXECUTOR")
            async with eng.begin() as c:
                await billed(c, tenant)

                def open_pilot(**kw: Any) -> Any:
                    args: dict[str, Any] = {
                        "kind": Kind.PILOT_CREDIT, "tenant_id": tenant, "invoice_id": None, "amount": D("100.00"), "currency": "USD", "category": "PILOT",
                        "reason": "design partner", "program_ref": "PILOT-1", "sponsor": "Head of IT", "requested_by": p["REVOPS"],
                        "ends_at": NOW + timedelta(days=30), "environment": "test",
                    } | kw  # fmt: skip
                    return finance.open_request(c, **args)

                # a pilot benefit is not against an invoice and is not a correction or a goodwill note
                await refused(c, open_pilot(invoice_id=invoice), "INVOICE_NOT_ALLOWED")
                await refused(c, open_pilot(category="CORRECTION"), "CATEGORY_INVALID")
                await refused(c, open_pilot(category="GOODWILL"), "CATEGORY_INVALID")
                await refused(c, open_pilot(program_ref=None), "PILOT_PROGRAM_REQUIRED")

                # a credit note is against one invoice and is not a pilot benefit
                await refused(
                    c,
                    finance.open_request(
                        c, kind=Kind.CREDIT_NOTE, tenant_id=tenant, invoice_id=None, amount=D("10.00"), currency="USD", category="GOODWILL",
                        reason="goodwill", requested_by=p["REVOPS"], environment="test",
                    ),
                    "INVOICE_REQUIRED",
                )  # fmt: skip
                await refused(
                    c,
                    finance.open_request(
                        c, kind=Kind.CREDIT_NOTE, tenant_id=tenant, invoice_id=invoice, amount=D("10.00"), currency="USD", category="PILOT",
                        reason="a pilot, said as a credit note", requested_by=p["REVOPS"], environment="test",
                    ),
                    "CATEGORY_INVALID",
                )  # fmt: skip

                # executing a pilot benefit posts a pilot credit and never a credit note on any invoice, and the reverse
                pilot_req = await open_pilot()
                await sign(c, pilot_req, "CCO", p["CCO"])
                await sign(c, pilot_req, "FINANCE_OPS_LEAD", p["FINANCE_OPS_LEAD"])
                ran = await finance.execute(
                    c,
                    pilot_req["request_id"],
                    executor=p["FINANCE_EXECUTOR"],
                    idempotency_key="pilot-run-0001",
                    environment="test",
                )
                assert (
                    ran["state"] == "APPLIED"
                    and "credit_note" not in (ran["result"] or {})
                    and ran["result"].get("pilot_credit_id")
                )

                note_req = await finance.open_request(
                    c, kind=Kind.CREDIT_NOTE, tenant_id=tenant, invoice_id=invoice, amount=D("10.00"), currency="USD", category="GOODWILL",
                    reason="goodwill", requested_by=p["REVOPS"], environment="test",
                )  # fmt: skip
                await sign(c, note_req, "FINANCE_OPS_LEAD", p["FINANCE_OPS_LEAD"])
                done = await finance.execute(
                    c,
                    note_req["request_id"],
                    executor=p["FINANCE_EXECUTOR"],
                    idempotency_key="note-run-0001",
                    environment="test",
                )
                assert (
                    done["state"] == "APPLIED"
                    and done["result"]["credit_note"]
                    and not done["result"].get("pilot_credit_id")
                )
                assert done["result"]["refund"] is None  # money is never moved by a credit note

    asyncio.run(run())

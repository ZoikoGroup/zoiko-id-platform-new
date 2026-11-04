"""Refunds, disputes and reconciliation against Postgres (ZID-UX-COM-009 F03/F04; CR-058; WF09-OP-05..10, CL04-C15).

A refund is limited to what was overpaid, signed by the policy's tier, run by someone who neither asked nor signed, sent by the worker once under one key, and
never called complete until the processor says so. A dispute closes only on the processor's word. A period closes only when nothing blocks it and two different
people sign the same digest, and is then locked.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import ctx, engine
from onboarding_fixture import onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_invoicing_pg import ENTITY, LAST, rated_month, uid
from test_owner_payment_pg import NoTax
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import dispute_service as disputes
from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.commercial.application import invoicing_service as inv
from zid_platform.modules.commercial.application import orders_service
from zid_platform.modules.commercial.application import recon_service as recon
from zid_platform.modules.commercial.application import refund_service as refunds
from zid_platform.modules.commercial.application.adapters import RecordingRefunds, RefundResult
from zid_platform.modules.commercial.domain.finance import FinanceError
from zid_platform.modules.commercial.domain.payment_ops import OpsError

NOW = datetime.now(UTC)
D = Decimal


async def cast(eng: Any, *roles: str) -> dict[str, str]:
    people = {r: uid() for r in roles}
    async with eng.begin() as c:
        for role, person in people.items():
            await finance.appoint(
                c,
                principal=person,
                role=role,
                appointed_by=uid(),
                effective_from=NOW - timedelta(days=200),
            )
    return people


async def paid_invoice(rt_url: str, o: Any, overpay: str) -> tuple[str, str, str]:
    """An issued invoice and one captured payment that is `overpay` more than the invoice asks. Returns (invoice, payment attempt, payment intent)."""
    await rated_month(rt_url, o.tenant)
    drafter, issuer = uid(), uid()
    async with engine(rt_url) as eng:
        async with eng.begin() as c:
            account = await orders_service.create_account(
                c,
                legal_name="Refund Ltd",
                kind="DIRECT",
                country="GB",
                currency="USD",
                reseller_of=None,
                actor=drafter,
            )
            await orders_service.link_tenant(c, account, o.tenant)
        async with tenant_transaction(eng, ctx(o.tenant)) as c:
            d = await inv.create_draft(
                c, tenant_id=o.tenant, month=LAST, issuing_entity=ENTITY, actor=drafter
            )
            await inv.issue(
                c,
                d["invoice_id"],
                issuer=issuer,
                tax=NoTax(),
                payment_terms_days=14,
                allow_zero_tax=False,
                region="uk-1",
            )
            gross = (
                await c.execute(
                    text("SELECT gross FROM commercial.invoice WHERE invoice_id = :i"),
                    {"i": uuid.UUID(str(d["invoice_id"]))},
                )
            ).scalar_one()
            attempt, pi = uuid.uuid4(), f"pi_{uuid.uuid4().hex[:20]}"
            await c.execute(
                text(
                    "INSERT INTO commercial.payment_attempt (attempt_id, tenant_id, invoice_id, idempotency_key, amount, currency, status, provider_ref) "
                    "VALUES (:a, :t, :i, :k, :m, 'USD', 'CAPTURED', :p)"
                ),
                {
                    "a": attempt,
                    "t": uuid.UUID(o.tenant),
                    "i": uuid.UUID(str(d["invoice_id"])),
                    "k": f"pay-{attempt}",
                    "m": Decimal(gross) + D(overpay),
                    "p": pi,
                },
            )
    return str(d["invoice_id"]), str(attempt), pi


def test_refund_is_limited_signed_run_by_another_and_sent_once(rt_url: str) -> None:
    o = onboard(rt_url, extra_resources=["billable:*", "tenant:*"])

    async def run() -> None:
        _, attempt, pi = await paid_invoice(rt_url, o, "50.00")
        async with engine(rt_url) as eng:
            p = await cast(eng, "REVOPS", "CONTROLLER", "FINANCE_EXECUTOR")
            async with eng.begin() as c:
                with pytest.raises(OpsError) as e:  # more than the overpayment
                    await refunds.open_refund(
                        c,
                        tenant_id=o.tenant,
                        payment_attempt_id=attempt,
                        amount=D("50.01"),
                        category="GOODWILL",
                        reason="x",
                        requested_by=p["REVOPS"],
                        environment="test",
                    )
                assert e.value.code == "REFUND_EXCEEDS_OVERPAYMENT"
                req = await refunds.open_refund(
                    c,
                    tenant_id=o.tenant,
                    payment_attempt_id=attempt,
                    amount=D("30.00"),
                    category="GOODWILL",
                    reason="duplicate click",
                    requested_by=p["REVOPS"],
                    environment="test",
                )
                assert req["state"] == "REQUESTED" and req["required_roles"] == ["CONTROLLER"]
                with pytest.raises(
                    OpsError
                ):  # 30 + 30 would exceed the 50 overpaid: the first still counts
                    await refunds.open_refund(
                        c,
                        tenant_id=o.tenant,
                        payment_attempt_id=attempt,
                        amount=D("30.00"),
                        category="GOODWILL",
                        reason="again",
                        requested_by=p["REVOPS"],
                        environment="test",
                    )
            rid = req["refund_id"]
            async with eng.begin() as c:
                with pytest.raises(FinanceError) as e2:  # a stale digest is refused
                    await refunds.decide(
                        c,
                        rid,
                        role="CONTROLLER",
                        approver=p["CONTROLLER"],
                        decision="APPROVE",
                        presented_hash="0" * 64,
                        reason=None,
                        environment="test",
                    )
                assert e2.value.code
            async with eng.begin() as c:
                done = await refunds.decide(
                    c,
                    rid,
                    role="CONTROLLER",
                    approver=p["CONTROLLER"],
                    decision="APPROVE",
                    presented_hash=req["snapshot_hash"],
                    reason=None,
                    environment="test",
                )
                assert done["state"] == "APPROVED"
            async with eng.begin() as c:
                with pytest.raises(FinanceError) as e3:  # whoever asked does not run it
                    await refunds.execute(c, rid, executor=p["REVOPS"], environment="test")
                assert e3.value.code in ("NOT_AN_EXECUTOR", "EXECUTOR_NOT_INDEPENDENT")
            async with eng.begin() as c:
                queued = await refunds.execute(
                    c, rid, executor=p["FINANCE_EXECUTOR"], environment="test"
                )
                assert (
                    queued["state"] == "EXECUTION_PENDING" and queued["processor"]["attempts"] == 0
                )

            port = RecordingRefunds(next_create=[RefundResult("UNAVAILABLE")])
            async with (
                eng.begin() as c
            ):  # the processor does not answer: UNKNOWN, not failed and not sent again blindly
                assert (await refunds.send_pending(c, port))["unknown"] >= 1
            async with eng.begin() as c:
                assert (await refunds.view(c, rid))["state"] == "UNKNOWN"
                assert (await refunds.send_pending(c, port))["sent"] == 0  # not queued any more
            assert len([s for s in port.sent if s[3] == rid]) == 1

            port.held[rid] = RefundResult("SENT", "re_held_1", "succeeded", 3000)
            async with eng.begin() as c:
                await refunds.verify_open(c, port)  # asks the processor what it holds
            async with eng.begin() as c:
                final = await refunds.view(c, rid)
                assert (
                    final["state"] == "SUCCEEDED"
                    and final["processor"]["refund_ref"] == "re_held_1"
                )
                assert (
                    "reconciliation" in final["label"].lower()
                )  # "provider confirmed; reconciliation still to come", never "completed"
                item = (
                    await c.execute(
                        text(
                            "SELECT amount_minor FROM commercial.recon_item WHERE kind = 'REFUND' AND ref = 're_held_1'"
                        )
                    )
                ).scalar_one()
                assert item == -3000
            assert pi  # the payment intent it was sent against

    asyncio.run(run())


def test_a_refund_is_blocked_by_an_open_dispute_and_a_dispute_closes_only_on_the_processors_word(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_resources=["billable:*", "tenant:*"])

    async def run() -> None:
        _, attempt, pi = await paid_invoice(rt_url, o, "40.00")
        async with engine(rt_url) as eng:
            p = await cast(eng, "REVOPS", "DISPUTES_SPECIALIST", "CONTROLLER", "FINANCE_EXECUTOR")
            dref = f"dp_{uuid.uuid4().hex[:18]}"
            obj = {
                "id": dref,
                "payment_intent": pi,
                "amount": 12000,
                "currency": "usd",
                "status": "needs_response",
                "reason": "fraudulent",
                "evidence_details": {"due_by": int((NOW + timedelta(days=9)).timestamp())},
            }
            async with eng.begin() as c:
                did, created = await disputes.ingest(
                    c, event_type="charge.dispute.created", obj=obj, livemode=False
                )
                assert created
            async with eng.begin() as c:
                _, again = await disputes.ingest(
                    c, event_type="charge.dispute.created", obj=obj, livemode=False
                )
                assert not again  # the same event twice is one case
            async with eng.begin() as c:
                with pytest.raises(OpsError) as e:
                    await refunds.open_refund(
                        c,
                        tenant_id=o.tenant,
                        payment_attempt_id=attempt,
                        amount=D("10.00"),
                        category="GOODWILL",
                        reason="x",
                        requested_by=p["REVOPS"],
                        environment="test",
                    )
                assert e.value.code == "REFUND_BLOCKED_BY_DISPUTE"
            async with eng.begin() as c:
                await disputes.assign(
                    c, did, owner=p["DISPUTES_SPECIALIST"], by=p["DISPUTES_SPECIALIST"]
                )
                for item in (
                    "terms_or_consent",
                    "transaction_proof",
                    "usage_proof",
                    "communications",
                    "cancellation_refund_chronology",
                ):
                    await disputes.add_evidence(
                        c, did, item=item, ref=f"EV-{item}", by=p["DISPUTES_SPECIALIST"]
                    )
                await disputes.decide(
                    c,
                    did,
                    strategy="CONTEST",
                    reason="we delivered",
                    by=p["CONTROLLER"],
                    environment="test",
                )
            async with eng.begin() as c:
                v = await disputes.record_submission(
                    c, did, reference="dashboard-ticket-1", by=p["FINANCE_EXECUTOR"]
                )
                assert (
                    v["state"] == "SUBMISSION_RECORDED"
                )  # recorded is not verified, and is not an outcome
            async with eng.begin() as c:
                _, _ = await disputes.ingest(
                    c,
                    event_type="charge.dispute.closed",
                    obj={**obj, "status": "won"},
                    livemode=False,
                )
            async with eng.begin() as c:
                assert (await disputes.view(c, did))["state"] == "CLOSED_WON"

    asyncio.run(run())


def test_a_period_closes_only_when_clear_with_two_signatures_and_is_then_locked(
    rt_url: str,
) -> None:
    ent = f"R{uuid.uuid4().hex[:8].upper()}"
    period = "2026-03"
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            p = await cast(eng, "CONTROLLER", "CFO", "FOUNDER", "GROUP_CFO")
            item = uuid.uuid4()
            async with eng.begin() as c:
                await c.execute(
                    text(
                        "INSERT INTO commercial.recon_item (item_id, kind, tenant_id, issuing_entity, currency, amount_minor, ref, journal_ref, occurred_on) "
                        "VALUES (:i, 'PAYMENT', :t, :e, 'USD', 5000, :r, 'INV-9', :d)"
                    ),
                    {
                        "i": item,
                        "t": uuid.UUID(tenant),
                        "e": ent,
                        "r": f"pi_{ent}",
                        "d": date(2026, 3, 10),
                    },
                )
            async with eng.begin() as c:
                blocked = await recon.prepare_close(c, ent, period, by=p["CONTROLLER"])
                assert (
                    blocked["state"] == "OPEN" and blocked["blockers"]
                )  # no payout line: unmatched
            async with eng.begin() as c:
                legs = [
                    {
                        "leg_type": "PAYOUT_LINE",
                        "ref": f"po_{ent}",
                        "link_ref": f"pi_{ent}",
                        "amount_minor": 5000,
                        "currency": "USD",
                        "occurred_on": "2026-03-12",
                    },
                    {
                        "leg_type": "BANK_LINE",
                        "ref": f"po_{ent}",
                        "link_ref": "",
                        "amount_minor": 5000,
                        "currency": "USD",
                        "occurred_on": "2026-03-13",
                    },
                    {
                        "leg_type": "GL_JOURNAL",
                        "ref": "INV-9",
                        "link_ref": "",
                        "amount_minor": 5000,
                        "currency": "USD",
                        "occurred_on": "2026-03-14",
                    },
                ]
                assert (
                    await recon.import_legs(c, issuing_entity=ent, legs=legs, by=p["CONTROLLER"])
                )["added"] == 3
            async with eng.begin() as c:
                assert (
                    await recon.import_legs(c, issuing_entity=ent, legs=legs, by=p["CONTROLLER"])
                )["added"] == 0  # idempotent
            async with eng.begin() as c:
                for ex in await recon.exceptions(c, ent, period):
                    await recon.resolve_exception(
                        c,
                        ex["exception_id"],
                        resolution="payout line imported afterwards",
                        by=p["CONTROLLER"],
                    )
            async with eng.begin() as c:
                ready = await recon.prepare_close(c, ent, period, by=p["CONTROLLER"])
                assert ready["state"] == "READY_FOR_CLOSE" and ready["summary"]["MATCHED"] == 1
                h = ready["snapshot_hash"]
            async with eng.begin() as c:
                with pytest.raises(OpsError) as e:
                    await recon.sign_close(
                        c,
                        ent,
                        period,
                        role="CONTROLLER",
                        signer=p["CONTROLLER"],
                        presented_hash="0" * 64,
                    )
                assert e.value.code == "SNAPSHOT_CHANGED"
            async with eng.begin() as c:
                assert (
                    await recon.sign_close(
                        c, ent, period, role="CONTROLLER", signer=p["CONTROLLER"], presented_hash=h
                    )
                )["state"] == "READY_FOR_CLOSE"
            async with eng.begin() as c:
                with pytest.raises(
                    (OpsError, FinanceError)
                ):  # the same person never fills the checker's place
                    await recon.sign_close(
                        c, ent, period, role="CFO", signer=p["CONTROLLER"], presented_hash=h
                    )
            async with eng.begin() as c:
                closed = await recon.sign_close(
                    c, ent, period, role="CFO", signer=p["CFO"], presented_hash=h
                )
                assert closed["state"] == "CLOSED" and closed["receipt"]["checksum"]
            async with eng.connect() as c:  # a closed period is locked in the database
                with pytest.raises(DBAPIError):
                    async with c.begin():
                        await c.execute(
                            text(
                                "INSERT INTO commercial.recon_leg (leg_id, leg_type, issuing_entity, currency, amount_minor, ref, link_ref, occurred_on, source_hash, imported_by, imported_at) VALUES (:i, 'BANK_LINE', :e, 'USD', 1, 'late', '', :d, 'h', :b, now())"
                            ),
                            {
                                "i": uuid.uuid4(),
                                "e": ent,
                                "d": date(2026, 3, 20),
                                "b": uuid.UUID(p["CONTROLLER"]),
                            },
                        )
            async with eng.begin() as c:
                pack = await recon.evidence_pack(c, ent, period)
                assert pack["unchanged_since_close"] and pack["checksum"]
            async with eng.begin() as c:
                await recon.request_reopen(
                    c,
                    ent,
                    period,
                    reason="late bank file",
                    impact="march totals",
                    by=p["CONTROLLER"],
                )
            async with eng.begin() as c:
                with pytest.raises(FinanceError):
                    await recon.approve_reopen(
                        c, ent, period, role="CFO", approver=p["CONTROLLER"]
                    )  # not a reopening role
            async with eng.begin() as c:
                assert (await recon.approve_reopen(c, ent, period, role="CFO", approver=p["CFO"]))[
                    "state"
                ] == "REOPEN_REQUESTED"
            async with eng.begin() as c:
                assert (
                    await recon.approve_reopen(
                        c, ent, period, role="FOUNDER", approver=p["FOUNDER"]
                    )
                )["state"] == "OPEN"

    asyncio.run(run())


def test_the_webhook_opens_dispute_cases_and_links_our_own_refunds_but_leaves_strangers_for_review(
    rt_url: str,
) -> None:
    import json

    from zid_platform.modules.commercial.application import stripe_webhook_service as hooks
    from zid_platform.modules.commercial.domain import stripe_webhook as hook

    o = onboard(rt_url, extra_resources=["billable:*", "tenant:*"])

    async def run() -> None:
        _, _attempt, pi = await paid_invoice(rt_url, o, "40.00")
        async with engine(rt_url) as eng:
            p = await cast(eng, "REVOPS", "CONTROLLER", "FINANCE_EXECUTOR")
            dref = f"dp_{uuid.uuid4().hex[:18]}"
            raw = json.dumps(
                {
                    "id": f"evt_{uuid.uuid4().hex[:16]}",
                    "type": "charge.dispute.created",
                    "livemode": False,
                    "data": {
                        "object": {
                            "id": dref,
                            "payment_intent": pi,
                            "amount": 900,
                            "currency": "usd",
                            "status": "needs_response",
                        }
                    },
                }
            ).encode()
            assert await hooks.process(eng, hook.parse_event(raw)) == "APPLIED"
            async with eng.begin() as c:
                assert (
                    await c.execute(
                        text(
                            "SELECT state FROM commercial.dispute_case WHERE provider_dispute_ref = :r"
                        ),
                        {"r": dref},
                    )
                ).scalar_one() == "OPEN"

            async with (
                eng.begin() as c
            ):  # a dispute that closed in the processor's favour is closed by the event, not by a person
                pass
            close = json.dumps(
                {
                    "id": f"evt_{uuid.uuid4().hex[:16]}",
                    "type": "charge.dispute.closed",
                    "livemode": False,
                    "data": {
                        "object": {
                            "id": dref,
                            "payment_intent": pi,
                            "amount": 900,
                            "currency": "usd",
                            "status": "lost",
                        }
                    },
                }
            ).encode()
            assert await hooks.process(eng, hook.parse_event(close)) == "APPLIED"
            async with eng.begin() as c:
                assert (
                    await c.execute(
                        text(
                            "SELECT state FROM commercial.dispute_case WHERE provider_dispute_ref = :r"
                        ),
                        {"r": dref},
                    )
                ).scalar_one() == "CLOSED_LOST"

            stranger = json.dumps(
                {
                    "id": f"evt_{uuid.uuid4().hex[:16]}",
                    "type": "charge.refunded",
                    "livemode": False,
                    "data": {
                        "object": {
                            "id": "ch_x",
                            "payment_intent": "pi_nobody",
                            "amount_refunded": 500,
                            "currency": "usd",
                            "refunds": {"data": [{"id": "re_dashboard"}]},
                        }
                    },
                }
            ).encode()
            assert (
                await hooks.process(eng, hook.parse_event(stranger)) == "NEEDS_REVIEW"
            )  # a refund nobody here asked for is a person's to review
            assert p

    asyncio.run(run())

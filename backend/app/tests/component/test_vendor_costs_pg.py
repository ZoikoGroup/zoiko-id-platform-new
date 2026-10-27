"""The vendor-cost ledger (ZID-COM-001 §7, §8, §23, §31; CR-029).

Every provider attempt is costed; a provider error is never charged to the customer; retries use the retry
price; a rate spike blocks the route for new quotes until it is approved again; a supplier invoice is reconciled
line by line and every difference becomes a dispute; recording a cost never blocks a verification.
"""

from __future__ import annotations

import asyncio
import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import vendor_service as vendors

D = Decimal
NOW = datetime.now(UTC)


def uid() -> str:
    return str(uuid.uuid4())


def test_costs_retries_spikes_and_reconciliation(rt_url: str) -> None:
    tenant, actor = uid(), uid()
    vid = f"idv-{uuid.uuid4().hex[:8]}"
    adapter = f"adapter-{vid}"

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                await vendors.register(
                    c,
                    vendor_id=vid,
                    display_name="Acme IDV",
                    legal_role="SUBPROCESSOR",
                    adapter_id=adapter,
                    sku_id="ZID-IDV",
                    spike_threshold_pct=D(20),
                    actor=actor,
                )
                first = await vendors.record_rate(
                    c,
                    vendor_id=vid,
                    check_type="IDENTITY_VERIFICATION",
                    country="GB",
                    base_price=D("1.20"),
                    retry_price=D("0.40"),
                    currency="USD",
                    confidence="SIGNED_TARIFF",
                    source_ref="tariff 2026-10",
                    actor=actor,
                )
                assert first["route_status"] == "ACTIVE" and not first["spike"]
                assert "ZID-IDV" in await vendors.confirmed_skus(c)

                # a 30% rise over a 20% threshold blocks the route for new quotes until approved again
                spike = await vendors.record_rate(
                    c,
                    vendor_id=vid,
                    check_type="IDENTITY_VERIFICATION",
                    country="GB",
                    base_price=D("1.56"),
                    retry_price=D("0.40"),
                    currency="USD",
                    confidence="SIGNED_TARIFF",
                    source_ref="tariff 2026-11",
                    actor=actor,
                )
                assert spike["spike"] and spike["route_status"] == "NEEDS_APPROVAL"
                # (other tests may confirm ZID-IDV through their own vendors; this vendor's route is what changed)
                route = (
                    await c.execute(
                        text("SELECT status FROM commercial.vendor_route WHERE vendor_id = :v"),
                        {"v": vid},
                    )
                ).scalar_one()
                assert route == "NEEDS_APPROVAL"
                await vendors.set_route(
                    c,
                    vid,
                    "IDENTITY_VERIFICATION",
                    "GB",
                    status="ACTIVE",
                    reason="approved by CFO",
                    actor=actor,
                )
                current = (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM commercial.vendor_rate WHERE vendor_id = :v AND superseded_at IS NULL"
                        ),
                        {"v": vid},
                    )
                ).scalar_one()
                assert current == 1  # one current rate; the old one is kept, superseded

                # an estimate never confirms a route for quoting
                await vendors.record_rate(
                    c,
                    vendor_id=vid,
                    check_type="IDENTITY_VERIFICATION",
                    country="FR",
                    base_price=D(2),
                    retry_price=D(1),
                    currency="USD",
                    confidence="ESTIMATE",
                    source_ref="email quote",
                    actor=actor,
                )

            async with tenant_transaction(eng, ctx(tenant)) as c:

                def check(
                    ref: str,
                    status: str,
                    reasons: list[str],
                    subject: str = "s1",
                    country: str = "GB",
                ) -> Any:
                    return vendors.record_check(
                        c,
                        tenant_id=tenant,
                        adapter_id=adapter,
                        supplier_ref=ref,
                        check_type="IDENTITY_VERIFICATION",
                        country=country,
                        decision_status=status,
                        reasons=reasons,
                        subject_ref=subject,
                        at=NOW,
                    )

                assert await check("r1", "VERIFIED", [])
                assert await check(
                    "r2", "FAILED", ["INSUFFICIENT_DATA"]
                )  # the same subject again: a retry
                assert await check("r3", "FAILED", ["PROVIDER_UNAVAILABLE"], subject="s2")
                assert not await check(
                    "r1", "VERIFIED", []
                )  # the same supplier event never costs twice
                assert await check(
                    "r4", "VERIFIED", [], subject="s3", country="DE"
                )  # no rate for DE: unpriced
                unknown = await vendors.record_check(
                    c,
                    tenant_id=tenant,
                    adapter_id="nobody",
                    supplier_ref="x",
                    check_type="IDENTITY_VERIFICATION",
                    country="GB",
                    decision_status="VERIFIED",
                    reasons=[],
                    subject_ref="s9",
                )
                assert not unknown and vendors.STATS["no_vendor"] >= 1

            async with eng.begin() as c:
                rows = {
                    r[0]: (r[1], D(r[2]), r[3], r[4])
                    for r in (
                        await c.execute(
                            text(
                                "SELECT supplier_ref, outcome, cost, chargeable, charge_basis FROM commercial.vendor_cost_event "
                                "WHERE vendor_id = :v"
                            ),
                            {"v": vid},
                        )
                    ).all()
                }
                assert rows["r1"] == ("COMPLETED", D("1.56"), True, "BASE")
                assert rows["r2"] == ("INSUFFICIENT_DATA", D("0.40"), True, "RETRY")
                assert rows["r3"] == (
                    "PROVIDER_ERROR",
                    D("1.56"),
                    False,
                    "NONE",
                )  # costed, never charged
                assert rows["r4"][1] == 0

                # the supplier's invoice: one match, one wrong amount, one they did not bill, one we never saw, a duplicate
                await vendors.import_invoice(
                    c,
                    vendor_id=vid,
                    invoice_ref="INV-1",
                    lines=[
                        ("r1", D("1.56"), "USD"),
                        ("r2", D("0.45"), "USD"),
                        ("r9", D("1.56"), "USD"),
                        ("r1", D("1.56"), "USD"),
                    ],
                    actor=actor,
                )
                window = {
                    "period_start": NOW - timedelta(days=1),
                    "period_end": NOW + timedelta(days=1),
                }
                rec = await vendors.reconcile_invoice(
                    c, vendor_id=vid, invoice_ref="INV-1", **window
                )
                assert rec == {
                    "matched": 1,
                    "amount_mismatch": 1,
                    "missing_on_invoice": 2,  # r3 and r4
                    "unknown_on_invoice": 1,
                    "duplicate_on_invoice": 1,
                    "clean": False,
                }
                await vendors.reconcile_invoice(
                    c, vendor_id=vid, invoice_ref="INV-1", **window
                )  # idempotent
                disputes = (
                    await c.execute(
                        text(
                            "SELECT kind, count(*) FROM commercial.vendor_dispute WHERE vendor_id = :v GROUP BY kind"
                        ),
                        {"v": vid},
                    )
                ).all()
                assert {k: int(n) for k, n in disputes} == {
                    "AMOUNT_MISMATCH": 1,
                    "MISSING_ON_INVOICE": 2,
                    "UNKNOWN_ON_INVOICE": 1,
                    "DUPLICATE_ON_INVOICE": 1,
                }
                report = [
                    r
                    for r in await vendors.cost_report(
                        c, NOW - timedelta(days=1), NOW + timedelta(days=1)
                    )
                    if r["vendor"] == vid
                ]
                gb = next(r for r in report if r["country"] == "GB")
                assert (
                    gb["attempts"] == 3 and gb["chargeable_attempts"] == 2 and gb["margin"] is None
                )

    asyncio.run(run())


def test_a_cost_ledger_failure_never_blocks_a_verification() -> None:
    class Broken:
        def begin_nested(self) -> Any:
            raise ConnectionError("down")

    async def run() -> None:
        before = vendors.STATS["write_failed"]
        ok = await vendors.record_check(
            Broken(),  # type: ignore[arg-type]
            tenant_id=uid(),
            adapter_id="a",
            supplier_ref="r",
            check_type="IDENTITY_VERIFICATION",
            country="GB",
            decision_status="VERIFIED",
            reasons=[],
            subject_ref="s",
        )
        assert ok is False and vendors.STATS["write_failed"] == before + 1

    asyncio.run(run())


def test_the_verification_callback_records_the_attempt_cost() -> None:
    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src"
        / "zid_platform"
        / "modules"
        / "verification"
    )
    body = (src / "application" / "service.py").read_text(encoding="utf-8")
    assert "await record_vendor_check(" in body and "supplier_ref=event_id" in body

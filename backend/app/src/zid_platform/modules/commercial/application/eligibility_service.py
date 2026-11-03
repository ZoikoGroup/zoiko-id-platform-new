"""Commercial activation and eligibility for one organization (ZID-UX-COM-009 O01; slice L-14d, CR-054).

Billing being visible is not billing being active. Each gate below is a fact read from a record, never a claim: a failed gate names itself and what to do,
and an organization that passes them all is only ELIGIBLE (an offer can be reviewed), never charged. The organization's readiness state belongs to the
onboarding module and is handed in at start-up; until it is, the identity gate fails (fail closed).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.commercial.application import invoicing_service as invoicing
from zid_platform.modules.commercial.infrastructure import catalogue_store

OrgState = Callable[[AsyncConnection, str], Awaitable[str]]
_org_state: OrgState | None = None
READY_STATES = ("ACTIVE", "ACTIVE_LIMITED")


def configure_org_state(fn: OrgState | None) -> None:
    global _org_state
    _org_state = fn


def _gate(key: str, label: str, passed: bool, detail: str, fix: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "passed": passed,
        "detail": detail,
        "remedy": None if passed else fix,
    }


async def check(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    issuer: str | None,
    tax_configured: bool,
    payments_configured: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    org = await _org_state(conn, tenant_id) if _org_state is not None else "UNKNOWN"
    linked = await invoicing.account_of_tenant(conn, tenant_id)
    account = None
    if linked:
        account = (
            (
                await conn.execute(
                    text("SELECT kind, country FROM commercial.account WHERE account_id = :a"),
                    {"a": uuid.UUID(linked["account_id"])},
                )
            )
            .mappings()
            .first()
        )
    order = (
        (
            await conn.execute(
                text(
                    "SELECT o.order_id, o.status, o.pilot, o.pilot_ends_at, o.effective_from, q.region, q.digest "
                    "FROM commercial.sales_order o JOIN commercial.quote q ON q.quote_id = o.quote_id "
                    "WHERE o.tenant_id = :t AND o.status IN ('SIGNED', 'ACTIVATED') ORDER BY o.created_at DESC LIMIT 1"
                ),
                {"t": uuid.UUID(tenant_id)},
            )
        )
        .mappings()
        .first()
    )
    released = False
    if order:
        released = (
            await conn.execute(
                text(
                    "SELECT 1 FROM commercial.region_release WHERE region = :r AND sre_gate_passed"
                ),
                {"r": order["region"]},
            )
        ).first() is not None
    books = [b for b in await catalogue_store.price_books(conn) if b["status"] == "APPROVED"]
    pilot = (
        await conn.execute(
            text(
                "SELECT ends_at FROM commercial.finance_pilot_credit WHERE tenant_id = :t AND ends_at > :n LIMIT 1"
            ),
            {"t": uuid.UUID(tenant_id), "n": now},
        )
    ).first()
    commitment = None
    if order:
        commitment = (
            await conn.execute(
                text(
                    "SELECT state FROM commercial.commitment_request WHERE order_id = :o ORDER BY created_at DESC LIMIT 1"
                ),
                {"o": order["order_id"]},
            )
        ).first()
    signed = bool((await finance.policy_state(conn))["effective"])

    gates = [
        _gate("IDENTITY", "Organization verified", org in READY_STATES, f"Readiness state: {org}.", "Complete the organization's verification before any paid activation."),
        _gate("LEGAL_PAYER", "Legal payer on a commercial account", account is not None, "A commercial account is linked." if account else "No commercial account is linked.", "Create the account and link the organization."),
        _gate("ISSUER", "Legal issuer configured", bool(issuer), f"Issuing entity {issuer}." if issuer else "No issuing entity.", "Configure the issuing legal entity."),
        _gate("TAX", "Tax engine configured", tax_configured, "A tax engine answers." if tax_configured else "Tax answers 'not configured'; an invoice cannot be issued without an explicit zero-tax override.", "Configure the tax engine."),
        _gate("MERCHANT", "Payment processor configured", payments_configured, "A processor is connected." if payments_configured else "No payment processor is connected.", "Connect the payment processor."),
        _gate("CATALOG", "Approved price book", bool(books), f"{len(books)} approved price book(s)." if books else "No approved price book.", "Approve a price book."),
        _gate("REGION", "Region released", released, f"Region {order['region']} is released." if released and order else "No signed order in a released region.", "Release the region or quote a released one."),
        _gate("CONTRACT", "Signed order or contract", order is not None, "A signed order exists." if order else "No signed order.", "Record the customer's signed order."),
        _gate("POLICY", "Finance policy signed by the Founder and the CFO", signed, "The policy is effective." if signed else "The finance policy has no authority until both sign it.", "The Founder and the CFO sign the policy."),
    ]  # fmt: skip
    failed = [g for g in gates if not g["passed"]]
    if account and account["kind"] == "INTERNAL":
        classification = "INTERNAL"
    elif pilot or (order and order["pilot"]):
        classification = "PILOT_NON_BILLABLE"
    elif order and order["status"] == "ACTIVATED":
        classification = "COMMERCIAL"
    else:
        classification = "FREE"
    if order and order["status"] == "ACTIVATED":
        state = "ACTIVE"
    elif failed:
        state = "NOT_ELIGIBLE"
    elif order and (commitment is None or commitment.state != "APPROVED"):
        state = "AWAITING_APPROVAL"
    else:
        state = "ELIGIBLE"
    schedule = None
    if order:
        schedule = {
            "order_id": str(order["order_id"]),
            "quote_hash": order["digest"],
            "effective_from": order["effective_from"].isoformat(),
            "pilot_ends_at": order["pilot_ends_at"].isoformat() if order["pilot_ends_at"] else None,
        }
    return {
        "tenant_id": tenant_id,
        "classification": classification,
        "state": state,
        "gates": gates,
        "blockers": [g["key"] for g in failed],
        "schedule": schedule,
        "commitment_state": commitment.state if commitment else None,
        "note": "Eligible means an offer can be reviewed. It is not a charge, a subscription or an automatic conversion.",
    }

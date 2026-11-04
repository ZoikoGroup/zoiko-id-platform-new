"""The customer billing workspace's own facts: the overview with its one next action, and the versioned billing profile (ZID-UX-COM-008 B01, B04; slice L-14d, CR-054).

The server decides what the page says. A free account is shown as free, with no invented invoice or price; the single next action is the most important
valid one; the payer's legal name and country come from the commercial account, so changing them is a reviewed change, never an inline edit. The profile
is replaced only with the version it was based on, every version is kept, and tax identifiers are never written to the change log.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.application import invoicing_service as invoicing
from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.infrastructure import catalogue_store

TAX_TYPES = ("VAT", "GST", "EIN", "COMPANY_REGISTRATION", "OTHER")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
ADDRESS_FIELDS = ("line1", "line2", "city", "region", "postal_code", "country")
CONTACT_FIELDS = ("invoice_email", "notices_email", "tax_document_email")
HELD = {"RESTRICTED", "SUSPENDED", "CANCELED", "CANCELLED"}

MESSAGES = {
    "FREE": "You are using a free account. No paid subscription is active.",
    "PENDING_APPROVAL": "Your plan change is awaiting approval.",
    "ACTIVE": "Your plan is active.",
    "PAST_DUE": "An invoice is past its due date.",
    "PILOT": "You are in an evaluation program. There is no automatic conversion to a paid plan.",
    "RESTRICTED": "This account has a restriction. Contact Sales; no checkout is available.",
}


class ProfileError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code


# ----------------------------------------------------------------------------------------------------------------------- overview


async def _account(conn: AsyncConnection, tenant_id: str) -> dict[str, Any] | None:
    linked = await invoicing.account_of_tenant(conn, tenant_id)
    if linked is None:
        return None
    row = (
        await conn.execute(
            text("SELECT legal_name, country, currency FROM commercial.account WHERE account_id = :a"), {"a": uuid.UUID(linked["account_id"])}
        )
    ).mappings().first()  # fmt: skip
    return (
        None
        if row is None
        else {
            "legal_name": row["legal_name"],
            "country": row["country"],
            "currency": row["currency"],
        }
    )


async def overview(
    conn: AsyncConnection, tenant_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    state = await invoicing.billing_state(conn)
    ent = await catalogue_store.active_entitlement(conn)
    invoices = await invoicing.list_invoices(conn)
    account = await _account(conn, tenant_id)
    pending = (
        await conn.execute(
            text(
                "SELECT request_id FROM commercial.commitment_request WHERE state = 'PENDING_APPROVAL' ORDER BY created_at LIMIT 1"
            )
        )
    ).first()
    plan_asked = (
        await conn.execute(
            text("SELECT 1 FROM commercial.plan_request WHERE status = 'OPEN' LIMIT 1")
        )
    ).first()
    pilot = (
        await conn.execute(
            text(
                "SELECT ends_at FROM commercial.finance_pilot_credit WHERE tenant_id = :t AND ends_at > :n ORDER BY ends_at DESC LIMIT 1"
            ),
            {"t": uuid.UUID(tenant_id), "n": now},
        )
    ).first()
    order = (
        await conn.execute(
            text("SELECT order_id, term_months, effective_from, pilot, pilot_ends_at FROM commercial.sales_order WHERE tenant_id = :t AND status = 'ACTIVATED' ORDER BY activated_at DESC LIMIT 1"),
            {"t": uuid.UUID(tenant_id)},
        )
    ).mappings().first()  # fmt: skip
    overdue = [
        i
        for i in invoices
        if i["outstanding"]
        and Decimal(i["outstanding"]) > 0
        and i["due_at"]
        and datetime.fromisoformat(i["due_at"]) < now
    ]
    published = bool(await catalogue_store.plans(conn))

    if state["state"] in HELD or state["safety_hold"]:
        kind = "RESTRICTED"
    elif state["state"] == "PAST_DUE" or overdue:
        kind = "PAST_DUE"
    elif pending is not None or plan_asked is not None:
        kind = "PENDING_APPROVAL"
    elif pilot is not None or (order is not None and order["pilot"]):
        kind = "PILOT"
    elif ent is not None and ent.get("source") == "ORDER":
        kind = "ACTIVE"
    else:
        kind = "FREE"

    if kind == "PAST_DUE" and overdue:
        oldest = min(overdue, key=lambda i: i["due_at"])
        action = {
            "kind": "PAY_INVOICE",
            "label": "Pay invoice",
            "target": "invoices",
            "ref": oldest["invoice_id"],
            "reason": f"{oldest['outstanding']} {oldest['currency']} is outstanding.",
        }
    elif kind == "PENDING_APPROVAL" and pending is not None:
        action = {
            "kind": "COMPLETE_APPROVAL",
            "label": "Complete approval",
            "target": "commitments",
            "ref": str(pending.request_id),
            "reason": "A commitment is waiting for an approver.",
        }
    elif kind == "PENDING_APPROVAL":
        action = {
            "kind": "NONE",
            "label": "No action",
            "target": None,
            "ref": None,
            "reason": "Your plan request is with the commercial team.",
        }
    elif kind in ("RESTRICTED",):
        action = {
            "kind": "CONTACT_SALES",
            "label": "Contact Sales",
            "target": None,
            "ref": None,
            "reason": "Checkout is not available while the account has a restriction.",
        }
    elif kind in ("FREE", "PILOT") and published:
        action = {
            "kind": "VIEW_PLANS",
            "label": "View plans",
            "target": "features",
            "ref": None,
            "reason": "Paid activation is a separate approval; nothing converts by itself.",
        }
    else:
        action = {
            "kind": "NONE",
            "label": "No action",
            "target": None,
            "ref": None,
            "reason": "Nothing needs your attention.",
        }

    pilot_ends = pilot.ends_at if pilot else (order["pilot_ends_at"] if order else None)
    return {
        "as_of": now.isoformat(), "state": kind,
        "message": MESSAGES[kind] if kind != "PAST_DUE" or not overdue else f"{len(overdue)} invoice(s) are past their due date.",
        "billing_state": state["state"], "safety_hold": state["safety_hold"], "restricted": state["restricted"],
        "plan": {
            "code": ent["plan_code"] if ent else None, "source": ent.get("source") if ent else None,
            "effective_from": ent["effective_from"].isoformat() if ent else None,
            "term_months": int(order["term_months"]) if order else None, "contract_ref": str(order["order_id"]) if order else None,
        },
        "next_action": action,
        "payer": {
            "legal_name": account["legal_name"] if account else None, "country": account["country"] if account else None,
            "currency": account["currency"] if account else None,
            "issuer": (invoices[0]["number"] or "").rsplit("-", 1)[0] or None if invoices else None,
        },
        "pilot": {"ends_at": pilot_ends.isoformat() if pilot_ends else None, "automatic_conversion": False} if kind == "PILOT" else None,
        "recent_activity": [
            {"kind": "INVOICE", "ref": i["number"], "invoice_id": i["invoice_id"], "at": i["issued_at"], "amount": i["gross"], "currency": i["currency"],
             "status": "PAID" if i["outstanding"] is not None and Decimal(i["outstanding"]) == 0 else "OPEN"}
            for i in invoices[:5]
        ],
    }  # fmt: skip


# ------------------------------------------------------------------------------------------------------------------------ profile


def clean(
    address: dict[str, Any], tax_ids: list[dict[str, Any]], contacts: dict[str, Any]
) -> tuple[dict[str, str], list[dict[str, str]], dict[str, str]]:
    """Trim and validate. Anything not named here is refused, so nothing unexpected is stored."""
    if set(address) - set(ADDRESS_FIELDS):
        raise ProfileError("ADDRESS_INVALID", "unknown address field")
    addr = {k: str(address.get(k) or "").strip()[:120] for k in ADDRESS_FIELDS}
    if not addr["line1"] or not addr["city"] or not re.fullmatch(r"[A-Z]{2}", addr["country"]):
        raise ProfileError(
            "ADDRESS_INVALID", "an address needs a first line, a city and a two-letter country"
        )
    if len(tax_ids) > 5:
        raise ProfileError("TAX_ID_INVALID", "at most five tax identifiers")
    taxes: list[dict[str, str]] = []
    for t in tax_ids:
        kind, value = str(t.get("type") or ""), str(t.get("value") or "").strip()
        if (
            kind not in TAX_TYPES
            or not re.fullmatch(r"[A-Za-z0-9 .\-/]{4,40}", value)
            or any(x["type"] == kind for x in taxes)
        ):
            raise ProfileError(
                "TAX_ID_INVALID",
                "each tax identifier has a known type once and 4 to 40 letters, digits or . - /",
            )
        taxes.append(
            {"type": kind, "value": value, "verification": "UNVERIFIED"}
        )  # there is no validation service: never claim it was checked
    if set(contacts) - set(CONTACT_FIELDS):
        raise ProfileError("CONTACT_INVALID", "unknown contact field")
    cont = {k: str(contacts.get(k) or "").strip().lower() for k in CONTACT_FIELDS}
    if not cont["invoice_email"] or any(v and not EMAIL.fullmatch(v) for v in cont.values()):
        raise ProfileError(
            "CONTACT_INVALID",
            "an invoice contact is required and every address must be an email address",
        )
    return addr, taxes, cont


def _profile_view(row: Any, account: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "version": row["version"] if row else 0,
        "payer": {
            "legal_name": account["legal_name"] if account else None, "country": account["country"] if account else None,
            "currency": account["currency"] if account else None, "change_note": "The legal payer and tax country change through a reviewed request, not here.",
        },
        "address": row["address"] if row else None, "tax_ids": row["tax_ids"] if row else [], "contacts": row["contacts"] if row else None,
        "contact_verification": "UNVERIFIED", "updated_at": row["updated_at"].isoformat() if row else None,
    }  # fmt: skip


async def get_profile(conn: AsyncConnection, tenant_id: str) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.billing_profile WHERE tenant_id = :t"),
                {"t": uuid.UUID(tenant_id)},
            )
        )
        .mappings()
        .first()
    )
    return _profile_view(row, await _account(conn, tenant_id))


async def history(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(text("SELECT version, changed_fields, changed_by, changed_at FROM commercial.billing_profile_history ORDER BY version DESC LIMIT 50"))
    ).mappings().all()  # fmt: skip
    return [
        {
            "version": r["version"],
            "changed_fields": list(r["changed_fields"]),
            "changed_by": str(r["changed_by"]),
            "changed_at": r["changed_at"].isoformat(),
        }
        for r in rows
    ]


async def save_profile(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    by: str,
    expected_version: int,
    address: dict[str, Any],
    tax_ids: list[dict[str, Any]],
    contacts: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    addr, taxes, cont = clean(address, tax_ids, contacts)
    account = await _account(conn, tenant_id)
    if account is None:
        raise ProfileError(
            "ACCOUNT_REQUIRED",
            "a billing profile belongs to an organization that has a commercial account",
        )
    if account["country"] and addr["country"] != account["country"]:
        raise ProfileError(
            "PAYER_CHANGE_REQUIRES_REVIEW",
            "a different country changes the legal payer or tax country, which is a reviewed change",
        )
    row = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.billing_profile WHERE tenant_id = :t FOR UPDATE"),
                {"t": uuid.UUID(tenant_id)},
            )
        )
        .mappings()
        .first()
    )
    current = row["version"] if row else 0
    if current != expected_version:
        raise ProfileError(
            "VERSION_CONFLICT", f"the profile is at version {current}; read it again"
        )
    changed = [
        n
        for n, new, old in (
            ("address", addr, row["address"] if row else None),
            ("tax_ids", taxes, row["tax_ids"] if row else None),
            ("contacts", cont, row["contacts"] if row else None),
        )
        if new != old
    ]
    if not changed:
        raise ProfileError("NO_CHANGE", "nothing changed")
    import json

    params = {
        "t": uuid.UUID(tenant_id),
        "v": current + 1,
        "a": json.dumps(addr),
        "x": json.dumps(taxes),
        "c": json.dumps(cont),
        "by": uuid.UUID(by),
        "n": now,
    }
    if row is None:
        await conn.execute(
            text(
                "INSERT INTO commercial.billing_profile (tenant_id, version, address, tax_ids, contacts, updated_by, updated_at) VALUES (:t, :v, CAST(:a AS jsonb), CAST(:x AS jsonb), CAST(:c AS jsonb), :by, :n)"
            ),
            params,
        )
    else:
        await conn.execute(
            text(
                "UPDATE commercial.billing_profile SET version = :v, address = CAST(:a AS jsonb), tax_ids = CAST(:x AS jsonb), contacts = CAST(:c AS jsonb), updated_by = :by, updated_at = :n WHERE tenant_id = :t"
            ),
            params,
        )
    await conn.execute(
        text(
            "INSERT INTO commercial.billing_profile_history (tenant_id, version, address, tax_ids, contacts, changed_fields, changed_by, changed_at) VALUES (:t, :v, CAST(:a AS jsonb), CAST(:x AS jsonb), CAST(:c AS jsonb), :f, :by, :n)"
        ),
        {**params, "f": changed},
    )
    # field NAMES only: addresses, tax identifiers and email addresses never reach the shared change log
    await log_change(
        conn,
        kind="BILLING_PROFILE_CHANGED",
        ref=tenant_id,
        tenant_id=tenant_id,
        actor=by,
        detail={"version": current + 1, "changed": changed},
    )
    return await get_profile(conn, tenant_id)

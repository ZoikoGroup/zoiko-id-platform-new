"""Commercial catalogue, entitlements and the tenant's billable ledger (ZID-COM-001 §2, §6, §7, §26, §29).

Public
    GET  /v1/catalog                                  published plans, released capabilities, APPROVED prices only
    POST /v1/billing/estimate                         non-binding estimate for the pricing calculator (same rating function)

Tenant (commercial.read)
    GET  /v1/tenants/{tenant_id}/entitlements         the caller's own plan, limits, capabilities and usage of caps
    GET  /v1/usage/billable?month=YYYY-MM             the caller's own billable ledger summary for a month
    GET  /v1/usage/billable/events?month=&meter=      the events behind a meter, so a customer can reproduce its bill
    GET  /v1/usage/billable/export?month=             the same ledger as CSV
    GET  /v1/usage/rated?month=YYYY-MM                the rated lines of a month, each with its formula
    GET  /v1/invoices, /v1/invoices/{id}, /v1/invoices/{id}/document   issued invoices and their documents
    GET  /v1/billing/preview                         bill so far and projected month-end bill (estimate)
    GET  /v1/budgets, PUT /v1/budgets, POST /v1/budgets:evaluate   usage budgets (commercial.budget.manage)
    GET  /v1/billing/orders                          the signed orders behind this tenant's plan
    GET  /v1/billing/state                           billing state, what it restricts and what it never does

The operator surface (price books, plans, overrides, freezing windows, adjustments) lives with the operator
plane in `operator_access/api/commercial_admin.py`. Client-supplied tenant, plan or price values are never
trusted: a tenant sees only its own rows.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.application import (
    budget_service,
    invoicing_service,
    orders_service,
    rating_service,
    recorder,
    service,
)
from zid_platform.modules.commercial.application import (
    plan_request_service as plan_requests,
)
from zid_platform.modules.commercial.domain.billable import METERS, window_month
from zid_platform.modules.commercial.domain.budgets import BudgetError
from zid_platform.modules.commercial.domain.invoicing import InvoicingError
from zid_platform.modules.commercial.domain.plan_requests import PlanRequestError
from zid_platform.modules.commercial.infrastructure import billable_store
from zid_platform.protected import protected_action

router = APIRouter(tags=["commercial"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

# ------------------------------------------------------------------------------- public and tenant


@router.get("/v1/catalog")
async def catalog(request: Request) -> dict[str, Any]:
    """Public and cached: only published plans, released capabilities and APPROVED prices. No tenant data."""
    async with request_engine(request).begin() as conn:
        out = await service.catalogue_view(conn)
    return out


@router.get("/v1/tenants/{tenant_id}/entitlements")
async def tenant_entitlements(
    tenant_id: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    mine = caller.ctx.tenant_id or ""
    try:
        same = uuid.UUID(tenant_id) == uuid.UUID(mine)
    except ValueError:
        same = False
    if not same:  # another tenant's id reveals nothing
        raise ZidError("TENANT_NOT_FOUND", "No such tenant.", http_status=404)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource=f"tenant:{mine}")
        # The composition root wires each counted capability to its owning module (no cross-module import).
        counters = getattr(request.app.state, "commercial_counters", {})
        used = {key: await count(conn) for key, count in counters.items()}
        return await service.entitlement_view(conn, used=used)


class EstimateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: str = Field(pattern=r"^[A-Z_]{3,24}$")
    mah: int = Field(default=0, ge=0, le=1_000_000_000)
    auth_events: int = Field(default=0, ge=0, le=100_000_000_000)
    policy_decisions: int = Field(default=0, ge=0, le=100_000_000_000)
    m2m_grants: int = Field(default=0, ge=0, le=100_000_000_000)
    sso_connections: int = Field(default=0, ge=0, le=10_000)
    scim_connections: int = Field(default=0, ge=0, le=10_000)


@router.post("/v1/billing/estimate")
async def billing_estimate(body: EstimateBody, request: Request) -> dict[str, Any]:
    """Public pricing calculator: the same rating function over the approved price book. Never a quotation."""
    usage = {
        "MAH": Decimal(body.mah),
        "AUTH_EVENT": Decimal(body.auth_events),
        "POLICY_DECISION": Decimal(body.policy_decisions),
        "M2M_GRANT": Decimal(body.m2m_grants),
    }
    async with request_engine(request).begin() as conn:
        return await service.estimate(
            conn,
            plan_code=body.plan,
            usage=usage,
            connections={"saml_sso": body.sso_connections, "scim": body.scim_connections},
        )


# ------------------------------------------------------------------------------- billable ledger


def _month(value: str) -> date:
    try:
        y, m = value.split("-")
        d = date(int(y), int(m), 1)
    except ValueError as e:
        raise ZidError("VALIDATION_FAILED", "month must be YYYY-MM.", http_status=422) from e
    return d


@router.get("/v1/usage/billable")
async def billable_summary(
    request: Request,
    caller: CallerDep,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
) -> dict[str, Any]:
    """One month's billable counts per meter with adjustments and the frozen-window hash, for this tenant."""
    m = _month(month) if month else window_month(datetime.now(UTC))
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        return await recorder.ledger_summary(conn, m)


@router.get("/v1/usage/billable/events")
async def billable_events(
    request: Request,
    caller: CallerDep,
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
    meter: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict[str, Any]:
    if meter is not None and meter not in METERS:
        raise ZidError("VALIDATION_FAILED", "Unknown meter.", http_status=422)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        rows = await billable_store.window_events(conn, _month(month), meter)
    return {
        "items": [
            {
                "event_id": str(r["event_id"]),
                "meter": r["meter_id"],
                "quantity": format(r["quantity"].normalize(), "f"),
                "occurred_at": r["occurred_at"].isoformat(),
                "late": r["late"],
                "recovered": r["recovered"],
            }
            for r in rows[:limit]
        ],
        "truncated": len(rows) > limit,
    }


@router.get("/v1/usage/billable/export")
async def billable_export(
    request: Request, caller: CallerDep, month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")]
) -> Response:
    """The customer's own ledger as CSV: event ids, meter, quantity and time. No principals, no other tenant."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        rows = await billable_store.window_events(conn, _month(month))
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["event_id", "meter", "quantity", "occurred_at", "late", "recovered", "row_hash"])
    for r in rows:
        w.writerow(
            [
                r["event_id"],
                r["meter_id"],
                format(r["quantity"].normalize(), "f"),
                r["occurred_at"].isoformat(),
                str(r["late"]).lower(),
                str(r["recovered"]).lower(),
                r["row_hash"],
            ]
        )
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="billable-{month}.csv"'},
    )


@router.get("/v1/usage/rated")
async def rated_usage(
    request: Request,
    caller: CallerDep,
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
) -> dict[str, Any]:
    """The tenant's computed rating for a month: every line with the arithmetic that produced it."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        out = await rating_service.latest(conn, _month(month))
    if out is None:
        raise ZidError("RATING_NOT_FOUND", "This month has not been rated yet.", http_status=404)
    return out


# ------------------------------------------------------------------------------- invoices and billing state


def _invoice_error(e: InvoicingError) -> ZidError:
    status = {"INVOICE_NOT_FOUND": 404, "INVOICE_NOT_ISSUED": 404}.get(e.code, 409)
    return ZidError(e.code, e.detail or e.code, http_status=status)


@router.get("/v1/invoices")
async def invoices(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        return {"items": await invoicing_service.list_invoices(conn)}


@router.get("/v1/invoices/{invoice_id}")
async def invoice(invoice_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        try:
            out = await invoicing_service.view(conn, str(invoice_id))
        except InvoicingError as e:
            raise _invoice_error(e) from e
    if (
        out["status"] != "ISSUED"
    ):  # a draft is the operator's working copy, not the customer's invoice
        raise ZidError("INVOICE_NOT_FOUND", "No such invoice.", http_status=404)
    return out


@router.get("/v1/invoices/{invoice_id}/document")
async def invoice_document(invoice_id: uuid.UUID, request: Request, caller: CallerDep) -> Response:
    """The issued invoice document. Its SHA-256 equals the invoice's `document_digest`."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        try:
            doc = await invoicing_service.document(conn, str(invoice_id))
        except InvoicingError as e:
            raise _invoice_error(e) from e
    return Response(doc, media_type="text/plain; charset=utf-8")


@router.get("/v1/billing/state")
async def billing_state(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The account's billing state, what it restricts and, explicitly, what it never restricts."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        return await invoicing_service.billing_state(conn)


@router.get("/v1/billing/orders")
async def my_orders(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The signed orders behind this tenant's entitlement, by its server-derived tenant id."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        return {"items": await orders_service.orders_of_tenant(conn, caller.ctx.tenant_id or "")}


class BudgetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meter: str | None = Field(default=None, max_length=32)
    monthly_limit: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    thresholds: list[int] = Field(default_factory=lambda: [50, 80, 100], min_length=1, max_length=5)


def _counts(request: Request) -> Any:
    return getattr(request.app.state, "commercial_counters", {})


@router.get("/v1/billing/preview")
async def billing_preview(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The bill so far and a projected month-end bill: an estimate with its assumptions, never a quotation."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        connections = {k: await f(conn) for k, f in _counts(request).items()}
        return budget_service.public(await budget_service.preview(conn, connections=connections))


class PlanRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: str = Field(pattern=r"^[A-Z_]{3,24}$")
    note: str | None = Field(default=None, max_length=1000)


_PLAN_REQUEST_STATUS = {
    "PLAN_NOT_AVAILABLE": 422,
    "PLAN_ALREADY_CURRENT": 409,
    "PLAN_REQUEST_OPEN": 409,
    "PLAN_REQUEST_CLOSED": 409,
    "NOT_FOUND": 404,
    "VALIDATION_FAILED": 422,
}


def plan_request_error(e: PlanRequestError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_PLAN_REQUEST_STATUS.get(e.code, 422),
    )


@router.get("/v1/billing/plan-requests")
async def my_plan_requests(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The company's own requests for a different plan, newest first, with the commercial team's answer."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        return {"items": await plan_requests.my_requests(conn)}


@router.post("/v1/billing/plan-requests", status_code=201)
async def request_a_plan(
    body: PlanRequestBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Ask for a different plan. Nothing about the entitlement changes: the move is a signed order, raised by us."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="commercial.plan.request", resource="billable:*"
        )
        try:
            return await plan_requests.request_plan(
                conn,
                caller.ctx.tenant_id or "",
                caller.ctx.principal_id or "",
                body.plan,
                body.note,
            )
        except PlanRequestError as e:
            raise plan_request_error(e) from e


@router.post("/v1/billing/plan-requests/{request_id}:withdraw")
async def withdraw_plan_request(
    request_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="commercial.plan.request", resource="billable:*"
        )
        try:
            return await plan_requests.withdraw(
                conn, caller.ctx.tenant_id or "", caller.ctx.principal_id or "", str(request_id)
            )
        except PlanRequestError as e:
            raise plan_request_error(e) from e


@router.get("/v1/budgets")
async def list_budgets(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        return {"items": await budget_service.budgets(conn)}


@router.put("/v1/budgets")
async def put_budget(body: BudgetBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    """A monthly target for the whole bill (with a currency) or one meter. It only ever notifies."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.budget.manage", resource="budget:*")
        try:
            return await budget_service.set_budget(
                conn,
                tenant_id=caller.ctx.tenant_id or "",
                meter=body.meter,
                monthly_limit=body.monthly_limit,
                currency=body.currency,
                thresholds=body.thresholds,
                actor=caller.ctx.principal_id or "",
            )
        except BudgetError as e:
            raise ZidError(e.code, e.detail or e.code, http_status=422) from e


@router.post("/v1/budgets:evaluate")
async def evaluate_budgets(request: Request, caller: CallerDep) -> dict[str, Any]:
    """Check budgets and included allowances now; each threshold notifies once per month."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.budget.manage", resource="budget:*")
        connections = {k: await f(conn) for k, f in _counts(request).items()}
        return await budget_service.evaluate(
            conn, tenant_id=caller.ctx.tenant_id or "", connections=connections
        )

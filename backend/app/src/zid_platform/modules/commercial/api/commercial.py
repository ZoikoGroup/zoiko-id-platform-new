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
    GET  /v1/billing/payment                         what is due now and the saved card (brand and last four only)
    POST /v1/billing/payment/setup, /v1/billing/payment/complete   save or replace the card on Stripe's hosted page
                                                     (commercial.payment.manage; CR-034)

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
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.application import (
    billing_gate,
    budget_service,
    entitlement_resolver,
    invoicing_service,
    orders_service,
    rating_service,
    recorder,
    service,
    usage_reservations,
)
from zid_platform.modules.commercial.application import (
    plan_request_service as plan_requests,
)
from zid_platform.modules.commercial.application.adapters import (
    PaymentSetupError,
    StripeCustomers,
    from_minor_units,
)
from zid_platform.modules.commercial.application.change_log import log_change
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


@router.get("/v1/entitlements/effective")
async def effective_entitlements(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The five-band view of what this tenant can use (ZID-ENG-DEC-008): Included, Available as an add-on, Available with upgrade."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="commercial.read", resource=f"tenant:{caller.ctx.tenant_id or ''}"
        )
        return await entitlement_resolver.effective(
            conn, enforcement=getattr(request.app.state, "commercial_enforcement", "shadow")
        )


@router.get("/v1/usage/summary")
async def usage_summary(request: Request, caller: CallerDep) -> dict[str, Any]:
    """Committed use, open reservations and any cap per meter for this period, with how incomplete and duplicate checks are treated."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="commercial.read", resource=f"tenant:{caller.ctx.tenant_id or ''}"
        )
        return await usage_reservations.summary(conn)


def _usage_scope(caller: Caller) -> None:
    """Reserving, committing and releasing usage is done by the platform's own services and workers, on a client with this scope."""
    if "usage:write" not in caller.scope:
        raise ZidError("SCOPE_INSUFFICIENT", "usage:write is required.", http_status=403)


class UsageReserveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meter: str = Field(pattern=r"^[a-z]+[.][a-z]+$")
    quantity: int = Field(ge=1, le=1_000_000_000)
    idempotency_key: str = Field(min_length=8, max_length=128)


class UsageResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


@router.post("/v1/usage/reservations", status_code=201)
async def reserve_usage(
    body: UsageReserveBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Reserve a quantity of a meter before the work happens. Idempotent by key; refuses only an operation that crosses a cap."""
    _usage_scope(caller)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await usage_reservations.reserve(
            conn, tenant, body.meter, body.quantity, body.idempotency_key
        )


@router.post("/v1/usage/reservations/{reservation_id}:commit")
async def commit_usage(
    reservation_id: uuid.UUID, body: UsageResolveBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _usage_scope(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await usage_reservations.commit(conn, str(reservation_id), outcome=body.outcome)


@router.post("/v1/usage/reservations/{reservation_id}:release")
async def release_usage(
    reservation_id: uuid.UUID, body: UsageResolveBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _usage_scope(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await usage_reservations.release(conn, str(reservation_id), outcome=body.outcome)


@router.get("/v1/entitlements/downgrade-impact")
async def downgrade_impact(
    request: Request, caller: CallerDep, to: Annotated[str, Query(pattern=r"^C[1-5]$")]
) -> dict[str, Any]:
    """What moving to a lower band would switch off, before anyone commits to it. It deletes nothing."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="commercial.read", resource=f"tenant:{caller.ctx.tenant_id or ''}"
        )
        return await entitlement_resolver.downgrade_view(conn, to)


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


@router.get("/v1/invoices", dependencies=[Depends(billing_gate.permission("billing.invoice.read"))])
async def invoices(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        return {"items": await invoicing_service.list_invoices(conn)}


@router.get(
    "/v1/invoices/{invoice_id}",
    dependencies=[Depends(billing_gate.permission("billing.invoice.read"))],
)
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


@router.get(
    "/v1/invoices/{invoice_id}/document",
    dependencies=[Depends(billing_gate.permission("billing.invoice.download"))],
)
async def invoice_document(invoice_id: uuid.UUID, request: Request, caller: CallerDep) -> Response:
    """The issued invoice document. Its SHA-256 equals the invoice's `document_digest`."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        try:
            doc = await invoicing_service.document(conn, str(invoice_id))
        except InvoicingError as e:
            raise _invoice_error(e) from e
    return Response(doc, media_type="text/plain; charset=utf-8")


@router.get(
    "/v1/billing/state", dependencies=[Depends(billing_gate.permission("billing.overview.read"))]
)
async def billing_state(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The account's billing state, what it restricts and, explicitly, what it never restricts."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        return await invoicing_service.billing_state(conn)


@router.get(
    "/v1/billing/orders", dependencies=[Depends(billing_gate.permission("billing.overview.read"))]
)
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


@router.get(
    "/v1/billing/preview", dependencies=[Depends(billing_gate.permission("billing.overview.read"))]
)
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


@router.get(
    "/v1/billing/plan-requests",
    dependencies=[Depends(billing_gate.permission("billing.overview.read"))],
)
async def my_plan_requests(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The company's own requests for a different plan, newest first, with the commercial team's answer."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        return {"items": await plan_requests.my_requests(conn)}


@router.post(
    "/v1/billing/plan-requests",
    status_code=201,
    dependencies=[Depends(billing_gate.permission("billing.subscription.request_change"))],
)
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


@router.post(
    "/v1/billing/plan-requests/{request_id}:withdraw",
    dependencies=[Depends(billing_gate.permission("billing.subscription.request_change"))],
)
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


# ------------------------------------------------------------------------------- how much is due, and the card (CR-034)


class PaymentCompleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=10, max_length=200, pattern=r"^cs_[A-Za-z0-9_]+$")


def _payment_setup(request: Request) -> StripeCustomers | None:
    return getattr(request.app.state, "payment_setup", None)


def _need_payment_setup(request: Request) -> StripeCustomers:
    setup = _payment_setup(request)
    if setup is None:
        raise ZidError(
            "PAYMENTS_NOT_CONFIGURED",
            "Card payments are not set up on this platform yet.",
            http_status=501,
        )
    return setup


_PAYMENT_MESSAGES = {
    "PROVIDER_UNAVAILABLE": "We could not reach the payment provider just now. Nothing was charged or changed; please try again in a moment.",
    "SESSION_INVALID": "That card page link is not valid. Start again from the billing page.",
    "SETUP_NOT_COMPLETE": "The card was not saved. Start again and finish the card page.",
    "PAYMENT_NOT_COMPLETE": "That payment was not completed, so nothing was recorded. You have not been charged unless Stripe shows otherwise.",
    "RESOURCE_MISSING": "The payment provider no longer has this billing record. Contact your account manager.",
}


def _payment_error(e: PaymentSetupError) -> ZidError:
    """A person-readable message per code; the code itself stays in the response for support."""
    unavailable = e.code == "PROVIDER_UNAVAILABLE"
    return ZidError(
        e.code,
        _PAYMENT_MESSAGES.get(e.code, "The payment provider could not complete this step."),
        http_status=503 if unavailable else 409,
        retryable=unavailable,
        retry_after_ms=3000 if unavailable else None,
    )


async def _holds(conn: Any, caller: Caller, action: str) -> bool:
    """Whether this person's authority covers `action`, so the page can show the control only to people who can use it.
    A fresh passkey check or a second administrator still lets the action through when the person is asked for it."""
    try:
        await protected_action(conn, caller, action=action, resource="billable:*")
    except ZidError as e:
        return e.code in ("STEP_UP_REQUIRED", "APPROVAL_REQUIRED")
    return True


def _method_json(m: Any) -> dict[str, Any]:
    return {"brand": m.brand, "last4": m.last4, "exp_month": m.exp_month, "exp_year": m.exp_year}


@router.get(
    "/v1/billing/payment",
    dependencies=[Depends(billing_gate.permission("billing.payment_method.read_masked"))],
)
async def billing_payment(request: Request, caller: CallerDep) -> dict[str, Any]:
    """What this company owes now (open issued invoices, per currency) and its saved card, if any. The card is read
    from the processor after the database transaction closes, and only its brand, last four and expiry come back."""
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        open_invoices = [
            i
            for i in await invoicing_service.list_invoices(conn)
            if Decimal(i["outstanding"] or 0) > 0
        ]
        account = await invoicing_service.account_of_tenant(conn, tenant)
        customer = await invoicing_service.payment_customer(conn, tenant)
        can_manage = await _holds(conn, caller, "commercial.payment.manage")
        history = (
            (
                await conn.execute(
                    text(
                        "SELECT p.attempted_at, p.amount, p.currency, p.status, p.failure_code, "
                        "i.issuing_entity, i.sequence_number FROM commercial.payment_attempt p "
                        "JOIN commercial.invoice i ON i.invoice_id = p.invoice_id "
                        "ORDER BY p.attempted_at DESC LIMIT 10"
                    )
                )
            )
            .mappings()
            .all()
        )
    due: dict[str, Decimal] = {}
    for i in open_invoices:
        due[i["currency"]] = due.get(i["currency"], Decimal(0)) + Decimal(i["outstanding"])
    setup = _payment_setup(request)
    method: dict[str, Any] | None = None
    method_error: str | None = None
    if setup is not None and customer:
        try:
            found = await setup.current_method(customer)
            method = _method_json(found) if found else None
        except PaymentSetupError as e:
            method_error = e.code
    return {
        "configured": setup is not None,
        "account_linked": account is not None,
        "can_manage": can_manage,
        "payments": [
            {
                "at": h["attempted_at"].isoformat(),
                "amount": str(h["amount"].quantize(Decimal("0.01"))),
                "currency": h["currency"],
                "status": h["status"],
                "failure_code": h["failure_code"],
                "invoice_number": f"{h['issuing_entity']}-{int(h['sequence_number']):08d}"
                if h["sequence_number"]
                else None,
            }
            for h in history
        ],
        "method": method,
        "method_error": method_error,
        "amount_due": [{"currency": c, "amount": str(a)} for c, a in sorted(due.items())],
        "open_invoices": [
            {
                "invoice_id": i["invoice_id"],
                "number": i["number"],
                "currency": i["currency"],
                "outstanding": i["outstanding"],
                "due_at": i["due_at"],
            }
            for i in open_invoices
        ],
    }


@router.post(
    "/v1/billing/payment/setup",
    dependencies=[Depends(billing_gate.permission("billing.payment_method.manage"))],
)
async def billing_payment_setup(request: Request, caller: CallerDep) -> dict[str, Any]:
    """Start saving (or replacing) the company's card: returns the address of Stripe's hosted page. The company's
    Stripe customer is created on first use and recorded against its commercial account."""
    setup = _need_payment_setup(request)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="commercial.payment.manage", resource="billable:*"
        )
        account = await invoicing_service.account_of_tenant(conn, tenant)
        customer = await invoicing_service.payment_customer(conn, tenant)
    if account is None:
        raise ZidError(
            "NO_BILLING_ACCOUNT",
            "Your billing account is set up by Zoiko when your plan is agreed. Contact your account manager.",
            http_status=409,
        )
    origin = getattr(request.app.state, "billing_return_origin", None)
    if not origin:
        raise ZidError(
            "PAYMENTS_NOT_CONFIGURED",
            "The billing return address is not configured.",
            http_status=501,
        )
    try:
        if customer is None:
            customer = await setup.create_customer(
                name=account["legal_name"],
                tenant_id=tenant,
                idempotency_key=f"customer-{account['account_id']}",
            )
            async with tenant_transaction(request_engine(request), caller.ctx) as conn:
                await invoicing_service.set_payment_customer(
                    conn,
                    account["account_id"],
                    provider="stripe",
                    customer_ref=customer,
                    actor=caller.ctx.principal_id or "",
                )
        url = await setup.start_setup(
            customer_ref=customer,
            success_url=f"{origin}/admin/billing?payment=saved&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{origin}/admin/billing?payment=cancelled",
        )
    except PaymentSetupError as e:
        raise _payment_error(e) from e
    return {"url": url}


@router.post(
    "/v1/billing/payment/complete",
    dependencies=[Depends(billing_gate.permission("billing.payment_method.manage"))],
)
async def billing_payment_complete(
    body: PaymentCompleteBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Finish saving the card after Stripe sends the buyer back: the session is verified at Stripe (it must be this
    company's customer and completed) before its card becomes the default the platform charges."""
    setup = _need_payment_setup(request)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="commercial.payment.manage", resource="billable:*"
        )
        customer = await invoicing_service.payment_customer(conn, tenant)
    if customer is None:
        raise ZidError(
            "NO_PAYMENT_CUSTOMER", "No card setup was started for this company.", http_status=409
        )
    try:
        method = await setup.complete_setup(session_id=body.session_id, customer_ref=customer)
    except PaymentSetupError as e:
        raise _payment_error(e) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await log_change(
            conn,
            kind="PAYMENT_METHOD_SAVED",
            ref=customer,
            tenant_id=tenant,
            actor=caller.ctx.principal_id,
            reason=None,
            detail={"brand": method.brand, "last4": method.last4},
        )
    return {"method": _method_json(method)}


class OnlinePayCompleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=10, max_length=200, pattern=r"^cs_[A-Za-z0-9_]+$")


@router.post(
    "/v1/invoices/{invoice_id}/pay-online",
    dependencies=[Depends(billing_gate.permission("billing.payment.initiate"))],
)
async def pay_invoice_online(
    invoice_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Pay what is outstanding on one invoice on Stripe's page: the way through when the saved card needs the bank's
    confirmation (3-D Secure), when there is no saved card, or when the buyer prefers another card. Nothing is charged
    here; the platform records the payment when Stripe reports it and the buyer returns (CR-041)."""
    setup = _need_payment_setup(request)
    tenant = caller.ctx.tenant_id or ""
    origin = getattr(request.app.state, "billing_return_origin", None)
    if not origin:
        raise ZidError(
            "PAYMENTS_NOT_CONFIGURED",
            "The billing return address is not configured.",
            http_status=501,
        )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="commercial.payment.manage", resource="billable:*"
        )
        customer = await invoicing_service.payment_customer(conn, tenant)
        try:
            shown = await invoicing_service.view(conn, str(invoice_id))
        except InvoicingError as e:
            raise _invoice_error(e) from e
        if shown["status"] != "ISSUED" or Decimal(shown["outstanding"] or 0) <= 0:
            raise ZidError(
                "INVOICE_NOT_PAYABLE", "Nothing is due on that invoice.", http_status=409
            )
    if customer is None:
        raise ZidError(
            "NO_PAYMENT_CUSTOMER",
            "Add a card first, or contact your account manager to set up billing.",
            http_status=409,
        )
    try:
        url = await setup.start_payment(
            customer_ref=customer,
            invoice_id=str(invoice_id),
            invoice_label=f"Invoice {shown.get('number') or invoice_id}",
            amount=Decimal(shown["outstanding"]),
            currency=shown["currency"],
            success_url=f"{origin}/admin/billing?payment=paid&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{origin}/admin/billing?payment=cancelled",
        )
    except PaymentSetupError as e:
        raise _payment_error(e) from e
    return {"url": url}


@router.post(
    "/v1/billing/payment/online-complete",
    dependencies=[Depends(billing_gate.permission("billing.payment.initiate"))],
)
async def pay_invoice_online_complete(
    body: OnlinePayCompleteBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Record a payment made on Stripe's page once the buyer is back. The session is verified at Stripe (this
    company's customer, paid, an invoice named) and the invoice must be this company's; a payment the webhook already
    recorded is not recorded again."""
    setup = _need_payment_setup(request)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="commercial.payment.manage", resource="billable:*"
        )
        customer = await invoicing_service.payment_customer(conn, tenant)
    if customer is None:
        raise ZidError(
            "NO_PAYMENT_CUSTOMER", "No payment was started for this company.", http_status=409
        )
    try:
        paid = await setup.complete_payment(session_id=body.session_id, customer_ref=customer)
    except PaymentSetupError as e:
        raise _payment_error(e) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            done = await invoicing_service.record_reported_payment(
                conn,
                paid.invoice_id,
                status="CAPTURED",
                provider_ref=paid.payment_intent,
                failure_code=None,
                amount=from_minor_units(paid.amount_minor, paid.currency),
                idempotency_key=f"session-{body.session_id}"[:120],
            )
        except InvoicingError as e:  # the invoice named is not this company's: nothing is recorded
            raise _invoice_error(e) from e
    return {"recorded": done is not None, "already_recorded": bool(done and done.get("replayed"))}


class PayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # One per click on "Pay now": the same key never charges twice, so a double click or a retry is safe.
    idempotency_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@router.post(
    "/v1/invoices/{invoice_id}/pay",
    dependencies=[Depends(billing_gate.permission("billing.payment.initiate"))],
)
async def pay_invoice(
    invoice_id: uuid.UUID, body: PayBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """The owner pays what is outstanding on one issued invoice with the saved card, now instead of at the due date.
    Same collection as the operator's (CR-027): idempotent, recorded as a payment attempt, and a full payment returns an
    overdue account to active."""
    payments = getattr(request.app.state, "payment_port", None)
    if payments is None:
        raise ZidError(
            "PAYMENT_NOT_CONFIGURED",
            "Card payments are not set up on this platform yet.",
            http_status=501,
        )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="commercial.payment.manage", resource="billable:*"
        )
        try:
            shown = await invoicing_service.view(conn, str(invoice_id))
            if shown["status"] != "ISSUED":
                raise ZidError("INVOICE_NOT_FOUND", "No such invoice.", http_status=404)
            out = await invoicing_service.collect(
                conn,
                str(invoice_id),
                payments=payments,
                idempotency_key=f"owner-{caller.ctx.tenant_id}-{body.idempotency_key}",
            )
        except InvoicingError as e:
            raise _invoice_error(e) from e
    return {"status": out["status"], "failure_code": out.get("failure_code")}

"""Pay before the plan switches (CR-062): the company's side.

GET  /v1/billing/activations                       orders that wait for the first month's platform fee, with the amount
POST /v1/billing/activations/{order_id}/pay        open Stripe's hosted page for that amount
POST /v1/billing/activations/{order_id}:confirm    read the payment back from Stripe and, if it is paid in full, switch the plan
GET  /v1/billing/plan-offers/{plan}                what buying this plan costs today, from the approved price book (CR-147)
POST /v1/billing/plans/{plan}:purchase             raise the order for that plan and open Stripe for its exact amount (CR-147)

The operator's activation puts an order here (see `orders_service.activate(require_payment=True)`). Nothing is charged by this API: Stripe takes the card on its
own page. The plan changes only when Stripe reports this order paid in full, and confirming again changes nothing.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.api.commercial import _need_payment_setup, _payment_error
from zid_platform.modules.commercial.application import activation_service as activations
from zid_platform.modules.commercial.application import billing_gate, invoicing_service
from zid_platform.modules.commercial.application import orders_service as orders
from zid_platform.modules.commercial.application import self_serve_service as self_serve
from zid_platform.modules.commercial.application.adapters import PaymentSetupError
from zid_platform.modules.commercial.domain.orders import OrderError
from zid_platform.protected import protected_action

router = APIRouter(tags=["commercial"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class ActivationConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = Field(
        default=None, min_length=10, max_length=200, pattern=r"^cs_[A-Za-z0-9_]+$"
    )


def _fail(e: activations.ActivationError) -> ZidError:
    return ZidError(e.code, e.detail, http_status=404 if e.code == "ACTIVATION_NOT_FOUND" else 409)


@router.get(
    "/v1/billing/activations",
    dependencies=[Depends(billing_gate.permission("billing.overview.read"))],
)
async def waiting_activations(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        return {"items": await activations.waiting(conn)}


@router.post(
    "/v1/billing/activations/{order_id}/pay",
    dependencies=[Depends(billing_gate.permission("billing.payment.initiate"))],
)
async def pay_activation(
    order_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
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
        try:
            row = await activations.waiting_one(conn, tenant, str(order_id))
        except activations.ActivationError as e:
            raise _fail(e) from e
        customer = await invoicing_service.payment_customer(conn, tenant)
        plan = (await orders.order_view(conn, str(order_id)))["plan"]
    if customer is None:
        raise ZidError(
            "NO_PAYMENT_CUSTOMER",
            "Billing is not set up for this company yet. Contact your account manager.",
            http_status=409,
        )
    try:
        url, session_id = await setup.start_activation_payment(
            customer_ref=customer,
            order_id=str(order_id),
            label=f"Zoiko iD {plan} plan: first month's platform fee",
            amount=Decimal(row["amount"]),
            currency=row["currency"],
            success_url=f"{origin}/admin/billing?activation=paid&order={order_id}&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{origin}/admin/billing?activation=cancelled",
        )
    except PaymentSetupError as e:
        raise _payment_error(e) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await activations.remember_session(conn, tenant, str(order_id), session_id)
    return {"url": url}


@router.post(
    "/v1/billing/activations/{order_id}:confirm",
    dependencies=[Depends(billing_gate.permission("billing.payment.initiate"))],
)
async def confirm_activation(
    order_id: uuid.UUID, body: ActivationConfirmBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Verify at Stripe and switch the plan. The session is the one just returned, or the one this order last opened (so a payment made with the browser closed is
    still found). The answer says whether the plan is now active; it is never active on the browser's word."""
    setup = _need_payment_setup(request)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="commercial.payment.manage", resource="billable:*"
        )
        row = await activations.load(conn, tenant, str(order_id))
        if row is None:
            raise ZidError(
                "ACTIVATION_NOT_FOUND", "No payment was asked for on that order.", http_status=404
            )
        customer = await invoicing_service.payment_customer(conn, tenant)
    session_id = body.session_id or row["session_id"]
    if customer is None or not session_id:
        return {
            "activated": False,
            "status": row["status"],
            "reason": "No payment has been started for this order yet.",
        }
    try:
        paid = await setup.complete_activation_payment(session_id=session_id, customer_ref=customer)
    except PaymentSetupError as e:
        if e.code == "PAYMENT_NOT_COMPLETE":
            return {
                "activated": False,
                "status": row["status"],
                "reason": "Stripe has not confirmed the payment yet.",
            }
        raise _payment_error(e) from e
    if paid.order_id != str(order_id):
        raise ZidError(
            "ACTIVATION_ORDER_MISMATCH",
            "That payment was made for a different order.",
            http_status=409,
        )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            await activations.record_payment(
                conn,
                tenant,
                str(order_id),
                payment_ref=paid.payment_intent,
                amount_minor=paid.amount_minor,
                currency=paid.currency,
            )
            done = await orders.activate(
                conn,
                str(order_id),
                actor=str(row["activated_by"]),
                direct=True,
                require_payment=True,
            )
        except activations.ActivationError as e:
            raise _fail(e) from e
        except OrderError as e:
            raise ZidError(e.code, getattr(e, "detail", None) or e.code, http_status=409) from e
    return {
        "activated": True,
        "status": "ACTIVATED",
        "already_active": bool(done.get("already_active")),
        "plan": done.get("plan"),
    }


class PlanPurchaseBody(BaseModel):
    """What the buyer reviewed. The amount and currency are checked against the approved price book again: a price that moved is refused, never charged."""

    model_config = ConfigDict(extra="forbid")
    expected_amount: str = Field(min_length=1, max_length=20, pattern=r"^\d{1,9}(\.\d{1,2})?$")
    expected_currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    legal_name: str | None = Field(default=None, min_length=2, max_length=200)
    country: str | None = Field(default=None, min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")


_SELF_SERVE_STATUS = {
    "PLAN_NOT_FOUND": 404,
    "PLAN_NOT_SELF_SERVE": 409,
    "NO_APPROVED_PRICE_BOOK": 409,
    "PRICE_NOT_PUBLISHED": 409,
    "ALREADY_ON_PLAN": 409,
    "DOWNGRADE_NOT_SELF_SERVE": 409,
    "PRICE_CHANGED": 409,
    "BILLING_DETAILS_REQUIRED": 422,
}


def _self_serve_fail(e: self_serve.SelfServeError) -> ZidError:
    return ZidError(e.code, e.detail, http_status=_SELF_SERVE_STATUS.get(e.code, 409))


@router.get(
    "/v1/billing/plan-offers/{plan}",
    dependencies=[Depends(billing_gate.permission("billing.overview.read"))],
)
async def plan_offer(plan: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    """The review figure for buying this plan now. Reads only; it creates nothing and charges nothing."""
    tenant = caller.ctx.tenant_id or ""
    region = getattr(request.app.state, "self_serve_region", None)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource="billable:*")
        try:
            offer = await self_serve.offer(conn, tenant, plan)
        except self_serve.SelfServeError as e:
            raise _self_serve_fail(e) from e
        account = await invoicing_service.account_of_tenant(conn, tenant)
    return {
        "plan": offer["plan"],
        "label": offer["label"],
        "amount": offer["amount"],
        "currency": offer["currency"],
        "price_book_version": offer["price_book_version"],
        "fee_is_floor": offer["fee_is_floor"],
        "current_plan": offer["current_plan"],
        "purchase_enabled": region is not None,
        "needs_billing_details": account is None,
        "billing_name": account["legal_name"] if account else None,
    }


@router.post(
    "/v1/billing/plans/{plan}:purchase",
    dependencies=[Depends(billing_gate.permission("billing.payment.initiate"))],
)
async def purchase_plan(
    plan: str, body: PlanPurchaseBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Buy a plan: raise the order, hold it for payment and open Stripe for the exact amount. The plan changes only when Stripe confirms this payment
    (`:confirm` above, or the signed webhook); this call changes no entitlement and charges no card."""
    setup = _need_payment_setup(request)
    tenant = caller.ctx.tenant_id or ""
    region = getattr(request.app.state, "self_serve_region", None)
    if region is None:
        raise ZidError(
            "SELF_SERVE_OFF",
            "Buying a plan online is not switched on yet. Ask our team to set your plan up.",
            http_status=501,
        )
    origin = getattr(request.app.state, "billing_return_origin", None)
    if not origin:
        raise ZidError(
            "PAYMENTS_NOT_CONFIGURED",
            "The billing return address is not configured.",
            http_status=501,
        )
    principal = caller.ctx.principal_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="commercial.payment.manage", resource="billable:*"
        )
        try:
            held = await self_serve.purchase(
                conn,
                tenant,
                plan,
                expected_amount=body.expected_amount,
                expected_currency=body.expected_currency,
                principal=principal,
                legal_name=body.legal_name,
                country=body.country,
                region=region,
            )
        except self_serve.SelfServeError as e:
            raise _self_serve_fail(e) from e
        account = await invoicing_service.account_of_tenant(conn, tenant)
        customer = await invoicing_service.payment_customer(conn, tenant)
    try:
        if customer is None and account is not None:
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
                    actor=principal,
                )
        if customer is None:
            raise ZidError(
                "NO_PAYMENT_CUSTOMER",
                "Billing is not set up for this company yet. Contact your account manager.",
                http_status=409,
            )
        order_id = held["order_id"]
        url, session_id = await setup.start_activation_payment(
            customer_ref=customer,
            order_id=order_id,
            label=f"Zoiko iD {held['label']} plan: first month's platform fee",
            amount=Decimal(held["amount"]),
            currency=held["currency"],
            success_url=f"{origin}/admin/billing?activation=paid&order={order_id}&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{origin}/admin/billing?activation=cancelled",
        )
    except PaymentSetupError as e:
        raise _payment_error(e) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await activations.remember_session(conn, tenant, order_id, session_id)
    return {
        "url": url,
        "order_id": order_id,
        "amount": held["amount"],
        "currency": held["currency"],
    }

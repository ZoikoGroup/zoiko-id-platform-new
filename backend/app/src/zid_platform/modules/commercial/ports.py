"""Public interface of the commercial module. Other modules import ONLY this.

- `entitled(conn, capability, used=, quantity=, enforcement=)`: the explicit entitlement decision.
- `record_identity_merge(...)`: a merged person counts once as MAH; whoever merges principals calls it.
- `record_billable(conn, tenant_id=, meter=, key=, ...)`: write one billable event inside the operation that
  earns it (never raises; CR-025). `record_active_human(...)` counts a monthly active human once.
- `require_entitlement(...)`: the same, raising `ENTITLEMENT_REQUIRED` (HTTP 402) when the paid extra is
  refused in `enforce` mode. Only paid-extra creation paths call it; security paths never do (CR-024).
- `run_rating(...)` rates a frozen month (CR-026); `latest_rating(...)` reads the computed run.
- The operator plane uses the catalogue and ledger stores, window freezing and the event helpers below.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_errors import ErrorDetail, ZidError

from zid_platform.modules.commercial.application import activation_service as activations
from zid_platform.modules.commercial.application import assurance_service as assurance
from zid_platform.modules.commercial.application import billing_gate, usage_reservations
from zid_platform.modules.commercial.application import budget_service as budgets
from zid_platform.modules.commercial.application import commitment_service as commitments
from zid_platform.modules.commercial.application import dispute_service as disputes
from zid_platform.modules.commercial.application import eligibility_service as eligibility
from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.commercial.application import invoicing_service as invoicing
from zid_platform.modules.commercial.application import orders_service as orders
from zid_platform.modules.commercial.application import plan_request_service as plan_requests
from zid_platform.modules.commercial.application import recon_service as recon
from zid_platform.modules.commercial.application import refund_service as refunds
from zid_platform.modules.commercial.application import service_request_service as service_requests
from zid_platform.modules.commercial.application import vendor_service as vendors
from zid_platform.modules.commercial.application.adapters import (
    FallbackTax,
    NoTaxConfigured,
    OnlinePayment,
    PaymentMethodSummary,
    PaymentPort,
    PaymentResult,
    PaymentSetupError,
    RateTableTax,
    RecordingPayments,
    RecordingRefunds,
    RefundPort,
    StripeCustomers,
    StripePayments,
    StripeRefunds,
    StripeTax,
    TaxLine,
    TaxPort,
)
from zid_platform.modules.commercial.application.change_log import log_change, verify_change_log
from zid_platform.modules.commercial.application.entitlement_resolver import (
    decide as decide_feature_entitlement,
)
from zid_platform.modules.commercial.application.entitlement_resolver import require_feature
from zid_platform.modules.commercial.application.events import (
    billable_event_adjusted,
    entitlement_changed,
    meter_window_frozen,
)
from zid_platform.modules.commercial.application.rating_service import latest as latest_rating
from zid_platform.modules.commercial.application.rating_service import run as run_rating
from zid_platform.modules.commercial.application.recorder import (
    configure as configure_ledger,
)
from zid_platform.modules.commercial.application.recorder import (
    freeze_month,
    ledger_summary,
    reconcile,
    record_active_human,
    record_identity_merge,
)
from zid_platform.modules.commercial.application.recorder import record as record_billable
from zid_platform.modules.commercial.application.service import entitled
from zid_platform.modules.commercial.application.service_request_service import ServiceRequestError
from zid_platform.modules.commercial.application.stripe_webhook_service import (
    open_refund as open_payment_refund,
)
from zid_platform.modules.commercial.application.stripe_webhook_service import (
    resolve_event as resolve_payment_event,
)
from zid_platform.modules.commercial.application.stripe_webhook_service import (
    review_queue as payment_event_queue,
)
from zid_platform.modules.commercial.application.transfer_gate import plan_aware_transfer_gate
from zid_platform.modules.commercial.domain import recon_import
from zid_platform.modules.commercial.domain.billable import (
    METERS,
    BillableError,
    key_auth,
    key_decision,
    key_m2m,
    key_vc_issued,
    key_vc_presented,
    next_month,
    window_month,
)
from zid_platform.modules.commercial.domain.catalogue import (
    PLAN_CODES,
    CommercialError,
    Enforcement,
    Entitlement,
)
from zid_platform.modules.commercial.domain.commitment import CommitmentError
from zid_platform.modules.commercial.domain.finance import FinanceError
from zid_platform.modules.commercial.domain.finance import Kind as FinanceKind
from zid_platform.modules.commercial.domain.invoicing import InvoicingError, verify_chain
from zid_platform.modules.commercial.domain.orders import OrderError, QuoteLine
from zid_platform.modules.commercial.domain.payment_ops import OpsError
from zid_platform.modules.commercial.domain.plan_requests import PlanRequestError
from zid_platform.modules.commercial.domain.rating import Credit, RatingError
from zid_platform.modules.commercial.domain.vendor import VendorError
from zid_platform.modules.commercial.infrastructure import (
    bank_statement,
    billable_store,
    catalogue_store,
    enforcement_store,
)
from zid_platform.modules.commercial.infrastructure.finance_config import finance_policy
from zid_platform.modules.commercial.infrastructure.plan_request_store import operator_scope

record_vendor_check = vendors.record_check
configure_billing_gate = billing_gate.configure
configure_commitment_authority = commitments.configure_authority
configure_org_state = eligibility.configure_org_state


async def require_entitlement(
    conn: AsyncConnection,
    capability: str,
    *,
    used: int | None = None,
    quantity: int = 1,
    enforcement: Enforcement = "shadow",
) -> Entitlement:
    result = await entitled(conn, capability, used=used, quantity=quantity, enforcement=enforcement)
    if not result.allowed:
        raise ZidError(
            "ENTITLEMENT_REQUIRED",
            f"Your plan does not include this ({result.reason}). Contact your account manager to change plan.",
            http_status=402,
            details=[
                ErrorDetail(field=capability, issue=str(result.reason)),
                ErrorDetail(field="plan", issue=result.plan_code),
            ],
        )
    return result


def plan_throughput_status() -> str:
    """CONFIRMED, or ENGINEERING_DEFAULT while Commercial & Finance have not signed the request-allowance multipliers off."""
    from zid_platform.modules.commercial.infrastructure.bands import (
        plan_throughput_status as status,
    )

    return status()


async def plan_request_limit(conn: AsyncConnection, base: int) -> int:
    """The company's request allowance per minute from its plan (0 = unlimited); see `plan-throughput.yaml`."""
    from zid_platform.modules.commercial.application.service import request_limit

    return await request_limit(conn, base)


async def require_production_environment(
    conn: AsyncConnection, *, used: int, enforcement: Enforcement = "shadow"
) -> None:
    """The plan's number of production environments: one more is refused (402) when the plan is full and enforcement is on."""
    from zid_platform.modules.commercial.application.service import production_environments_check

    allowed, plan, limit = await production_environments_check(
        conn, used=used, enforcement=enforcement
    )
    if not allowed:
        raise ZidError(
            "ENTITLEMENT_REQUIRED",
            f"Your plan allows {limit} production environment{'' if limit == 1 else 's'}. Contact your account manager to change plan.",
            http_status=402,
            details=[
                ErrorDetail(field="production_environments", issue="PLAN_LIMIT"),
                ErrorDetail(field="plan", issue=plan),
            ],
        )


__all__ = [
    "METERS",
    "PLAN_CODES",
    "BillableError",
    "CommercialError",
    "CommitmentError",
    "Credit",
    "Enforcement",
    "Entitlement",
    "FallbackTax",
    "FinanceError",
    "FinanceKind",
    "InvoicingError",
    "NoTaxConfigured",
    "OnlinePayment",
    "OpsError",
    "OrderError",
    "PaymentMethodSummary",
    "PaymentPort",
    "PaymentResult",
    "PaymentSetupError",
    "PlanRequestError",
    "QuoteLine",
    "RateTableTax",
    "RatingError",
    "RecordingPayments",
    "RecordingRefunds",
    "RefundPort",
    "ServiceRequestError",
    "StripeCustomers",
    "StripePayments",
    "StripeRefunds",
    "StripeTax",
    "TaxLine",
    "TaxPort",
    "VendorError",
    "activations",
    "assurance",
    "bank_statement",
    "billable_event_adjusted",
    "billable_store",
    "budgets",
    "catalogue_store",
    "commitments",
    "configure_billing_gate",
    "configure_commitment_authority",
    "configure_ledger",
    "configure_org_state",
    "decide_feature_entitlement",
    "disputes",
    "eligibility",
    "enforcement_store",
    "entitled",
    "entitlement_changed",
    "finance",
    "finance_policy",
    "freeze_month",
    "invoicing",
    "key_auth",
    "key_decision",
    "key_m2m",
    "key_vc_issued",
    "key_vc_presented",
    "latest_rating",
    "ledger_summary",
    "log_change",
    "meter_window_frozen",
    "next_month",
    "open_payment_refund",
    "operator_scope",
    "orders",
    "payment_event_queue",
    "plan_aware_transfer_gate",
    "plan_request_limit",
    "plan_requests",
    "plan_throughput_status",
    "recon",
    "recon_import",
    "reconcile",
    "record_active_human",
    "record_billable",
    "record_identity_merge",
    "record_vendor_check",
    "refunds",
    "require_entitlement",
    "require_feature",
    "require_production_environment",
    "resolve_payment_event",
    "run_rating",
    "service_requests",
    "usage_reservations",
    "vendors",
    "verify_chain",
    "verify_change_log",
    "window_month",
]

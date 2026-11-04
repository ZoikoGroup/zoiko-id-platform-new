"""Which platform events send which email, and to whom (Communication Estate §03 SOURCE OF TRUTH).

An email is triggered only by a durable, versioned domain event (the outbox), never by a request handler.
Each route names a template from the copybook, an audience, and a builder that turns the event and the
current state into the merge values the copy needs. Events carry ids, not names or addresses, so a builder
reads what it needs (the organization's name, the invoice) inside the tenant's own transaction, and a
recipient is resolved from the contact registry, never from the event.

Only events this platform really emits are wired. The copybook's other templates exist in the catalogue and
render, but nothing sends them until the flow that produces their event exists and a person has enabled
them (§07 "launch scope: never send templates for unimplemented flows"). `coverage()` says which is which.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.ports import InvoicingError, invoicing
from zid_platform.modules.communication.application import contacts
from zid_platform.modules.communication.application.contacts import Recipient
from zid_platform.modules.communication.domain.render import Money
from zid_platform.modules.communication.infrastructure.catalogue_file import load as load_catalogue
from zid_platform.modules.tenant.ports import load_tenant

log = logging.getLogger("zid.communication.dispatch")
PREFIX = "com.zoiko.id."

Merge = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class BuildContext:
    conn: AsyncConnection
    tenant_id: str
    event_time: datetime
    data: Mapping[str, Any]
    event_id: str


Builder = Callable[[BuildContext], Awaitable["Merge | None"]]


@dataclass(frozen=True, slots=True)
class Route:
    template_id: str
    audience: str  # "role:<PURPOSE>" or "principal:<event data field>"
    build: Builder
    when: Callable[[Mapping[str, Any]], bool] = lambda data: True


def event_name(event_type: str) -> str:
    """`com.zoiko.id.mandate.revoked.v1` -> `mandate.revoked`."""
    parts = event_type.removeprefix(PREFIX).split(".")
    if parts and parts[-1].startswith("v") and parts[-1][1:].isdigit():
        parts = parts[:-1]
    return ".".join(parts)


async def _org(c: BuildContext) -> str | None:
    tenant = await load_tenant(c.conn, c.tenant_id)
    name = getattr(tenant, "display_name", None)
    return str(name) if name else None


def _when(raw: object, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _invoice(c: BuildContext, invoice_id: object) -> dict[str, Any] | None:
    try:
        return dict(await invoicing.view(c.conn, str(invoice_id)))
    except (
        InvoicingError
    ):  # an invoice that cannot be read is a skipped email, never a failed event
        log.warning("email: invoice not readable for event %s", c.event_id)
        return None


def _money(amount: object, currency: object) -> Money | None:
    try:
        return Money(Decimal(str(amount)), str(currency))
    except (InvalidOperation, ValueError):
        return None


async def _invoice_issued(c: BuildContext) -> Merge | None:
    inv = await _invoice(c, c.data.get("invoice_id"))
    org = await _org(c)
    if inv is None or org is None or inv.get("due_at") is None:
        return None
    total = _money(inv["gross"], inv["currency"])
    if total is None:
        return None
    return {
        "organization_name": org,
        "invoice_reference": str(c.data["number"]),
        "invoice_total_formatted": total,
        "due_at_local": _when(inv["due_at"], c.event_time),
        "trusted_billing_url": None,
    }


async def _payment_ok(c: BuildContext) -> Merge | None:
    inv = await _invoice(c, c.data.get("invoice_id"))
    amount = _money(c.data.get("amount"), c.data.get("currency"))
    if inv is None or amount is None or not inv.get("number"):
        return None
    return {
        "payment_amount_formatted": amount,
        "invoice_reference": str(inv["number"]),
        "event_at_local": c.event_time,
        "trusted_billing_url": None,
    }


async def _payment_failed(c: BuildContext) -> Merge | None:
    inv = await _invoice(c, c.data.get("invoice_id"))
    if inv is None or not inv.get("number"):
        return None
    return {
        "invoice_reference": str(inv["number"]),
        "event_at_local": c.event_time,
        "trusted_billing_url": None,
    }


async def _subscription(c: BuildContext) -> Merge | None:
    org = await _org(c)
    if org is None or not c.data.get("plan"):
        return None
    return {
        "organization_name": org,
        "event_at_local": _when(c.data.get("effective_from"), c.event_time),
        "plan_public_name": str(c.data["plan"]).replace("_", " ").title(),
        "trusted_billing_url": None,
    }


_METRIC = {
    "MAH": "active people",
    "AUTH_EVENT": "sign-ins",
    "POLICY_DECISION": "access decisions",
    "M2M_GRANT": "machine grants",
}


async def _usage(c: BuildContext) -> Merge | None:
    org = await _org(c)
    key = str(c.data.get("key", ""))
    if org is None or not c.data.get("threshold") or not c.data.get("month"):
        return None
    meter = key.split(":", 1)[-1]
    return {
        "organization_name": org,
        "usage_public_percentage": f"{int(c.data['threshold'])}%",
        "usage_metric_public": "monthly bill" if meter == "BILL" else _METRIC.get(meter, "usage"),
        "billing_period_public": str(c.data["month"]),
        "trusted_billing_url": None,
    }


_FACTORS = {
    "WEBAUTHN": "passkey",
    "PASSKEY": "passkey",
    "TOTP": "authenticator app",
    "RECOVERY_CODE": "recovery code",
}


async def _authenticator(c: BuildContext) -> Merge | None:
    kind = str(c.data.get("authenticator_type", "")).upper()
    return {
        "authenticator_type_public": _FACTORS.get(kind, "sign-in method"),
        "event_at_local": c.event_time,
        "secure_console_url": None,
    }


async def _agent_suspended(c: BuildContext) -> Merge | None:
    org = await _org(c)  # noqa: F841 - the copy names the agent, not the organization
    agent = str(c.data.get("agent_principal_id", ""))
    if not agent:
        return None
    return {
        "agent_display_name": f"agent {agent[:8]}",
        "event_at_local": c.event_time,
        "secure_console_url": None,
    }


async def _credential_issued(c: BuildContext) -> Merge | None:
    org = await _org(c)
    kind = str(c.data.get("credential_type", ""))
    if org is None or not kind:
        return None
    return {
        "credential_public_label": kind,
        "issuer_display_name": org,
        "event_at_local": c.event_time,
        "trusted_wallet_url": None,
    }


async def _recovery(c: BuildContext) -> Merge | None:
    return {
        "event_at_local": c.event_time,
        "trusted_recovery_status_url": None,
        "secure_console_url": None,
        "case_reference": str(c.data.get("recovery_id", "")) or None,
    }


async def _recovery_denied(c: BuildContext) -> Merge | None:
    case = str(c.data.get("recovery_id", ""))
    return {"case_reference": case, "trusted_recovery_status_url": None} if case else None


async def _proofing(c: BuildContext) -> Merge | None:
    case = str(c.data.get("session_id", ""))
    if not case:
        return None
    return {
        "event_at_local": c.event_time,
        "case_reference": case,
        "trusted_proofing_url": None,
    }


async def _organization_event(c: BuildContext) -> Merge | None:
    org = await _org(c)
    return (
        {"organization_name": org, "event_at_local": c.event_time, "secure_console_url": None}
        if org
        else None
    )


async def _member_removed(c: BuildContext) -> Merge | None:
    return await _organization_event(c)


async def _export_ready(c: BuildContext) -> Merge | None:
    org, package = await _org(c), str(c.data.get("package_id", ""))
    if not org or not package:
        return None
    return {
        "organization_name": org,
        "event_at_local": c.event_time,
        "package_reference": package,
        "trusted_audit_url": None,
    }


async def _integrity_alert(c: BuildContext) -> Merge | None:
    partition = str(c.data.get("partition_id", ""))
    if not partition:
        return None
    return {"event_at_local": c.event_time, "event_reference": partition, "trusted_audit_url": None}


async def _agent_registered(c: BuildContext) -> Merge | None:
    org, agent = await _org(c), str(c.data.get("agent_principal_id", ""))
    if not org or not agent:
        return None
    return {
        "agent_display_name": f"agent {agent[:8]}",
        "organization_name": org,
        "event_at_local": c.event_time,
        "secure_console_url": None,
    }


async def _workload_registered(c: BuildContext) -> Merge | None:
    org, agent = await _org(c), str(c.data.get("logical_principal_id", ""))
    if not org or not agent:
        return None
    return {
        "agent_display_name": f"agent {agent[:8]}",
        "organization_name": org,
        "event_at_local": c.event_time,
        "secure_console_url": None,
    }


async def _approval_requested(c: BuildContext) -> Merge | None:
    """ZID-EM-074: a protected change now waits for its approvers. The mail names the change by its public action label and a short reference, never its payload,
    its reason or the requester, and says to decide in the approval centre, not from the mail."""
    org, case, action = await _org(c), str(c.data.get("case_id", "")), str(c.data.get("action_id", ""))
    if not org or not case or not action:
        return None
    return {
        "organization_name": org,
        "action_public_label": action.replace("_", " ").replace(".", " ").replace("-", " ").capitalize(),
        "action_reference": case[:8],
        "trusted_approval_url": None,
    }


EVENT_ROUTES: Mapping[str, tuple[Route, ...]] = {
    "governance.approval.case_created": (
        Route("ZID-EM-074", "role:OWNER", _approval_requested),
        Route("ZID-EM-074", "role:SECURITY", _approval_requested),
    ),
    "commercial.invoice.issued": (Route("ZID-EM-130", "role:BILLING", _invoice_issued),),
    "commercial.payment.recorded": (
        Route(
            "ZID-EM-131", "role:BILLING", _payment_ok, when=lambda d: d.get("status") == "CAPTURED"
        ),
        Route(
            "ZID-EM-132",
            "role:BILLING",
            _payment_failed,
            when=lambda d: d.get("status") == "FAILED",
        ),
    ),
    "commercial.entitlement.changed": (
        Route(
            "ZID-EM-127", "role:BILLING", _subscription, when=lambda d: not d.get("previous_plan")
        ),
        Route(
            "ZID-EM-128", "role:BILLING", _subscription, when=lambda d: bool(d.get("previous_plan"))
        ),
    ),
    "commercial.budget.threshold_crossed": (Route("ZID-EM-129", "role:BILLING", _usage),),
    "authenticator.registered": (Route("ZID-EM-023", "principal:principal_id", _authenticator),),
    "authenticator.revoked": (Route("ZID-EM-024", "principal:principal_id", _authenticator),),
    "agent.suspended": (Route("ZID-EM-082", "role:OWNER", _agent_suspended),),
    "recovery.started": (Route("ZID-EM-013", "principal:principal_id", _recovery),),
    "recovery.completed": (Route("ZID-EM-015", "principal:principal_id", _recovery),),
    "recovery.denied": (Route("ZID-EM-016", "principal:principal_id", _recovery_denied),),
    "verification.started": (Route("ZID-EM-026", "principal:subject_principal_id", _proofing),),
    "verification.completed": (
        Route(
            "ZID-EM-029",
            "principal:subject_principal_id",
            _proofing,
            when=lambda d: bool(d.get("assertions")),
        ),
        Route(
            "ZID-EM-030",
            "principal:subject_principal_id",
            _proofing,
            when=lambda d: not d.get("assertions"),
        ),
    ),
    "tenant.member.changed": (
        Route(
            "ZID-EM-154",
            "principal:principal_id",
            _member_removed,
            when=lambda d: d.get("role_status") == "REVOKED",
        ),
    ),
    "tenant.organization_verified": (Route("ZID-EM-036", "role:OWNER", _organization_event),),
    "evidence.export.created": (Route("ZID-EM-115", "role:SECURITY", _export_ready),),
    "evidence.integrity.failed": (Route("ZID-EM-116", "role:SECURITY", _integrity_alert),),
    "agent.registered": (Route("ZID-EM-078", "role:OWNER", _agent_registered),),
    "machine.workload.registered": (Route("ZID-EM-077", "role:OWNER", _workload_registered),),
    "credential.issued": (
        Route("ZID-EM-039", "principal:subject_principal_id", _credential_issued),
    ),
}


@dataclass(frozen=True, slots=True)
class Job:
    template_id: str
    recipient: Recipient
    merge: Merge


async def plan(
    conn: AsyncConnection,
    *,
    event_type: str,
    event_id: str,
    event_time: datetime,
    tenant_id: str,
    data: Mapping[str, Any],
) -> list[Job]:
    """Everything an event should send: routes that apply, their recipients and merge values."""
    jobs: list[Job] = []
    for route in EVENT_ROUTES.get(event_name(event_type), ()):
        if not route.when(data):
            continue
        kind, _, target = route.audience.partition(":")
        if kind == "role":
            recipients = await contacts.resolve_role(conn, tenant_id, target)
        else:
            subject = data.get(target)
            recipients = (
                await contacts.resolve_principal(conn, tenant_id, str(subject)) if subject else []
            )
        if not recipients:
            log.info("email: no contact for template=%s audience=%s", route.template_id, kind)
            continue
        merge = await route.build(BuildContext(conn, tenant_id, event_time, data, event_id))
        if merge is None:
            log.info("email: not enough data for template=%s", route.template_id)
            continue
        fields = (
            load_catalogue().get(route.template_id).fields
        )  # extra values are dropped, never sent
        merge = {k: v for k, v in merge.items() if k in fields}
        jobs += [Job(route.template_id, r, merge) for r in recipients]
    return jobs


def coverage() -> list[dict[str, object]]:
    """Every template, the event it waits for and whether this platform emits it (the launch-scope view)."""
    wired = {r.template_id for routes in EVENT_ROUTES.values() for r in routes}
    return [
        {"template": t.id, "event": t.event, "class": t.message_class.value, "wired": t.id in wired}
        for t in load_catalogue()
    ]

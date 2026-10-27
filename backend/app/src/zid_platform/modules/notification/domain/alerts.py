"""Alerts and tasks (ZID-UX-001 §20). Pure domain, no I/O.

- Severity CRITICAL / HIGH / MEDIUM / INFO with the UX behaviour of each (a task is an alert that needs
  someone to act; CRITICAL and mandatory notices can never be suppressed).
- OPEN -> ACKNOWLEDGED -> RESOLVED. Acknowledging is never resolving: both states are kept apart.
- Repeated correlated alerts collapse into one (`correlation_key`), to prevent alert fatigue.
- Derived from catalogued platform events by fixed templates. The text carries no personal data, only
  the object type and its opaque reference; the alert links to the object and a safe next step.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

PREFIX = "com.zoiko.id."


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    INFO = "INFO"


class AlertState(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class AlertError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AlertDraft:
    severity: Severity
    kind: str
    title: str
    body: str
    object_type: str
    object_ref: str
    href: str
    recommended_action: str
    is_task: bool
    mandatory: bool
    correlation_key: str
    source_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class Template:
    severity: Severity
    title: str
    href: str
    action: str
    task: bool = False


def _t(severity: Severity, title: str, href: str, action: str, task: bool = False) -> Template:
    return Template(severity, title, href, action, task)


C, H, M, I = Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.INFO

# name (event type without prefix and version) -> template. Tenant-facing facts only: operator-plane
# events (incidents, cells) are never turned into a customer's alerts.
TEMPLATES: dict[str, Template] = {
    # --- CRITICAL: active compromise, trust failure
    "authority_root.revoked": _t(
        C,
        "Authority root revoked",
        "/admin/organization",
        "Review the organization's roots and the grants that depended on them.",
        True,
    ),
    "crypto.key.compromised": _t(
        C,
        "A cryptographic key was reported compromised",
        "/admin/residency",
        "Rotate the affected key and review recent use.",
        True,
    ),
    "evidence.integrity.failed": _t(
        C,
        "Evidence integrity check failed",
        "/admin/evidence",
        "Open the evidence integrity view and start a case.",
        True,
    ),
    "tenant.suspended": _t(
        C,
        "This workspace was suspended",
        "/admin/settings",
        "Contact your Zoiko iD administrator; only governed resumption is possible.",
        True,
    ),
    # --- HIGH: expiring authority, failed integrations, rotation issues
    "authority_root.stale": _t(
        H,
        "Authority root went stale",
        "/admin/organization",
        "Refresh the root; stale roots cannot back high-risk actions.",
        True,
    ),
    "authority_root.conflicted": _t(
        H,
        "Authority root is in conflict",
        "/admin/organization",
        "Resolve the conflict between registry sources.",
        True,
    ),
    "authority_root.suspended": _t(
        H,
        "Authority root suspended",
        "/admin/organization",
        "Review why the root was suspended.",
        True,
    ),
    "connector.quarantined": _t(
        H,
        "An integration connector was quarantined",
        "/admin/integrations",
        "Open the connector, fix the cause and request release.",
        True,
    ),
    "integration.credential.expiring": _t(
        H,
        "An integration credential is about to expire",
        "/admin/integrations",
        "Rotate the connector credential before it lapses.",
        True,
    ),
    "tenant.domain.stale": _t(
        H,
        "A verified domain needs re-verification",
        "/admin/domains",
        "Re-prove control of the domain.",
        True,
    ),
    "webhook.delivery.exhausted": _t(
        H,
        "A webhook delivery ran out of retries",
        "/admin/developers",
        "Check the receiver, then replay the delivery.",
        True,
    ),
    "webhook.endpoint.disabled": _t(
        H,
        "A webhook endpoint was disabled",
        "/admin/developers",
        "The receiver answered 410 Gone; register a new endpoint if it is still wanted.",
        True,
    ),
    "verification.provider.quarantined": _t(
        H,
        "A verification provider was quarantined",
        "/admin/verification",
        "Check provider health before relying on its results.",
        True,
    ),
    "machine.attestation.failed": _t(
        H, "Workload attestation failed", "/admin/workloads", "Review the workload binding.", True
    ),
    "workload.attestation.failed": _t(
        H, "Workload attestation failed", "/admin/workloads", "Review the workload binding.", True
    ),
    "tenant.failover.failed": _t(
        H, "A cell failover failed", "/admin/residency", "Contact Zoiko iD support.", True
    ),
    "commercial.budget.threshold_crossed": _t(
        H,
        "A usage budget or included allowance threshold was reached",
        "/billing",
        "Review usage and the projected bill; nothing has been throttled.",
    ),
    "commercial.plan_request.answered": _t(
        I,
        "Your request for a different plan was answered",
        "/admin/billing",
        "Open Billing to see your plan.",
    ),
    "commercial.billing_state.changed": _t(
        H,
        "The account's billing status changed",
        "/billing",
        "Review invoices and payment; sign-in and revocation are never affected.",
        True,
    ),
    "tenant.migration.failed": _t(
        H, "A tenant migration failed", "/admin/residency", "Contact Zoiko iD support.", True
    ),
    # --- MEDIUM: drift, upcoming expiry, review queue
    "mandate.exhausted": _t(
        M,
        "A mandate budget is exhausted",
        "/admin/mandates",
        "Review the mandate; raise it through a new, approved mandate if needed.",
    ),
    "mandate.revoked": _t(
        M, "A mandate was revoked", "/admin/mandates", "Check dependent agents and workflows."
    ),
    "authority.grant.suspended": _t(
        M,
        "An authority grant was suspended",
        "/admin/authority",
        "Review the grant and its revalidation state.",
    ),
    "authority.grant.revoked": _t(
        M,
        "An authority grant was revoked",
        "/admin/authority",
        "Check delegations and mandates built on it.",
    ),
    "credential.revoked": _t(
        M, "A credential was revoked", "/admin/credentials", "Check where it was in use."
    ),
    "verification.assertion.expired": _t(
        M, "A verification assertion expired", "/admin/verification", "Request reproof if needed."
    ),
    "integration.connector.stale": _t(
        M,
        "An integration connector is stale",
        "/admin/integrations",
        "Check the connector's sync and health.",
    ),
    "security.exception.expired": _t(
        M,
        "A security exception expired",
        "/admin/signals",
        "Confirm the exception is no longer needed.",
    ),
    # --- INFO: routine progress
    "tenant.failover.completed": _t(
        I, "A cell failover completed", "/admin/residency", "No action is needed."
    ),
}

# Notices a tenant can never mute (UX §20): CRITICAL and HIGH are mandatory security notices.
MANDATORY_KINDS: frozenset[str] = frozenset(
    name for name, t in TEMPLATES.items() if t.severity in (Severity.CRITICAL, Severity.HIGH)
)

# Kafka topic domains the alert consumer subscribes to (first segment of each template name).
DOMAINS = sorted({name.split(".")[0] for name in TEMPLATES})


def event_name(event_type: str) -> str:
    """`com.zoiko.id.mandate.revoked.v1` -> `mandate.revoked`."""
    parts = event_type.removeprefix(PREFIX).split(".")
    if parts and parts[-1].startswith("v") and parts[-1][1:].isdigit():
        parts = parts[:-1]
    return ".".join(parts)


def draft_for_event(
    event_type: str,
    *,
    subject: str | None,
    aggregate_type: str,
    aggregate_id: str,
    event_id: str | None,
) -> AlertDraft | None:
    """The alert a catalogued event raises, or None when it is not alert-worthy."""
    name = event_name(event_type)
    template = TEMPLATES.get(name)
    if template is None:
        return None
    ref = subject or f"{aggregate_type}/{aggregate_id}"
    return AlertDraft(
        severity=template.severity,
        kind=name,
        title=template.title,
        body="",
        object_type=aggregate_type or name.split(".")[0],
        object_ref=ref,
        href=template.href,
        recommended_action=template.action,
        is_task=template.task,
        mandatory=template.severity in (Severity.CRITICAL, Severity.HIGH),
        correlation_key=f"{name}:{ref}",
        source_event_id=event_id,
    )


def check_transition(current: str, to: AlertState) -> None:
    """OPEN -> ACKNOWLEDGED -> RESOLVED; OPEN may also resolve directly. Resolved is terminal."""
    allowed = {
        AlertState.ACKNOWLEDGED: {AlertState.OPEN},
        AlertState.RESOLVED: {AlertState.OPEN, AlertState.ACKNOWLEDGED},
    }
    if AlertState(current) not in allowed.get(to, set()):
        raise AlertError("ALERT_STATE", f"cannot move a {current} alert to {to.value}")

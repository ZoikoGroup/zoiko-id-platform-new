"""Shared plumbing of the access readiness service: the runtime settings it is given, the audit record and the event (CR-091)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit, next_version

from zid_platform.modules.access_readiness.application.explain import ExplanationGateway
from zid_platform.modules.access_readiness.application.usage import (
    DEFAULT_TIMEOUT_SECONDS,
    UsagePolicy,
)
from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit

DEFAULT_APPROVAL_TTL = timedelta(days=7)

# Audit-only names (records in the audit trail, not events on the bus; the catalogue declares only emitted events).
SETUP_CREATED = "access_readiness.setup.created"
SETUP_CHANGED = "access_readiness.setup.changed"
DRY_RUN_COMPLETED = "access_readiness.dry_run.completed"
PUBLISH_REQUESTED = "access_readiness.publish.requested"
PUBLISH_APPROVED = "access_readiness.publish.approved"
SETUP_PAUSED = "access_readiness.setup.paused"
SETUP_RESUMED = "access_readiness.setup.resumed"
RUN_REQUESTED = "access_readiness.run.requested"
PROPOSAL_REVIEWED = "access_readiness.proposal.reviewed"
HANDOFF_SIGNED = "access_readiness.handoff.signed"
EVIDENCE_EXPORTED = "access_readiness.evidence.exported"
EVIDENCE_EXPORT_REFUSED = "access_readiness.evidence.export_refused"
# the notification raised for the agent's sponsor when a run ends (the alert kind; run id, state and counts only)
RUN_FINISHED_ALERT = "access_readiness.run.finished"
# Emitted events (ZID-AG-WF-001 section 10)
EVENT_RUN_DENIED = "agent.run.policy_denied"
EVENT_RUN_COMPLETED = "agent.run.completed"
EVENT_PROPOSAL_CREATED = "agent.review.proposal_created"


@dataclass(frozen=True, slots=True)
class Runtime:
    """What the deployment says about this cell: the switch, the regions it is approved for, who signs a handoff. Default OFF."""

    enabled: bool = False
    regions: frozenset[str] = frozenset()
    region: str = ""  # this cell's region (server-set; never taken from a request)
    cell_uuid: str | None = None
    approval_ttl: timedelta = DEFAULT_APPROVAL_TTL
    signer: Callable[[dict[str, Any]], str] | None = None
    # the optional explanation (ZID_ACCESS_READINESS_AI_EXPLAIN, default off; a setup must also opt in) and the gateway that drafts it
    ai_explain: bool = False
    explainer: ExplanationGateway | None = None
    ai_timeout_seconds: float = 8.0
    # the usage and budget hook asked before a run and before each step (None: the Q1 hook, which sets no limit); fails closed
    usage_policy: UsagePolicy | None = None
    usage_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    # the database role a queued run executes AS in the worker (None: the worker's own connection). See `zid_db.as_role`.
    run_role: str | None = None

    @property
    def region_supported(self) -> bool:
        return bool(self.region) and self.region in self.regions


def utcnow() -> datetime:
    return datetime.now(UTC)


async def record(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    name: str,
    event: bool,
    aggregate_type: str,
    aggregate_id: str,
    actor: str | None,
    data: dict[str, Any],
    actor_type: str = "HUMAN",
    outcome: str = "SUCCESS",
    reason_code: str | None = None,
    evidence: EvidenceClass = EvidenceClass.E3,
) -> None:
    """One audit record and, for the three catalogued events, one outbox event. Ids, codes and digests only."""
    event_id = None
    if event:
        event_id = await emit(
            conn,
            name=name,
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=await next_version(conn, aggregate_type, aggregate_id),
            data={**data, "evidence_class": evidence.value},
        )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=name,
            actor_type=actor_type,
            action=name.rsplit(".", 1)[-1],
            target_type=aggregate_type,
            target_ref=aggregate_id,
            outcome=outcome,
            evidence_class=evidence,
            occurred_at=utcnow(),
            actor_principal_id=actor,
            reason_code=reason_code,
            source_service="access_readiness",
            source_event_id=event_id,
            details={k: v for k, v in data.items() if isinstance(v, (str, int, bool))},
        ),
    )

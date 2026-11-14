"""The human side of a review proposal: dismiss, assign, hand off (ZID-AG-WF-001 section 08; CR-091).

A review queue, not an identity-administration shortcut. Nothing here changes a mandate, a grant, a role or a credential, on open or on
acceptance. "Open governed access workflow" records a signed handoff and nothing else: the access-change service has to take it up with its
own permissions, a fresh policy evaluation and an explicit human execution (AT-12). The signature is over the digest of the handoff record;
without a signing key no handoff is recorded rather than recorded unsigned.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.access_readiness.application import common
from zid_platform.modules.access_readiness.application.common import Runtime, record, utcnow
from zid_platform.modules.access_readiness.domain.policy import (
    ProposalStatus,
    ReadinessError,
    proposal_transition,
)
from zid_platform.modules.access_readiness.domain.rules import digest
from zid_platform.modules.access_readiness.infrastructure import store

NOTICE = "No access permissions will change as a result of this review proposal."
TARGET_WORKFLOW = "governed_access_workflow"


def mask(target_ref: str) -> str:
    """A mandate shown to a reviewer is masked to its kind and the first characters of its id."""
    kind, _, ident = target_ref.partition(":")
    return f"{kind}:{ident[:8]}..." if ident else target_ref


def proposal_view(p: dict[str, Any], handoff: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "proposal_id": p["proposal_id"],
        "submitted_by": {"type": "agent", "agent_id": p["created_by_agent_id"]},
        "target": mask(p["target_ref"]),
        "rule_id": p["rule_id"],
        "rule_version": p["rule_version"],
        "finding_code": p["finding_code"],
        "severity": p["severity"],  # how serious the finding is
        "confidence": p["confidence"],  # a different question: a rule is deterministic, not a guess
        "evidence_refs": list(p["evidence_refs"]),
        "run_id": p["run_id"],
        "status": p["status"],
        "reviewed_by": p["reviewed_by"],
        "assignee": p["assignee"],
        "created_at": p["created_at"].isoformat(),
        "handoff": None
        if handoff is None
        else {
            "handoff_id": handoff["handoff_id"],
            "target_workflow": handoff["target_workflow"],
            "handoff_digest": handoff["handoff_digest"],
            "signature": handoff["signature"],
            "executes": handoff["executes"],
            "signed_at": handoff["signed_at"].isoformat(),
        },
        "notice": NOTICE,
    }


async def _load(conn: AsyncConnection, proposal_id: str) -> dict[str, Any]:
    p = await store.load_proposal(conn, proposal_id, lock=True)
    if p is None:
        raise ReadinessError("PROPOSAL_NOT_FOUND")
    return p


async def get_proposal(conn: AsyncConnection, proposal_id: str) -> dict[str, Any]:
    p = await store.load_proposal(conn, proposal_id)
    if p is None:
        raise ReadinessError("PROPOSAL_NOT_FOUND")
    return proposal_view(p, await store.load_handoff(conn, proposal_id))


async def review(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    actor: str,
    proposal_id: str,
    target: ProposalStatus,
    assignee: str | None = None,
) -> dict[str, Any]:
    if target is ProposalStatus.HANDED_OFF:
        raise ReadinessError("USE_HANDOFF")
    p = await _load(conn, proposal_id)
    proposal_transition(ProposalStatus(p["status"]), target)
    updated = await store.review_proposal(
        conn, proposal_id, status=target.value, reviewer=actor, assignee=assignee
    )
    await record(
        conn,
        tenant_id,
        name=common.PROPOSAL_REVIEWED,
        event=False,
        aggregate_type="agent_review",
        aggregate_id=proposal_id,
        actor=actor,
        data={"status": target.value},
    )
    return proposal_view(updated)


async def hand_off(
    conn: AsyncConnection, rt: Runtime, tenant_id: str, *, actor: str, proposal_id: str
) -> dict[str, Any]:
    """Record a signed request to open the governed access workflow. Executes nothing; the proposal stays a recommendation."""
    p = await _load(conn, proposal_id)
    existing = await store.load_handoff(conn, proposal_id)
    if existing is not None:
        return proposal_view(p, existing)
    proposal_transition(ProposalStatus(p["status"]), ProposalStatus.HANDED_OFF)
    if rt.signer is None:
        raise ReadinessError(
            "SIGNING_UNAVAILABLE", "no signing key is configured; no handoff is recorded unsigned"
        )
    record_body = {
        "proposal_id": proposal_id,
        "target_ref": p["target_ref"],
        "rule_id": p["rule_id"],
        "finding_code": p["finding_code"],
        "handed_off_by": actor,
        "target_workflow": TARGET_WORKFLOW,
        "executes": False,
        "signed_at": utcnow().isoformat(),
    }
    signature = rt.signer(record_body)
    handoff = await store.insert_handoff(
        conn,
        tenant_id,
        proposal_id=proposal_id,
        handed_off_by=actor,
        target_workflow=TARGET_WORKFLOW,
        handoff_digest=digest(record_body),
        signature=signature,
    )
    updated = await store.review_proposal(
        conn, proposal_id, status=ProposalStatus.HANDED_OFF.value, reviewer=actor
    )
    await record(
        conn,
        tenant_id,
        name=common.HANDOFF_SIGNED,
        event=False,
        aggregate_type="agent_review",
        aggregate_id=proposal_id,
        actor=actor,
        data={"handoff_id": handoff["handoff_id"], "handoff_digest": handoff["handoff_digest"]},
    )
    return proposal_view(updated, handoff)

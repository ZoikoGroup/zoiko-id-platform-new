"""Export permitted evidence of one run (ZID-AG-WF-001 section 07, WF-AG-03 'Export permitted evidence'; CR-091).

What leaves is references and digests only: the hash-chained envelopes (verified now, so a broken link is exported as an integrity
exception, never as a clean chain), the policy decisions and the proposals' rule, code and evidence references. Never a document, a
token, a personal field or a finding's detail. A caller who may read the run but not review its proposals gets a redacted export
(resources reduced to their kind, targets and evidence references withheld) that says it is redacted, never an empty success.

As with every other evidence export, a stated purpose is required: a request without one is refused and the refusal is recorded; an
export is recorded with its purpose's digest, its shape and whether it was redacted.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.access_readiness.application import common
from zid_platform.modules.access_readiness.application.common import record, utcnow
from zid_platform.modules.access_readiness.application.review import mask
from zid_platform.modules.access_readiness.infrastructure import store

EXPORT_SCHEMA = "zid.access-readiness.evidence.v1"
REDACTED = "redacted"


def has_purpose(purpose: str | None) -> bool:
    return len((purpose or "").strip()) >= 3


def _kind(resource: str) -> str:
    kind, _, rest = resource.partition(":")
    return f"{kind}:{REDACTED}" if rest else resource


async def export_evidence(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    actor: str,
    run: dict[str, Any],
    purpose: str,
    full: bool,
) -> dict[str, Any]:
    run_id = run["run_id"]
    envelopes = await store.list_evidence(conn, run_id)
    problems = store.verify_chain(envelopes)
    decisions = await store.list_decisions(conn, run_id)
    proposals = await store.list_proposals(conn, run_id=run_id, limit=500)
    summary = run["summary"] if isinstance(run["summary"], dict) else {}
    out: dict[str, Any] = {
        "schema": EXPORT_SCHEMA,
        "run_id": run_id,
        "agent_id": run["agent_principal_id"],
        "state": run["state"],
        "rule_set_version": run["rule_set_version"],
        "policy_hash": run["policy_hash"],
        "run_definition_hash": run["run_definition_hash"],
        "result_digest": summary.get("result_digest") or None,
        "region": run["home_region"],
        "exported_at": utcnow().isoformat(),
        "redacted": not full,
        "chain": {
            "verified": not problems and bool(envelopes),
            "integrity_exceptions": problems,
        },
        "evidence": [
            {
                "seq": e["seq"],
                "event_type": e["event_type"],
                "source_id": e["source_id"],
                "source_version": e["source_version"],
                "payload_digest": e["payload_digest"],
                "chain_digest": e["chain_digest"],
                "classification": e["classification"],
                "region": e["region"],
                "recorded_at": e["recorded_at"].isoformat(),
            }
            for e in envelopes
        ],
        "decisions": [
            {
                "action": d["action"],
                "resource": d["resource"] if full else _kind(d["resource"]),
                "outcome": d["outcome"],
                "reason_code": d["reason_code"],
                "policy_hash": d["policy_hash"],
                "decided_at": d["decided_at"].isoformat(),
            }
            for d in decisions
        ],
        "proposals": [
            {
                "proposal_id": p["proposal_id"],
                "rule_id": p["rule_id"],
                "rule_version": p["rule_version"],
                "finding_code": p["finding_code"],
                "severity": p["severity"],
                "status": p["status"],
                "target": mask(p["target_ref"]) if full else REDACTED,
                "evidence_refs": list(p["evidence_refs"]) if full else [],
                "evidence_ref_count": len(p["evidence_refs"]),
            }
            for p in proposals
        ],
    }
    await record(
        conn,
        tenant_id,
        name=common.EVIDENCE_EXPORTED,
        event=False,
        aggregate_type="agent_run",
        aggregate_id=run_id,
        actor=actor,
        data={
            "agent_id": run["agent_principal_id"],
            "redacted": not full,
            "envelopes": len(envelopes),
            "chain_verified": out["chain"]["verified"],
            "purpose_sha256": hashlib.sha256(purpose.strip().encode()).hexdigest(),
        },
    )
    return out


async def record_refusal(
    conn: AsyncConnection, tenant_id: str, *, actor: str, run_id: str, code: str
) -> None:
    await record(
        conn,
        tenant_id,
        name=common.EVIDENCE_EXPORT_REFUSED,
        event=False,
        aggregate_type="agent_run",
        aggregate_id=run_id,
        actor=actor,
        outcome="DENIED",
        reason_code=code,
        data={"reason": code},
    )

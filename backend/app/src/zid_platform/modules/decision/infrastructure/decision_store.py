"""Operational decision records (ZID-DATA-001 §7.4; ZID-AUTHZ-001 §12 step 12, §24).

One append-only row per decision with the exact policy release, revisions, chain commitment and a
commitment to the deterministic inputs, so an E3 decision can be replayed. Sensitive inputs are
committed (hashed), not copied. The pseudonymous DecisionReceipt is the evidence module's job (Phase F).
"""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.decision.domain.pdp import Decision, DecisionRequest


def input_commitment(decision: Decision) -> str:
    canonical = json.dumps(decision.inputs, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def record_decision(
    conn: AsyncConnection, req: DecisionRequest, executor_id: str, d: Decision
) -> None:
    await conn.execute(
        text("""
            INSERT INTO authz.policy_decision (decision_id, tenant_id, subject_principal_id, represented_principal_id,
                action, resource_ref, risk_class, evidence_class, outcome, reason_codes, obligations, policy_release_id,
                relation_revision, security_revision, authority_chain_commitment, mandate_id, input_commitment,
                decided_at, expires_at)
            VALUES (:id, :t, :sub, :rep, :act, :res, :risk, :ev, CAST(:out AS authz.decision_outcome), :codes,
                    CAST(:obl AS jsonb), :rel, :rrev, :srev, :chain, :mid, :inp, :at, :exp)
        """),
        {
            "id": uuid.UUID(d.decision_id),
            "t": uuid.UUID(req.tenant_id),
            "sub": uuid.UUID(executor_id),
            "rep": uuid.UUID(req.represented_principal_id)
            if req.represented_principal_id
            else None,
            "act": req.action.name,
            "res": req.resource_id,
            "risk": d.risk_class.name,
            "ev": d.evidence_class.name,
            "out": d.outcome.value,
            "codes": list(d.reason_codes),
            "obl": json.dumps([dict(o) for o in d.obligations]),
            "rel": uuid.UUID(d.policy_release_id) if d.policy_release_id else None,
            "rrev": d.relation_revision,
            "srev": d.security_revision,
            "chain": d.authority_chain_commitment,
            "mid": uuid.UUID(d.mandate_id) if d.mandate_id else None,
            "inp": input_commitment(d),
            "at": req.at,
            "exp": d.valid_until,
        },
    )

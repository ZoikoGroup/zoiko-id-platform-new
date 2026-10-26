"""Every PDP decision leaves evidence (EVIDENCE-001 §2.1, §3, §4, §19; DEC-F1; F-W2).

- `emit_receipt`: E0 -> aggregate count; E1-E3 -> a pseudonymous DecisionReceipt written in the
  decision's own transaction. The material input projection (request + every resolved fact + the
  policy result) is committed in the receipt and, for E3, retained under the actor's erasable key.
- `replay`: the pinned evaluator for AUTHORIZATION receipts. It rebuilds the facts from the
  retained projection, re-evaluates the *historic* immutable policy release (never today's), and
  runs `decide` again; the evidence module compares outcome, reasons and obligations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.decision.domain.pdp import (
    ActionSpec,
    AuthnFact,
    AuthorityFact,
    Decision,
    DecisionRequest,
    FactState,
    MandateFact,
    PolicyFact,
    PrincipalFact,
    RelationFact,
    decide,
)
from zid_platform.modules.decision.domain.risk import EvidenceClass, RiskClass, WeakeningException
from zid_platform.modules.evidence.ports import EvidenceClass as EvClass
from zid_platform.modules.evidence.ports import (
    LedgerError,
    Node,
    ReceiptFacts,
    add_edges,
    artifact,
    record_decision_receipt,
    register_artifact,
    register_replayer,
)
from zid_platform.modules.policy_authoring.ports import load_release
from zid_platform.modules.policy_runtime.ports import (
    PolicyError,
    PolicyRelease,
    build_schema,
    evaluate,
)

ENGINE_VERSION = "zid-pdp/1 cedar"


def _fact(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    out = asdict(value)
    for k, v in out.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, FactState):
            out[k] = v.value
        elif isinstance(v, tuple):
            out[k] = list(v)
    return out


def material(
    req: DecisionRequest,
    *,
    principal: PrincipalFact,
    authn: AuthnFact,
    authority: AuthorityFact | None,
    relation: RelationFact | None,
    mandate: MandateFact | None,
    policy: PolicyFact,
    decision: Decision,
) -> dict[str, Any]:
    """The material input projection (§4): every input `decide` used, nothing presentational."""
    exc = req.risk_exception
    return {
        "schema_version": "authz-material.v1",
        "request": {
            "decision_id": req.decision_id,
            "tenant_id": req.tenant_id,
            "action": {
                "name": req.action.name,
                "min_risk": int(req.action.min_risk),
                "min_evidence": int(req.action.min_evidence),
                "consumes_mandate": req.action.consumes_mandate,
                "requires_approval": req.action.requires_approval,
            },
            "resource_id": req.resource_id,
            "represented_principal_id": req.represented_principal_id,
            "purpose": req.purpose,
            "tool_id": req.tool_id,
            "at": req.at.isoformat(),
            "requested_risk": int(req.requested_risk) if req.requested_risk is not None else None,
            "tenant_override": int(req.tenant_override)
            if req.tenant_override is not None
            else None,
            "risk_exception": (
                {
                    "action": exc.action,
                    "tenant_id": exc.tenant_id,
                    "lowered_to": int(exc.lowered_to),
                    "approved_by": sorted(exc.approved_by),
                    "compensating_control": exc.compensating_control,
                    "expires_at": exc.expires_at.isoformat(),
                    "approval_ref": exc.approval_ref,
                }
                if exc
                else None
            ),
            "approval_state": req.approval_state,
            "bounded_cache_approved": req.bounded_cache_approved,
        },
        "facts": {
            "principal": _fact(principal),
            "authn": _fact(authn),
            "authority": _fact(authority),
            "relation": _fact(relation),
            "mandate": _fact(mandate),
        },
        "policy": {
            "release_id": policy.release_id,
            "allowed": policy.allowed,
            "codes": list(policy.codes),
            "context": dict(decision.inputs.get("context", {})),  # type: ignore[call-overload]
        },
    }


def _decision_type(action: str) -> str:
    return "AUTHORIZATION"


def release_artifact(release: PolicyRelease) -> dict[str, Any]:
    """The immutable snapshot of a policy release (no author/reviewer identities)."""
    return {
        "release_id": release.release_id,
        "schema_version": release.schema_version,
        "policies": release.policies,
        "release_content_hash": release.content_hash,
    }


def action_artifact(action: ActionSpec) -> dict[str, Any]:
    return {
        "action": action.name,
        "min_risk": int(action.min_risk),
        "min_evidence": int(action.min_evidence),
        "consumes_mandate": action.consumes_mandate,
        "requires_approval": action.requires_approval,
    }


async def emit_receipt(
    conn: AsyncConnection,
    req: DecisionRequest,
    executor_id: str,
    decision: Decision,
    mat: Mapping[str, Any],
    release: PolicyRelease | None = None,
) -> str | None:
    cls = EvClass(f"E{int(decision.evidence_class)}")
    facts = ReceiptFacts(
        decision_id=decision.decision_id,
        decision_type=_decision_type(req.action.name),
        evidence_class=cls,
        action=req.action.name,
        resource_class=req.resource_id.split(":", 1)[0] if ":" in req.resource_id else "resource",
        outcome=decision.outcome.value,
        reason_codes=decision.reason_codes,
        risk_class=decision.risk_class.name,
        effective_at=req.at,
        actor_principal_id=executor_id,
        represented_principal_id=req.represented_principal_id,
        resource_ref=req.resource_id,
        workload_ref=req.workload_ref,
        tool_ref=req.tool_id,
        purpose_id=req.purpose,
        policy_release_id=decision.policy_release_id or None,
        policy_engine_version=ENGINE_VERSION,
        relation_revision=decision.relation_revision,
        security_revision=decision.security_revision,
        authority_chain_commitment=decision.authority_chain_commitment or None,
        mandate_commitment=decision.mandate_id,
        approval_commitment=req.approval_state,
        obligations=decision.obligations,
        valid_until=decision.valid_until,
    )
    receipt_id = await record_decision_receipt(
        conn,
        req.tenant_id,
        facts,
        floor=EvClass(f"E{int(req.action.min_evidence)}"),
        material=mat,
    )
    if receipt_id is None:  # E0: aggregated, no lineage to record
        return None
    # Immutable artifacts the decision used, and its PROV lineage (EVIDENCE §8-9, EVID-28)
    action_body = action_artifact(req.action)
    action_id, _ = await register_artifact(
        conn,
        None,
        "ACTION_DEFINITION",
        req.action.name,
        f"{action_body['min_risk']}.{action_body['min_evidence']}",
        action_body,
    )
    decision_node = Node("activity", "authorization_decision", decision.decision_id)
    edges = [
        (Node("entity", "decision_receipt", receipt_id), "wasGeneratedBy", decision_node),
        (decision_node, "used", Node("entity", "action_definition", action_id)),
        (decision_node, "wasAssociatedWith", Node("agent", "service", ENGINE_VERSION)),
    ]
    if release is not None:
        policy_id, _ = await register_artifact(
            conn,
            req.tenant_id,
            "POLICY_RELEASE",
            release.release_id,
            release.content_hash,
            release_artifact(release),
        )
        edges.append((decision_node, "used", Node("entity", "policy_release", policy_id)))
    if decision.authority_chain_commitment:
        edges.append(
            (
                decision_node,
                "used",
                Node("entity", "authority_chain", decision.authority_chain_commitment),
            )
        )
    if decision.mandate_id:
        edges.append((decision_node, "used", Node("entity", "mandate", decision.mandate_id)))
    await add_edges(conn, req.tenant_id, edges, receipt_id=receipt_id)
    return receipt_id


def _t(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _rebuild(m: Mapping[str, Any]) -> tuple[DecisionRequest, dict[str, Any]]:
    r = m["request"]
    a = r["action"]
    exc = r.get("risk_exception")
    req = DecisionRequest(
        decision_id=r["decision_id"],
        tenant_id=r["tenant_id"],
        action=ActionSpec(
            a["name"],
            RiskClass(a["min_risk"]),
            EvidenceClass(a["min_evidence"]),
            a["consumes_mandate"],
            a["requires_approval"],
        ),
        resource_id=r["resource_id"],
        represented_principal_id=r["represented_principal_id"],
        purpose=r["purpose"],
        tool_id=r["tool_id"],
        at=_t(r["at"]),
        requested_risk=RiskClass(r["requested_risk"]) if r["requested_risk"] is not None else None,
        tenant_override=RiskClass(r["tenant_override"])
        if r["tenant_override"] is not None
        else None,
        risk_exception=(
            WeakeningException(
                exc["action"],
                exc["tenant_id"],
                RiskClass(exc["lowered_to"]),
                frozenset(exc["approved_by"]),
                exc["compensating_control"],
                _t(exc["expires_at"]),
                exc["approval_ref"],
            )
            if exc
            else None
        ),
        approval_state=r["approval_state"],
        bounded_cache_approved=r["bounded_cache_approved"],
    )
    f = m["facts"]

    def state(d: Mapping[str, Any]) -> FactState:
        return FactState(d.get("state", "OK"))

    p = f["principal"]
    facts: dict[str, Any] = {
        "principal": PrincipalFact(
            p["principal_id"],
            p["principal_type"],
            p["active"],
            p["security_state"],
            p["security_revision"],
            state(p),
        ),
        "authn": AuthnFact(f["authn"]["level"], _t(f["authn"]["auth_time"]), state(f["authn"])),
        "authority": None,
        "relation": None,
        "mandate": None,
    }
    if (au := f.get("authority")) is not None:
        facts["authority"] = AuthorityFact(
            au["valid"],
            tuple(au["codes"]),
            au["chain_commitment"],
            _t(au["valid_until"]) if au["valid_until"] else None,
            state(au),
        )
    if (rel := f.get("relation")) is not None:
        facts["relation"] = RelationFact(rel["relation_revision"], rel["fresh"], state(rel))
    if (md := f.get("mandate")) is not None:
        facts["mandate"] = MandateFact(
            md["mandate_id"],
            md["active"],
            md["in_scope"],
            md["tool_allowed"],
            _t(md["valid_until"]) if md["valid_until"] else None,
            state(md),
        )
    return req, facts


def replayer(catalog: Mapping[str, Mapping[str, Any]]):  # type: ignore[no-untyped-def]
    async def replay(
        conn: AsyncConnection, receipt: Mapping[str, Any], m: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        req, facts = _rebuild(m)
        pol = m["policy"]
        release = None
        if pol["release_id"]:
            try:
                release = await load_release(conn, pol["release_id"])  # immutable (EVID-08)
            except PolicyError as e:
                raise LedgerError("EVIDENCE_POLICY_ARTIFACT_MISSING", str(e)) from e
            registered = await artifact(conn, req.tenant_id, "POLICY_RELEASE", release.release_id)
            if not registered:
                raise LedgerError(
                    "EVIDENCE_POLICY_ARTIFACT_MISSING", "policy release was never registered"
                )
            if not any(
                a["body"].get("release_content_hash") == release.content_hash for a in registered
            ):
                raise LedgerError(
                    "EVIDENCE_REPLAY_MISMATCH",
                    "policy release differs from the registered artifact",
                )
        schema = build_schema(catalog)

        def policy(context: Mapping[str, object]) -> PolicyFact:
            if release is None:
                return PolicyFact("", False, ("POLICY_NO_ACTIVE_RELEASE",))
            out = evaluate(
                release,
                principal_id=facts["principal"].principal_id,
                action=req.action.name,
                resource_id=req.resource_id,
                context=context,
                schema=schema,
            )
            return PolicyFact(release.release_id, out.allowed, out.codes)

        d = decide(req, policy=policy, **facts)
        return {
            "outcome": d.outcome.value,
            "reason_codes": list(d.reason_codes),
            "obligations": [dict(o) for o in d.obligations],
        }

    return replay


def register(catalog: Mapping[str, Mapping[str, Any]]) -> None:
    register_replayer("AUTHORIZATION", replayer(catalog))


__all__ = ["emit_receipt", "material", "register", "replayer"]

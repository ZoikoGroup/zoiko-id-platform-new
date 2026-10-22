"""Decision service: gather facts through module ports, then `decide` (ZID-AUTHZ-001 §12, §17).

Each Policy Information Point call is wrapped so that a failure becomes a typed UNAVAILABLE fact,
never an empty one (AUTHZ §17.1). The PDP fetches the minimum facts the request needs; it never
reads another module's tables. Security state comes from the signals PIP (Phase E); until then the
caller passes it, and the deny-safe default is UNKNOWN (which fails closed for R2/R3).
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.authority.ports import Request, authorize_request
from zid_platform.modules.decision.application.receipts import emit_receipt, material
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
from zid_platform.modules.decision.domain.risk import EvidenceClass, RiskClass
from zid_platform.modules.decision.infrastructure.decision_store import record_decision
from zid_platform.modules.policy_authoring.ports import active_release
from zid_platform.modules.policy_runtime.ports import PolicyRelease, build_schema, evaluate
from zid_platform.modules.principal.ports import PrincipalStatus, load_principal
from zid_platform.modules.relationship.ports import current_revision


def action_spec(name: str, catalog: Mapping[str, Mapping[str, Any]]) -> ActionSpec:
    """Unknown actions are refused: services can't invent action strings at runtime (AUTHZ §3.1)."""
    spec = catalog.get(name)
    if spec is None:
        raise KeyError(f"ACTION_UNKNOWN: {name}")
    return ActionSpec(
        name=name,
        min_risk=RiskClass[spec["min_risk_class"]],
        min_evidence=EvidenceClass[spec["min_evidence_class"]],
        consumes_mandate=bool(spec.get("consumes_mandate", False)),
        requires_approval=bool(spec.get("requires_approval", False)),
    )


async def _guard[T](fetch: Callable[[], Awaitable[T]], unavailable: T) -> T:
    try:
        return await fetch()
    except Exception:  # noqa: BLE001 - a failed PIP is a typed UNAVAILABLE fact, never absence
        return unavailable


def chain_commitment(chain_ids: tuple[str, ...], root_id: str | None) -> str:
    return "sha256:" + hashlib.sha256("|".join([*chain_ids, root_id or ""]).encode()).hexdigest()


async def evaluate_request(
    conn: AsyncConnection,
    *,
    req: DecisionRequest,
    executor_id: str,
    authority_request: Request,
    authn: AuthnFact,
    security_state: str = "UNKNOWN",
    relation: RelationFact | None = None,
    mandate: MandateFact | None = None,
    release: PolicyRelease | None = None,
    catalog: Mapping[str, Mapping[str, Any]],
    record: bool = True,
) -> Decision:
    async def principal_fact() -> PrincipalFact:
        p, _ = await load_principal(conn, executor_id)
        return PrincipalFact(
            p.principal_id,
            p.principal_type.value,
            p.status is PrincipalStatus.ACTIVE,
            security_state,
            p.security_revision,
        )

    principal = await _guard(
        principal_fact,
        PrincipalFact(executor_id, "UNKNOWN", False, "UNKNOWN", 0, FactState.UNAVAILABLE),
    )

    async def authority_fact() -> AuthorityFact:
        d = await authorize_request(
            conn,
            tenant_id=req.tenant_id,
            grantee_id=executor_id,
            request=authority_request,
            risk_level=int(req.action.min_risk),
        )
        res = d.resolution
        commitment = chain_commitment(res.chain_ids, res.root_id) if res else ""
        return AuthorityFact(d.allowed, () if d.allowed else d.codes, commitment, None)

    authority = await _guard(
        authority_fact, AuthorityFact(False, (), "", None, FactState.UNAVAILABLE)
    )
    schema = build_schema(catalog)
    if release is None:
        release = await _guard(lambda: active_release(conn, req.tenant_id), None)

    evaluated: list[PolicyFact] = []

    def policy(context: Mapping[str, object]) -> PolicyFact:
        if release is None:
            fact = PolicyFact("", False, ("POLICY_NO_ACTIVE_RELEASE",))
        else:
            out = evaluate(
                release,
                principal_id=executor_id,
                action=req.action.name,
                resource_id=req.resource_id,
                context=context,
                schema=schema,
            )
            fact = PolicyFact(release.release_id, out.allowed, out.codes)
        evaluated.append(fact)
        return fact

    if (
        relation is None
    ):  # no relationship check needed: still bind the decision to the tenant revision
        relation = await _guard(
            lambda: _current_relation(conn, req.tenant_id),
            RelationFact(0, False, FactState.UNAVAILABLE),
        )
    decision = decide(
        req,
        principal=principal,
        authn=authn,
        authority=authority,
        relation=relation,
        mandate=mandate,
        policy=policy,
    )
    if record:
        await record_decision(conn, req, executor_id, decision)
        # F-W2: every recorded decision leaves a receipt (E0: aggregate only), same transaction
        await emit_receipt(
            conn,
            req,
            executor_id,
            decision,
            material(
                req,
                principal=principal,
                authn=authn,
                authority=authority,
                relation=relation,
                mandate=mandate,
                policy=evaluated[-1],
                decision=decision,
            ),
            release=release,
        )
    return decision


async def _current_relation(conn: AsyncConnection, tenant_id: str) -> RelationFact:
    return RelationFact(await current_revision(conn, tenant_id), True)


__all__ = ["action_spec", "chain_commitment", "evaluate_request"]

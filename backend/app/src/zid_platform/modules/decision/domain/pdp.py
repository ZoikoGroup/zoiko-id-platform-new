"""Deterministic decision algorithm (ZID-AUTHZ-001 §1, §12-15; DEC-B1, DEC-C1; INV-02/05/06/14/15).

`decide` composes facts that Policy Information Points already resolved, so the same policy release
and the same fact revisions always give the same decision (AUTHZ §4.1). It never calls out.

- A fact a PIP could not establish is `UNAVAILABLE`, never "absent": for R2/R3 it ends in
  STATE_UNAVAILABLE, and it can never turn into an allow (AUTHZ §17.1, INV-14).
- Authentication, relationships, verification and risk are inputs; authority comes only from a
  resolved grant chain, and a mandate where an agent/service acts for someone (INV-02, MACH-13).
- Precedence: STATE_UNAVAILABLE > DENY > STEP_UP > APPROVAL_REQUIRED > ALLOW.
- Every material decision carries the revisions it evaluated and the obligations a PEP must fulfil.
Pure domain, no I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from zid_platform.modules.decision.domain.risk import (
    EvidenceClass,
    FailurePosture,
    RiskClass,
    WeakeningException,
    effective_risk_class,
    evidence_floor,
    failure_posture,
    revocation_slo,
)


class Outcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    STEP_UP = "STEP_UP"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    STATE_UNAVAILABLE = "STATE_UNAVAILABLE"


# ZID-SIGNALS-001 §2: states a fresh strong authentication can satisfy; any other known
# non-NORMAL state denies R2/R3 outright, and UNKNOWN fails closed as unavailable.
STEP_UP_SECURITY_STATES = frozenset({"ELEVATED_RISK", "STEP_UP_REQUIRED", "REAUTH_REQUIRED"})


class FactState(StrEnum):
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"  # the PIP failed or timed out: typed, never treated as absence


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """From the action catalog (contracts/action-catalog): floors the caller cannot lower."""

    name: str
    min_risk: RiskClass
    min_evidence: EvidenceClass
    consumes_mandate: bool = False
    requires_approval: bool = False


@dataclass(frozen=True, slots=True)
class PrincipalFact:
    principal_id: str
    principal_type: str  # HUMAN, AI_AGENT, SERVICE, WORKLOAD, ...
    active: bool
    security_state: str  # NORMAL | COMPROMISED | UNKNOWN | a SIGNALS §2 state name
    security_revision: int
    state: FactState = FactState.OK


@dataclass(frozen=True, slots=True)
class AuthnFact:
    level: int  # AAL-Z0..Z3
    auth_time: datetime
    state: FactState = FactState.OK


@dataclass(frozen=True, slots=True)
class AuthorityFact:
    valid: bool
    codes: tuple[str, ...]
    chain_commitment: str  # commitment to the resolved root/grant chain (evidence, AUTHZ §24)
    valid_until: datetime | None
    state: FactState = FactState.OK


@dataclass(frozen=True, slots=True)
class RelationFact:
    relation_revision: int
    fresh: bool  # projection at or beyond the required revision for this risk class
    state: FactState = FactState.OK


@dataclass(frozen=True, slots=True)
class MandateFact:
    mandate_id: str
    active: bool
    in_scope: bool  # action/resource/purpose/tool inside the mandate
    tool_allowed: bool
    valid_until: datetime | None
    state: FactState = FactState.OK


@dataclass(frozen=True, slots=True)
class PolicyFact:
    release_id: str
    allowed: bool
    codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    decision_id: str
    tenant_id: str
    action: ActionSpec
    resource_id: str
    represented_principal_id: str | None
    purpose: str | None
    tool_id: str | None
    at: datetime
    requested_risk: RiskClass | None = None
    tenant_override: RiskClass | None = None
    risk_exception: WeakeningException | None = None
    approval_state: str | None = (
        None  # APPROVED | PENDING | DENIED | EXPIRED | STALE (composite.py)
    )
    bounded_cache_approved: bool = False


# Minimum authentication level and maximum age per risk class. Provisional engineering floor until
# ZID-AUTH-001 (Phase E) fixes the AAL-Z mapping; tenants may raise these, never lower them.
AUTHN_FLOOR: dict[RiskClass, tuple[int, timedelta]] = {
    RiskClass.R0: (0, timedelta(hours=24)),
    RiskClass.R1: (1, timedelta(hours=12)),
    RiskClass.R2: (2, timedelta(hours=1)),
    RiskClass.R3: (3, timedelta(minutes=15)),
}
MACHINE_TYPES = frozenset({"AI_AGENT", "SERVICE", "WORKLOAD", "APPLICATION"})


@dataclass(frozen=True, slots=True)
class Decision:
    decision_id: str
    outcome: Outcome
    reason_codes: tuple[str, ...]
    obligations: tuple[Mapping[str, object], ...]
    risk_class: RiskClass
    evidence_class: EvidenceClass
    policy_release_id: str | None
    relation_revision: int | None
    security_revision: int | None
    authority_chain_commitment: str | None
    mandate_id: str | None
    valid_until: datetime
    inputs: Mapping[str, object] = field(default_factory=dict)  # deterministic inputs for E3 replay


def decide(
    req: DecisionRequest,
    *,
    principal: PrincipalFact,
    authn: AuthnFact,
    authority: AuthorityFact | None,
    relation: RelationFact | None,
    mandate: MandateFact | None,
    policy: Callable[[Mapping[str, object]], PolicyFact],
) -> Decision:
    """AUTHZ §12 steps 2-11 over resolved facts. `policy` evaluates the typed policy set (Cedar)."""
    risk = effective_risk_class(
        req.action.name,
        req.tenant_id,
        req.action.min_risk,
        requested=req.requested_risk,
        tenant_override=req.tenant_override,
        exception=req.risk_exception,
        now=req.at,
    )
    evidence = max(req.action.min_evidence, evidence_floor(risk))
    high = risk >= RiskClass.R2
    unavailable: list[str] = []
    deny: list[str] = []
    step_up: list[str] = []

    # step 2: principal, security state, authentication freshness
    if principal.state is FactState.UNAVAILABLE:
        unavailable.append("STATE_UNAVAILABLE")
    elif not principal.active:
        deny.append("PRINCIPAL_INACTIVE")
    elif principal.security_state == "COMPROMISED":
        deny.append("SECURITY_STATE_COMPROMISED")
    elif principal.security_state == "UNKNOWN" and high:
        unavailable.append("STATE_UNAVAILABLE")  # cannot be established: fail closed (INV-14)
    elif principal.security_state in STEP_UP_SECURITY_STATES and high:
        step_up.append("SECURITY_STEP_UP_REQUIRED")  # SIGNALS §2: fresh strong auth clears it
    elif principal.security_state != "NORMAL" and high:
        deny.append("SECURITY_STATE_RESTRICTED")  # SUSPENDED, RESTRICTED, REPROOF_REQUIRED, ...
    if authn.state is FactState.UNAVAILABLE:
        unavailable.append("STATE_UNAVAILABLE")
    else:
        min_level, max_age = AUTHN_FLOOR[risk]
        if authn.level < min_level:
            step_up.append("ASSURANCE_INSUFFICIENT")
        if req.at - authn.auth_time > max_age:
            step_up.append("AUTHENTICATION_TOO_OLD")

    # step 5: relationship revision freshness
    if relation is not None:
        if relation.state is FactState.UNAVAILABLE and high:
            unavailable.append("STATE_UNAVAILABLE")
        elif not relation.fresh and high:
            deny.append("AUTHORITY_STALE")

    # step 6: authority chain; required for represented actions and for every R2/R3 action
    acting_for_other = req.represented_principal_id not in (None, principal.principal_id)
    if acting_for_other or high:
        if authority is None:
            deny.append("AUTHORITY_MISSING")
        elif authority.state is FactState.UNAVAILABLE:
            (unavailable if high else deny).append(
                "STATE_UNAVAILABLE" if high else "AUTHORITY_MISSING"
            )
        elif not authority.valid:
            deny.extend(authority.codes or ("AUTHORITY_MISSING",))

    # step 7: mandate when a machine acts for someone, or the action consumes a mandate
    needs_mandate = (
        principal.principal_type in MACHINE_TYPES and acting_for_other
    ) or req.action.consumes_mandate
    if needs_mandate:
        if mandate is None:
            deny.append("MANDATE_MISSING")
        elif mandate.state is FactState.UNAVAILABLE:
            (unavailable if high else deny).append(
                "STATE_UNAVAILABLE" if high else "MANDATE_MISSING"
            )
        else:
            if not mandate.active:
                deny.append("MANDATE_NOT_ACTIVE")
            if not mandate.in_scope:
                deny.append("MANDATE_SCOPE_EXCEEDED")
            if req.tool_id and not mandate.tool_allowed:
                deny.append("MANDATE_TOOL_NOT_ALLOWED")

    # step 9: typed policy over the verified fact bundle (Cedar sees facts, never computes authority)
    context: dict[str, object] = {
        "tenant_match": True,  # the tenant is server-derived; cross-tenant requests never reach here
        "authority_valid": bool(authority and authority.state is FactState.OK and authority.valid),
        "authority_stale": bool(relation is not None and not relation.fresh)
        or (authority is not None and "AUTHORITY_STALE" in authority.codes),
        "security_state": principal.security_state
        if principal.state is FactState.OK
        else "UNKNOWN",
        "risk_class": int(risk),
        "authn_level": authn.level if authn.state is FactState.OK else 0,
        "purpose": req.purpose or "",
    }
    pol = policy(context)
    if not pol.allowed:
        deny.extend(pol.codes)

    # step 10: composite approvals
    approval_needed = req.action.requires_approval or req.approval_state is not None
    approval_pending = False
    if approval_needed:
        if req.approval_state in ("DENIED",):
            deny.append("APPROVAL_DENIED")
        elif req.approval_state in ("EXPIRED", "STALE"):
            deny.append("APPROVAL_STALE")
        elif req.approval_state != "APPROVED":
            approval_pending = True

    # R2 may use a bounded cached decision only where explicitly approved (DEC-B1 §4)
    if unavailable and failure_posture(risk, bounded_cache_approved=req.bounded_cache_approved) is (
        FailurePosture.DEGRADE_PER_TENANT_POLICY
    ):
        deny.extend(unavailable)  # R0/R1 degrade: still no allow without the facts
        unavailable = []

    if unavailable:
        outcome, codes = Outcome.STATE_UNAVAILABLE, unavailable + deny
    elif deny:
        outcome, codes = Outcome.DENY, deny
    elif step_up:
        outcome, codes = Outcome.STEP_UP, step_up
    elif approval_pending:
        outcome, codes = Outcome.APPROVAL_REQUIRED, ["APPROVAL_REQUIRED"]
    else:
        outcome, codes = (
            Outcome.ALLOW,
            ["AUTHORITY_VALID" if authority else "POLICY_PERMIT", *pol.codes],
        )

    obligations: list[Mapping[str, object]] = [
        {"type": "DECISION_RECEIPT", "evidence_class": evidence.name}
    ]
    if outcome is Outcome.ALLOW:
        if high:
            obligations.append({"type": "COMMIT_RECHECK"})
        if needs_mandate and req.action.consumes_mandate:
            obligations.append(
                {"type": "MANDATE_RESERVE", "mandate_id": mandate.mandate_id if mandate else None}
            )
        if req.tool_id:
            obligations.append({"type": "TOOL_ALLOWLIST", "tool_id": req.tool_id})
    elif outcome is Outcome.APPROVAL_REQUIRED:
        obligations.append({"type": "HUMAN_APPROVAL"})
    elif outcome is Outcome.STEP_UP:
        obligations.append({"type": "STEP_UP", "min_authn_level": AUTHN_FLOOR[risk][0]})

    # §15.1 valid_until = min of every deadline the decision rests on, capped by the class TTL
    deadlines = [
        req.at + revocation_slo(risk),
        req.at + AUTHN_FLOOR[risk][1] - (req.at - authn.auth_time),
    ]
    for d in (
        authority.valid_until if authority else None,
        mandate.valid_until if mandate else None,
    ):
        if d is not None:
            deadlines.append(d)
    valid_until = min(deadlines) if outcome is Outcome.ALLOW else req.at

    return Decision(
        decision_id=req.decision_id,
        outcome=outcome,
        reason_codes=tuple(dict.fromkeys(codes)),
        obligations=tuple(obligations),
        risk_class=risk,
        evidence_class=evidence,
        policy_release_id=pol.release_id,
        relation_revision=relation.relation_revision if relation else None,
        security_revision=principal.security_revision if principal.state is FactState.OK else None,
        authority_chain_commitment=authority.chain_commitment if authority else None,
        mandate_id=mandate.mandate_id if mandate else None,
        valid_until=valid_until,
        inputs={
            "context": context,
            "action": req.action.name,
            "resource": req.resource_id,
            "at": req.at.isoformat(),
        },
    )

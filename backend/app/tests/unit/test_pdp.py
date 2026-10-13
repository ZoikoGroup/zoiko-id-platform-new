"""Decision algorithm (ZID-AUTHZ-001 §12-15; AUTHZ-01/09/10/12/14/17/23, MACH-13; INV-02/05/14)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.decision.domain.pdp import (
    ActionSpec,
    AuthnFact,
    AuthorityFact,
    DecisionRequest,
    FactState,
    MandateFact,
    Outcome,
    PolicyFact,
    PrincipalFact,
    RelationFact,
    decide,
)
from zid_platform.modules.decision.domain.risk import EvidenceClass, RiskClass, revocation_slo

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
APPROVE = ActionSpec("invoice.approve", RiskClass.R2, EvidenceClass.E2)
PAY = ActionSpec("payment.release", RiskClass.R3, EvidenceClass.E3, consumes_mandate=True)
HUMAN = PrincipalFact("prn_h", "HUMAN", True, "NORMAL", 7)
AGENT = PrincipalFact("agt_1", "AI_AGENT", True, "NORMAL", 3)
AUTHN = AuthnFact(3, NOW - timedelta(minutes=5))
AUTHORITY = AuthorityFact(True, (), "sha256:chain", NOW + timedelta(days=1))
RELATION = RelationFact(882191, True)
MANDATE = MandateFact("m_1", True, True, True, NOW + timedelta(hours=1))


def permit(ctx: object) -> PolicyFact:
    return PolicyFact("rel_1", True, ("POLICY_PERMIT",))


def no_permit(ctx: object) -> PolicyFact:
    return PolicyFact("rel_1", False, ("POLICY_NO_PERMIT",))


def req(action: ActionSpec = APPROVE, **kw: object) -> DecisionRequest:
    base: dict[str, object] = {
        "decision_id": "dec_1",
        "tenant_id": "ten_1",
        "action": action,
        "resource_id": "inv_1",
        "represented_principal_id": "org_1",
        "purpose": "accounts_payable",
        "tool_id": None,
        "at": NOW,
    } | kw
    return DecisionRequest(**base)  # type: ignore[arg-type]


def run(r: DecisionRequest | None = None, **facts: object):  # type: ignore[no-untyped-def]
    f: dict[str, object] = {
        "principal": HUMAN,
        "authn": AUTHN,
        "authority": AUTHORITY,
        "relation": RELATION,
        "mandate": None,
        "policy": permit,
    } | facts
    return decide(r or req(), **f)  # type: ignore[arg-type]


def test_allow_carries_revisions_obligations_and_bounded_validity() -> None:
    d = run()
    assert d.outcome is Outcome.ALLOW
    assert (d.relation_revision, d.security_revision, d.policy_release_id) == (882191, 7, "rel_1")
    assert {o["type"] for o in d.obligations} == {
        "DECISION_RECEIPT",
        "COMMIT_RECHECK",
    }  # AUTHZ-14/17
    assert d.valid_until <= NOW + revocation_slo(RiskClass.R2)


def test_default_deny_without_a_permit() -> None:
    """AUTHZ-01."""
    d = run(policy=no_permit)
    assert d.outcome is Outcome.DENY and "POLICY_NO_PERMIT" in d.reason_codes


def test_authentication_is_not_authority() -> None:
    """AUTHZ-10: AAL-Z3 user with no authority is denied the represented action."""
    d = run(authority=None)
    assert d.outcome is Outcome.DENY and "AUTHORITY_MISSING" in d.reason_codes
    d = run(authority=replace(AUTHORITY, valid=False, codes=("AUTHORITY_REVOKED",)))
    assert d.outcome is Outcome.DENY and d.reason_codes[0] == "AUTHORITY_REVOKED"


def test_risk_signals_can_deny_but_never_grant() -> None:
    """AUTHZ-09: a NORMAL security state with no authority still denies; COMPROMISED always denies."""
    assert run(authority=None).outcome is Outcome.DENY
    assert run(principal=replace(HUMAN, security_state="COMPROMISED")).outcome is Outcome.DENY


@pytest.mark.parametrize(
    "facts",
    [
        {"authority": replace(AUTHORITY, state=FactState.UNAVAILABLE)},
        {"relation": replace(RELATION, state=FactState.UNAVAILABLE)},
        {"principal": replace(HUMAN, state=FactState.UNAVAILABLE)},
        {"principal": replace(HUMAN, security_state="UNKNOWN")},
    ],
)
def test_unavailable_state_fails_closed_for_r2_r3(facts: dict[str, object]) -> None:
    """AUTHZ-23 / INV-14: a PIP failure is never read as absence."""
    d = run(**facts)
    assert d.outcome is Outcome.STATE_UNAVAILABLE and "STATE_UNAVAILABLE" in d.reason_codes


@pytest.mark.parametrize(
    ("state", "outcome", "code"),
    [
        ("ELEVATED_RISK", Outcome.STEP_UP, "SECURITY_STEP_UP_REQUIRED"),
        ("STEP_UP_REQUIRED", Outcome.STEP_UP, "SECURITY_STEP_UP_REQUIRED"),
        ("REAUTH_REQUIRED", Outcome.STEP_UP, "SECURITY_STEP_UP_REQUIRED"),
        ("SUSPENDED", Outcome.DENY, "SECURITY_STATE_RESTRICTED"),
        ("RESTRICTED", Outcome.DENY, "SECURITY_STATE_RESTRICTED"),
        ("REPROOF_REQUIRED", Outcome.DENY, "SECURITY_STATE_RESTRICTED"),
    ],
)
def test_known_security_states_tighten_r2_r3(state: str, outcome: Outcome, code: str) -> None:
    """SIGNALS §2: a known restriction is a definite answer, not 'unavailable'."""
    d = run(principal=replace(HUMAN, security_state=state))
    assert d.outcome is outcome and code in d.reason_codes


def test_unavailable_state_never_allows_r0_r1_either() -> None:
    low = ActionSpec("application.access", RiskClass.R1, EvidenceClass.E1)
    d = run(req(low), authority=replace(AUTHORITY, state=FactState.UNAVAILABLE))
    assert d.outcome is Outcome.DENY


def test_stale_relation_projection_denies_r2() -> None:
    """AT-05 / AUTHZ-07 at the decision level."""
    d = run(relation=replace(RELATION, fresh=False))
    assert d.outcome is Outcome.DENY and "AUTHORITY_STALE" in d.reason_codes


def test_agent_needs_a_mandate_and_valid_scope() -> None:
    """MACH-13 / AUTHZ-12."""
    assert run(principal=AGENT).reason_codes == ("MANDATE_MISSING",)
    d = run(principal=AGENT, mandate=replace(MANDATE, in_scope=False))
    assert d.outcome is Outcome.DENY and "MANDATE_SCOPE_EXCEEDED" in d.reason_codes
    d = run(req(tool_id="tool_wire"), principal=AGENT, mandate=replace(MANDATE, tool_allowed=False))
    assert "MANDATE_TOOL_NOT_ALLOWED" in d.reason_codes
    ok = run(req(PAY, tool_id="tool_pay"), principal=AGENT, mandate=MANDATE)
    assert ok.outcome is Outcome.ALLOW
    assert {o["type"] for o in ok.obligations} >= {
        "MANDATE_RESERVE",
        "TOOL_ALLOWLIST",
        "COMMIT_RECHECK",
    }
    assert ok.evidence_class is EvidenceClass.E3


def test_weak_or_old_authentication_steps_up_only_when_otherwise_eligible() -> None:
    d = run(authn=AuthnFact(1, NOW - timedelta(minutes=1)))
    assert d.outcome is Outcome.STEP_UP and "ASSURANCE_INSUFFICIENT" in d.reason_codes
    d = run(authn=AuthnFact(3, NOW - timedelta(hours=2)))
    assert d.outcome is Outcome.STEP_UP and "AUTHENTICATION_TOO_OLD" in d.reason_codes
    d = run(authn=AuthnFact(1, NOW), authority=None)  # missing authority dominates step-up
    assert d.outcome is Outcome.DENY


def test_approvals() -> None:
    gated = ActionSpec("invoice.approve", RiskClass.R2, EvidenceClass.E2, requires_approval=True)
    assert run(req(gated)).outcome is Outcome.APPROVAL_REQUIRED
    assert run(req(gated, approval_state="APPROVED")).outcome is Outcome.ALLOW
    assert run(req(gated, approval_state="STALE")).reason_codes == ("APPROVAL_STALE",)


def test_caller_cannot_lower_the_catalog_floor() -> None:
    d = run(req(requested_risk=RiskClass.R0))
    assert d.risk_class is RiskClass.R2 and d.evidence_class is EvidenceClass.E2


def test_decisions_are_deterministic() -> None:
    """AUTHZ §4.1: same release and same fact revisions -> same decision (E3 replay)."""
    assert run() == run()
    assert run().inputs["context"] == run().inputs["context"]

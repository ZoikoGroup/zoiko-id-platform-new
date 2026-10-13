"""Security state and the deterministic signal rule engine (ZID-SIGNALS-001 §2-4, §10-13, §22).

Pure domain, no I/O. A signal can tighten trust or demand re-authentication; it never grants
anything. Rules are versioned (`RULE_RELEASE`) and map a canonical event type to a required action
and a target state. Precedence (§22):
- a tightening is applied whatever the event's age (a late compromise report is still true);
- a loosening (account re-enabled) applies only from an AUTHORITATIVE source, only if its effective
  time is later than the current state's, and never restores old sessions (SIG-06, SIG-16);
- REVOKED and PURGED are terminal.
STEP_UP_REQUIRED and REAUTH_REQUIRED are satisfied by an authentication *after* the state was
committed: the PDP then sees NORMAL for that session, without anyone editing the state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

RULE_RELEASE = "signals-rules-2026-10-13"


class State(StrEnum):
    TRUSTED = "TRUSTED"
    ELEVATED_RISK = "ELEVATED_RISK"
    STEP_UP_REQUIRED = "STEP_UP_REQUIRED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    REPROOF_REQUIRED = "REPROOF_REQUIRED"
    RESTRICTED = "RESTRICTED"
    SUSPENDED = "SUSPENDED"
    COMPROMISED = "COMPROMISED"
    REVOKED = "REVOKED"
    PURGED = "PURGED"


RANK = {s: i for i, s in enumerate(State)}
TERMINAL = frozenset({State.REVOKED, State.PURGED})
CLEARED_BY_FRESH_AUTH = frozenset({State.STEP_UP_REQUIRED, State.REAUTH_REQUIRED})


class Action(StrEnum):
    NONE = "NONE"
    REEVALUATE = "REEVALUATE"
    STEP_UP = "STEP_UP"
    REAUTH = "REAUTH"
    REPROOF = "REPROOF"
    RESTRICT = "RESTRICT"
    SUSPEND = "SUSPEND"
    REVOKE = "REVOKE"
    PURGE = "PURGE"


class SignalError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Rule:
    action: Action
    state: State | None  # None: no change to the subject's security state
    severity: str
    risk_floor: int
    reason: str
    revoke_sessions: bool = False
    bump_revision: bool = False  # existing sessions must re-authenticate
    suspend_principal: bool = False
    restores: bool = False  # a loosening rule (account re-enabled)
    sever_link: bool = False  # the external identity link is no longer safe
    revoke_credential: bool = False


# Canonical Zoiko signal types; CAEP/RISC URIs map onto these at the normalization boundary.
RULES: dict[str, Rule] = {
    "session.revoked": Rule(
        Action.REVOKE, State.REAUTH_REQUIRED, "MEDIUM", 1, "SESSION_REVOKED", revoke_sessions=True
    ),
    "token.claims_changed": Rule(Action.REEVALUATE, None, "LOW", 1, "TOKEN_CLAIMS_CHANGED"),
    "credential.revoked": Rule(
        Action.REAUTH,
        State.REAUTH_REQUIRED,
        "HIGH",
        2,
        "CREDENTIAL_REVOKED",
        bump_revision=True,
        revoke_credential=True,
    ),
    "credential.changed": Rule(Action.REEVALUATE, None, "LOW", 1, "CREDENTIAL_CHANGED"),
    "assurance.decreased": Rule(
        Action.STEP_UP, State.STEP_UP_REQUIRED, "MEDIUM", 2, "ASSURANCE_DECREASED"
    ),
    "assurance.increased": Rule(Action.NONE, None, "INFO", 0, "ASSURANCE_INCREASED"),
    "device.noncompliant": Rule(
        Action.RESTRICT, State.RESTRICTED, "MEDIUM", 2, "DEVICE_NONCOMPLIANT"
    ),
    "device.compliant": Rule(Action.REEVALUATE, None, "INFO", 0, "DEVICE_COMPLIANT"),
    "session.established": Rule(Action.NONE, None, "INFO", 0, "SESSION_ESTABLISHED"),
    "account.credential_change_required": Rule(
        Action.REAUTH,
        State.REAUTH_REQUIRED,
        "HIGH",
        2,
        "CREDENTIAL_CHANGE_REQUIRED",
        bump_revision=True,
    ),
    "account.purged": Rule(
        Action.PURGE,
        State.REAUTH_REQUIRED,
        "HIGH",
        2,
        "ACCOUNT_PURGED_AT_SOURCE",
        revoke_sessions=True,
        sever_link=True,
    ),
    "account.disabled": Rule(
        Action.SUSPEND,
        State.SUSPENDED,
        "HIGH",
        2,
        "ACCOUNT_DISABLED",
        revoke_sessions=True,
        suspend_principal=True,
    ),
    "account.enabled": Rule(
        Action.REEVALUATE, State.REAUTH_REQUIRED, "INFO", 1, "ACCOUNT_ENABLED", restores=True
    ),
    "identifier.changed": Rule(Action.REEVALUATE, None, "LOW", 1, "IDENTIFIER_CHANGED"),
    "identifier.recycled": Rule(
        Action.REPROOF,
        State.REPROOF_REQUIRED,
        "HIGH",
        3,
        "IDENTIFIER_RECYCLED",
        revoke_sessions=True,
        sever_link=True,
    ),
    "credential.compromised": Rule(
        Action.REVOKE,
        State.COMPROMISED,
        "CRITICAL",
        3,
        "CREDENTIAL_COMPROMISE",
        revoke_sessions=True,
        bump_revision=True,
    ),
    "recovery.activated": Rule(
        Action.REAUTH,
        State.REAUTH_REQUIRED,
        "HIGH",
        3,
        "RECOVERY_ACTIVATED",
        revoke_sessions=True,
        bump_revision=True,
    ),
    "recovery.information_changed": Rule(
        Action.STEP_UP, State.STEP_UP_REQUIRED, "MEDIUM", 2, "RECOVERY_INFO_CHANGED"
    ),
    "sessions.revoked": Rule(
        Action.REVOKE, State.REAUTH_REQUIRED, "MEDIUM", 1, "SESSIONS_REVOKED", revoke_sessions=True
    ),
    "principal.compromised": Rule(
        Action.REVOKE,
        State.COMPROMISED,
        "CRITICAL",
        3,
        "PRINCIPAL_COMPROMISED",
        revoke_sessions=True,
        bump_revision=True,
    ),
    "risk.elevated": Rule(Action.STEP_UP, State.ELEVATED_RISK, "MEDIUM", 2, "RISK_ELEVATED"),
}

# Normalization boundary (§10, §11): external URIs never reach core state directly.
CAEP = "https://schemas.openid.net/secevent/caep/event-type/"
RISC = "https://schemas.openid.net/secevent/risc/event-type/"
EXTERNAL_EVENTS: dict[str, str] = {
    CAEP + "session-revoked": "session.revoked",
    CAEP + "token-claims-change": "token.claims_changed",
    CAEP + "credential-change": "credential.changed",  # refined by change_type below
    CAEP + "assurance-level-change": "assurance.decreased",  # refined by change_direction
    CAEP + "device-compliance-change": "device.noncompliant",  # refined by current_status
    CAEP + "session-established": "session.established",
    RISC + "account-credential-change-required": "account.credential_change_required",
    RISC + "account-purged": "account.purged",
    RISC + "account-disabled": "account.disabled",
    RISC + "account-enabled": "account.enabled",
    RISC + "identifier-changed": "identifier.changed",
    RISC + "identifier-recycled": "identifier.recycled",
    RISC + "credential-compromise": "credential.compromised",
    RISC + "recovery-activated": "recovery.activated",
    RISC + "recovery-information-changed": "recovery.information_changed",
    RISC + "sessions-revoked": "sessions.revoked",
}


def normalize_event(uri: str, body: dict[str, object]) -> str:
    """External event URI + its payload -> canonical type (SIGNALS §3 `event_type`)."""
    canonical = EXTERNAL_EVENTS.get(uri)
    if canonical is None:
        raise SignalError("SIGNAL_EVENT_NOT_ALLOWED", uri)
    if canonical == "credential.changed" and body.get("change_type") in ("revoke", "delete"):
        return "credential.revoked"
    if canonical == "assurance.decreased" and body.get("change_direction") == "increase":
        return "assurance.increased"
    if canonical == "device.noncompliant" and body.get("current_status") == "compliant":
        return "device.compliant"
    return canonical


@dataclass(frozen=True, slots=True)
class CurrentState:
    state: State
    revision: int
    effective_at: datetime
    committed_at: datetime


@dataclass(frozen=True, slots=True)
class Outcome:
    status: str  # APPLIED | OUT_OF_ORDER | INFORMATIONAL
    new_state: State | None
    reason: str


def evaluate(
    rule: Rule, current: CurrentState | None, *, effective_at: datetime, confidence: str
) -> Outcome:
    if rule.state is None:
        return Outcome(
            "INFORMATIONAL" if rule.action is Action.NONE else "APPLIED", None, rule.reason
        )
    have = current.state if current else State.TRUSTED
    if have in TERMINAL:
        return Outcome("OUT_OF_ORDER", None, "SECURITY_STATE_TERMINAL")
    if rule.restores:
        if confidence != "AUTHORITATIVE":
            return Outcome("INFORMATIONAL", None, "SIGNAL_SOURCE_NOT_AUTHORITATIVE")
        if current is None or effective_at <= current.effective_at:
            return Outcome("OUT_OF_ORDER", None, "SIGNAL_OUT_OF_ORDER")  # SIG-06
        return Outcome("APPLIED", rule.state, rule.reason)
    if RANK[rule.state] >= RANK[have]:
        return Outcome("APPLIED", rule.state, rule.reason)
    # Weaker than the current state: the consequences still apply (revoke a session), the state
    # stays where it is - nothing loosens without an explicit authoritative restore.
    return Outcome("APPLIED", have, rule.reason)


def pdp_state(current: CurrentState | None, *, auth_time: datetime | None) -> str:
    """The value the PDP sees: NORMAL, COMPROMISED, or the restricting state's name."""
    if current is None or current.state is State.TRUSTED:
        return "NORMAL"
    if (
        current.state in CLEARED_BY_FRESH_AUTH
        and auth_time is not None
        and auth_time > current.committed_at
    ):
        return "NORMAL"
    return "COMPROMISED" if current.state is State.COMPROMISED else current.state.value

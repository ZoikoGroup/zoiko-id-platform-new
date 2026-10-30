"""Step-up authentication tiers S0-S2 (ZID-ENG-DEC-009, proposed). Pure domain, no I/O, no clock.

Step-up proves current control of an authenticator for one command. It never replaces permission, authority, screening, evidence or an
independent approval, and it is never an unrestricted token: S2 is bound to one actor, tenant, session, action, resource and payload
digest, expires in five minutes and is used once.

Binding is cryptographic, not a UI confirmation: the WebAuthn challenge the passkey signs is derived from the server-held digest of the
exact command (`webauthn_challenge`), so an assertion for one command is useless for any other, and the server separately keeps and
re-checks that digest when the command commits. A catalogue that lets S2 be satisfied by email, SMS, a security question, an API token
or a device cookie does not load.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


class StepUpError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code, self.detail = code, detail


class Tier(StrEnum):
    S0 = "S0"
    S1 = "S1"
    S2 = "S2"

    @property
    def rank(self) -> int:
        return int(self.value[1])


class Method(StrEnum):
    PASSKEY = "PASSKEY"  # WebAuthn with user verification
    ENTERPRISE_MFA = "ENTERPRISE_MFA"  # for example TOTP plus a protected password and session
    FEDERATED_ASSERTION = "FEDERATED_ASSERTION"
    EMAIL_LINK = "EMAIL_LINK"
    SMS = "SMS"
    SECURITY_QUESTION = "SECURITY_QUESTION"
    API_TOKEN = "API_TOKEN"
    DEVICE_COOKIE = "DEVICE_COOKIE"


# Named by the record: none of these can authorize S2.
REQUIRED_S2_FORBIDDEN = frozenset(
    {"email_link", "sms", "security_question", "api_token", "device_cookie"}
)
FAMILIES = frozenset(
    {
        "account-security",
        "organization-governance",
        "credentials",
        "authority-and-agents",
        "keys-and-integrations",
        "data-and-evidence",
        "support-access",
    }
)


@dataclass(frozen=True, slots=True)
class TierPolicy:
    max_age: timedelta | None
    single_use: bool
    phishing_resistant: bool
    action_bound: bool


@dataclass(frozen=True, slots=True)
class StepUpAction:
    action_id: str
    group: str
    name: str
    tier: Tier
    escalates_to: Tier | None
    recovery: bool
    family: str
    gate: str
    control: str
    catalogue_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Catalogue:
    version: str
    tiers: dict[Tier, TierPolicy]
    challenge_ttl: timedelta
    s1_fallback_mfa: bool
    s2_forbidden_methods: frozenset[str]
    escalation_states: frozenset[str]
    actions: dict[str, StepUpAction]


def validate(c: Catalogue) -> None:
    if not c.version or not c.actions:
        raise StepUpError("CATALOGUE_INVALID", "an empty catalogue is not a policy")
    if set(c.tiers) != set(Tier):
        raise StepUpError("CATALOGUE_INVALID", "S0, S1 and S2 are all defined")
    s0, s1, s2 = c.tiers[Tier.S0], c.tiers[Tier.S1], c.tiers[Tier.S2]
    if s0.max_age is not None or s0.single_use or s0.action_bound:
        raise StepUpError("CATALOGUE_INVALID", "S0 asks for no separate step-up")
    if s1.max_age is None or not timedelta(0) < s1.max_age <= timedelta(minutes=10):
        raise StepUpError("CATALOGUE_INVALID", "S1 is fresh within ten minutes")
    if s2.max_age is None or not timedelta(0) < s2.max_age <= timedelta(minutes=5):
        raise StepUpError("CATALOGUE_INVALID", "S2 is fresh within five minutes")
    if not (s2.single_use and s2.action_bound and s2.phishing_resistant):
        raise StepUpError(
            "CATALOGUE_INVALID", "S2 is single-use, action-bound and phishing-resistant"
        )
    if not timedelta(0) < c.challenge_ttl <= timedelta(minutes=5):
        raise StepUpError("CATALOGUE_INVALID", "a challenge waits at most five minutes")
    if not REQUIRED_S2_FORBIDDEN <= c.s2_forbidden_methods:
        raise StepUpError(
            "CATALOGUE_INVALID",
            f"S2 forbids {sorted(REQUIRED_S2_FORBIDDEN - c.s2_forbidden_methods)} and must say so",
        )
    for a in c.actions.values():
        if a.family not in FAMILIES:
            raise StepUpError("CATALOGUE_INVALID", f"{a.action_id}: unknown family {a.family}")
        if a.escalates_to is not None and a.escalates_to.rank <= a.tier.rank:
            raise StepUpError("CATALOGUE_INVALID", f"{a.action_id}: it escalates to a higher tier")
        if a.tier is Tier.S2 and a.escalates_to is not None:
            raise StepUpError("CATALOGUE_INVALID", f"{a.action_id}: nothing is above S2")


def build(
    version: str,
    tiers: dict[Tier, TierPolicy],
    challenge_ttl: timedelta,
    s1_fallback_mfa: bool,
    s2_forbidden_methods: frozenset[str],
    escalation_states: frozenset[str],
    actions: list[StepUpAction],
) -> Catalogue:
    by_id: dict[str, StepUpAction] = {}
    for a in actions:
        if a.action_id in by_id:
            raise StepUpError("CATALOGUE_INVALID", f"duplicate action {a.action_id}")
        by_id[a.action_id] = a
    c = Catalogue(
        version,
        tiers,
        challenge_ttl,
        s1_fallback_mfa,
        s2_forbidden_methods,
        escalation_states,
        by_id,
    )
    validate(c)
    return c


# ------------------------------------------------------------------------------------ classification


@dataclass(frozen=True, slots=True)
class Classification:
    action: StepUpAction
    tier: Tier
    reasons: tuple[str, ...]


def classify(
    c: Catalogue, action_id: str, *, security_state: str = "NORMAL", critical: bool = False
) -> Classification:
    """The tier this command needs now. An action the catalogue does not know is refused, never treated as S0: a sensitive command that
    nobody classified must not slip through as an ordinary one. A risk signal raises the tier by one; `critical` takes an action that
    the record lists as "S1 or S2" to S2."""
    action = c.actions.get(action_id)
    if action is None:
        raise StepUpError("STEP_UP_ACTION_UNKNOWN", "this action has no step-up classification")
    tier, reasons = action.tier, [f"ACTION_{action.tier.value}"]
    if critical and action.escalates_to is not None:
        tier = max(tier, action.escalates_to, key=lambda t: t.rank)
        reasons.append("CRITICAL_ESCALATION")
    if security_state in c.escalation_states and tier is not Tier.S2:
        tier = Tier(f"S{tier.rank + 1}")
        reasons.append("RISK_ESCALATION")
    return Classification(action, tier, tuple(reasons))


# ----------------------------------------------------------------- the command and its digest


_SECRET_KEY = re.compile(r"secret|password|token|key_material|private", re.IGNORECASE)


def _canonical(value: Any, path: str = "$") -> Any:
    """JSON with one reading: sorted keys, no floats, no bytes, no surprises. A payload that cannot be read one way is refused."""
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise StepUpError(
            "STEP_UP_PAYLOAD_INVALID",
            f"{path}: floats have no single reading; use an integer or a string",
        )
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise StepUpError("STEP_UP_PAYLOAD_INVALID", f"{path}: keys are text")
            out[k] = _canonical(v, f"{path}.{k}")
        return out
    if isinstance(value, list | tuple):
        return [_canonical(v, f"{path}[]") for v in value]
    raise StepUpError("STEP_UP_PAYLOAD_INVALID", f"{path}: unsupported value")


def canonical_json(obj: Any) -> bytes:
    return json.dumps(
        _canonical(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def command_hash(
    *, tenant_id: str, action_id: str, resource: str, change: Mapping[str, Any]
) -> str:
    """The digest the server holds for this exact command: tenant, action, target and every material change."""
    body = {"tenant": tenant_id, "action": action_id, "resource": resource, "change": change}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def webauthn_challenge(nonce: bytes, digest_hex: str, challenge_id: str) -> bytes:
    """The 32 bytes the passkey signs. Derived from the command's digest, so the signature itself covers the command."""
    return hashlib.sha256(
        b"zoiko-step-up-v1\x00"
        + nonce
        + b"\x00"
        + bytes.fromhex(digest_hex)
        + b"\x00"
        + challenge_id.encode()
    ).digest()


def describe(action: StepUpAction, resource: str, change: Mapping[str, Any]) -> str:
    """What the person is shown before they touch their passkey, written by the server from the same fields that are hashed."""
    parts = []
    for k in sorted(change):
        v = "(hidden)" if _SECRET_KEY.search(k) else str(change[k])
        parts.append(f"{k} = {v[:80]}")
    shown = "; ".join(parts) if parts else "no other changes"
    return f"{action.name} on {resource[:120]}. {shown}."[:500]


# ------------------------------------------------------------------------------- who and how


def human_only(
    *, principal_type: str, delegated_actor: str | None, workload_bound: bool
) -> str | None:
    """An agent, a service or a delegated token cannot perform a human step-up or stand in as a second human."""
    if principal_type != "HUMAN" or delegated_actor is not None or workload_bound:
        return "STEP_UP_HUMAN_REQUIRED"
    return None


def method_satisfies(
    c: Catalogue, tier: Tier, method: Method, *, user_verified: bool, phishing_resistant: bool
) -> str | None:
    """None when this method can authorize this tier, else why not. S2 accepts only a user-verified phishing-resistant passkey: no
    federated assertion (Zoiko iD runs its own step-up or denies), no email, SMS, question, API token or device cookie."""
    if tier is Tier.S0:
        return None
    if method is Method.PASSKEY:
        if not user_verified:
            return "USER_VERIFICATION_REQUIRED"
        return None if phishing_resistant else "NOT_PHISHING_RESISTANT"
    if tier is Tier.S2:
        return "S2_METHOD_FORBIDDEN"
    if method is Method.ENTERPRISE_MFA and c.s1_fallback_mfa:
        return None
    return "S1_METHOD_NOT_APPROVED"


def fresh_enough(policy: TierPolicy, verified_at: datetime, now: datetime) -> bool:
    """On the server's clock. A proof dated in the future is not fresh; neither is one older than the window."""
    if policy.max_age is None:
        return True
    return timedelta(0) <= now - verified_at <= policy.max_age


# ------------------------------------------------------------------------------------- the proof


@dataclass(frozen=True, slots=True)
class Binding:
    tenant_id: str
    actor_id: str
    session_id: str | None
    action_id: str
    family: str
    resource: str
    command_hash: str


def check_binding(c: Catalogue, tier: Tier, expected: Binding, proven: Binding) -> str | None:
    """None when the proof belongs to this command. S2 must match everything: another endpoint, resource or payload is a different
    command. S1 is reusable inside the same tenant, actor, session and action family only."""
    if expected.tenant_id != proven.tenant_id:
        return "STEP_UP_TENANT_MISMATCH"
    if expected.actor_id != proven.actor_id:
        return "STEP_UP_ACTOR_MISMATCH"
    if expected.session_id != proven.session_id:
        return "STEP_UP_SESSION_MISMATCH"
    if tier is Tier.S1:
        return None if expected.family == proven.family else "STEP_UP_ACTION_MISMATCH"
    if expected.action_id != proven.action_id:
        return "STEP_UP_ACTION_MISMATCH"
    if expected.resource != proven.resource or expected.command_hash != proven.command_hash:
        return "STEP_UP_PAYLOAD_MISMATCH"
    return None

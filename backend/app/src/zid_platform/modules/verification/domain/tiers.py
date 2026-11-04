"""Progressive verification (ZID-EDR-006). Pure domain: which risk tier a person is at, what the next tier needs, and which optional checks are ever required.

Nothing here decides who a person is. It reads facts the verification, organization and authentication modules established and says what tier they
support. A signal that is never identity proof (a phone code, an email confirmation, single sign-on, a selfie alone, domain control) cannot be passed in as
a fact at all, so no code path can raise a tier with one. An alternative way of being verified never lowers what a tier means: until it is completed the
person stays where they are."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TierError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Tier(StrEnum):
    NONE = "NONE"  # no verified email yet
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"

    @property
    def rank(self) -> int:
        return ("NONE", "R0", "R1", "R2", "R3").index(self.value)


class Need(StrEnum):
    """The only reasons an optional check can be required."""

    STATUTORY = "STATUTORY"
    REGULATED = "REGULATED"
    CONTRACT = "CONTRACT"
    DOCUMENTED_RISK = "DOCUMENTED_RISK"
    WORKFLOW_POSSESSION = "WORKFLOW_POSSESSION"


@dataclass(frozen=True, slots=True)
class Facts:
    """What the other modules established. Every field is a fact about the person, never a signal."""

    email_proved: bool = False  # control of the inbox, proved at sign-up
    proofed: bool = False  # a valid, unexpired identity assertion from an approved proofing policy (remote route, alternative or accepted equivalent)
    company_verified: bool = False  # a separate organization check
    mandate_evidence: bool = False  # evidence of the person's authority for that organization
    strong_authentication: bool = (
        False  # a phishing-resistant authenticator is registered and was used
    )
    fresh_strong_sign_in: bool = False
    independent_approval: bool = False


@dataclass(frozen=True, slots=True)
class TierPolicy:
    version: str
    tiers: dict[Tier, dict[str, Any]]
    optional: dict[str, dict[str, Any]]
    never_identity_proof: frozenset[str]
    never_authorizes_critical_change: frozenset[str]
    alternatives: tuple[dict[str, Any], ...]
    states: tuple[str, ...]
    claim_fields: tuple[str, ...]

    def needs(self, tier: Tier) -> tuple[str, ...]:
        return tuple(self.tiers[tier]["needs"])


def parse(doc: dict[str, Any]) -> TierPolicy:
    """A policy that asks for more by default, or that lets a non-proof signal count, does not load."""
    tiers: dict[Tier, dict[str, Any]] = {}
    for key, body in (doc.get("tiers") or {}).items():
        try:
            tiers[Tier(str(key))] = {
                "label": str(body["label"]),
                "meaning": str(body.get("meaning", "")),
                "needs": tuple(str(n) for n in body["needs"]),
            }
        except (ValueError, KeyError) as e:
            raise TierError("POLICY_INVALID", f"tier {key}") from e
    if set(tiers) != {Tier.R0, Tier.R1, Tier.R2, Tier.R3}:
        raise TierError("POLICY_INVALID", "the policy names R0 to R3")
    r0 = tiers[Tier.R0]["needs"]
    if r0 != ("EMAIL",):
        raise TierError(
            "POLICY_INVALID",
            "R0 needs the email address and nothing else: no ID, selfie, address or phone at registration",
        )
    for lower, higher in ((Tier.R0, Tier.R1), (Tier.R1, Tier.R2), (Tier.R2, Tier.R3)):
        if not set(tiers[lower]["needs"]) <= set(tiers[higher]["needs"]):
            raise TierError(
                "POLICY_INVALID", f"{higher.value} must need at least what {lower.value} needs"
            )
    optional = {str(k): dict(v) for k, v in (doc.get("optional_checks") or {}).items()}
    for name, body in optional.items():
        reasons = body.get("required_for") or []
        if not reasons:
            raise TierError(
                "POLICY_INVALID",
                f"{name} is required for nothing, so it is never required; remove it",
            )
        for r in reasons:
            try:
                Need(str(r))
            except ValueError as e:
                raise TierError(
                    "POLICY_INVALID", f"{name}: {r} is not a reason a check may be required"
                ) from e
    if {"PHONE_OTP", "EMAIL_CONFIRMATION", "SSO", "SELFIE_ALONE", "DOMAIN_CONTROL"} - set(
        doc.get("never_identity_proof") or []
    ):
        raise TierError(
            "POLICY_INVALID",
            "a phone code, an email confirmation, single sign-on, a selfie alone and domain control are never identity proof",
        )
    alts = tuple(dict(a) for a in doc.get("alternatives") or [])
    for a in alts:
        if "downgrade" in str(a.get("effect", "")).lower() or not a.get("effect"):
            raise TierError(
                "POLICY_INVALID",
                f"alternative {a.get('id')} says what it gives, and never a lower tier",
            )
    return TierPolicy(
        str(doc.get("version", "")), tiers, optional, frozenset(doc.get("never_identity_proof") or []),
        frozenset(doc.get("never_authorizes_critical_change") or []), alts, tuple(doc.get("states") or []), tuple(doc.get("claim_fields") or []),
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class TierResult:
    tier: Tier
    label: str
    meaning: str
    next_tier: Tier | None
    missing: tuple[str, ...]  # what the next tier still needs
    reasons: tuple[str, ...] = field(default_factory=tuple)


_FACT_FOR_CHECK = {
    "EMAIL": "email_proved",
    "GOVERNMENT_ID": "proofed",  # a validated document and a live selfie come as one proofing result, or an accepted equivalent
    "SELFIE_LIVENESS": "proofed",
    "COMPANY_CHECK": "company_verified",
    "MANDATE_EVIDENCE": "mandate_evidence",
    "STRONG_AUTHENTICATION": "strong_authentication",
    "FRESH_STRONG_SIGN_IN": "fresh_strong_sign_in",
    "INDEPENDENT_APPROVAL": "independent_approval",
}


def tier_of(facts: Facts, policy: TierPolicy) -> TierResult:
    """The highest tier whose every need is met by a fact. A higher tier is never reached by skipping a lower one."""
    achieved = Tier.NONE
    for tier in (Tier.R0, Tier.R1, Tier.R2, Tier.R3):
        if all(getattr(facts, _FACT_FOR_CHECK[n]) for n in policy.needs(tier)):
            achieved = tier
        else:
            break
    nxt = {Tier.NONE: Tier.R0, Tier.R0: Tier.R1, Tier.R1: Tier.R2, Tier.R2: Tier.R3, Tier.R3: None}[
        achieved
    ]
    missing = (
        tuple(
            sorted(
                {n for n in policy.needs(nxt) if not getattr(facts, _FACT_FOR_CHECK[n])},
                key=policy.needs(nxt).index,
            )
        )
        if nxt
        else ()
    )
    body = policy.tiers.get(
        achieved,
        {
            "label": "Email not yet confirmed",
            "meaning": "Confirm your email address to have an account.",
        },
    )
    reasons = ("EMAIL_NOT_PROVED",) if achieved is Tier.NONE else ()
    return TierResult(achieved, str(body["label"]), str(body["meaning"]), nxt, missing, reasons)


# ---------------------------------------------------------------------------------------------------------------- what is asked, and why


@dataclass(frozen=True, slots=True)
class CheckRequirement:
    check: str
    required: bool
    reason: str | None  # why it is required, or None when it is not


def required_checks(
    target: Tier,
    policy: TierPolicy,
    *,
    needs: frozenset[Need] = frozenset(),
    alternative: str | None = None,
) -> tuple[CheckRequirement, ...]:
    """The checks to ask for to reach `target`, and, for each optional check, whether a stated reason makes it required.

    Address is required only for a statutory, regulated, contract or documented-risk need; a phone only when a workflow depends on possession. Asking
    for either without such a reason is refused, never quietly accepted. With an approved alternative, the document and selfie checks are replaced by
    that named process (never dropped)."""
    if target is Tier.NONE:
        raise TierError("TARGET_INVALID", "NONE is where a person starts, not a tier to reach")
    if alternative is not None and alternative not in {a["id"] for a in policy.alternatives}:
        raise TierError("ALTERNATIVE_UNKNOWN", alternative)
    out: list[CheckRequirement] = []
    for n in policy.needs(target):
        if alternative and n in ("GOVERNMENT_ID", "SELFIE_LIVENESS"):
            if not any(r.check == alternative for r in out):
                out.append(CheckRequirement(alternative, True, "ALTERNATIVE_TO_REMOTE_CHECK"))
            continue
        out.append(CheckRequirement(n, True, f"NEEDED_FOR_{target.value}"))
    for name, body in policy.optional.items():
        wanted = [Need(r) for r in body["required_for"] if Need(r) in needs]
        out.append(CheckRequirement(name, bool(wanted), wanted[0].value if wanted else None))
    unknown = needs - {Need(r) for b in policy.optional.values() for r in b["required_for"]}
    if unknown:
        raise TierError("NEED_NOT_RECOGNISED", ", ".join(sorted(n.value for n in unknown)))
    return tuple(out)


def check_optional_request(check: str, need: Need | None, policy: TierPolicy) -> None:
    """Somebody asks to require an optional check. Refused without one of its stated reasons (EDR06-08, EDR06-09)."""
    body = policy.optional.get(check)
    if body is None:
        raise TierError("CHECK_UNKNOWN", check)
    if need is None or need.value not in body["required_for"]:
        raise TierError(
            f"{check}_NEEDS_REASON",
            f"{check.lower()} is required only for {', '.join(body['required_for'])}",
        )


# -------------------------------------------------------------------------------------------------------------- no false assurance


PROOF_SOURCES = frozenset(
    {"PROOFING_RESULT", "ASSISTED_PROOFING_RESULT", "ACCEPTED_EQUIVALENT_CREDENTIAL"}
)


def counts_as_identity_proof(signal: str, policy: TierPolicy) -> bool:
    """Only a proofing result, an assisted-proofing result or an accepted equivalent credential is identity proof. A phone code, an email
    confirmation, single sign-on, a selfie alone and domain control never are, and neither is anything nobody listed."""
    s = signal.upper()
    return s in PROOF_SOURCES and s not in policy.never_identity_proof


def may_authorize_critical_change(signal: str, policy: TierPolicy) -> bool:
    """A phone code or an SMS can never authorize a critical change on its own; only a strong authenticator can."""
    return signal.upper() not in policy.never_authorizes_critical_change and signal.upper() in {
        "PASSKEY",
        "STRONG_AUTHENTICATOR",
    }


def address_label(*, entered: bool, verified: bool, policy: TierPolicy) -> str:
    """A customer-typed address is never shown as verified."""
    body = policy.optional["ADDRESS"]
    if verified:
        return str(body["label_when_verified"])
    return str(body["label_when_entered"]) if entered else "Not provided"


def alternatives_for(needs: set[str], policy: TierPolicy) -> tuple[dict[str, Any], ...]:
    """The approved alternatives for what the person cannot do. Empty when they can do the remote check: nobody is steered away from it."""
    return tuple(a for a in policy.alternatives if needs & set(a.get("for") or ()))


def claim_view(row: dict[str, Any], policy: TierPolicy, now: Any) -> dict[str, Any]:
    """One claim with everything a later decision needs to explain it: method, source, policy, scope, issue and expiry times, and its revocation."""
    valid_until = row.get("valid_until")
    revoked_at = row.get("revoked_at")
    state = (
        "REVOKED"
        if revoked_at or row.get("status") == "REVOKED"
        else "EXPIRED"
        if valid_until and valid_until <= now
        else str(row.get("status", "UNKNOWN"))
    )
    return {
        "claim": row["claim_type"],
        "method": row["verification_method"],
        "source": row.get("provider_id"),
        "policy": {
            "level": row["assurance_level"],
            "profile": row.get("profile_name"),
            "version": row.get("profile_version"),
        },
        "scope": list(row.get("purposes") or []),
        "issued_at": row["performed_at"].isoformat() if row.get("performed_at") else None,
        "valid_until": valid_until.isoformat() if valid_until else None,
        "revocation": {
            "state": state,
            "revoked_at": revoked_at.isoformat() if revoked_at else None,
            "assertion_id": str(row.get("assertion_id", "")),
        },
    }

"""Verification profiles, sessions, decisions and reliance (ZID-VERIFY-001 §2-7, §10, §13, §16-18).

Pure domain, no I/O. A provider result is an *input*: the decision here is deterministic from the
recorded facts and the profile, claim by claim. Key rules:
- assurance is per claim and per method; a verified name says nothing about the address (§0.2);
- the caller cannot pick a weaker profile than the policy floor, and failover never changes the
  method promised (equivalence group only, §10);
- biometric evidence counts only where the profile and the jurisdiction both allow it (§13);
- fraud never auto-passes: it fails, or goes to manual review when the profile allows (§14);
- reuse is a fresh reliance decision, never a permanent badge (§17).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

IAL_RANK = {"IAL-Z0": 0, "IAL-Z1": 1, "IAL-Z2": 2, "IAL-Z3": 3}
EVIDENCE_TYPES = frozenset(
    {
        "GOVERNMENT_ID",
        "DIGITAL_CREDENTIAL",
        "AUTHORITATIVE_SOURCE",
        "FINANCIAL_SOURCE",
        "ADDRESS_EVIDENCE",
        "BIOMETRIC_BINDING",
        "IN_PERSON_ATTESTATION",
        "TRUSTED_IDENTITY_PROVIDER",
        "ORGANIZATION_SOURCE",
    }
)
SESSION_TTL = timedelta(days=7)
CIRCUIT_THRESHOLD = 3
CIRCUIT_OPEN = timedelta(minutes=5)


class VerificationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class SessionStatus(StrEnum):
    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_EXTERNAL = "PENDING_EXTERNAL"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


TERMINAL = frozenset(
    {SessionStatus.VERIFIED, SessionStatus.FAILED, SessionStatus.CANCELED, SessionStatus.EXPIRED}
)
_NEXT = {
    SessionStatus.CREATED: {
        SessionStatus.IN_PROGRESS,
        SessionStatus.CANCELED,
        SessionStatus.EXPIRED,
    },
    SessionStatus.IN_PROGRESS: {
        SessionStatus.PENDING_EXTERNAL,
        SessionStatus.MANUAL_REVIEW,
        SessionStatus.VERIFIED,
        SessionStatus.FAILED,
        SessionStatus.CANCELED,
        SessionStatus.EXPIRED,
    },
    SessionStatus.PENDING_EXTERNAL: {
        SessionStatus.IN_PROGRESS,
        SessionStatus.MANUAL_REVIEW,
        SessionStatus.VERIFIED,
        SessionStatus.FAILED,
        SessionStatus.CANCELED,
        SessionStatus.EXPIRED,
    },
    SessionStatus.MANUAL_REVIEW: {
        SessionStatus.VERIFIED,
        SessionStatus.FAILED,
        SessionStatus.EXPIRED,
    },
}


def transition(current: SessionStatus, target: SessionStatus) -> SessionStatus:
    if target not in _NEXT.get(current, set()):
        raise VerificationError("VERIFICATION_INVALID_TRANSITION", f"{current} -> {target}")
    return target


@dataclass(frozen=True, slots=True)
class Profile:
    profile_id: str
    tenant_id: str
    name: str
    version: int
    level: str  # IAL-Z1..Z3
    required_claims: tuple[str, ...]
    accepted_evidence: frozenset[str]
    binding_required: bool
    liveness_required: bool
    fraud_check_required: bool
    biometric_jurisdictions: frozenset[str]  # empty: biometrics never allowed
    jurisdictions: frozenset[str]
    freshness: timedelta
    manual_review: bool
    review_approvals: int
    reliance_purposes: frozenset[str]
    status: str

    @staticmethod
    def from_json(
        profile_id: str,
        tenant_id: str,
        name: str,
        version: int,
        level: str,
        req: Mapping[str, Any],
        status: str,
    ) -> Profile:
        return Profile(
            profile_id=profile_id,
            tenant_id=tenant_id,
            name=name,
            version=version,
            level=level,
            required_claims=tuple(req["required_claims"]),
            accepted_evidence=frozenset(req["accepted_evidence"]),
            binding_required=bool(req.get("binding_required", False)),
            liveness_required=bool(req.get("liveness_required", False)),
            fraud_check_required=bool(req.get("fraud_check_required", False)),
            biometric_jurisdictions=frozenset(req.get("biometric_jurisdictions", ())),
            jurisdictions=frozenset(req["jurisdictions"]),
            freshness=timedelta(days=int(req.get("freshness_days", 365))),
            manual_review=bool(req.get("manual_review", False)),
            review_approvals=int(req.get("review_approvals", 1)),
            reliance_purposes=frozenset(req.get("reliance_purposes", ())),
            status=status,
        )


def validate_requirements(level: str, req: Mapping[str, Any]) -> None:
    """A profile must be coherent before it can ever be activated."""
    if level not in ("IAL-Z1", "IAL-Z2", "IAL-Z3"):
        raise VerificationError("PROFILE_INVALID", "level")
    claims, evidence = req.get("required_claims"), req.get("accepted_evidence")
    if not isinstance(claims, list) or not claims or not all(isinstance(c, str) for c in claims):
        raise VerificationError("PROFILE_INVALID", "required_claims")
    if not isinstance(evidence, list) or not set(evidence) or not set(evidence) <= EVIDENCE_TYPES:
        raise VerificationError("PROFILE_INVALID", "accepted_evidence")
    if not isinstance(req.get("jurisdictions"), list) or not req["jurisdictions"]:
        raise VerificationError("PROFILE_INVALID", "jurisdictions")
    if level != "IAL-Z1" and not req.get("binding_required"):
        raise VerificationError("PROFILE_INVALID", "IAL-Z2+ requires person-to-evidence binding")
    if not 1 <= int(req.get("review_approvals", 1)) <= 3:
        raise VerificationError("PROFILE_INVALID", "review_approvals")
    bio = set(req.get("biometric_jurisdictions", ()))
    if not bio <= set(req["jurisdictions"]):
        raise VerificationError("PROFILE_INVALID", "biometric jurisdictions outside the profile")


def select_profile(profile: Profile, *, floor: str | None, jurisdiction: str) -> None:
    """§0.2: the caller cannot ask for less than the action's floor; the profile must apply here."""
    if profile.status != "ACTIVE":
        raise VerificationError("PROFILE_NOT_AVAILABLE")
    if jurisdiction not in profile.jurisdictions:
        raise VerificationError("PROFILE_NOT_AVAILABLE", "jurisdiction")
    if floor is not None and IAL_RANK[profile.level] < IAL_RANK.get(floor, 4):
        raise VerificationError("ASSURANCE_NOT_MET", "profile below the required floor")


@dataclass(frozen=True, slots=True)
class ClaimResult:
    claim: str
    verified: bool
    value: str  # becomes a commitment; never stored raw
    method: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """The adapter-normalized facts of one provider transaction (§9 status/reason mapping)."""

    status: str  # COMPLETED | FAILED | PENDING
    claims: tuple[ClaimResult, ...]
    binding: str | None  # BIOMETRIC | NON_BIOMETRIC | None
    liveness_passed: bool | None
    fraud_signals: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()
    evidence_commitment: str | None = None
    fraud_check: str | None = None  # PERFORMED | UNAVAILABLE | None (the engine said nothing)


@dataclass(frozen=True, slots=True)
class Decision:
    status: SessionStatus  # VERIFIED | FAILED | MANUAL_REVIEW | PENDING_EXTERNAL
    outcome: str  # §23 code
    verified_claims: tuple[ClaimResult, ...] = ()
    reasons: tuple[str, ...] = field(default_factory=tuple)


def source_conflicts(profile: Profile, result: ProviderResult) -> tuple[str, ...]:
    """Claims that accepted evidence reports with different outcomes or values (CONFLICTED)."""
    seen: dict[str, set[tuple[bool, str]]] = {}
    for c in result.claims:
        if set(c.evidence) & profile.accepted_evidence:
            seen.setdefault(c.claim, set()).add((c.verified, c.value))
    return tuple(sorted(name for name, outcomes in seen.items() if len(outcomes) > 1))


def decide(profile: Profile, result: ProviderResult, *, jurisdiction: str) -> Decision:
    if result.status == "PENDING":
        return Decision(SessionStatus.PENDING_EXTERNAL, "PENDING_EXTERNAL")
    if result.status != "COMPLETED":
        return Decision(SessionStatus.FAILED, "EVIDENCE_INVALID", reasons=result.reason_codes)
    if profile.fraud_check_required and result.fraud_check != "PERFORMED":
        # §14: a profile that needs the fraud engine never passes without its verdict
        return Decision(
            SessionStatus.FAILED,
            "FRAUD_CHECK_UNAVAILABLE",
            reasons=("FRAUD_CHECK_UNAVAILABLE",),
        )
    reasons: list[str] = []
    if result.binding == "BIOMETRIC" and jurisdiction not in profile.biometric_jurisdictions:
        return Decision(
            SessionStatus.FAILED, "BIOMETRIC_NOT_PERMITTED", reasons=("BIOMETRIC_NOT_PERMITTED",)
        )
    if profile.binding_required and result.binding not in ("BIOMETRIC", "NON_BIOMETRIC"):
        reasons.append("BINDING_FAILED")
    if profile.liveness_required and result.binding == "BIOMETRIC" and not result.liveness_passed:
        reasons.append("LIVENESS_FAILED")
    conflicted = source_conflicts(profile, result)
    good = {
        c.claim: c
        for c in result.claims
        if c.verified and set(c.evidence) & profile.accepted_evidence and c.claim not in conflicted
    }
    missing = [c for c in profile.required_claims if c not in good]
    if conflicted:
        # §15: sources that disagree are never resolved in the applicant's favour automatically
        status = SessionStatus.MANUAL_REVIEW if profile.manual_review else SessionStatus.FAILED
        return Decision(
            status,
            "SOURCE_CONFLICT",
            tuple(good.values()),
            ("SOURCE_CONFLICT", *(f"CONFLICT:{c}" for c in conflicted), *reasons),
        )
    if missing:
        reasons.append("INSUFFICIENT_EVIDENCE")
    if result.fraud_signals:
        status = SessionStatus.MANUAL_REVIEW if profile.manual_review else SessionStatus.FAILED
        return Decision(
            status, "FRAUD_SUSPECTED", tuple(good.values()), ("FRAUD_SUSPECTED", *reasons)
        )
    if reasons:
        if profile.manual_review and reasons == ["BINDING_FAILED"]:
            return Decision(
                SessionStatus.MANUAL_REVIEW,
                "MANUAL_REVIEW_REQUIRED",
                tuple(good.values()),
                tuple(reasons),
            )
        return Decision(
            SessionStatus.FAILED, "ASSURANCE_NOT_MET", tuple(good.values()), tuple(reasons)
        )
    return Decision(
        SessionStatus.VERIFIED, "VERIFIED", tuple(good.values()), ("PROFILE_SATISFIED",)
    )


def failover_candidates(
    adapters: Sequence[Mapping[str, Any]], *, group: str, attempted: Sequence[str], now: datetime
) -> list[str]:
    """§10: only ACTIVE adapters of the same equivalence group, circuit closed, not yet tried."""
    out = []
    for a in adapters:
        if a["equivalence_group"] != group or a["adapter_id"] in attempted:
            continue
        if a["status"] != "ACTIVE":
            continue
        if a.get("open_until") is not None and a["open_until"] > now:
            continue
        out.append(str(a["adapter_id"]))
    return sorted(out)


@dataclass(frozen=True, slots=True)
class HeldAssertion:
    assertion_id: str
    claim_type: str
    assurance_level: str
    status: str
    valid_until: datetime | None
    jurisdiction: str | None
    profile_purposes: frozenset[str]


def reliance(
    assertions: Sequence[HeldAssertion],
    *,
    required_level: str,
    claims: Sequence[str],
    purpose: str,
    jurisdiction: str | None,
    now: datetime,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """§17: (outcome, reasons, assertion ids relied upon)."""
    usable: dict[str, HeldAssertion] = {}
    stale = False
    for a in assertions:
        if a.claim_type not in claims or a.status != "VALID":
            continue
        if a.valid_until is not None and a.valid_until <= now:
            stale = True
            continue
        if IAL_RANK.get(a.assurance_level, 0) < IAL_RANK.get(required_level, 4):
            continue
        if jurisdiction is not None and a.jurisdiction not in (None, jurisdiction):
            continue
        if purpose not in a.profile_purposes:
            continue
        usable.setdefault(a.claim_type, a)
    if not assertions:
        return "REVERIFY_REQUIRED", ("NO_PRIOR_VERIFICATION",), ()
    missing = [c for c in claims if c not in usable]
    if not missing:
        return (
            "REUSE_ALLOWED",
            ("RELIANCE_POLICY_SATISFIED",),
            tuple(a.assertion_id for a in usable.values()),
        )
    if usable:
        return "SUPPLEMENT_REQUIRED", tuple(f"MISSING:{c}" for c in missing), ()
    return "REVERIFY_REQUIRED", ("SOURCE_STALE" if stale else "RELIANCE_NOT_ALLOWED",), ()


def decision_basis(
    result: ProviderResult, *, adapter_id: str, mapping_version: str | None, salt: str
) -> dict[str, Any]:
    """The normalized facts a decision was derived from (§16): no raw value, only a commitment
    (salted per session, so equal values give equal commitments and conflicts stay detectable)."""
    return {
        "adapter_id": adapter_id,
        "mapping_version": mapping_version,
        "status": result.status,
        "binding": result.binding,
        "liveness_passed": result.liveness_passed,
        "fraud_signals": list(result.fraud_signals),
        "fraud_check": result.fraud_check,
        "reason_codes": list(result.reason_codes),
        "evidence_commitment": result.evidence_commitment,
        "claims": [
            {
                "claim": c.claim,
                "verified": c.verified,
                "method": c.method,
                "evidence": list(c.evidence),
                "value_commitment": hashlib.sha256(
                    f"{salt}\x1f{c.claim}\x1f{c.value}".encode()
                ).hexdigest(),
            }
            for c in result.claims
        ],
    }


def reconstruct(profile: Profile, basis: Mapping[str, Any], *, jurisdiction: str) -> Decision:
    """Re-derive a recorded decision from the pinned profile and the recorded normalized facts."""
    result = ProviderResult(
        status=str(basis["status"]),
        claims=tuple(
            ClaimResult(
                c["claim"],
                bool(c["verified"]),
                c["value_commitment"],
                c["method"],
                tuple(c["evidence"]),
            )
            for c in basis["claims"]
        ),
        binding=basis["binding"],
        liveness_passed=basis["liveness_passed"],
        fraud_signals=tuple(basis["fraud_signals"]),
        reason_codes=tuple(basis["reason_codes"]),
        evidence_commitment=basis["evidence_commitment"],
        fraud_check=basis.get("fraud_check"),
    )
    return decide(profile, result, jurisdiction=jurisdiction)

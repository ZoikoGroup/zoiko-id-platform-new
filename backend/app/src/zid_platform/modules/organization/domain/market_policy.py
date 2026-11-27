"""Market policy packs for company verification: what makes one enable-able and publishable (ZID-DTV-WF-02 K09, section 7; acceptance T12, T23, T39). Pure domain, no I/O.

A pack says, for one jurisdiction, legal form, purpose and risk tier, which sources may be used and on what legal basis, which documents prove which claims and for how long,
how ownership and control are judged, how many independent people review, and how long each kind of record is kept. The default is NOT ENABLED. A pack that is unsigned, has no
retention schedule, names a source with no lawful basis or no credential, or relies on one reviewer for a document-based approval is not activated: the problems are listed
by code so the people who own each one can see what is missing.

Publishing is a separate act from drafting: the author cannot approve their own pack, the named sign-off roles must each approve, the pack's own test cases must have passed,
and a change to something high-impact (an ownership threshold, a document age, a retention period, the reviewer count) needs two different approvers. Past decisions keep the
digest of the pack that made them; a rollback adds a new effective interval and rewrites nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from zid_platform.modules.organization.domain.company_documents import DocumentRule

SIGN_OFF_ROLES = frozenset({"COMPLIANCE", "LEGAL", "PRIVACY", "SECURITY"})
RECORD_CLASSES = ("RAW_DOCUMENTS", "EXTRACTED_DATA", "DECISION_METADATA", "SCREENING_EVIDENCE")
# Provisional ceilings for low-risk, non-regulated uses (D-06). A longer period needs a ratification reference.
RECHECK_CEILING_DAYS = {"K1": 180, "K2": 365}
HIGH_IMPACT_FIELDS = (
    "ubo",
    "documents",
    "retention",
    "reviewers_for_document_first",
    "recheck_days",
)


@dataclass(frozen=True, slots=True)
class Source:
    """An evidence source the pack may use. `legal_basis` is the reference to the lawful-use record, `credential_ref` the secret held in the key store (never the secret)."""

    adapter: str
    tier: int  # 1 official live register ... 4 licensed commercial
    legal_basis: str | None
    credential_ref: str | None
    credential_required: bool = True
    terms_version: str | None = None


@dataclass(frozen=True, slots=True)
class UboPolicy:
    """No universal percentage: the threshold is signed for the jurisdiction and purpose, and control by other means is judged separately."""

    threshold_percent: Decimal | None
    other_control_tests: bool
    signed_by: str | None  # the local Compliance owner who signed this threshold


@dataclass(frozen=True, slots=True)
class Retention:
    days: dict[str, int | None] = field(
        default_factory=dict
    )  # per record class; None means no schedule yet
    legal_basis: str | None = None
    legal_hold_supported: bool = False
    deletion_job_ref: str | None = None


@dataclass(frozen=True, slots=True)
class MarketPolicy:
    jurisdiction: str
    region: str | None
    legal_form: str
    purpose: str
    risk_tier: str
    version: str
    sources: tuple[Source, ...] = ()
    documents: tuple[DocumentRule, ...] = ()
    ubo: UboPolicy | None = None
    reviewers_for_document_first: int = 0
    recheck_days: dict[str, int] = field(default_factory=dict)
    recheck_ratified: bool = (
        False  # a reference to a ratification exists for periods longer than the ceilings
    )
    retention: Retention = field(default_factory=Retention)

    @property
    def key(self) -> str:
        return "|".join(
            (
                self.jurisdiction,
                self.region or "-",
                self.legal_form,
                self.purpose,
                self.risk_tier,
                self.version,
            )
        )


def _canonical(p: MarketPolicy) -> bytes:
    def default(o):  # type: ignore[no-untyped-def]
        if isinstance(o, Decimal):
            return format(o, "f")
        if hasattr(o, "__dataclass_fields__"):
            return {k: getattr(o, k) for k in o.__dataclass_fields__}
        if hasattr(o, "value"):
            return o.value
        if hasattr(o, "total_seconds"):
            return o.total_seconds()
        raise TypeError(type(o))

    return json.dumps(default(p), default=default, sort_keys=True, separators=(",", ":")).encode()  # type: ignore[no-untyped-call]


def policy_hash(p: MarketPolicy) -> str:
    """The digest a decision records, so it can be replayed against exactly the pack that made it."""
    return "sha256:" + hashlib.sha256(_canonical(p)).hexdigest()


def activation_problems(p: MarketPolicy) -> list[str]:
    """Why this pack cannot be enabled. Empty means the pack is complete, not that it is right: Legal, Privacy and Security decide that."""
    out: list[str] = []
    if not p.sources and not p.documents:
        out.append("NO_EVIDENCE_ROUTE")
    for s in p.sources:
        if not s.legal_basis:
            out.append(f"SOURCE_{s.adapter.upper()}_LEGAL_BASIS_MISSING")
        if s.credential_required and not s.credential_ref:
            out.append(f"SOURCE_{s.adapter.upper()}_CREDENTIAL_MISSING")
        if not s.terms_version:
            out.append(f"SOURCE_{s.adapter.upper()}_TERMS_NOT_RECORDED")
    if p.sources and not p.documents:
        pass  # a registry-only pack is allowed: documents are only the fallback
    if p.documents and p.reviewers_for_document_first < 2:
        out.append("TWO_REVIEWERS_REQUIRED_FOR_DOCUMENT_FIRST")
    if p.ubo is None or p.ubo.threshold_percent is None:
        out.append("UBO_THRESHOLD_MISSING")
    elif not p.ubo.signed_by:
        out.append("UBO_THRESHOLD_NOT_SIGNED")
    if p.ubo is not None and not p.ubo.other_control_tests:
        out.append("CONTROL_BY_OTHER_MEANS_NOT_ASSESSED")
    for cls in RECORD_CLASSES:
        if p.retention.days.get(cls) is None:
            out.append(f"RETENTION_{cls}_MISSING")
    if not p.retention.legal_basis:
        out.append("RETENTION_LEGAL_BASIS_MISSING")
    if not p.retention.legal_hold_supported:
        out.append("LEGAL_HOLD_NOT_SUPPORTED")
    if not p.retention.deletion_job_ref:
        out.append("DELETION_JOB_MISSING")
    for grade, ceiling in RECHECK_CEILING_DAYS.items():
        days = p.recheck_days.get(grade)
        if days is None:
            out.append(f"RECHECK_{grade}_MISSING")
        elif days > ceiling and not p.recheck_ratified:
            out.append(f"RECHECK_{grade}_ABOVE_CEILING_NOT_RATIFIED")
    return list(dict.fromkeys(out))


def high_impact_changes(old: MarketPolicy | None, new: MarketPolicy) -> list[str]:
    """Which high-impact areas differ from the previous version. A first version changes all of them."""
    if old is None:
        return list(HIGH_IMPACT_FIELDS)
    changed = []
    if old.ubo != new.ubo:
        changed.append("ubo")
    if old.documents != new.documents:
        changed.append("documents")
    if old.retention != new.retention:
        changed.append("retention")
    if old.reviewers_for_document_first != new.reviewers_for_document_first:
        changed.append("reviewers_for_document_first")
    if old.recheck_days != new.recheck_days:
        changed.append("recheck_days")
    return changed


@dataclass(frozen=True, slots=True)
class Approval:
    person: str
    role: str
    policy_hash: str  # what they approved: an approval of a different version counts for nothing


def publish_problems(
    draft: MarketPolicy,
    previous: MarketPolicy | None,
    *,
    author: str,
    approvals: Sequence[Approval],
    tests_passed: bool,
) -> list[str]:
    """Why this version cannot be published (T12, T39). Empty means every control is met."""
    out = activation_problems(draft)
    if not tests_passed:
        out.append("PUBLISH_TESTS_NOT_PASSED")
    digest = policy_hash(draft)
    valid = [a for a in approvals if a.policy_hash == digest]
    if any(a.person == author for a in valid):
        out.append("AUTHOR_CANNOT_APPROVE_OWN_POLICY")
    independent = [a for a in valid if a.person != author]
    roles = {a.role for a in independent}
    for role in sorted(SIGN_OFF_ROLES - roles):
        out.append(f"SIGN_OFF_{role}_MISSING")
    if high_impact_changes(previous, draft) and len({a.person for a in independent}) < 2:
        out.append("TWO_PERSON_PUBLISH_REQUIRED")
    return list(dict.fromkeys(out))


@dataclass(frozen=True, slots=True)
class Effective:
    policy_hash: str
    version: str
    from_: datetime
    until: datetime | None


def activate(history: Sequence[Effective], draft: MarketPolicy, *, at: datetime) -> list[Effective]:
    """Add a new effective interval and close the open one. Nothing earlier is rewritten, so a decision made last month still replays against last month's pack."""
    closed = [
        Effective(e.policy_hash, e.version, e.from_, at if e.until is None else e.until)
        for e in history
    ]
    return [*closed, Effective(policy_hash(draft), draft.version, at, None)]


def effective_at(history: Iterable[Effective], moment: datetime) -> Effective | None:
    for e in history:
        if e.from_ <= moment and (e.until is None or moment < e.until):
            return e
    return None


def rollback(history: Sequence[Effective], to_hash: str, *, at: datetime) -> list[Effective]:
    """Re-activate an earlier version by adding a new interval for it; the interval it had before is untouched."""
    earlier = next((e for e in history if e.policy_hash == to_hash), None)
    if earlier is None:
        raise ValueError("POLICY_VERSION_UNKNOWN")
    closed = [
        Effective(e.policy_hash, e.version, e.from_, at if e.until is None else e.until)
        for e in history
    ]
    return [*closed, Effective(earlier.policy_hash, earlier.version, at, None)]


# ------------------------------------------------------------------------------------------------------------- storable form


def policy_to_dict(p: MarketPolicy) -> dict[str, object]:
    """The pack as plain JSON, for storage. The digest is taken over this same content."""
    return {
        "jurisdiction": p.jurisdiction,
        "region": p.region,
        "legal_form": p.legal_form,
        "purpose": p.purpose,
        "risk_tier": p.risk_tier,
        "version": p.version,
        "sources": [
            {
                "adapter": s.adapter,
                "tier": s.tier,
                "legal_basis": s.legal_basis,
                "credential_ref": s.credential_ref,
                "credential_required": s.credential_required,
                "terms_version": s.terms_version,
            }
            for s in p.sources
        ],
        "documents": [
            {
                "doc_class": d.doc_class,
                "claim": d.claim.value,
                "max_age_s": None if d.max_age is None else int(d.max_age.total_seconds()),
            }
            for d in p.documents
        ],
        "ubo": None
        if p.ubo is None
        else {
            "threshold_percent": None
            if p.ubo.threshold_percent is None
            else format(p.ubo.threshold_percent, "f"),
            "other_control_tests": p.ubo.other_control_tests,
            "signed_by": p.ubo.signed_by,
        },
        "reviewers_for_document_first": p.reviewers_for_document_first,
        "recheck_days": dict(p.recheck_days),
        "recheck_ratified": p.recheck_ratified,
        "retention": {
            "days": dict(p.retention.days),
            "legal_basis": p.retention.legal_basis,
            "legal_hold_supported": p.retention.legal_hold_supported,
            "deletion_job_ref": p.retention.deletion_job_ref,
        },
    }


def policy_from_dict(d: dict[str, object]) -> MarketPolicy:
    """The inverse. A malformed record raises ValueError: a stored pack that cannot be read is never used."""
    from datetime import timedelta

    from zid_platform.modules.organization.domain.company_documents import Claim

    try:
        ubo = d.get("ubo")
        ret = d["retention"]
        return MarketPolicy(
            jurisdiction=str(d["jurisdiction"]),
            region=None if d.get("region") is None else str(d["region"]),
            legal_form=str(d["legal_form"]),
            purpose=str(d["purpose"]),
            risk_tier=str(d["risk_tier"]),
            version=str(d["version"]),
            sources=tuple(
                Source(
                    str(s["adapter"]),
                    int(s["tier"]),
                    s.get("legal_basis"),
                    s.get("credential_ref"),
                    bool(s.get("credential_required", True)),
                    s.get("terms_version"),
                )
                for s in d.get("sources", [])  # type: ignore[attr-defined]
            ),
            documents=tuple(
                DocumentRule(
                    str(x["doc_class"]),
                    Claim(str(x["claim"])),
                    None if x.get("max_age_s") is None else timedelta(seconds=int(x["max_age_s"])),
                )
                for x in d.get("documents", [])  # type: ignore[attr-defined]
            ),
            ubo=None
            if not ubo
            else UboPolicy(
                None
                if ubo.get("threshold_percent") is None  # type: ignore[attr-defined]
                else Decimal(str(ubo["threshold_percent"])),  # type: ignore[index]
                bool(ubo.get("other_control_tests")),  # type: ignore[attr-defined]
                ubo.get("signed_by"),  # type: ignore[attr-defined]
            ),
            reviewers_for_document_first=int(d.get("reviewers_for_document_first", 0)),  # type: ignore[call-overload]
            recheck_days={str(k): int(v) for k, v in dict(d.get("recheck_days", {})).items()},  # type: ignore[call-overload]
            recheck_ratified=bool(d.get("recheck_ratified", False)),
            retention=Retention(
                {str(k): (None if v is None else int(v)) for k, v in dict(ret["days"]).items()},  # type: ignore[index]
                ret.get("legal_basis"),  # type: ignore[attr-defined]
                bool(ret.get("legal_hold_supported")),  # type: ignore[attr-defined]
                ret.get("deletion_job_ref"),  # type: ignore[attr-defined]
            ),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ValueError("KYB_POLICY_UNREADABLE") from e

"""DecisionReceipt and AuditRecord rules (EVIDENCE-001 §1, §1.1, §3, §3.1, §4, §7; DEC-F1).

Pure: no I/O. The application layer pseudonymizes, encrypts and stores; this module decides what a
valid, minimized receipt is.

- Evidence floor (§1.1): a producer can raise the class, never lower it below the action's floor.
- Material inputs (§4): E2/E3 receipts must carry the commitments the class requires.
- Minimization (§3.1, EVID-12): no names, emails, documents, biometrics, prompts, tokens, credentials
  or raw payloads. Keys that name such data, and values that look like emails or JWTs, are refused.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from zid_platform.modules.evidence.domain.ledger import EvidenceClass, LedgerError

DECISION_TYPES = frozenset(
    {"AUTHORIZATION", "VERIFICATION", "AUTHORITY", "MANDATE", "CREDENTIAL", "RECOVERY", "ADMIN", "OTHER"}
)
ACTOR_TYPES = frozenset({"HUMAN", "SERVICE", "AGENT", "OPERATOR", "SYSTEM"})
AUDIT_OUTCOMES = frozenset({"SUCCESS", "FAILURE", "DENIED", "PARTIAL"})
ANNOTATION_KINDS = frozenset({"CORRECTION", "SUPERSESSION", "ANNOTATION", "INVALIDATION"})
CUSTODY_ACTIONS = frozenset(
    {"COLLECTED", "ACCESSED", "EXPORTED", "TRANSFERRED", "SEALED", "DESTROYED"}
)
EXPORT_MODES = frozenset({"CUSTOMER_VERIFY", "AUDITOR", "REGULATOR_LEGAL", "INCIDENT", "INTERNAL_REPLAY"})
# Handle domains: the same principal gets unrelated handles per domain (DEC-F1 §2).
ACTOR_DOMAIN = "evidence:actor"
SUBJECT_DOMAIN = "evidence:subject"
RECEIPT_SCHEMA_VERSION = "decision-receipt.v1"
AUDIT_SCHEMA_VERSION = "audit-record.v1"

# EVIDENCE-001 §22 posture, as retention class names; durations are the DPO's (DEC-F1 §4).
RETENTION_CLASS = {
    EvidenceClass.E1: "evidence.e1.audit",
    EvidenceClass.E2: "evidence.e2.receipt",
    EvidenceClass.E3: "evidence.e3.defensible",
}

_PROHIBITED_KEY = re.compile(
    r"(?i)(^|_)(e?mail|name|given_name|family_name|full_name|phone|address|document|passport|"
    r"biometric|face|selfie|fingerprint|prompt|password|secret|token|credential_value|raw|payload|"
    r"ssn|national_id|dob|birth)(_|$)"
)
_ALLOWED_KEYS = frozenset({"action", "purpose_id", "outcome"})  # never treated as prohibited
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[A-Za-z]{2,}")
_JWT = re.compile(r"^eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.")
_MAX_STRING = 512


def require_class(requested: EvidenceClass | str, floor: EvidenceClass | str) -> EvidenceClass:
    """EVID-02: the class actually recorded is never below the action's floor."""
    try:
        req, low = EvidenceClass(requested), EvidenceClass(floor)
    except ValueError as e:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "unknown evidence class") from e
    if int(req.value[1]) < int(low.value[1]):
        raise LedgerError(
            "EVIDENCE_CLASS_INSUFFICIENT", f"{req.value} is below the action floor {low.value}"
        )
    return req


def check_minimized(value: Any, path: str = "") -> None:
    """EVID-12 / PRIV-29: refuse prohibited fields and identifying values anywhere in a record."""
    if isinstance(value, Mapping):
        for k, v in value.items():
            key = str(k)
            if key not in _ALLOWED_KEYS and _PROHIBITED_KEY.search(key):
                raise LedgerError("EVIDENCE_SCHEMA_INVALID", f"prohibited field {path}{key}")
            check_minimized(v, f"{path}{key}.")
    elif isinstance(value, list | tuple):
        for i, v in enumerate(value):
            check_minimized(v, f"{path}{i}.")
    elif isinstance(value, str):
        if len(value) > _MAX_STRING:
            raise LedgerError("EVIDENCE_SCHEMA_INVALID", f"{path.rstrip('.')} is not minimized")
        if _EMAIL.search(value) or _JWT.match(value):
            raise LedgerError("EVIDENCE_SCHEMA_INVALID", f"{path.rstrip('.')} holds an identifier")
    elif isinstance(value, float):
        raise LedgerError("EVIDENCE_CANONICALIZATION_FAILED", "floats are not evidence values")


@dataclass(frozen=True, slots=True)
class ReceiptFacts:
    """What a producer knows about a decision; identifiers are pseudonymized before storage."""

    decision_id: str
    decision_type: str
    evidence_class: EvidenceClass
    action: str
    resource_class: str
    outcome: str
    reason_codes: tuple[str, ...]
    risk_class: str
    effective_at: datetime
    actor_principal_id: str
    represented_principal_id: str | None = None
    resource_ref: str | None = None
    purpose_id: str | None = None
    policy_release_id: str | None = None
    policy_engine_version: str | None = None
    relation_revision: int | None = None
    security_revision: int | None = None
    authority_chain_commitment: str | None = None
    mandate_commitment: str | None = None
    approval_commitment: str | None = None
    verification_commitment: str | None = None
    credential_commitment: str | None = None
    transaction_context_hash: str | None = None
    input_commitment: str | None = None
    obligations: tuple[Mapping[str, Any], ...] = ()
    assurance_summary: Mapping[str, Any] = field(default_factory=dict)
    valid_until: datetime | None = None
    producer_service: str = "zid-platform"
    previous_receipt_id: str | None = None


def check_material(facts: ReceiptFacts) -> None:
    """EVID-01 / §4: the commitments each class requires; a producer cannot omit them."""
    if facts.decision_type not in DECISION_TYPES:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "unknown decision_type")
    if facts.evidence_class is EvidenceClass.E0:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "E0 is aggregated, never a receipt")
    if facts.risk_class not in ("R0", "R1", "R2", "R3"):
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "risk_class must be R0-R3")
    if not facts.reason_codes:
        raise LedgerError("EVIDENCE_MATERIAL_INPUT_MISSING", "reason_codes")
    missing: list[str] = []
    if facts.evidence_class in (EvidenceClass.E2, EvidenceClass.E3):
        if facts.input_commitment is None:
            missing.append("input_commitment")
        if facts.decision_type == "AUTHORIZATION" and facts.relation_revision is None:
            if "STATE_UNAVAILABLE" not in facts.reason_codes:
                missing.append("relation_revision")
    if facts.evidence_class is EvidenceClass.E3:
        if facts.decision_type == "AUTHORIZATION" and facts.outcome == "ALLOW":
            if not facts.policy_release_id:
                missing.append("policy_release_id")
            if facts.security_revision is None:
                missing.append("security_revision")
    if missing:
        raise LedgerError("EVIDENCE_MATERIAL_INPUT_MISSING", ", ".join(missing))


def receipt_body(
    facts: ReceiptFacts,
    *,
    receipt_id: str,
    tenant_id: str,
    actor_handle: str,
    represented_handle: str | None,
    resource_commitment: str | None,
    material_object_id: str | None,
) -> dict[str, Any]:
    """The canonical receipt (§3): handles and commitments only. This exact object is hashed."""
    body: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "decision_id": facts.decision_id,
        "tenant_id": tenant_id,
        "evidence_class": facts.evidence_class.value,
        "decision_type": facts.decision_type,
        "outcome": facts.outcome,
        "reason_codes": list(facts.reason_codes),
        "actor_handle": actor_handle,
        "represented_handle": represented_handle,
        "action_id": facts.action,
        "resource_class": facts.resource_class,
        "resource_commitment": resource_commitment,
        "purpose_id": facts.purpose_id,
        "transaction_context_hash": facts.transaction_context_hash,
        "policy_release_id": facts.policy_release_id,
        "policy_engine_version": facts.policy_engine_version,
        "relation_revision": facts.relation_revision,
        "security_revision": facts.security_revision,
        "authority_chain_commitment": facts.authority_chain_commitment,
        "mandate_commitment": facts.mandate_commitment,
        "verification_commitment": facts.verification_commitment,
        "credential_commitment": facts.credential_commitment,
        "approval_commitment": facts.approval_commitment,
        "input_commitment": facts.input_commitment,
        "material_object_id": material_object_id,
        "obligations": [dict(o) for o in facts.obligations],
        "risk_class": facts.risk_class,
        "assurance_summary": dict(facts.assurance_summary),
        "effective_at": facts.effective_at.isoformat(),
        "valid_until": facts.valid_until.isoformat() if facts.valid_until else None,
        "producer_service": facts.producer_service,
        "previous_receipt_id": facts.previous_receipt_id,
        "retention_class": RETENTION_CLASS[facts.evidence_class],
    }
    check_minimized(body)
    return body


@dataclass(frozen=True, slots=True)
class AuditFacts:
    """A protected administrative/security event (§7, §7.1). `target_ref` is an id, never content."""

    event_type: str
    actor_type: str
    action: str
    target_type: str
    target_ref: str
    outcome: str
    evidence_class: EvidenceClass
    occurred_at: datetime
    actor_principal_id: str | None = None
    reason_code: str | None = None
    source_service: str = "zid-platform"
    before_commitment: str | None = None
    after_commitment: str | None = None
    approval_refs: tuple[str, ...] = ()
    case_commitment: str | None = None
    actor_auth_context: Mapping[str, Any] = field(default_factory=dict)
    security_revision: int | None = None
    source_event_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)  # ids, codes and commitments only


def check_audit(facts: AuditFacts) -> None:
    if facts.actor_type not in ACTOR_TYPES:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "unknown actor_type")
    if facts.outcome not in AUDIT_OUTCOMES:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "unknown audit outcome")
    if facts.evidence_class is EvidenceClass.E0:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "E0 is telemetry, not audit evidence")
    if not re.fullmatch(r"[a-z][a-z0-9_.]{0,127}", facts.event_type):
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "event_type")


def audit_body(
    facts: AuditFacts, *, audit_id: str, tenant_id: str | None, actor_handle: str | None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_id": audit_id,
        "tenant_id": tenant_id,
        "event_type": facts.event_type,
        "actor_type": facts.actor_type,
        "actor_handle": actor_handle,
        "actor_auth_context": dict(facts.actor_auth_context),
        "target_type": facts.target_type,
        "target_ref": facts.target_ref,
        "action": facts.action,
        "outcome": facts.outcome,
        "reason_code": facts.reason_code,
        "source_service": facts.source_service,
        "before_commitment": facts.before_commitment,
        "after_commitment": facts.after_commitment,
        "approval_refs": list(facts.approval_refs),
        "case_commitment": facts.case_commitment,
        "occurred_at": facts.occurred_at.isoformat(),
        "security_revision": facts.security_revision,
        "evidence_class": facts.evidence_class.value,
        "source_event_id": facts.source_event_id,
        "details": dict(facts.details),
    }
    check_minimized(body)
    return body


class ReplayResult:
    """EVIDENCE-001 §19.1 outcomes."""

    VERIFIED_MATCH = "VERIFIED_MATCH"
    VERIFIED_STRUCTURAL_ONLY = "VERIFIED_STRUCTURAL_ONLY"
    MISMATCH = "MISMATCH"
    INCOMPLETE_BY_LAWFUL_ERASURE = "INCOMPLETE_BY_LAWFUL_ERASURE"
    UNVERIFIABLE_INTEGRITY_FAILURE = "UNVERIFIABLE_INTEGRITY_FAILURE"


def compare_outcome(
    recorded: Mapping[str, Any], replayed: Mapping[str, Any], fields: Sequence[str]
) -> list[str]:
    """Names of the material fields where a replay differs from the recorded receipt."""
    return [f for f in fields if recorded.get(f) != replayed.get(f)]


__all__ = [
    "ACTOR_DOMAIN",
    "ANNOTATION_KINDS",
    "CUSTODY_ACTIONS",
    "DECISION_TYPES",
    "EXPORT_MODES",
    "RETENTION_CLASS",
    "SUBJECT_DOMAIN",
    "AuditFacts",
    "ReceiptFacts",
    "ReplayResult",
    "audit_body",
    "check_audit",
    "check_material",
    "check_minimized",
    "compare_outcome",
    "receipt_body",
    "require_class",
]

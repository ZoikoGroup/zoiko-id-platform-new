"""The deterministic rule engine of the Delegated Authority & Access Readiness agent, DAARA (ZID-AG-WF-001 section 09; CR-091).

Rules R-01 to R-06 are pure functions of the metadata they are given, a versioned rule set and a trusted clock reading the caller
passes in. No clock, no I/O, no model: the same version and the same input always give the same findings and the same digest (AT-13).
Anything the engine cannot trust (bad metadata, an unsupported adapter, a clock it cannot read) is UNKNOWN and blocked, never a pass
(R-06, AT-14). A finding is a recommendation for a person; nothing here can change a right.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

RULE_SET_VERSION = "daara-rules/1"
POLICY_VERSION = "daara-policy/1"
PURPOSE = "access_posture_review"
RESOURCE_KINDS = frozenset({"mandates"})
SUPPORTED_ADAPTERS = frozenset({"mandate-metadata/1"})
DEFAULT_WARNING_WINDOW_DAYS = 14
UNRESTRICTED = "*"  # an authority scope that names no limit

# the mandate states the rules understand; anything else is R-06
KNOWN_STATUSES = frozenset(
    {"PENDING_APPROVAL", "ACTIVE", "PAUSED", "EXHAUSTED", "COMPLETED", "EXPIRED", "REVOKED"}
)
LIVE_STATUSES = frozenset({"ACTIVE", "PAUSED"})


class Rule(StrEnum):
    R01 = "R-01"
    R02 = "R-02"
    R03 = "R-03"
    R04 = "R-04"
    R05 = "R-05"
    R06 = "R-06"


class Outcome(StrEnum):
    CLEAN = "CLEAN"
    FINDINGS = "FINDINGS"
    UNKNOWN = "UNKNOWN"


# rule -> (finding code, severity). Severity is how serious the finding is; confidence is always DETERMINISTIC (a rule, not a guess).
FINDINGS: dict[Rule, tuple[str, str]] = {
    Rule.R01: ("EXPIRED_MANDATE", "HIGH"),
    Rule.R02: ("EXPIRING_SOON", "LOW"),
    Rule.R03: ("SPONSOR_NOT_ACTIVE", "HIGH"),
    Rule.R04: ("SCOPE_VARIANCE", "MEDIUM"),
    Rule.R05: ("EVIDENCE_GAP", "MEDIUM"),
    Rule.R06: ("UNKNOWN_STATE", "INFO"),
}
CONFIDENCE = "DETERMINISTIC"


@dataclass(frozen=True, slots=True)
class MandateFacts:
    """The approved metadata projection of one mandate: opaque ids, a state, a date and scope sets. Nothing personal."""

    mandate_id: str
    status: str
    valid_to: datetime | None
    assigned_actions: frozenset[str] | None
    assigned_tools: frozenset[str] | None
    grant_found: bool
    approved_actions: frozenset[str] | None = None
    approved_tools: frozenset[str] | None = None
    evidence_ref: str | None = None
    grant_ref: str | None = None
    sponsor_required: bool = False
    sponsor_ref: str | None = None
    sponsor_active: bool | None = None  # None: the registry could not say
    adapter: str = "mandate-metadata/1"


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    rule_version: str
    finding_code: str
    severity: str
    target_ref: str
    evidence_refs: tuple[str, ...]
    detail: tuple[tuple[str, int | str | bool], ...] = ()
    confidence: str = CONFIDENCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "finding_code": self.finding_code,
            "severity": self.severity,
            "confidence": self.confidence,
            "target_ref": self.target_ref,
            "evidence_refs": list(self.evidence_refs),
            "detail": dict(self.detail),
        }


@dataclass(frozen=True, slots=True)
class RecordResult:
    target_ref: str
    outcome: Outcome
    findings: tuple[Finding, ...] = ()
    unknown_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_ref": self.target_ref,
            "outcome": self.outcome.value,
            "findings": [f.as_dict() for f in self.findings],
            "unknown_reasons": list(self.unknown_reasons),
        }


@dataclass(frozen=True, slots=True)
class RunResult:
    rule_set_version: str
    records: tuple[RecordResult, ...]
    result_digest: str
    blocked: tuple[
        str, ...
    ] = ()  # why the whole run is blocked (a clock it cannot trust, a rule set it does not hold)

    @property
    def findings(self) -> tuple[Finding, ...]:
        """The findings a person should review: R-06 is an incomplete result, reported as such, never as a recommendation."""
        return tuple(f for r in self.records if r.outcome is Outcome.FINDINGS for f in r.findings)

    @property
    def unknown_count(self) -> int:
        return sum(1 for r in self.records if r.outcome is Outcome.UNKNOWN)

    @property
    def complete(self) -> bool:
        """Healthy only if every record could be judged and the clock could be trusted."""
        return not self.blocked and self.unknown_count == 0


def canonical(value: Any) -> str:
    """One byte-exact JSON form, so a digest means the same thing everywhere."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _outside(assigned: frozenset[str], approved: frozenset[str]) -> int:
    """How many assigned entries the approved scope does not cover."""
    return 0 if UNRESTRICTED in approved else len(assigned - approved)


def unknown_reasons(f: MandateFacts) -> tuple[str, ...]:
    """R-06 for one record: why its metadata cannot be trusted. Empty means every rule may be applied."""
    reasons: list[str] = []
    if f.adapter not in SUPPORTED_ADAPTERS:
        reasons.append("ADAPTER_UNSUPPORTED")
    if not isinstance(f.mandate_id, str) or not f.mandate_id:
        reasons.append("TARGET_UNKNOWN")
    if f.status not in KNOWN_STATUSES:
        reasons.append("STATE_UNKNOWN")
    if not _aware(f.valid_to):
        reasons.append("VALIDITY_UNREADABLE")
    if f.assigned_actions is None or f.assigned_tools is None:
        reasons.append("SCOPE_UNREADABLE")
    if f.grant_found and (f.approved_actions is None or f.approved_tools is None):
        reasons.append("APPROVED_SCOPE_UNREADABLE")
    return tuple(reasons)


def _finding(
    rule: Rule, f: MandateFacts, detail: Iterable[tuple[str, int | str | bool]] = ()
) -> Finding:
    code, severity = FINDINGS[rule]
    refs = (f"mandate:{f.mandate_id}",) + ((f"grant:{f.grant_ref}",) if f.grant_ref else ())
    return Finding(
        rule.value, RULE_SET_VERSION, code, severity, f"mandate:{f.mandate_id}", refs, tuple(detail)
    )


def _unknown(f: MandateFacts, reasons: tuple[str, ...]) -> RecordResult:
    return RecordResult(
        f"mandate:{f.mandate_id}",
        Outcome.UNKNOWN,
        (_finding(Rule.R06, f, [("reasons", len(reasons))]),),
        reasons,
    )


def evaluate_record(
    f: MandateFacts, trusted_now: datetime, warning_window: timedelta
) -> RecordResult:
    """R-01 to R-05 for one record, or UNKNOWN (R-06) when it cannot be judged. Findings come out in rule order."""
    reasons = unknown_reasons(f)
    if reasons:
        return _unknown(f, reasons)
    assert (
        f.valid_to is not None and f.assigned_actions is not None and f.assigned_tools is not None
    )
    found: list[Finding] = []
    remaining = f.valid_to - trusted_now
    if f.status == "ACTIVE":
        if f.valid_to < trusted_now:  # R-01
            found.append(_finding(Rule.R01, f, [("days_overdue", (trusted_now - f.valid_to).days)]))
        elif remaining <= warning_window:  # R-02: 0 <= remaining <= window
            found.append(_finding(Rule.R02, f, [("days_remaining", remaining.days)]))
    if f.status in LIVE_STATUSES:
        if f.sponsor_required and not (f.sponsor_ref and f.sponsor_active is True):  # R-03
            found.append(_finding(Rule.R03, f))
        if f.grant_found and f.approved_actions is not None and f.approved_tools is not None:
            actions = _outside(f.assigned_actions, f.approved_actions)
            tools = _outside(f.assigned_tools, f.approved_tools)
            if actions or tools:  # R-04: never "corrected" here
                found.append(
                    _finding(Rule.R04, f, [("actions_outside", actions), ("tools_outside", tools)])
                )
        if not f.grant_found:  # R-05: the policy evidence the mandate rests on cannot be found
            found.append(_finding(Rule.R05, f, [("reason", "GRANT_UNVERIFIABLE")]))
        elif not f.evidence_ref:
            found.append(_finding(Rule.R05, f, [("reason", "EVIDENCE_REFERENCE_MISSING")]))
    return RecordResult(
        f"mandate:{f.mandate_id}", Outcome.FINDINGS if found else Outcome.CLEAN, tuple(found)
    )


def evaluate_run(
    records: Iterable[MandateFacts],
    trusted_now: datetime,
    warning_window: timedelta = timedelta(days=DEFAULT_WARNING_WINDOW_DAYS),
    *,
    rule_set_version: str = RULE_SET_VERSION,
) -> RunResult:
    """Apply the rule set to every record. A version this engine does not hold, or a clock it cannot read, blocks the whole run."""
    blocked: list[str] = []
    if rule_set_version != RULE_SET_VERSION:
        blocked.append("RULE_SET_UNSUPPORTED")
    if not _aware(trusted_now):
        blocked.append("CLOCK_UNTRUSTED")
    if not isinstance(warning_window, timedelta) or warning_window < timedelta(0):
        blocked.append("WINDOW_INVALID")
    ordered = sorted(records, key=lambda r: str(r.mandate_id))
    if blocked:
        results = tuple(_unknown(r, tuple(blocked)) for r in ordered)
    else:
        results = tuple(evaluate_record(r, trusted_now, warning_window) for r in ordered)
    body = {
        "rule_set_version": rule_set_version,
        "blocked": blocked,
        "records": [r.as_dict() for r in results],
    }
    return RunResult(rule_set_version, results, digest(body), tuple(blocked))


def facts_digest(records: Iterable[MandateFacts]) -> str:
    """What was read, as a digest: the evidence keeps this and never the metadata itself."""
    rows = []
    for r in sorted(records, key=lambda r: str(r.mandate_id)):
        rows.append(
            {
                "id": r.mandate_id,
                "status": r.status,
                "valid_to": r.valid_to.isoformat() if isinstance(r.valid_to, datetime) else None,
                "assigned": sorted(r.assigned_actions) if r.assigned_actions is not None else None,
                "tools": sorted(r.assigned_tools) if r.assigned_tools is not None else None,
                "approved": sorted(r.approved_actions) if r.approved_actions is not None else None,
                "approved_tools": sorted(r.approved_tools)
                if r.approved_tools is not None
                else None,
                "grant": r.grant_ref,
                "evidence": r.evidence_ref,
                "sponsor": [r.sponsor_required, r.sponsor_ref, r.sponsor_active],
                "adapter": r.adapter,
            }
        )
    return digest(rows)


def proposal_key(tenant_id: str, target_ref: str, finding: Finding) -> str:
    """One proposal per tenant, target, rule, rule version and finding code (idempotent upsert; AT-11)."""
    return digest(
        [tenant_id, target_ref, finding.rule_id, finding.rule_version, finding.finding_code]
    )


FIXTURES = frozenset({"baseline"})


def synthetic_fixture(name: str, now: datetime) -> list[MandateFacts]:
    """Dry-run input (AT-06): invented records, never live data. Only named fixtures exist."""
    if name not in FIXTURES:
        raise KeyError(name)
    day = timedelta(days=1)
    read, none = frozenset({"read"}), frozenset[str]()

    def mandate(mandate_id: str, valid_to: datetime, **kw: Any) -> MandateFacts:
        kw.setdefault("assigned_actions", read)
        kw.setdefault("evidence_ref", "synthetic-evidence")
        return MandateFacts(
            mandate_id,
            "ACTIVE",
            valid_to,
            assigned_tools=none,
            grant_found=True,
            approved_actions=read,
            approved_tools=none,
            grant_ref="synthetic-grant",
            **kw,
        )

    return [
        mandate("synthetic-expired", now - 3 * day),
        mandate("synthetic-expiring", now + 5 * day),
        mandate(
            "synthetic-orphan",
            now + 90 * day,
            sponsor_required=True,
            sponsor_ref="synthetic-sponsor",
            sponsor_active=False,
        ),
        mandate("synthetic-wide", now + 90 * day, assigned_actions=frozenset({"read", "write"})),
        mandate("synthetic-healthy", now + 90 * day),
        mandate("synthetic-no-evidence", now + 90 * day, evidence_ref=None),
        MandateFacts("synthetic-unreadable", "ACTIVE", None, read, none, grant_found=False),
    ]

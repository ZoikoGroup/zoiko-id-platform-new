"""Regulatory notification deadlines for incidents (ZID-RCG-003 section 08, acceptance case T45). Pure rules, no I/O.

When an incident may have to be reported (a personal-data breach to a supervisory authority, a supplier outage to the customers relying on it,
a trust-service security breach to a regulator), each rule says who must be told, within how many hours of awareness, under which legal source,
and who escalates when the clock runs short. The rules are owner content (Legal); this module only applies them:
- every rule whose conditions the incident meets produces an obligation, routed to its recipient, with a due time from the awareness time;
- an obligation is MET only with the time it was sent and an evidence reference, and only if sent no later than the due time (LATE otherwise);
- an open obligation is DUE, then ESCALATED to Legal once the share of the window given by the rule has passed, then OVERDUE;
- nothing is inferred: an incident that meets no rule has no obligation, and an unknown rule never stops another.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from zid_platform.modules.operations.domain.incidents import Severity

_RANK = {
    s: i
    for i, s in enumerate(
        (Severity.SEV4, Severity.SEV3, Severity.SEV2, Severity.SEV1, Severity.SEV0)
    )
}


class NotificationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class State(StrEnum):
    DUE = "DUE"
    ESCALATED = "ESCALATED"
    OVERDUE = "OVERDUE"
    MET = "MET"
    LATE = "LATE"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    recipient: str  # e.g. SUPERVISORY_AUTHORITY, AFFECTED_CUSTOMERS, TRUST_SERVICE_REGULATOR
    deadline_hours: int
    legal_source: str
    escalate_to: str = "LEGAL"
    escalate_at: float = 0.5  # share of the window after which Legal is called in
    min_severity: Severity = Severity.SEV4
    personal_data: bool | None = None  # None: either
    security: bool | None = None
    countries: frozenset[str] = field(default_factory=frozenset)  # empty: any


@dataclass(frozen=True)
class Incident:
    incident_id: str
    severity: Severity
    security: bool
    personal_data: bool
    countries: frozenset[str]
    aware_at: datetime


@dataclass(frozen=True)
class Sent:
    rule_id: str
    sent_at: datetime
    evidence_ref: str


@dataclass(frozen=True)
class Obligation:
    rule_id: str
    recipient: str
    legal_source: str
    due_at: datetime
    escalate_at: datetime
    escalate_to: str
    state: State
    evidence_ref: str | None = None


def parse(doc: Mapping[str, Any]) -> list[Rule]:
    out: list[Rule] = []
    seen: set[str] = set()
    for raw in doc.get("rules") or []:
        rid = str(raw.get("rule_id", ""))
        if not rid or rid in seen:
            raise NotificationError("NOTIFICATION_RULE_INVALID", rid or "missing id")
        seen.add(rid)
        hours = int(raw.get("deadline_hours", 0))
        share = float(raw.get("escalate_at", 0.5))
        if (
            hours <= 0
            or not 0 < share < 1
            or not raw.get("legal_source")
            or not raw.get("recipient")
        ):
            raise NotificationError("NOTIFICATION_RULE_INVALID", rid)
        out.append(
            Rule(
                rule_id=rid,
                recipient=str(raw["recipient"]),
                deadline_hours=hours,
                legal_source=str(raw["legal_source"]),
                escalate_to=str(raw.get("escalate_to", "LEGAL")),
                escalate_at=share,
                min_severity=Severity(str(raw.get("min_severity", "SEV4"))),
                personal_data=raw.get("personal_data"),
                security=raw.get("security"),
                countries=frozenset(raw.get("countries") or ()),
            )
        )
    return out


def applies(rule: Rule, incident: Incident) -> bool:
    if _RANK[incident.severity] < _RANK[rule.min_severity]:
        return False
    if rule.personal_data is not None and rule.personal_data != incident.personal_data:
        return False
    if rule.security is not None and rule.security != incident.security:
        return False
    return not rule.countries or bool(rule.countries & incident.countries)


def obligations(
    incident: Incident, rules: Iterable[Rule], sent: Sequence[Sent], *, now: datetime
) -> list[Obligation]:
    """Every notification this incident requires, with its clock and state at `now`."""
    by_rule = {s.rule_id: s for s in sent}
    out: list[Obligation] = []
    for r in rules:
        if not applies(r, incident):
            continue
        window = timedelta(hours=r.deadline_hours)
        due = incident.aware_at + window
        escalate = incident.aware_at + window * r.escalate_at
        s = by_rule.get(r.rule_id)
        if s is not None and s.evidence_ref.strip():
            state = State.MET if s.sent_at <= due else State.LATE
            ref: str | None = s.evidence_ref
        else:
            state = (
                State.OVERDUE if now > due else State.ESCALATED if now >= escalate else State.DUE
            )
            ref = None
        out.append(
            Obligation(
                r.rule_id, r.recipient, r.legal_source, due, escalate, r.escalate_to, state, ref
            )
        )
    return sorted(out, key=lambda o: o.due_at)

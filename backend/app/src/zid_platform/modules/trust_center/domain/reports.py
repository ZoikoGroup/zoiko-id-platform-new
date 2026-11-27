"""Vulnerability reports and incident advisories (ZID-CP-WF12 v2.0, WF-12.07 and WF-12.08). Pure domain, no I/O.

A report has a promised first response and a state that only moves forward. An advisory is a sequence of versions: a correction is a new version that says why, and the earlier one stays visible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from zid_platform.modules.trust_center.domain import disclosure as dz


class ReportState(StrEnum):
    RECEIVED = "RECEIVED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    TRIAGED = "TRIAGED"
    FIXING = "FIXING"
    FIXED = "FIXED"
    DISCLOSED = "DISCLOSED"
    CLOSED = "CLOSED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


_NEXT: dict[ReportState, frozenset[ReportState]] = {
    ReportState.RECEIVED: frozenset({ReportState.ACKNOWLEDGED, ReportState.OUT_OF_SCOPE}),
    ReportState.ACKNOWLEDGED: frozenset({ReportState.TRIAGED, ReportState.OUT_OF_SCOPE}),
    ReportState.TRIAGED: frozenset(
        {ReportState.FIXING, ReportState.OUT_OF_SCOPE, ReportState.CLOSED}
    ),
    ReportState.FIXING: frozenset({ReportState.FIXED}),
    ReportState.FIXED: frozenset({ReportState.DISCLOSED, ReportState.CLOSED}),
    ReportState.DISCLOSED: frozenset({ReportState.CLOSED}),
    ReportState.CLOSED: frozenset(),
    ReportState.OUT_OF_SCOPE: frozenset({ReportState.CLOSED}),
}
SEVERITIES = ("critical", "high", "medium", "low", "info")
ACK_HOURS = {
    "critical": 4,
    "high": 24,
    "medium": 72,
    "low": 120,
    "info": 120,
}  # placeholders until DEC-09 sets the promise; the receipt says "target", not "guarantee"


def can_move(a: ReportState, b: ReportState) -> bool:
    return b in _NEXT[a]


def ack_due(received: datetime, severity: str) -> datetime:
    return received + timedelta(hours=ACK_HOURS.get(severity, 120))


REFERENCE = re.compile(r"^VR-[0-9A-Z]{10}$")


def intake_problems(summary: str, affected: str, *, contact: str | None) -> list[str]:
    """A report is taken as written; only what would harm the researcher or the platform is refused. A report may contain the details: it goes to staff, never to a public page."""
    out: list[str] = []
    if len(summary.strip()) < 20:
        out.append("Say what you found in at least a sentence or two.")
    if len(summary) > 8000 or len(affected) > 2000:
        out.append("The report is too long; send a summary and offer details on request.")
    if contact is not None and len(contact) > 200:
        out.append("The contact is too long.")
    return out


def receipt(reference: str, received: datetime, severity: str) -> dict[str, str]:
    """What the researcher is told: a reference and a target, never their own text back and never a promise of a bounty."""
    return {
        "reference": reference, "received_at": received.isoformat(), "first_response_target": ack_due(received, severity).isoformat(),
        "note": "This is a target, not a guarantee. We do not currently operate a paid reward program.",
    }  # fmt: skip


# ---------------------------------------------------------------------------------------------------- advisories


class AdvisoryKind(StrEnum):
    PUBLISH = "PUBLISH"
    UPDATE = "UPDATE"
    CORRECTION = "CORRECTION"
    RESOLVE = "RESOLVE"
    POSTMORTEM = "POSTMORTEM"


@dataclass(frozen=True)
class AdvisoryVersion:
    seq: int
    kind: AdvisoryKind
    title: str
    body: str
    reason: str
    author: str
    approved_by: str | None


def advisory_problems(v: AdvisoryVersion, previous: AdvisoryVersion | None) -> list[str]:
    """Rules for any public version. A correction says why and replaces nothing; a postmortem carries no attack detail; nobody approves their own text."""
    out: list[str] = []
    if v.approved_by is None:
        out.append("Security and Legal must approve what is made public.")
    elif v.approved_by == v.author:
        out.append("The author of an advisory cannot approve it.")
    if v.kind == AdvisoryKind.CORRECTION and not v.reason.strip():
        out.append("A correction states why the earlier version was wrong.")
    if v.kind != AdvisoryKind.PUBLISH and previous is None:
        out.append("There is no earlier version to update or correct.")
    if v.kind == AdvisoryKind.PUBLISH and previous is not None:
        out.append("This advisory is already public; add an update or a correction.")
    out += dz.safe_for_public(v.title + "\n" + v.body)
    return out


def public_history(versions: list[AdvisoryVersion]) -> list[dict[str, object]]:
    """Every approved version, oldest first, with corrections marked. An earlier version is never dropped."""
    out = []
    for v in sorted(versions, key=lambda x: x.seq):
        if v.approved_by is None:
            continue
        out.append(
            {
                "seq": v.seq,
                "kind": str(v.kind),
                "title": v.title,
                "body": v.body,
                "correction_reason": v.reason if v.kind == AdvisoryKind.CORRECTION else None,
            }
        )
    return out

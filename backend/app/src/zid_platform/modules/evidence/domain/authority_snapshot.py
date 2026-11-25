"""A signed history of who held which authority at the moment of a decision (ZID-CP-WF08 v2.0 E02, section 08.1; CR-170). Pure domain, no I/O.

A receipt says a decision was made under some authority. Later the authority changes: a grant is revoked, a mandate ends, a sponsor leaves. The snapshot is
taken WHEN the decision is made and never edited, so a reviewer can see the authority as it stood then and not as it reads today. Each edge names its source,
its version, who issued it, when we obtained it, the period it was in effect and a digest of the evidence behind it.

The verdict is deliberately cautious. A required edge that is missing, two edges that disagree, an edge with no usable evidence digest or one we only
learned about after the decision make the whole snapshot INDETERMINATE, with every reason named. There is no "probably fine".
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

SCHEMA = "zoiko.authority-snapshot.v1"
EDGE_TYPES = ("GRANT", "MANDATE", "SPONSOR", "AGENT", "EXTERNAL_IDP")
REQUIRED = ("GRANT",)  # an authority-bearing decision rests on at least one grant
_DIGEST = re.compile(r"^sha-(256|384|512):[0-9a-f]+$")
LATE_TOLERANCE = timedelta(minutes=5)  # an edge obtained within this of the decision counts as known at the time (clock skew, same transaction)
MAX_EDGES = 200


def _t(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else None
    return None


def normalise_edge(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The stored form of one edge: fixed keys, times as ISO strings, nothing the caller added."""
    out: dict[str, Any] = {
        "edge_type": str(raw.get("edge_type", "")),
        "edge_id": str(raw.get("edge_id", "")),
        "version": raw.get("version"),
        "subject": str(raw.get("subject", "")),
        "state": str(raw.get("state", "")),
        "issuer": str(raw.get("issuer", "")),
        "evidence_digest": raw.get("evidence_digest"),
    }
    for key in ("obtained_at", "effective_from", "effective_until"):
        parsed = _t(raw.get(key))
        out[key] = parsed.isoformat() if parsed else (None if raw.get(key) in (None, "") else "INVALID")
    return out


def _edge_problems(e: Mapping[str, Any]) -> list[str]:
    tag = f"{e['edge_type']}:{e['edge_id']}"
    problems = []
    if e["edge_type"] not in EDGE_TYPES:
        problems.append(f"EDGE_TYPE_UNKNOWN:{tag}")
    if not e["edge_id"] or not e["subject"] or not e["issuer"]:
        problems.append(f"EDGE_INCOMPLETE:{tag}")
    if not isinstance(e["version"], int) or isinstance(e["version"], bool) or e["version"] < 0:
        problems.append(f"EDGE_VERSION_INVALID:{tag}")
    if not isinstance(e["evidence_digest"], str) or not _DIGEST.match(e["evidence_digest"]):
        problems.append(f"EDGE_EVIDENCE_DIGEST_MISSING:{tag}")
    if e["effective_from"] in (None, "INVALID") or e["obtained_at"] in (None, "INVALID") or e["effective_until"] == "INVALID":
        problems.append(f"EDGE_TIME_INVALID:{tag}")
    return problems


def assess(edges: Sequence[Mapping[str, Any]], decision_at: datetime, *, required: Sequence[str] = REQUIRED) -> dict[str, Any]:
    """The snapshot body for these edges at `decision_at`. Never raises: a defect is a reason and an INDETERMINATE verdict."""
    reasons: list[str] = []
    if decision_at.tzinfo is None:
        raise ValueError("decision_at must be timezone-aware")
    if len(edges) > MAX_EDGES:
        reasons.append("TOO_MANY_EDGES")
    norm = [normalise_edge(e) for e in list(edges)[:MAX_EDGES]]
    usable: list[dict[str, Any]] = []
    for e in norm:
        bad = _edge_problems(e)
        reasons += bad
        if not bad:
            usable.append(e)

    in_effect: list[dict[str, Any]] = []
    for e in usable:
        start, end, got = _t(e["effective_from"]), _t(e["effective_until"]), _t(e["obtained_at"])
        assert start is not None and got is not None
        if start > decision_at or (end is not None and end <= decision_at):
            reasons.append(f"EDGE_NOT_IN_EFFECT:{e['edge_type']}:{e['edge_id']}")
            continue
        if got > decision_at + LATE_TOLERANCE:
            reasons.append(f"EDGE_OBTAINED_AFTER_DECISION:{e['edge_type']}:{e['edge_id']}")  # a later reconstruction, not a record made at the time
            continue
        in_effect.append(e)

    # two edges about the same subject and kind that were both in effect must agree on state and must be one version of one source
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for e in in_effect:
        key = (e["edge_type"], e["subject"])
        other = seen.get(key)
        if other is None:
            seen[key] = e
        else:
            states_differ = other["state"] != e["state"]
            two_sources = other["edge_id"] != e["edge_id"] and e["edge_type"] in ("MANDATE", "SPONSOR", "AGENT")  # one mandate, one sponsor, one agent record at a time
            two_versions = other["edge_id"] == e["edge_id"] and other["version"] != e["version"]
            if states_differ or two_sources or two_versions:
                reasons.append(f"CONTRADICTORY_EDGES:{e['edge_type']}:{e['subject']}")
    for e in in_effect:
        if e["state"] in ("REVOKED", "ENDED", "SUSPENDED"):
            reasons.append(f"EDGE_NOT_ACTIVE_AT_DECISION:{e['edge_type']}:{e['edge_id']}")

    present = {e["edge_type"] for e in in_effect}
    reasons += [f"MISSING_EDGE:{t}" for t in required if t not in present]
    ordered = sorted(norm, key=lambda e: (e["edge_type"], e["edge_id"], str(e["version"])))
    return {
        "schema": SCHEMA,
        "decision_at": decision_at.isoformat(),
        "edges": ordered,
        "verdict": "INDETERMINATE" if reasons else "COMPLETE",
        "reasons": sorted(set(reasons)),
        "caveat": "This is the authority as the platform recorded it. It does not prove the authority existed in law or that the decision was right.",
    }

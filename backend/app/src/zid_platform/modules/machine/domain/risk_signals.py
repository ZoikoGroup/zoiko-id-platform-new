"""Signals worth a person's attention about agents (ZID-CP-WF07 v2.0 A07-14; CR-169). Pure domain, no I/O.

A signal says something looks off and where to look. It is never a score of an agent, never a verdict and never an input to a decision: nothing here stops an agent, and nothing here is read by the gateway. Each signal names the
exact thing it is about and the plain fact that raised it, and the report says what it cannot see (a denied call is not kept here, so it is not counted).

Only tables this module owns are read, and only about the caller's own company.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

UNRESOLVED_AFTER = timedelta(hours=1)  # a call dispatched, or an outcome unknown, for longer than this
KINDS: dict[str, dict[str, str]] = {
    "SPONSOR_NOT_ACTIVE": {"severity": "attention", "text": "An active agent's sponsor is no longer active. Its next call is refused; its mandates should be reviewed."},
    "UNRESOLVED_OUTCOME": {"severity": "attention", "text": "A tool call was started and its outcome has not been reported, or is unknown, for over an hour. Capacity held for it stays held."},
    "PROVIDER_CHECK_FAILED": {"severity": "attention", "text": "The last token an outside provider presented for this agent failed a check."},
    "PROVIDER_NOT_HEARD_FROM": {"severity": "info", "text": "An outside provider has not presented a valid token lately, so its evidence is not relied on."},
    "STOP_STILL_OPEN": {"severity": "info", "text": "A stop of agents has not been closed by anyone yet, so those agents cannot be reinstated."},
}
CANNOT_SEE = [
    "Denied tool calls: they are recorded in the audit trail, not kept here, so they are not counted.",
    "What a tool did after it was given a grant, other than what its server reports.",
    "Anything about another company.",
]


def signal(kind: str, *, subject: str, since: datetime | None, fact: Mapping[str, Any]) -> dict[str, Any]:
    k = KINDS[kind]
    return {"kind": kind, "severity": k["severity"], "subject": subject, "since": since.isoformat() if since else None, "fact": dict(fact), "text": k["text"], "is_a_finding": False}


def unresolved(actions: Sequence[Mapping[str, Any]], now: datetime) -> list[dict[str, Any]]:
    return [
        signal("UNRESOLVED_OUTCOME", subject=f"action:{a['action_id']}", since=a["updated_at"], fact={"state": a["state"], "agent_id": str(a["agent_principal_id"]), "tool_id": a["tool_id"]})
        for a in actions
        if a["state"] in ("DISPATCHED", "UNKNOWN") and now - a["updated_at"] >= UNRESOLVED_AFTER
    ]


def providers(bindings: Sequence[Mapping[str, Any]], now: datetime, stale_after: timedelta) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in bindings:
        if b["status"] != "ACTIVE":
            continue
        if b["last_failure"]:
            out.append(signal("PROVIDER_CHECK_FAILED", subject=f"binding:{b['binding_id']}", since=b["last_ok_at"], fact={"reason": b["last_failure"], "agent_id": str(b["agent_principal_id"])}))
        elif b["last_ok_at"] is None or now - b["last_ok_at"] > stale_after:
            out.append(signal("PROVIDER_NOT_HEARD_FROM", subject=f"binding:{b['binding_id']}", since=b["last_ok_at"], fact={"agent_id": str(b["agent_principal_id"]), "issuer": b["issuer"]}))
    return out


def open_stops(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        signal("STOP_STILL_OPEN", subject=f"stop:{e['event_id']}", since=e["effective_at"], fact={"epoch": e["epoch"], "scope": e["scope"], "agents": len(e["targets"].get("agent_ids", []))})
        for e in events
        if e["closed_at"] is None
    ]


def orphaned(agent_ids_without_sponsor: Sequence[str]) -> list[dict[str, Any]]:
    return [signal("SPONSOR_NOT_ACTIVE", subject=f"agent:{a}", since=None, fact={"agent_id": a}) for a in agent_ids_without_sponsor]


def report(signals: Sequence[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Grouped by kind with the counts beside them, most urgent first. A quiet report says so: no signals is not proof of safety."""
    order = {"attention": 0, "info": 1}
    ordered = sorted(signals, key=lambda s: (order[s["severity"]], s["kind"], s["subject"]))
    by_kind = {k: sum(1 for s in ordered if s["kind"] == k) for k in KINDS}
    return {
        "as_of": now.isoformat(), "signals": ordered, "counts": {"total": len(ordered), "attention": sum(1 for s in ordered if s["severity"] == "attention"), "by_kind": by_kind},
        "cannot_see": CANNOT_SEE, "score": None, "note": "A signal is a place to look. It is not a finding about an agent, and nothing here changes what an agent may do. No signals is not proof that nothing is wrong.",
    }  # fmt: skip

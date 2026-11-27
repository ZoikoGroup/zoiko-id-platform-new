"""Webhook endpoint health and the trace of one verification (ZID-CP-WF10 v2.0, WF-10.09, WF-10.10; CR-173). Pure domain, no I/O.

A developer who cannot tell why events are not arriving loses days. So a delivery is put into one named class, and an endpoint's recent deliveries are summarised into a verdict
that says what is wrong and what to do. The classes are only the ones the platform can really tell apart:

    DELIVERED             the endpoint answered 2xx
    REJECTED_BY_ENDPOINT  the endpoint answered 4xx (it refused the event: often a signature check or a wrong URL)
    ENDPOINT_ERROR        the endpoint answered 5xx (its own failure; the platform retries)
    TIMED_OUT             no answer in time
    COULD_NOT_CONNECT     the connection failed. DNS, TLS and a refused connection are NOT told apart here, and the answer says so
    DESTINATION_BLOCKED   the platform refused to call the address (for example it now resolves to a private network): a policy stop, not the endpoint's fault
    WAITING               not attempted yet

"Signature confirmed by the partner" is separate: it comes from the partner's own confirmation (WF-10.01), because the platform cannot see whether a partner checked a signature.

The trace puts the canonical ids of one verification in time order: the API requests, the route decision and its attempts, the decisions, and the webhook deliveries. It carries no
applicant data, only ids, states and times.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

CLASSES = (
    "DELIVERED",
    "REJECTED_BY_ENDPOINT",
    "ENDPOINT_ERROR",
    "TIMED_OUT",
    "COULD_NOT_CONNECT",
    "DESTINATION_BLOCKED",
    "WAITING",
)
ADVICE = {
    "DELIVERED": "Working.",
    "REJECTED_BY_ENDPOINT": "Your endpoint is refusing events with a 4xx. Check the signature verification and that the URL is the right route, then replay.",
    "ENDPOINT_ERROR": "Your endpoint is failing with a 5xx. Fix the error on your side; the platform keeps retrying and a failed event can be replayed.",
    "TIMED_OUT": "Your endpoint did not answer in time. Reply quickly (acknowledge, then do the work) so a retry is a duplicate, not a second action.",
    "COULD_NOT_CONNECT": "The connection failed. This could be DNS, TLS or a refused connection; the platform does not tell these apart. Check the host name, certificate and firewall.",
    "DESTINATION_BLOCKED": "The platform will not call this address (it resolves to a private or blocked network). Use a public HTTPS address you control.",
    "WAITING": "No attempt has been made yet.",
}
SAMPLE = 20  # the most recent deliveries an endpoint's verdict is read from


def classify(status: str, status_code: int | None, error: str | None) -> str:
    """The class of one delivery from what the platform recorded about it."""
    if status == "DELIVERED":
        return "DELIVERED"
    if status in ("QUEUED", "IN_FLIGHT") and status_code is None and not error:
        return "WAITING"
    if error == "WEBHOOK_DESTINATION_BLOCKED":
        return "DESTINATION_BLOCKED"
    if status_code is not None:
        if 400 <= status_code < 500:
            return "REJECTED_BY_ENDPOINT"
        if status_code >= 500:
            return "ENDPOINT_ERROR"
        if 200 <= status_code < 300:
            return "DELIVERED"
    if error and "Timeout" in error:
        return "TIMED_OUT"
    if error:
        return "COULD_NOT_CONNECT"
    return "WAITING"


def endpoint_health(
    deliveries: Sequence[Mapping[str, Any]], confirmed_events: int = 0
) -> dict[str, Any]:
    """A verdict from the newest deliveries: HEALTHY, DEGRADED, FAILING or UNKNOWN (nothing delivered yet), with the counts and what to do."""
    recent = list(deliveries)[:SAMPLE]
    counts = {c: 0 for c in CLASSES}
    for d in recent:
        counts[classify(str(d["status"]), d.get("last_status_code"), d.get("last_error"))] += 1
    attempted = sum(v for k, v in counts.items() if k != "WAITING")
    if attempted == 0:
        verdict, main = "UNKNOWN", "WAITING"
    else:
        ok = counts["DELIVERED"]
        main = max(
            (c for c in CLASSES if c not in ("DELIVERED", "WAITING")), key=lambda c: counts[c]
        )
        failing = attempted - ok
        verdict = (
            "HEALTHY"
            if failing == 0
            else ("FAILING" if ok == 0 or failing * 2 > attempted else "DEGRADED")
        )
        if verdict == "HEALTHY":
            main = "DELIVERED"
    return {
        "verdict": verdict, "counts": counts, "sampled": len(recent), "main_problem": main if verdict in ("DEGRADED", "FAILING", "UNKNOWN") else None, "advice": ADVICE[main],
        "partner_confirmed_signature_events": confirmed_events,
        "note": "DNS, TLS and a refused connection are shown together because the platform cannot tell them apart. Whether your server checked the signature is known only from your own confirmation.",
    }  # fmt: skip


def _t(v: Any) -> str:
    return v.isoformat() if isinstance(v, datetime) else str(v)


def build_trace(
    session_id: str,
    session: Mapping[str, Any] | None,
    requests: Sequence[Mapping[str, Any]],
    routes: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    deliveries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The ordered chain for one verification. An absent link is reported as absent and never invented."""
    steps: list[dict[str, Any]] = []
    steps += [
        {
            "at": _t(r["occurred_at"]),
            "kind": "API_REQUEST",
            "id": str(r["trace_id"]),
            "summary": f"{r['method']} {r['route']} answered {r['status']}"
            + (f" ({r['error_code']})" if r.get("error_code") else ""),
        }
        for r in requests
    ]
    steps += [
        {
            "at": _t(r["created_at"]),
            "kind": "ROUTE_DECISION",
            "id": str(r["decision_id"]),
            "summary": f"{r['status']}" + (", provider chosen" if r.get("selected") else ""),
        }
        for r in routes
    ]
    steps += [
        {
            "at": _t(d["created_at"]),
            "kind": "DECISION",
            "id": str(d["decision_id"]),
            "summary": f"version {d['version']}: {d['outcome']}",
        }
        for d in decisions
    ]
    steps += [
        {
            "at": _t(w["created_at"]),
            "kind": "WEBHOOK_DELIVERY",
            "id": str(w["event_id"]),
            "summary": f"{w['event_type']}: {classify(str(w['status']), w.get('last_status_code'), w.get('last_error')).lower().replace('_', ' ')}",
        }
        for w in deliveries
    ]
    steps.sort(key=lambda s: (s["at"], s["kind"], s["id"]))
    missing = [
        name
        for name, rows in (
            ("API_REQUEST", requests),
            ("ROUTE_DECISION", routes),
            ("DECISION", decisions),
            ("WEBHOOK_DELIVERY", deliveries),
        )
        if not rows
    ]
    return {
        "session_id": session_id, "found": session is not None, "status": session.get("status") if session else None, "steps": steps, "missing_links": missing,
        "note": "Ids, states and times only: no document, face, name or score appears here. A missing link is listed, not guessed.",
    }  # fmt: skip


def request_filters_problems(status: int | None, route: str | None, limit: int) -> list[str]:
    out: list[str] = []
    if status is not None and not 100 <= status <= 599:
        out.append("STATUS_OUT_OF_RANGE")
    if route is not None and (len(route) > 120 or "?" in route):
        out.append("ROUTE_FILTER_INVALID")
    if not 1 <= limit <= 200:
        out.append("LIMIT_OUT_OF_RANGE")
    return out


def route_template(path_template: str | None, fallback: str) -> str:
    """The route as its template (/v1/verify-api/verification-sessions/{session_id}), never the path with real ids in it, and never a query string."""
    t = path_template or fallback
    return t.split("?", 1)[0][:120]

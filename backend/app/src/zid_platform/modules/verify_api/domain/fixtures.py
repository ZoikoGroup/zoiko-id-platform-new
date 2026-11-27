"""The sandbox persona catalog, delivery fault plans and the integration test report (ZID-CP-WF10 v2.0, WF-10.08; CR-173). Pure domain, no I/O.

A sandbox that only ever approves teaches a partner nothing. Every integration also has to survive the awkward cases: a duplicate webhook, events arriving in the wrong
order, a stale or tampered signature, an endpoint that times out, an outcome nobody knows. This module defines them.

- PERSONAS are versioned and fixed: each names the outcome it produces and the events a partner's server should see, in order. The same persona at the same version
  always means the same thing.
- DELIVERY PLANS say exactly how the events of a persona should be delivered to a partner's endpoint: which are duplicated, which are swapped, how old each signature is,
  and which body is changed. A plan depends only on the persona, its version and a seed, so a partner's CI and Zoiko's agree byte for byte.
- The REPORT scores what a partner's server observed against the persona's expectations. It is a record of one test run. It is not a certification and not a go-live
  approval; those are separate decisions with their own evidence.

Everything here is synthetic. Nothing calls a provider, a government source or a living person.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

CATALOG_VERSION = "1"
SIGNATURE_TOLERANCE_S = (
    300  # a signature older than this must be refused by the partner (the documented window)
)

FAULTS = (
    "NONE",
    "DUPLICATE",
    "OUT_OF_ORDER",
    "STALE_SIGNATURE",
    "TAMPERED_BODY",
    "SCHEMA_MISMATCH",
    "ENDPOINT_TIMEOUT",
)


@dataclass(frozen=True, slots=True)
class Persona:
    id: str
    fixture: (
        str  # the sandbox outcome it produces (approved, declined, review, inconclusive, expired)
    )
    title: str
    events: tuple[str, ...]  # what a correct server sees, in order, with a clean delivery
    final_outcome: str | None  # the decision outcome, none when the session simply expires
    fault: str = "NONE"
    teaches: str = ""


PERSONAS: tuple[Persona, ...] = (
    Persona(
        "clear_pass",
        "approved",
        "A clean pass",
        ("session.created", "session.submitted", "decision.finalized"),
        "VERIFIED",
        teaches="the happy path, and reading the final decision from the API before acting",
    ),
    Persona(
        "clear_decline",
        "declined",
        "A clean decline",
        ("session.created", "session.submitted", "decision.finalized"),
        "NOT_VERIFIED",
        teaches="a decline that is a decision about evidence, not an error",
    ),
    Persona(
        "needs_review",
        "review",
        "A person must look",
        ("session.created", "session.submitted", "case.pending"),
        "REVIEW_REQUIRED",
        teaches="a pending review is never a verified result",
    ),
    Persona(
        "provider_unavailable",
        "inconclusive",
        "The check could not complete",
        ("session.created", "session.submitted", "decision.finalized"),
        "INCONCLUSIVE",
        teaches="an inconclusive result is not a decline and not a pass",
    ),
    Persona(
        "session_expired",
        "expired",
        "The applicant never finished",
        ("session.created", "session.closed"),
        None,
        teaches="expiry ends a session without any decision",
    ),
    Persona(
        "duplicate_webhook",
        "approved",
        "Every event arrives twice",
        ("session.created", "session.submitted", "decision.finalized"),
        "VERIFIED",
        "DUPLICATE",
        "deduplicating on the event id so a repeat never acts twice",
    ),
    Persona(
        "out_of_order",
        "approved",
        "Events arrive in the wrong order",
        ("session.created", "session.submitted", "decision.finalized"),
        "VERIFIED",
        "OUT_OF_ORDER",
        "comparing object versions and reading the session when events disagree",
    ),
    Persona(
        "stale_signature",
        "approved",
        "A delivery signed long ago",
        ("session.created", "session.submitted", "decision.finalized"),
        "VERIFIED",
        "STALE_SIGNATURE",
        "refusing a signature outside the time window",
    ),
    Persona(
        "tampered_body",
        "approved",
        "A body changed after signing",
        ("session.created", "session.submitted", "decision.finalized"),
        "VERIFIED",
        "TAMPERED_BODY",
        "refusing a body that does not match its signature, using the raw bytes",
    ),
    Persona(
        "schema_drift",
        "approved",
        "An unexpected field appears",
        ("session.created", "session.submitted", "decision.finalized"),
        "VERIFIED",
        "SCHEMA_MISMATCH",
        "tolerating new fields and refusing a missing required one",
    ),
    Persona(
        "endpoint_timeout",
        "approved",
        "Your endpoint is slow to answer",
        ("session.created", "session.submitted", "decision.finalized"),
        "VERIFIED",
        "ENDPOINT_TIMEOUT",
        "answering quickly and doing the work afterwards, so a retry is a duplicate not a second action",
    ),
)

_BY_ID = {p.id: p for p in PERSONAS}


def persona(pid: str) -> Persona | None:
    return _BY_ID.get(pid)


def _stream(persona_id: str, seed: str, n: int) -> list[int]:
    """Deterministic numbers from (catalog version, persona, seed): the same inputs always give the same plan."""
    out: list[int] = []
    block = 0
    while len(out) < n:
        h = hashlib.sha256(f"{CATALOG_VERSION}|{persona_id}|{seed}|{block}".encode()).digest()
        out.extend(h)
        block += 1
    return out[:n]


def delivery_plan(pid: str, seed: str) -> dict[str, Any]:
    """The exact delivery pattern for a persona and seed: one entry per delivery attempt, in the order they are to be sent."""
    p = persona(pid)
    if p is None:
        raise KeyError(pid)
    r = _stream(pid, seed, 64)
    base = f"evt_{hashlib.sha256(f'{pid}|{seed}'.encode()).hexdigest()[:16]}"
    events = [
        {"event_id": f"{base}_{i + 1}", "event_type": t, "object_version": i + 1, "attempt": 1, "delay_ms": 100 + r[i] * 4, "signature_age_s": 0, "body": "AS_SIGNED", "respond_after_ms": 0}
        for i, t in enumerate(p.events)
    ]  # fmt: skip
    deliveries = [dict(e) for e in events]
    if p.fault == "DUPLICATE":
        deliveries = [
            d
            for e in events
            for d in (dict(e), {**e, "attempt": 2, "delay_ms": e["delay_ms"] + 50 + r[10]})  # type: ignore[operator]
        ]
    elif p.fault == "OUT_OF_ORDER":
        deliveries = [
            dict(events[-1]),
            *[dict(e) for e in events[:-1]],
        ]  # the last event first, then the rest in order
    elif p.fault == "STALE_SIGNATURE":
        deliveries = [{**e, "signature_age_s": SIGNATURE_TOLERANCE_S * 2 + r[11]} for e in events]
    elif p.fault == "TAMPERED_BODY":
        deliveries = [
            {**e, "body": "ALTERED_AFTER_SIGNING"} if i == len(events) - 1 else dict(e)
            for i, e in enumerate(events)
        ]
    elif p.fault == "SCHEMA_MISMATCH":
        deliveries = [
            {**e, "body": "EXTRA_FIELD_ADDED"}
            if i == 1
            else ({**e, "body": "REQUIRED_FIELD_REMOVED"} if i == len(events) - 1 else dict(e))
            for i, e in enumerate(events)
        ]
    elif p.fault == "ENDPOINT_TIMEOUT":
        deliveries = [
            {**e, "respond_after_ms": 30000} if i == 0 else dict(e) for i, e in enumerate(events)
        ]
    return {
        "catalog_version": CATALOG_VERSION,
        "persona": pid,
        "seed": seed,
        "deliveries": deliveries,
        "plan_digest": "sha-256:"
        + hashlib.sha256(
            json.dumps(deliveries, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "synthetic": True,
        "marker": "TEST / SYNTHETIC",
    }


def expectations(pid: str) -> dict[str, Any]:
    """What a correct partner server must do for this persona, as checkable facts."""
    p = persona(pid)
    if p is None:
        raise KeyError(pid)
    return {
        "persona": pid, "events_in_order": list(p.events), "final_outcome": p.final_outcome, "must_refuse_signature": p.fault in ("STALE_SIGNATURE", "TAMPERED_BODY"),
        "must_deduplicate": p.fault == "DUPLICATE", "must_read_authoritative_state": p.fault == "OUT_OF_ORDER" or p.final_outcome == "REVIEW_REQUIRED", "must_not_act_on_pending": p.final_outcome == "REVIEW_REQUIRED",
        "must_tolerate_extra_fields": p.fault == "SCHEMA_MISMATCH",
    }  # fmt: skip


def catalog_view() -> dict[str, Any]:
    return {
        "catalog_version": CATALOG_VERSION, "synthetic": True, "marker": "TEST / SYNTHETIC",
        "personas": [{"id": p.id, "title": p.title, "fixture": p.fixture, "events": list(p.events), "final_outcome": p.final_outcome, "fault": p.fault, "teaches": p.teaches} for p in PERSONAS],
        "note": "These are fixed, synthetic scenarios. The same persona and seed always produce the same delivery pattern. Passing them is a test result, not a certification.",
    }  # fmt: skip


# ------------------------------------------------------------------------------------------------------------------------- the report


@dataclass(frozen=True, slots=True)
class Observation:
    """What a partner's server did when the plan for one persona was played at it."""

    persona: str
    seed: str
    plan_digest: str
    signatures_refused: int  # deliveries it refused for signature reasons
    acted_on_event_ids: tuple[str, ...]  # event ids it acted on (after any de-duplication)
    final_outcome_seen: str | None
    read_authoritative_state: bool
    acted_on_pending: bool
    refused_missing_field: bool
    accepted_extra_field: bool


def score(obs: Observation) -> dict[str, Any]:
    """Pass or fail each expectation for one persona, with the reason. A failed expectation names what the server did wrong."""
    p = persona(obs.persona)
    if p is None:
        return {
            "persona": obs.persona,
            "passed": False,
            "checks": [{"check": "KNOWN_PERSONA", "passed": False, "detail": "no such persona"}],
        }
    plan = delivery_plan(obs.persona, obs.seed)
    ex = expectations(obs.persona)
    checks: list[dict[str, Any]] = [
        {
            "check": "PLAN_MATCHES",
            "passed": obs.plan_digest == plan["plan_digest"],
            "detail": "the plan the partner played is the one for this persona and seed",
        }
    ]
    unique_acted = len(set(obs.acted_on_event_ids)) == len(obs.acted_on_event_ids)
    checks.append(
        {
            "check": "NEVER_ACTS_TWICE_ON_ONE_EVENT",
            "passed": unique_acted,
            "detail": "each event id acted on at most once",
        }
    )
    if ex["must_refuse_signature"]:
        refused_needed = sum(
            1
            for d in plan["deliveries"]
            if d["signature_age_s"] > SIGNATURE_TOLERANCE_S or d["body"] == "ALTERED_AFTER_SIGNING"
        )
        checks.append(
            {
                "check": "REFUSES_BAD_SIGNATURES",
                "passed": obs.signatures_refused >= refused_needed,
                "detail": f"refused {obs.signatures_refused} of {refused_needed} deliveries that must be refused",
            }
        )
        if p.fault == "STALE_SIGNATURE":
            checks.append(
                {
                    "check": "DOES_NOT_ACT_ON_REFUSED",
                    "passed": len(obs.acted_on_event_ids) == 0,
                    "detail": "a refused delivery is never acted on",
                }
            )
    if ex["must_deduplicate"]:
        checks.append(
            {
                "check": "DEDUPLICATES",
                "passed": unique_acted and len(obs.acted_on_event_ids) == len(p.events),
                "detail": "each event acted on once although each arrived twice",
            }
        )
    if ex["must_read_authoritative_state"]:
        checks.append(
            {
                "check": "READS_AUTHORITATIVE_STATE",
                "passed": obs.read_authoritative_state,
                "detail": "read the session from the API when events disagreed or a review was pending",
            }
        )
    if ex["must_not_act_on_pending"]:
        checks.append(
            {
                "check": "DOES_NOT_TREAT_PENDING_AS_VERIFIED",
                "passed": not obs.acted_on_pending and obs.final_outcome_seen != "VERIFIED",
                "detail": "a pending review is not a verified result",
            }
        )
    if ex["must_tolerate_extra_fields"]:
        checks.append(
            {
                "check": "TOLERATES_NEW_FIELDS",
                "passed": obs.accepted_extra_field,
                "detail": "a new field did not break it",
            }
        )
        checks.append(
            {
                "check": "REFUSES_MISSING_REQUIRED_FIELD",
                "passed": obs.refused_missing_field,
                "detail": "a missing required field was refused",
            }
        )
    if p.fault in ("NONE", "OUT_OF_ORDER", "DUPLICATE", "ENDPOINT_TIMEOUT"):
        checks.append(
            {
                "check": "ENDS_WITH_THE_RIGHT_OUTCOME",
                "passed": obs.final_outcome_seen == p.final_outcome,
                "detail": f"expected {p.final_outcome}, saw {obs.final_outcome_seen}",
            }
        )
    return {
        "persona": obs.persona,
        "seed": obs.seed,
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
    }


def report(observations: Sequence[Observation]) -> dict[str, Any]:
    """The score of a whole run. Deterministic: the same observations give the same digest, so a partner's QA can reproduce it. Not a certification."""
    rows = [score(o) for o in sorted(observations, key=lambda x: (x.persona, x.seed))]
    covered = {r["persona"] for r in rows}
    missing = [p.id for p in PERSONAS if p.id not in covered]
    body = {"catalog_version": CATALOG_VERSION, "results": rows, "missing_personas": missing}
    return {
        **body, "passed": all(r["passed"] for r in rows) and not missing, "personas_passed": sum(1 for r in rows if r["passed"]), "personas_total": len(PERSONAS),
        "report_digest": "sha-256:" + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "is_certification": False, "synthetic": True,
        "statement": "This is the result of one synthetic test run. It is not a certification and it does not approve production access.",
    }  # fmt: skip


def parse_observation(raw: Mapping[str, Any]) -> Observation:
    return Observation(
        persona=str(raw["persona"]), seed=str(raw["seed"]), plan_digest=str(raw["plan_digest"]), signatures_refused=int(raw.get("signatures_refused", 0)),
        acted_on_event_ids=tuple(str(x) for x in raw.get("acted_on_event_ids", ())), final_outcome_seen=raw.get("final_outcome_seen"), read_authoritative_state=bool(raw.get("read_authoritative_state", False)),
        acted_on_pending=bool(raw.get("acted_on_pending", False)), refused_missing_field=bool(raw.get("refused_missing_field", False)), accepted_extra_field=bool(raw.get("accepted_extra_field", False)),
    )  # fmt: skip


# ------------------------------------------------------------------------------------------------------------- what a stored run is worth

from datetime import datetime, timedelta

RUN_FRESH_FOR = timedelta(days=90)


def suite_status(latest: Mapping[str, Any] | None, now: datetime) -> dict[str, Any]:
    """Is the newest scored run a pass of the whole CURRENT catalog, and recent enough? A pass of an older catalog, or of only some personas, or from last year, does not count.

    This is the result of a synthetic test run. It is NOT a certification of the integration and NOT an approval to go live: those are separate decisions with their own evidence."""
    if latest is None:
        return {
            "status": "NOT_RUN",
            "met": False,
            "reason": "No scored run has been recorded for this application.",
            "approves_go_live": False,
        }
    body = latest["report"]
    problems: list[str] = []
    if not latest["passed"]:
        problems.append("THE_LATEST_RUN_DID_NOT_PASS")
    if body.get("catalog_version") != CATALOG_VERSION:
        problems.append("RUN_IS_AGAINST_AN_OLDER_CATALOG")
    if latest["personas_total"] != len(PERSONAS) or body.get("missing_personas"):
        problems.append("NOT_EVERY_PERSONA_WAS_RUN")
    if now - latest["created_at"] > RUN_FRESH_FOR:
        problems.append("THE_RUN_IS_OLDER_THAN_90_DAYS")
    return {
        "status": "PASSED" if not problems else "NOT_CURRENT", "met": not problems, "reasons": problems, "run_id": str(latest["run_id"]), "report_digest": latest["report_digest"],
        "personas_passed": latest["personas_passed"], "personas_total": latest["personas_total"], "ran_at": latest["created_at"].isoformat(), "approves_go_live": False,
        "statement": "A passed run shows this integration handled every synthetic scenario. It does not certify the integration and does not approve production.",
    }  # fmt: skip

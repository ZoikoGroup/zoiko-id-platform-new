"""The pure rules of the fraud graph (CR-168; ZID-CP-WF05 v2.0). No I/O.

A connected graph is an investigative HYPOTHESIS: it is never proof of fraud, never a reason for automatic rejection and never a score of a person. So:

- an OBSERVATION is a fact with a source, a time and a tenant: a token made from the company's own customer reference. Nothing else is invented, and a session's outcome is read from its decision;
- an EDGE links two of the company's sessions through one kind of observation. Each kind has a STRENGTH, and a weak or similarity-only link never supports an adverse step;
- a LEAD is what a deterministic rule makes of a group of edges: a place for a person to look, with its uncertain facts listed. It carries no finding until a person has adjudicated it in the review queue;
- an ACTION PROPOSAL names exact targets and is approved by someone else. This build never changes a session: an approved proposal is a signed instruction for the company's own people and systems.

Biometric matching and sharing between companies are OFF and cannot be turned on from here (`require_feature`).
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from zid_platform.modules.verify_api.domain.model import PartnerError

RULES_VERSION = "fraud-rules-1.0"
GRAPH_LIMIT_MAX = 200  # nodes in one read
GRAPH_DEPTH_MAX = 3
GROUP_MAX = 50  # sessions one token may link: more is cut and said so
OBSERVATION_TTL = timedelta(days=180)  # a market sets its own; there is no single global period
LEAD_EXPIRY = timedelta(days=60)
ACTION_MAX_TARGETS = 25
ACTION_MAX_AGE = timedelta(days=30)
NOTE_MIN, NOTE_MAX = 3, 1000
BASIS_MIN, BASIS_MAX = 3, 120


# ------------------------------------------------------------------------------- strength and the registry of edge kinds


class Strength(StrEnum):
    STRONG = "strong"  # same signed evidence token from a verified source: a lead after provenance, never proof
    CORROBORATING = "corroborating"  # supports another link; assess false joins and lifecycle
    WEAK = "weak"  # shared infrastructure: context only
    SIMILARITY = "similarity"  # a method, a threshold and an error rate must be shown: never a probability of fraud


RANK = {Strength.STRONG: 3, Strength.CORROBORATING: 2, Strength.SIMILARITY: 1, Strength.WEAK: 0}
SUPPORTS_ADVERSE = frozenset(
    {Strength.STRONG, Strength.CORROBORATING}
)  # the only strengths a human may rest a step on


@dataclass(frozen=True)
class EdgeKind:
    name: str
    strength: Strength
    method: str
    source: str  # the signal source that may produce it
    note: str  # what it does NOT establish


EDGE_KINDS: dict[str, EdgeKind] = {
    "EXACT_REFERENCE": EdgeKind(
        "EXACT_REFERENCE",
        Strength.CORROBORATING,
        "exact equality of a tenant-keyed token",
        "verify_sessions",
        "The company chose this reference. Repeat attempts under one reference are normal after a failed capture.",
    ),
    # Registered so the taxonomy is complete and the rule 'weak never stands alone' is testable; their sources are not enabled.
    "DEVICE_SHARED": EdgeKind(
        "DEVICE_SHARED",
        Strength.CORROBORATING,
        "provider-confirmed persistent device signal",
        "device_attestation",
        "Devices are shared and transferred.",
    ),
    "DOCUMENT_TOKEN": EdgeKind(
        "DOCUMENT_TOKEN",
        Strength.STRONG,
        "tokenized document identifier equality",
        "document_token",
        "A confirmed match is a lead, not a finding.",
    ),
    "IP_SHARED": EdgeKind(
        "IP_SHARED",
        Strength.WEAK,
        "same public address",
        "ip_network",
        "Households, dormitories, carriers and public Wi-Fi share addresses.",
    ),
    "NEAR_IMAGE": EdgeKind(
        "NEAR_IMAGE",
        Strength.SIMILARITY,
        "image similarity above a stated threshold",
        "image_similarity",
        "A similarity is not a probability and not an identity.",
    ),
}


@dataclass(frozen=True)
class Source:
    name: str
    enabled: bool
    qualified: bool
    purpose: str
    reason: str


SOURCES: dict[str, Source] = {
    "verify_sessions": Source(
        "verify_sessions",
        True,
        True,
        "fraud_investigation",
        "The company's own Verify API sessions and results.",
    ),
    "device_attestation": Source(
        "device_attestation",
        False,
        False,
        "fraud_investigation",
        "Not connected to Verify API sessions yet.",
    ),
    "ip_network": Source(
        "ip_network",
        False,
        False,
        "fraud_investigation",
        "Not collected. An address alone never links people.",
    ),
    "document_token": Source(
        "document_token",
        False,
        False,
        "fraud_investigation",
        "Not collected by Verify API sessions yet.",
    ),
    "image_similarity": Source(
        "image_similarity",
        False,
        False,
        "fraud_investigation",
        "Needs an independent accuracy evaluation first.",
    ),
}
OBSERVATION_KINDS = {
    "CUSTOMER_REFERENCE": "verify_sessions"
}  # a session's outcome is read from its decision, never copied


def source_problem(source: str, kind: str) -> str | None:
    """Why an observation from this source is quarantined, or None. A disabled or unqualified source produces no production link."""
    src = SOURCES.get(source)
    if src is None:
        return "UNKNOWN_SOURCE"
    if not (src.enabled and src.qualified):
        return "SOURCE_NOT_ENABLED"
    if OBSERVATION_KINDS.get(kind) != source:
        return "KIND_NOT_FROM_SOURCE"
    return None


# ------------------------------------------------------------------------------- features that stay OFF


FEATURES: dict[str, str] = {
    "biometric_1n": "Face matching against many people needs a necessity case, a privacy assessment, a vendor audit and an accuracy test. None exists.",
    "face_blocklist": "A face list is special-category processing. It stays off until separately approved.",
    "cross_tenant": "Nothing is shared or compared between companies. A hash of an identifier is still personal data.",
}


def require_feature(name: str) -> None:
    """Deny-only. There is no setting, field or approval in this build that turns one of these on; the answer never depends on the request."""
    raise PartnerError(
        "SENSITIVE_FEATURE_DISABLED", FEATURES.get(name, "This capability is not available.")
    )


def gates() -> list[dict[str, Any]]:
    return [{"feature": k, "state": "OFF", "reason": v} for k, v in FEATURES.items()]


# ------------------------------------------------------------------------------- tokens


def reference_token(case_hash: bytes) -> str:
    """The observation token for a customer reference, made from the company-scoped reference hash the disputes feature already keeps (never from the reference itself), so an erasure request can find and remove it."""
    return hashlib.sha256(b"verify-api-fraud:" + case_hash).hexdigest()


def observation_digest(
    source: str, source_event_id: str, kind: str, token: str | None, observed_at: datetime
) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps([source, source_event_id, kind, token, observed_at.isoformat()]).encode()
        ).hexdigest()
    )


# ------------------------------------------------------------------------------- edges


def order_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def best_strength(strengths: Iterable[str]) -> Strength | None:
    got = [Strength(s) for s in strengths]
    return max(got, key=lambda s: RANK[s]) if got else None


def only_weak(strengths: Iterable[str]) -> bool:
    """True when there is no link a person may rest a step on. A node with no link at all is not 'only weak': it simply has none."""
    got = [Strength(s) for s in strengths]
    return bool(got) and not any(s in SUPPORTS_ADVERSE for s in got)


def check_challenge(action: str, reason: str | None) -> tuple[str, str]:
    if action not in ("exclude", "restore"):
        raise PartnerError("VALIDATION_FAILED", "A link can be excluded or restored.")
    clean = " ".join((reason or "").split())
    if not NOTE_MIN <= len(clean) <= NOTE_MAX:
        raise PartnerError("NOTE_REQUIRED", "Say why, in a few words: the next person reads it.")
    return action, clean


def explain_edge(kind: str, *, first_seen: datetime, last_seen: datetime, n: int) -> str:
    """Computed from the cited edge only: what it says and what it does not. No narration, no cause is guessed."""
    k = EDGE_KINDS[kind]
    return f"{k.method}; {n} observation{'s' if n != 1 else ''} between {first_seen:%Y-%m-%d} and {last_seen:%Y-%m-%d}. Strength: {k.strength.value}. It does not show: {k.note}"


# ------------------------------------------------------------------------------- the lead rules (deterministic for one rules version)


class LeadStatus(StrEnum):
    OPEN = "OPEN"
    IN_ANALYSIS = "IN_ANALYSIS"
    ESCALATED = "ESCALATED"
    CLEARED = "CLEARED"
    EXPIRED = "EXPIRED"


class Finding(StrEnum):
    UNREVIEWED = "UNREVIEWED"
    CONFIRMED = "CONFIRMED"
    NO_FINDING = "NO_FINDING"
    INCONCLUSIVE = "INCONCLUSIVE"


L = LeadStatus
LEAD_TRANSITIONS: dict[LeadStatus, frozenset[LeadStatus]] = {
    L.OPEN: frozenset({L.IN_ANALYSIS, L.CLEARED, L.ESCALATED, L.EXPIRED}),
    L.IN_ANALYSIS: frozenset({L.CLEARED, L.ESCALATED, L.EXPIRED}),
    L.ESCALATED: frozenset(),  # only a person's finding after the review case is final moves it on
    L.CLEARED: frozenset(),
    L.EXPIRED: frozenset(),
}


def check_lead_transition(src: LeadStatus, dst: LeadStatus) -> LeadStatus:
    if dst not in LEAD_TRANSITIONS[src]:
        raise PartnerError(
            "LEAD_STATE_INVALID",
            f"A lead that is {src.value.lower().replace('_', ' ')} cannot become {dst.value.lower().replace('_', ' ')}.",
        )
    return dst


@dataclass(frozen=True)
class SessionFact:
    session_id: str
    application_id: str
    created_at: datetime
    outcome: str | None  # verified | not_verified | inconclusive | None while undecided


@dataclass(frozen=True)
class Candidate:
    rule_id: str
    risk_class: str
    nodes: tuple[str, ...]
    uncertain: tuple[str, ...]
    summary: dict[str, Any]


RISK = ("LOW", "MODERATE", "HIGH")


def evaluate_group(facts: Sequence[SessionFact], *, truncated: bool = False) -> Candidate | None:
    """One group of sessions sharing a reference token. Identical facts give the identical answer for this rules version.

    REPEAT_AFTER_REFUSAL: three or more sessions under one reference, two or more of them not verified.
    MULTI_APPLICATION: one reference used by two or more applications.
    The most serious applicable rule is the lead. Nothing here is a finding.
    """
    if len(facts) < 2:
        return None
    ordered = sorted(facts, key=lambda f: (f.created_at, f.session_id))
    refused = sum(1 for f in ordered if f.outcome == "not_verified")
    apps = {f.application_id for f in ordered}
    undecided = sum(1 for f in ordered if f.outcome is None)
    uncertain = [
        "The company chose this reference.",
        "Repeat attempts are normal after a failed capture.",
    ]
    if undecided:
        uncertain.append(
            f"{undecided} session{'s have' if undecided != 1 else ' has'} no result yet."
        )
    if truncated:
        uncertain.append(
            f"More than {GROUP_MAX} sessions share this reference: only the first {GROUP_MAX} are shown."
        )
    nodes = tuple(f.session_id for f in ordered)
    summary = {"sessions": len(ordered), "not_verified": refused, "applications": len(apps)}
    if len(ordered) >= 3 and refused >= 2:
        risk = "HIGH" if refused >= 4 else "MODERATE"
        return Candidate("REPEAT_AFTER_REFUSAL", risk, nodes, tuple(uncertain), summary)
    if len(apps) >= 2:
        return Candidate("MULTI_APPLICATION", "LOW", nodes, tuple(uncertain), summary)
    return None


def snapshot_hash(
    node_ids: Iterable[str], edge_ids: Iterable[str], rules_version: str = RULES_VERSION
) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps({"n": sorted(node_ids), "e": sorted(edge_ids), "r": rules_version}).encode()
        ).hexdigest()
    )


def group_key(rule_id: str, token: str) -> str:
    return hashlib.sha256(f"{rule_id}:{token}".encode()).hexdigest()


# ------------------------------------------------------------------------------- bounded reads


def check_read_limits(depth: int, limit: int) -> tuple[int, int]:
    if not 1 <= depth <= GRAPH_DEPTH_MAX or not 1 <= limit <= GRAPH_LIMIT_MAX:
        raise PartnerError(
            "QUERY_LIMIT_EXCEEDED",
            f"A read goes at most {GRAPH_DEPTH_MAX} steps and returns at most {GRAPH_LIMIT_MAX} sessions.",
        )
    return depth, limit


def expand(
    seed: str, edges: Iterable[tuple[str, str, str]], *, depth: int, limit: int
) -> tuple[list[str], list[str], bool]:
    """Breadth-first from the seed over (edge_id, a, b), nearest first, never more than `limit` nodes. Returns (nodes, edge ids among them, whether more exist). Deterministic: ties break by id."""
    adj: dict[str, list[tuple[str, str]]] = {}
    for eid, a, b in edges:
        adj.setdefault(a, []).append((b, eid))
        adj.setdefault(b, []).append((a, eid))
    seen = {seed: 0}
    order = [seed]
    frontier = [seed]
    more = False
    for step in range(1, depth + 1):
        nxt: list[str] = []
        for node in frontier:
            for other, _ in sorted(adj.get(node, [])):
                if other in seen:
                    continue
                if len(order) >= limit:
                    more = True
                    continue
                seen[other] = step
                order.append(other)
                nxt.append(other)
        frontier = sorted(nxt)
    if not more:
        more = any(other not in seen for node in frontier for other, _ in adj.get(node, []))
    inside = set(order)
    kept = sorted({eid for eid, a, b in edges if a in inside and b in inside})
    return order, kept, more


# ------------------------------------------------------------------------------- finding and action proposals


def check_finding(value: str) -> Finding:
    try:
        f = Finding(value)
    except ValueError as e:
        raise PartnerError(
            "VALIDATION_FAILED", "A finding is CONFIRMED, NO_FINDING or INCONCLUSIVE."
        ) from e
    if f is Finding.UNREVIEWED:
        raise PartnerError(
            "VALIDATION_FAILED", "A finding is CONFIRMED, NO_FINDING or INCONCLUSIVE."
        )
    return f


def finding_problems(
    *, lead_status: LeadStatus, case_final: bool, person: str, case_people: Iterable[str]
) -> list[str]:
    """A finding rests on an adjudicated review case, and is recorded by someone who did not take part in it."""
    out: list[str] = []
    if lead_status is not LeadStatus.ESCALATED:
        out.append("LEAD_NOT_ESCALATED")
    elif not case_final:
        out.append("CASE_NOT_FINAL")
    if person in set(case_people):
        out.append("REVIEWER_NOT_INDEPENDENT")
    return out


ACTION_TYPES = ("temporary_hold", "enhanced_review")


def check_basis(text: str | None, label: str) -> str:
    clean = " ".join((text or "").split())
    if not BASIS_MIN <= len(clean) <= BASIS_MAX:
        raise PartnerError(
            "LEGAL_BASIS_REQUIRED",
            f"Give the {label}: a reference to the policy or instruction this rests on.",
        )
    return clean


@dataclass(frozen=True)
class TargetFacts:
    session_id: str
    in_lead: bool
    strengths: tuple[str, ...]  # of the target's ACTIVE edges inside the lead


def proposal_problems(
    *,
    finding: Finding,
    targets: Sequence[str],
    facts: Mapping[str, TargetFacts],
    proposal_type: str,
    expires_at: datetime,
    now: datetime,
) -> list[str]:
    out: list[str] = []
    if finding is not Finding.CONFIRMED:
        out.append("NO_CONFIRMED_FINDING")
    if proposal_type not in ACTION_TYPES:
        out.append("UNKNOWN_ACTION_TYPE")
    if not targets:
        out.append("NO_TARGETS")
    if len(set(targets)) != len(targets):
        out.append("DUPLICATE_TARGET")
    if len(targets) > ACTION_MAX_TARGETS:
        out.append("TOO_MANY_TARGETS")
    if not now < expires_at <= now + ACTION_MAX_AGE:
        out.append("BAD_EXPIRY")
    for t in targets:
        f = facts.get(t)
        if f is None or not f.in_lead:
            out.append(f"TARGET_NOT_IN_LEAD:{t}")
        elif only_weak(f.strengths):
            out.append(f"TARGET_WEAK_ONLY:{t}")
        elif not f.strengths:
            out.append(f"TARGET_UNLINKED:{t}")
    return out


def manifest_checksum(
    *,
    tenant_id: str,
    lead_id: str,
    proposal_type: str,
    targets: Sequence[str],
    basis_ref: str,
    impact_ref: str,
    snapshot: str,
    expires_at: datetime,
) -> str:
    """Over the targets IN THE ORDER signed: a different order or a different target is a different manifest and needs its own approval."""
    body = {
        "t": tenant_id,
        "l": lead_id,
        "p": proposal_type,
        "x": list(targets),
        "b": basis_ref,
        "i": impact_ref,
        "s": snapshot,
        "e": expires_at.astimezone(UTC).isoformat(),
    }
    return "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def approval_problems(
    *,
    status: str,
    maker: str,
    approver: str,
    expires_at: datetime,
    now: datetime,
    supplied: str | None,
    stored: str,
    still_linked: bool,
) -> list[str]:
    out: list[str] = []
    if status != "PROPOSED":
        out.append("ACTION_NOT_PROPOSED")
    if approver == maker:
        out.append("ACTION_APPROVAL_REQUIRED")  # nobody approves their own proposal
    if expires_at <= now:
        out.append("ACTION_EXPIRED")
    if supplied != stored:
        out.append(
            "ACTION_TARGET_SET_CHANGED"
        )  # the approver must name the manifest they looked at
    if not still_linked:
        out.append("ACTION_TARGET_SET_CHANGED")
    return sorted(set(out))


def check_note(text: str | None, *, required: bool = True) -> str:
    clean = " ".join((text or "").split())
    if not clean and not required:
        return ""
    if not NOTE_MIN <= len(clean) <= NOTE_MAX:
        raise PartnerError("NOTE_REQUIRED", "Write a few words: the next person reads it.")
    return clean


def check_version(current: int, expected: int | None) -> None:
    if expected is None or expected != current:
        raise PartnerError(
            "GRAPH_SNAPSHOT_STALE",
            "It changed since you looked. Reload and look again; nothing was overwritten.",
        )


# ------------------------------------------------------------------------------- the query builder (typed, allowlisted, never free text)

QUERY_KEYS = frozenset(
    {
        "edge_types",
        "min_strength",
        "min_sessions",
        "min_not_verified",
        "multi_application",
        "window_days",
        "risk_class",
        "require_corroboration",
    }
)
QUERY_SCAN_MAX = 5000  # groups one query may look at
QUERY_STATUSES = ("DRAFT", "IN_REVIEW", "PUBLISHED", "REJECTED", "RETIRED")


def _int(raw: Mapping[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    v = raw.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise PartnerError("VALIDATION_FAILED", f"{key} is a whole number from {lo} to {hi}.")
    return v


def compile_query(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A query is a handful of typed fields. There is no free text, no search by a person's details and no way to name a field this build does not hold.

    A weak or similarity-only join (a shared address, a recycled number, a payment-card range, a look-alike image) is refused: it may only appear TOGETHER with a link that can support a step, and only when the query says it needs both.
    """
    if not isinstance(raw, Mapping):
        raise PartnerError("VALIDATION_FAILED", "A query is a set of named fields.")
    unknown = sorted(set(raw) - QUERY_KEYS)
    if unknown:
        raise PartnerError(
            "UNSUPPORTED_FIELD",
            "These fields are not part of a query: "
            + ", ".join(str(u)[:30] for u in unknown)
            + ". A query cannot search by a person's details or by free text.",
        )
    types = raw.get("edge_types", ["EXACT_REFERENCE"])
    if (
        not isinstance(types, list)
        or not types
        or not all(isinstance(x, str) and x in EDGE_KINDS for x in types)
    ):
        raise PartnerError(
            "VALIDATION_FAILED",
            "edge_types is a list of link types: " + ", ".join(EDGE_KINDS) + ".",
        )
    types = sorted(set(types))
    weak = [x for x in types if EDGE_KINDS[x].strength in (Strength.WEAK, Strength.SIMILARITY)]
    both = bool(raw.get("require_corroboration", False))
    if weak and (len(weak) == len(types) or not both):
        raise PartnerError(
            "PROHIBITED_JOIN",
            "A shared address, recycled number, payment-card range or look-alike image cannot join people alone. Add a link that can support a step and set require_corroboration.",
        )
    strength = raw.get("min_strength", Strength.CORROBORATING.value)
    if strength not in (Strength.CORROBORATING.value, Strength.STRONG.value):
        raise PartnerError(
            "PROHIBITED_JOIN",
            "A query starts from a corroborating or strong link, never a weaker one.",
        )
    risk = raw.get("risk_class", "MODERATE")
    if risk not in RISK:
        raise PartnerError("VALIDATION_FAILED", "risk_class is LOW, MODERATE or HIGH.")
    multi = raw.get("multi_application", False)
    if not isinstance(multi, bool) or not isinstance(both, bool):
        raise PartnerError(
            "VALIDATION_FAILED", "multi_application and require_corroboration are true or false."
        )
    return {
        "edge_types": types,
        "min_strength": strength,
        "min_sessions": _int(raw, "min_sessions", 3, 2, GROUP_MAX),
        "min_not_verified": _int(raw, "min_not_verified", 0, 0, GROUP_MAX),
        "multi_application": multi,
        "window_days": _int(raw, "window_days", 30, 1, 365),
        "risk_class": risk,
        "require_corroboration": both,
    }


def re_key(key: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,38}[a-z0-9]", key))


def query_checksum(params: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def publish_problems(params: Mapping[str, Any]) -> list[str]:
    """A query that names a link type whose source is not enabled would never find anything: it is refused rather than published as if it worked."""
    return sorted(
        {
            f"SOURCE_NOT_ENABLED:{EDGE_KINDS[t].source}"
            for t in params["edge_types"]
            if not SOURCES[EDGE_KINDS[t].source].enabled
        }
    )


def query_rule_id(key: str) -> str:
    return "QUERY_" + key.upper().replace("-", "_")


def evaluate_query(
    params: Mapping[str, Any],
    key: str,
    facts: Sequence[SessionFact],
    best: Strength | None,
    *,
    now: datetime,
    truncated: bool = False,
) -> Candidate | None:
    """One group of sessions sharing a token, judged by a published query. Pure, so a replay and the real run agree."""
    cutoff = now - timedelta(days=int(params["window_days"]))
    inside = sorted(
        (x for x in facts if x.created_at >= cutoff), key=lambda x: (x.created_at, x.session_id)
    )
    if (
        len(inside) < int(params["min_sessions"])
        or best is None
        or RANK[best] < RANK[Strength(params["min_strength"])]
    ):
        return None
    refused = sum(1 for x in inside if x.outcome == "not_verified")
    apps = {x.application_id for x in inside}
    if refused < int(params["min_not_verified"]) or (params["multi_application"] and len(apps) < 2):
        return None
    uncertain = [
        "The company chose this reference.",
        "This lead came from a custom query: read its parameters before acting.",
    ]
    if truncated:
        uncertain.append(
            f"More than {GROUP_MAX} sessions share this reference: only the first {GROUP_MAX} are shown."
        )
    return Candidate(
        query_rule_id(key),
        str(params["risk_class"]),
        tuple(x.session_id for x in inside),
        tuple(uncertain),
        {"sessions": len(inside), "not_verified": refused, "applications": len(apps)},
    )


def replay_summary(candidates: Sequence[Candidate | None], scanned: int) -> dict[str, Any]:
    """What a replay shows: how many leads it WOULD open and how much review work that is. It opened none."""
    hits = [c for c in candidates if c is not None]
    return {
        "groups_scanned": scanned,
        "would_open": len(hits),
        "by_risk": {k: sum(1 for c in hits if c.risk_class == k) for k in RISK},
        "review_workload_sessions": sum(len(c.nodes) for c in hits),
        "side_effects": {
            "leads_opened": 0,
            "provider_calls": 0,
            "charges": 0,
            "decisions_changed": 0,
        },
    }


# ------------------------------------------------------------------------------- the timeline and the as-of picture


def edge_active_at(
    edge: Mapping[str, Any], history: Sequence[tuple[datetime, str]], at: datetime
) -> bool:
    """Was this link part of what the company could see at `at`? From what was recorded: when it was learned, every exclusion and restoration, when it expired. A link whose data was ERASED is never reproduced."""
    if edge["status_reason"] == "ERASED" and edge["status"] == "RETRACTED":
        return False
    if edge["created_at"] > at or edge["expires_at"] <= at:
        return False
    state = "ACTIVE"
    for when, kind in sorted(history):
        if when > at:
            break
        state = (
            "EXCLUDED"
            if kind == "edge_excluded"
            else "ACTIVE"
            if kind == "edge_restored"
            else state
        )
    return state == "ACTIVE"


# ------------------------------------------------------------------------------- the company's own blocklist

BLOCK_REASONS: dict[str, int] = {
    "CONFIRMED_REPEAT_ABUSE": 180,
    "CONFIRMED_COORDINATED_ABUSE": 365,
}  # reason -> days an entry lasts before it must be looked at again
BLOCK_REVIEW_EVERY = timedelta(days=60)
BLOCK_STATUSES = ("PROPOSED", "ACTIVE", "CHALLENGED", "REMOVED", "EXPIRED", "REJECTED")


def check_block_reason(code: str | None) -> str:
    if code not in BLOCK_REASONS:
        raise PartnerError(
            "VALIDATION_FAILED", "Choose a reason: " + ", ".join(BLOCK_REASONS) + "."
        )
    return str(code)


def admission_problems(
    *,
    finding: Finding,
    lead_status: LeadStatus,
    has_token: bool,
    strengths: Iterable[str],
    already_listed: bool,
) -> list[str]:
    """An entry rests on a confirmed finding about a reference the graph still holds, and on a link that can support a step. A look-alike or a shared address alone never makes an entry."""
    out: list[str] = []
    if finding is not Finding.CONFIRMED or lead_status is not LeadStatus.ESCALATED:
        out.append("NO_CONFIRMED_FINDING")
    if not has_token:
        out.append("SOURCE_ERASED")
    if only_weak(strengths) or not list(strengths):
        out.append("SIMILARITY_ONLY")
    if already_listed:
        out.append("ALREADY_LISTED")
    return sorted(set(out))


def block_effective(status: str, expires_at: datetime, now: datetime) -> str:
    """An entry past its time stops counting at the next look, whatever its stored state says."""
    return "EXPIRED" if status in ("ACTIVE", "CHALLENGED") and expires_at <= now else status


# ------------------------------------------------------------------------------- data quality and honest numbers

FRESH_FOR = timedelta(hours=24)
MIN_CELL = 5  # fewer than this and a figure is not shown: it would single people out
MIN_LABELED = 30  # fewer labelled outcomes than this and no rate is called reliable


def freshness(last_ingested: datetime | None, now: datetime, *, sessions_recent: int) -> str:
    """OK, STALE (sessions arrived but nothing was read in), or NO_DATA. A quiet source is reported as missing data, never as 'less risk'."""
    if last_ingested is None:
        return "NO_DATA"
    if now - last_ingested <= FRESH_FOR or sessions_recent == 0:
        return "OK"
    return "STALE"


def shown(n: int) -> int | str:
    return n if n >= MIN_CELL else "fewer than " + str(MIN_CELL)


def label_quality(confirmed: int, cleared: int) -> dict[str, Any]:
    """Precision on adjudicated leads only, with the denominator beside it. Never a claim before there are enough labels."""
    n = confirmed + cleared
    return {
        "labelled": n,
        "confirmed": confirmed,
        "no_finding": cleared,
        "precision": round(confirmed / n, 3) if n else None,
        "reliable": n >= MIN_LABELED,
        "note": "Precision among leads a person adjudicated. Unreviewed leads are not counted as right or wrong."
        + (
            ""
            if n >= MIN_LABELED
            else f" Fewer than {MIN_LABELED} labelled leads: do not rely on this."
        ),
    }


# ------------------------------------------------------------------------------- how long a lead may wait

SLA_BY_RISK = {
    "HIGH": timedelta(hours=24),
    "MODERATE": timedelta(hours=72),
    "LOW": timedelta(days=7),
}
DUE_SOON_SHARE = 0.25  # the last quarter of the time


def lead_sla(
    *,
    status: str,
    finding: str,
    risk: str,
    created_at: datetime,
    now: datetime,
    data_missing: bool = False,
) -> dict[str, Any]:
    """Where a lead stands against its time. The clock starts when the lead was opened (the recorded event time), the same basis the review queue uses for its own age.

    DONE: nobody is waiting (cleared, expired, or a finding recorded). WITH_REVIEW: the lead was handed to the review queue, whose own clock now applies, so this one is paused, not breached.
    UNKNOWN: the data feeding the graph is missing, so a quiet queue proves nothing.
    """
    due = created_at + SLA_BY_RISK[risk]
    if finding != Finding.UNREVIEWED.value or status in (
        LeadStatus.CLEARED.value,
        LeadStatus.EXPIRED.value,
    ):
        state = "DONE"
    elif status == LeadStatus.ESCALATED.value:
        state = "WITH_REVIEW"
    elif data_missing:
        state = "UNKNOWN"
    elif now >= due:
        state = "BREACHED"
    elif (due - now) <= (due - created_at) * DUE_SOON_SHARE:
        state = "DUE_SOON"
    else:
        state = "RUNNING"
    return {
        "state": state,
        "due_at": due.isoformat(),
        "age_seconds": max(0, int((now - created_at).total_seconds())),
        "basis": "from the time the lead was opened",
    }


# ------------------------------------------------------------------------------- the masked export

EXPORT_PURPOSES = ("fraud_investigation", "audit_review")
EXPORT_FORMATS = ("csv", "svg")
EXPORT_TTL = timedelta(hours=24)
EXPORT_PER_DAY = 10  # per person: nobody downloads the graph in bulk


def check_export(fmt: str | None, purpose: str | None, reason: str | None) -> tuple[str, str, str]:
    if fmt not in EXPORT_FORMATS:
        raise PartnerError("VALIDATION_FAILED", "An export is csv or svg.")
    if purpose not in EXPORT_PURPOSES:
        raise PartnerError(
            "VALIDATION_FAILED", "Name the purpose: " + ", ".join(EXPORT_PURPOSES) + "."
        )
    return str(fmt), str(purpose), check_note(reason)


def csv_cell(value: Any) -> str:
    """A cell that a spreadsheet will not run as a formula."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def csv_line(cells: Sequence[Any]) -> str:
    """One row, quoted where a comma, a quote or a line break needs it."""
    out = []
    for c in cells:
        s = "" if c is None else str(c)
        out.append(
            '"' + s.replace('"', '""') + '"' if any(ch in s for ch in (",", '"', "\n", "\r")) else s
        )
    return ",".join(out)


def render_csv(g: Mapping[str, Any], meta: Mapping[str, Any]) -> str:
    rows: list[Sequence[Any]] = [
        [
            "record",
            "id",
            "a",
            "b",
            "type",
            "strength",
            "outcome",
            "country",
            "evidence",
            "first_seen",
            "last_seen",
        ]
    ]
    rows += [["META", csv_cell(k), csv_cell(v)] for k, v in meta.items()]
    rows += [
        [
            "NODE",
            csv_cell(n["id"]),
            "",
            "",
            "session",
            "",
            csv_cell(n.get("outcome")),
            csv_cell(n.get("country")),
        ]
        for n in g["nodes"]
    ]
    for e in g["edges"]:
        a, b = e["nodes"]
        rows.append(
            [
                "EDGE",
                csv_cell(e["edge_id"]),
                csv_cell(a),
                csv_cell(b),
                csv_cell(e["type"]),
                csv_cell(e["strength"]),
                "",
                "",
                csv_cell(" ".join(e["evidence_ref"])),
                csv_cell(e["first_seen"]),
                csv_cell(e["last_seen"]),
            ]
        )
    return "\n".join(csv_line(r) for r in rows) + "\n"


def render_svg(g: Mapping[str, Any], meta: Mapping[str, Any]) -> str:
    """A plain picture: the seed in the middle, the rest on a ring. Weak links are dashed. Everything typed into it is escaped."""
    nodes = list(g["nodes"])
    cx = cy = 260
    pos: dict[str, tuple[float, float]] = {}
    ring = [n for n in nodes if not n.get("is_seed")]
    for n in nodes:
        if n.get("is_seed"):
            pos[n["id"]] = (cx, cy)
    for i, n in enumerate(ring):
        ang = 2 * math.pi * i / max(1, len(ring))
        pos[n["id"]] = (cx + 200 * math.cos(ang), cy + 200 * math.sin(ang))
    esc = html.escape
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="520" height="560" viewBox="0 0 520 560" role="img" aria-label="Link picture">',
        "<title>Link picture (a hypothesis, not a finding)</title>",
    ]
    for e in g["edges"]:
        a, b = e["nodes"]
        (x1, y1), (x2, y2) = pos.get(a, (cx, cy)), pos.get(b, (cx, cy))
        dash = ' stroke-dasharray="4 4"' if e["strength"] in ("weak", "similarity") else ""
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#555" stroke-width="1.5"{dash}><title>{esc(str(e["type"]))}, {esc(str(e["strength"]))}</title></line>'
        )
    for n in nodes:
        x, y = pos[n["id"]]
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{12 if n.get("is_seed") else 8}" fill="{"#1b4f8a" if n.get("is_seed") else "#8a8a8a"}"><title>{esc(str(n["id"]))}: {esc(str(n.get("outcome", "")))}</title></circle>'
        )
    parts.append(
        f'<text x="10" y="530" font-size="11" font-family="sans-serif">Purpose {esc(str(meta["purpose"]))}. Snapshot {esc(str(meta["graph_snapshot_id"])[:23])}. Keep until {esc(str(meta["do_not_keep_after"]))}.</text>'
    )
    parts.append(
        '<text x="10" y="548" font-size="11" font-family="sans-serif">A link is a hypothesis, not a finding.</text></svg>'
    )
    return "".join(parts)


# ------------------------------------------------------------------------------- limits on how much a query may do

QUERY_TIME_BUDGET = 5.0  # seconds a replay may take
QUERY_EDGE_SCAN_MAX = 20000  # links one replay may read


def budget_exceeded(started: float, now: float, budget: float, edges_read: int) -> str | None:
    if now - started > budget:
        return "TIME"
    if edges_read > QUERY_EDGE_SCAN_MAX:
        return "FAN_OUT"
    return None


# ------------------------------------------------------------------------------- one source for every number

METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    "leads_raised": {
        "definition": "Leads a rule or a published query opened. A lead is a place to look, not a finding.",
        "denominator": "all leads",
        "window": "retention window",
        "source": "api_fraud_lead",
    },
    "under_investigation": {
        "definition": "Leads in analysis, or escalated to the review queue.",
        "denominator": "all leads",
        "window": "retention window",
        "source": "api_fraud_lead",
    },
    "confirmed_findings": {
        "definition": "Leads whose finding was recorded after a final review case, by someone who took no part in it.",
        "denominator": "all leads",
        "window": "retention window",
        "source": "api_fraud_lead",
    },
    "cleared": {
        "definition": "Leads an analyst cleared with a reason.",
        "denominator": "all leads",
        "window": "retention window",
        "source": "api_fraud_lead",
    },
    "no_outcome_yet": {
        "definition": "Open, in analysis, or escalated without a finding. Never counted as cleared or confirmed.",
        "denominator": "all leads",
        "window": "retention window",
        "source": "api_fraud_lead",
    },
    "labelled": {
        "definition": "Leads a person adjudicated as confirmed or no finding.",
        "denominator": "all leads",
        "window": "retention window",
        "source": "api_fraud_lead",
    },
    "precision": {
        "definition": "Confirmed over labelled. Not a rate of fraud; only how often a lead proved right once a person looked.",
        "denominator": "labelled leads",
        "window": "retention window",
        "source": "api_fraud_lead",
    },
    "queue_breached": {
        "definition": "Leads past their time that nobody has handed on or closed.",
        "denominator": "leads still waiting",
        "window": "now",
        "source": "api_fraud_lead + SLA by risk",
    },
    "queue_with_review": {
        "definition": "Leads handed to the review queue, whose own clock applies.",
        "denominator": "leads still waiting",
        "window": "now",
        "source": "api_fraud_lead",
    },
    "oldest_waiting_seconds": {
        "definition": "Age of the oldest lead still waiting, from the time it was opened.",
        "denominator": "-",
        "window": "now",
        "source": "api_fraud_lead.created_at",
    },
}

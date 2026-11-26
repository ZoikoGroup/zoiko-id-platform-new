"""Global coverage and deterministic provider routing (ZID-CP-WF09 v2.0; CR-171). Pure domain, no I/O.

One routing decision must be explainable from recorded inputs. So this is a resolver, not a model:

- HARD GATES, in the document's order (04.2): signed context, approved workflow and purpose, market legality, the whole data path, method and assurance,
  qualification and contract, device and SDK, capacity and health. A candidate that fails any gate is rejected with every failing reason, and no soft
  preference can bring it back: a cheaper provider that fails a gate is never chosen.
- SOFT OPTIMISATION only among the survivors: risk-adjusted cost per valid outcome (price, plus the cost of the reviews and retries a weak method causes,
  divided by how often it yields a valid outcome), then measured latency, then the provider id, so the same snapshot always gives the same answer.
- NO ROUTE is a result with a machine reason, never an applicant failure and never a fallback to something unapproved.
- ATTEMPTS are a state machine. A transport timeout is not "not processed". An unknown outcome is reconciled, never retried at another vendor. A vendor
  "success" is evidence, not an identity decision: only the owning verifier finalises.

Nothing here knows a provider exists. Capabilities, contracts and legal determinations arrive as facts with their dates; an unknown fact is "not eligible".
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Any

ALGORITHM_VERSION = "zoiko.route-resolver.v1"

# What a method proves, weakest to strongest. A document check never satisfies a requirement for a chip read.
METHOD_STRENGTH = {"DOCUMENT": 1, "DOCUMENT_FACE": 2, "NFC_CHIP": 3, "EID": 3, "REGISTRY": 2}
MAX_CANDIDATES = 200


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


def canonical(value: Any) -> bytes:
    """Stable bytes for a JSON-like value, so a digest means the same thing everywhere."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("ascii")


def digest(value: Any) -> str:
    return "sha-256:" + hashlib.sha256(canonical(value)).hexdigest()


# ----------------------------------------------------------------------------------------------------------------------------- the facts


class Gate(StrEnum):
    CONTEXT = "G1_CONTEXT"
    WORKFLOW = "G2_WORKFLOW_PURPOSE"
    MARKET = "G3_MARKET_LEGALITY"
    DATA_PATH = "G4_DATA_PATH"
    METHOD = "G5_METHOD_ASSURANCE"
    QUALIFICATION = "G6_QUALIFICATION_CONTRACT"
    DEVICE = "G7_DEVICE_SDK"
    CAPACITY = "G8_CAPACITY_HEALTH"


GATE_ORDER = tuple(Gate)


@dataclass(frozen=True, slots=True)
class RouteInput:
    """Everything the resolver may look at, frozen with the session (the snapshot). Nothing else is read."""

    tenant_id: str
    session_id: str
    workflow_version: str
    purpose: str
    issuer_country: str
    residence_country: str
    method: str
    assurance_floor: int
    channel: str  # "web", "ios", "android"
    sdk_version: str | None
    allowed_regions: frozenset[str]  # where the tenant's data may be processed
    now: datetime
    tenant_entitled_methods: frozenset[str]
    workflow_approved: bool
    market_open: bool  # a launched, current market release for this country, method and purpose
    context_verified: bool = True
    chip_required: bool = False


@dataclass(frozen=True, slots=True)
class Candidate:
    """One provider's capability for one method, as approved. Dates and approvals are facts the platform recorded."""

    provider_id: str
    method: str
    assurance: int
    countries: frozenset[str]
    channels: frozenset[str]
    min_sdk_version: tuple[int, ...] | None
    processing_regions: frozenset[str]
    subprocessor_regions: frozenset[str]
    legal_determination_ref: str | None  # a signed legal determination for this provider's whole data path
    legal_determination_valid_until: datetime | None
    state: str  # DRAFT / UNDER_REVIEW / APPROVED / SUSPENDED / EXPIRED / RETIRED
    qualified_until: datetime | None
    contract_until: datetime | None
    tenant_ids: frozenset[str] | None  # None = every entitled tenant; a set restricts the provider to those tenants
    health: str  # UP / DEGRADED / DOWN / UNKNOWN
    quota_remaining: int | None
    unit_cost_minor: int  # per attempt, in the contract currency's minor unit
    review_cost_minor: int  # expected extra cost of a manual review caused by this method
    valid_outcome_rate_bp: int  # basis points of attempts that end in a valid outcome (measured, not marketed)
    p95_latency_ms: int
    measured: bool  # False when the rate and latency come from the vendor's own claim: never used to rank
    # facts the platform works out itself (CR-172); each defaults to "nothing known against it" so older callers keep working
    entitlement_refusals: tuple[str, ...] = ()  # why this company's own entitlement for this provider and method does not cover the request
    data_flow_problems: tuple[str, ...] = ()  # why the provider's registered data-flow graph is not covered by its legal determination
    breaker: str = "CLOSED"  # CLOSED / HALF_OPEN / OPEN, from measured outcomes
    quality_quarantined: bool = False  # measured quality below the floor, whatever the uptime


@dataclass(frozen=True, slots=True)
class Rejection:
    provider_id: str
    gates: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Ranked:
    provider_id: str
    risk_adjusted_cost_minor: int
    p95_latency_ms: int
    rank: int


@dataclass(frozen=True, slots=True)
class RouteDecision:
    selected: str | None
    ranked: tuple[Ranked, ...]
    rejected: tuple[Rejection, ...]
    no_route_reasons: tuple[str, ...]
    algorithm_version: str
    input_digest: str
    snapshot_digest: str
    decision_digest: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected,
            "ranked": [{"provider_id": r.provider_id, "risk_adjusted_cost_minor": r.risk_adjusted_cost_minor, "p95_latency_ms": r.p95_latency_ms, "rank": r.rank} for r in self.ranked],
            "rejected": [{"provider_id": r.provider_id, "gates": list(r.gates), "reasons": list(r.reasons)} for r in self.rejected],
            "no_route_reasons": list(self.no_route_reasons),
            "algorithm_version": self.algorithm_version,
            "input_digest": self.input_digest,
            "snapshot_digest": self.snapshot_digest,
            "decision_digest": self.decision_digest,
            "notes": list(self.notes),
            "status": "ROUTE_SELECTED" if self.selected else "NO_ROUTE",
            "caveat": "A selected provider is not a verified identity. The attempt must be reserved, dispatched, reconciled and validated, and the owning verifier decides.",
        }


def _sdk_ok(have: str | None, need: tuple[int, ...] | None) -> bool:
    if need is None:
        return True
    if not have:
        return False
    try:
        parts = tuple(int(p) for p in have.split("."))
    except ValueError:
        return False
    return parts >= need


# ------------------------------------------------------------------------------------------------------------------------ hard gates


def input_failures(i: RouteInput) -> list[tuple[Gate, str]]:
    """Gates that do not depend on the provider: if one fails there is no route for anyone, and the reason is about the request."""
    out: list[tuple[Gate, str]] = []
    if not i.context_verified:
        out.append((Gate.CONTEXT, "CONTEXT_NOT_VERIFIED"))
    if not i.workflow_approved:
        out.append((Gate.WORKFLOW, "WORKFLOW_NOT_APPROVED"))
    if i.method not in i.tenant_entitled_methods:
        out.append((Gate.WORKFLOW, "METHOD_NOT_ENTITLED_FOR_TENANT"))
    if not i.market_open:
        out.append((Gate.MARKET, "MARKET_NOT_RELEASED"))
    return out


def candidate_failures(c: Candidate, i: RouteInput) -> list[tuple[Gate, str]]:
    """Every gate this candidate fails, in gate order. Empty means it may be considered. An unknown fact is a failure, never a pass."""
    out: list[tuple[Gate, str]] = []
    now = i.now
    # G4: the whole data path, not the hostname
    if not c.processing_regions or not c.processing_regions <= i.allowed_regions:
        out.append((Gate.DATA_PATH, "PROCESSING_REGION_NOT_ALLOWED"))
    if c.subprocessor_regions and not c.subprocessor_regions <= i.allowed_regions:
        out.append((Gate.DATA_PATH, "SUBPROCESSOR_REGION_NOT_ALLOWED"))
    out.extend((Gate.DATA_PATH, p) for p in c.data_flow_problems)
    if not c.legal_determination_ref:
        out.append((Gate.DATA_PATH, "NO_LEGAL_DETERMINATION"))
    elif c.legal_determination_valid_until is None or c.legal_determination_valid_until <= now:
        out.append((Gate.DATA_PATH, "LEGAL_DETERMINATION_EXPIRED"))
    # G5: method and assurance. A weaker method never stands in for a stronger one.
    if c.method != i.method:
        out.append((Gate.METHOD, "METHOD_NOT_OFFERED"))
    if i.chip_required and METHOD_STRENGTH.get(c.method, 0) < METHOD_STRENGTH["NFC_CHIP"]:
        out.append((Gate.METHOD, "CHIP_REQUIRED_BUT_METHOD_IS_WEAKER"))
    if c.assurance < i.assurance_floor:
        out.append((Gate.METHOD, "ASSURANCE_BELOW_FLOOR"))
    if i.issuer_country.upper() not in c.countries:
        out.append((Gate.METHOD, "ISSUER_COUNTRY_NOT_COVERED"))
    # G6: qualification and contract
    if c.state != "APPROVED":
        out.append((Gate.QUALIFICATION, f"PROVIDER_{c.state}" if c.state in ("SUSPENDED", "EXPIRED", "RETIRED") else "PROVIDER_NOT_APPROVED"))
    if c.qualified_until is None or c.qualified_until <= now:
        out.append((Gate.QUALIFICATION, "QUALIFICATION_EXPIRED"))
    if c.contract_until is None or c.contract_until <= now:
        out.append((Gate.QUALIFICATION, "CONTRACT_EXPIRED"))
    if c.tenant_ids is not None and i.tenant_id not in c.tenant_ids:
        out.append((Gate.QUALIFICATION, "TENANT_NOT_ENTITLED_TO_PROVIDER"))
    out.extend((Gate.QUALIFICATION, f"ENTITLEMENT:{x}") for x in c.entitlement_refusals)
    # G7: device and SDK
    if i.channel not in c.channels:
        out.append((Gate.DEVICE, "CHANNEL_NOT_SUPPORTED"))
    if not _sdk_ok(i.sdk_version, c.min_sdk_version):
        out.append((Gate.DEVICE, "SDK_VERSION_NOT_SUPPORTED"))
    # G8: capacity and health. Unknown health is not healthy.
    if c.health in ("DOWN", "UNKNOWN"):
        out.append((Gate.CAPACITY, f"PROVIDER_HEALTH_{c.health}"))
    if c.quota_remaining is not None and c.quota_remaining <= 0:
        out.append((Gate.CAPACITY, "QUOTA_EXHAUSTED"))
    if c.breaker == "OPEN":
        out.append((Gate.CAPACITY, "BREAKER_OPEN"))
    if c.quality_quarantined:
        out.append((Gate.CAPACITY, "QUALITY_QUARANTINED"))
    return out


# -------------------------------------------------------------------------------------------------------------------- soft objective


def risk_adjusted_cost_minor(c: Candidate) -> int:
    """(price + expected review cost) divided by the share of attempts that end in a valid outcome. Cheap and unreliable loses to dearer and reliable.

    A provider whose rate is only the vendor's own claim is ranked as if its valid-outcome rate were half of the claim: a claim is not a measurement."""
    rate = Decimal(max(1, c.valid_outcome_rate_bp)) / Decimal(10000)
    if not c.measured:
        rate = rate / Decimal(2)
    cost = (Decimal(c.unit_cost_minor) + Decimal(c.review_cost_minor)) / rate
    return int(cost.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def snapshot_of(candidates: Iterable[Candidate]) -> list[dict[str, Any]]:
    return [
        {
            "provider_id": c.provider_id, "method": c.method, "assurance": c.assurance, "countries": sorted(c.countries), "channels": sorted(c.channels),
            "min_sdk": list(c.min_sdk_version) if c.min_sdk_version else None, "regions": sorted(c.processing_regions), "subprocessor_regions": sorted(c.subprocessor_regions),
            "legal": c.legal_determination_ref, "legal_until": c.legal_determination_valid_until.isoformat() if c.legal_determination_valid_until else None, "state": c.state,
            "qualified_until": c.qualified_until.isoformat() if c.qualified_until else None, "contract_until": c.contract_until.isoformat() if c.contract_until else None,
            "tenants": sorted(c.tenant_ids) if c.tenant_ids is not None else None, "health": c.health, "quota": c.quota_remaining, "cost": c.unit_cost_minor,
            "review_cost": c.review_cost_minor, "rate_bp": c.valid_outcome_rate_bp, "p95": c.p95_latency_ms, "measured": c.measured, "entitlement": list(c.entitlement_refusals), "flow": list(c.data_flow_problems), "breaker": c.breaker, "quality_quarantined": c.quality_quarantined,
        }
        for c in sorted(candidates, key=lambda x: (x.provider_id, x.method))
    ]  # fmt: skip


def input_of(i: RouteInput) -> dict[str, Any]:
    return {
        "tenant_id": i.tenant_id, "session_id": i.session_id, "workflow_version": i.workflow_version, "purpose": i.purpose, "issuer_country": i.issuer_country,
        "residence_country": i.residence_country, "method": i.method, "assurance_floor": i.assurance_floor, "channel": i.channel, "sdk_version": i.sdk_version,
        "allowed_regions": sorted(i.allowed_regions), "now": i.now.isoformat(), "entitled_methods": sorted(i.tenant_entitled_methods), "workflow_approved": i.workflow_approved,
        "market_open": i.market_open, "context_verified": i.context_verified, "chip_required": i.chip_required,
    }  # fmt: skip


def resolve(i: RouteInput, candidates: Sequence[Candidate], *, exclude: Iterable[str] = (), algorithm_version: str = ALGORITHM_VERSION) -> RouteDecision:
    """Pick a provider, or say why none can be picked. Same input and snapshot, same answer, byte for byte."""
    excluded = set(exclude)
    pool = [c for c in candidates if c.provider_id not in excluded][:MAX_CANDIDATES]
    in_digest, snap_digest = digest(input_of(i)), digest(snapshot_of(pool))
    notes: list[str] = []
    if excluded:
        notes.append("EXCLUDED_BEFORE_RESOLVING:" + ",".join(sorted(excluded)))

    shared = input_failures(i)
    rejected: list[Rejection] = []
    ranked: list[Ranked] = []
    survivors: list[Candidate] = []
    for c in sorted(pool, key=lambda x: (x.provider_id, x.method)):
        fails = shared + candidate_failures(c, i)
        if fails:
            rejected.append(Rejection(c.provider_id, tuple(sorted({g.value for g, _ in fails})), tuple(sorted({r for _, r in fails}))))
        else:
            survivors.append(c)
    if shared:
        reasons = tuple(sorted({r for _, r in shared}))
        return _decision(None, [], rejected, reasons, algorithm_version, in_digest, snap_digest, notes)
    if not survivors:
        reasons = tuple(sorted({r for rej in rejected for r in rej.reasons})) or ("NO_PROVIDER_REGISTERED",)
        return _decision(None, [], rejected, reasons, algorithm_version, in_digest, snap_digest, notes)

    unmeasured = [c.provider_id for c in survivors if not c.measured]
    if unmeasured:
        notes.append("RANKED_ON_CLAIMED_NOT_MEASURED:" + ",".join(sorted(unmeasured)))
    order = sorted(survivors, key=lambda c: (risk_adjusted_cost_minor(c), c.p95_latency_ms, c.provider_id))
    ranked = [Ranked(c.provider_id, risk_adjusted_cost_minor(c), c.p95_latency_ms, n + 1) for n, c in enumerate(order)]
    return _decision(order[0].provider_id, ranked, rejected, (), algorithm_version, in_digest, snap_digest, notes)


def _decision(selected: str | None, ranked: list[Ranked], rejected: list[Rejection], reasons: tuple[str, ...], version: str, in_d: str, snap_d: str, notes: list[str]) -> RouteDecision:
    body = {"selected": selected, "ranked": [(r.provider_id, r.risk_adjusted_cost_minor, r.p95_latency_ms) for r in ranked], "rejected": [(r.provider_id, r.gates, r.reasons) for r in rejected], "reasons": reasons, "version": version, "input": in_d, "snapshot": snap_d}
    return RouteDecision(selected, tuple(ranked), tuple(rejected), reasons, version, in_d, snap_d, digest(body), tuple(notes))


# ---------------------------------------------------------------------------------------------------------------- attempt state machine


class AttemptState(StrEnum):
    ROUTE_REQUESTED = "ROUTE_REQUESTED"
    NO_ROUTE = "NO_ROUTE"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    ELIGIBLE = "ELIGIBLE"
    CANCELLED = "CANCELLED"
    ATTEMPT_RESERVED = "ATTEMPT_RESERVED"
    FAILED_BEFORE_DISPATCH = "FAILED_BEFORE_DISPATCH"
    DISPATCHED = "DISPATCHED"
    PENDING = "PENDING"
    RESULT_RECEIVED = "RESULT_RECEIVED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    RECONCILING = "RECONCILING"
    EXCEPTION_REVIEW = "EXCEPTION_REVIEW"
    EXPLICIT_RECAPTURE_APPROVED = "EXPLICIT_RECAPTURE_APPROVED"
    NORMALIZED = "NORMALIZED"
    EVIDENCE_REJECTED = "EVIDENCE_REJECTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FINALIZED = "FINALIZED"
    CONFIRMED_NOT_PROCESSED = "CONFIRMED_NOT_PROCESSED"


S = AttemptState
ALLOWED: dict[AttemptState, frozenset[AttemptState]] = {
    S.ROUTE_REQUESTED: frozenset({S.ELIGIBLE, S.NO_ROUTE, S.POLICY_BLOCKED}),
    S.ELIGIBLE: frozenset({S.ATTEMPT_RESERVED, S.CANCELLED}),
    S.ATTEMPT_RESERVED: frozenset({S.DISPATCHED, S.FAILED_BEFORE_DISPATCH}),
    S.DISPATCHED: frozenset({S.PENDING, S.RESULT_RECEIVED, S.OUTCOME_UNKNOWN}),
    S.PENDING: frozenset({S.RESULT_RECEIVED, S.OUTCOME_UNKNOWN}),
    S.OUTCOME_UNKNOWN: frozenset({S.RECONCILING, S.EXCEPTION_REVIEW, S.EXPLICIT_RECAPTURE_APPROVED}),
    S.RECONCILING: frozenset({S.RESULT_RECEIVED, S.CONFIRMED_NOT_PROCESSED, S.EXCEPTION_REVIEW}),
    S.RESULT_RECEIVED: frozenset({S.NORMALIZED, S.EVIDENCE_REJECTED, S.REVIEW_REQUIRED}),
    S.NORMALIZED: frozenset({S.FINALIZED, S.REVIEW_REQUIRED}),
    S.REVIEW_REQUIRED: frozenset({S.FINALIZED, S.EVIDENCE_REJECTED}),
}
TERMINAL = frozenset({S.NO_ROUTE, S.POLICY_BLOCKED, S.CANCELLED, S.FAILED_BEFORE_DISPATCH, S.EVIDENCE_REJECTED, S.FINALIZED, S.CONFIRMED_NOT_PROCESSED, S.EXPLICIT_RECAPTURE_APPROVED, S.EXCEPTION_REVIEW})
# states from which ANOTHER provider may safely be asked: nothing was, or provably was not, processed
SAFE_TO_REROUTE = frozenset({S.NO_ROUTE, S.CANCELLED, S.FAILED_BEFORE_DISPATCH, S.CONFIRMED_NOT_PROCESSED, S.EXPLICIT_RECAPTURE_APPROVED})
# states from which the SAME provider may be retried with the same idempotency key
SAFE_TO_RETRY_SAME_PROVIDER = frozenset({S.FAILED_BEFORE_DISPATCH, S.CONFIRMED_NOT_PROCESSED})
BILLABLE_FROM = frozenset({S.DISPATCHED})


class TransitionError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def transition(current: AttemptState, target: AttemptState, *, actor: str = "SYSTEM") -> AttemptState:
    """Move an attempt. Refused moves are named: nothing skips a state, and only the owning verifier may finalise."""
    if current in TERMINAL and target not in ALLOWED.get(current, frozenset()):
        raise TransitionError("ATTEMPT_ALREADY_TERMINAL", f"{current} is final")
    if target not in ALLOWED.get(current, frozenset()):
        raise TransitionError("ATTEMPT_TRANSITION_FORBIDDEN", f"{current} cannot become {target}")
    if target is S.FINALIZED and actor != "VERIFIER":
        raise TransitionError("ONLY_THE_OWNING_VERIFIER_FINALISES", "a provider result or a routing service cannot finalise a verification")
    return target


def classify_failure(*, dispatched: bool, transport_timeout: bool = False, provider_confirmed_not_processed: bool = False, late_result: bool = False, bad_result: bool = False, provider_outage: bool = False) -> str:
    """The failure class (WF-09.09 element 1). A timeout after dispatch is UNKNOWN, never "not processed"."""
    if not dispatched:
        return "PRE_DISPATCH"
    if bad_result:
        return "BAD_RESULT"
    if late_result:
        return "LATE_RESULT"
    if provider_confirmed_not_processed:
        return "DEFINITELY_NOT_PROCESSED"
    if transport_timeout:
        return "OUTCOME_UNKNOWN"
    if provider_outage:
        return "VENDOR_OUTAGE_AFTER_DISPATCH_UNKNOWN"
    return "PENDING"


FAILURE_NEXT: dict[str, AttemptState] = {
    "PRE_DISPATCH": S.FAILED_BEFORE_DISPATCH,
    "DEFINITELY_NOT_PROCESSED": S.CONFIRMED_NOT_PROCESSED,
    "OUTCOME_UNKNOWN": S.OUTCOME_UNKNOWN,
    "VENDOR_OUTAGE_AFTER_DISPATCH_UNKNOWN": S.OUTCOME_UNKNOWN,
    "LATE_RESULT": S.RESULT_RECEIVED,
    "BAD_RESULT": S.EVIDENCE_REJECTED,
    "PENDING": S.PENDING,
}


def may_ask_another_provider(state: AttemptState) -> bool:
    return state in SAFE_TO_REROUTE


@dataclass(frozen=True, slots=True)
class Handoff:
    kind: str  # "SAME_SESSION_SWITCH", "EVIDENCE_TRANSFER", "RECAPTURE", "RECONCILE"
    reasons: tuple[str, ...]


def failover_plan(state: AttemptState, *, contract_allows_transfer: bool = False, consent_covers_transfer: bool = False, data_flow_approved: bool = False, format_portable: bool = False) -> Handoff:
    """What may happen after a failure, without ever promising a seamless switch (WF-09.07, .09). Unknown outcomes are reconciled, never retried elsewhere."""
    if state in (S.OUTCOME_UNKNOWN, S.RECONCILING, S.DISPATCHED, S.PENDING):
        return Handoff("RECONCILE", ("OUTCOME_NOT_KNOWN",))
    if state in (S.NO_ROUTE, S.CANCELLED, S.FAILED_BEFORE_DISPATCH):
        return Handoff("SAME_SESSION_SWITCH", ())  # nothing was sent, so nothing needs carrying over
    if state in (S.CONFIRMED_NOT_PROCESSED, S.EXPLICIT_RECAPTURE_APPROVED):
        missing = tuple(
            code for code, ok in (("CONTRACT_DOES_NOT_ALLOW_TRANSFER", contract_allows_transfer), ("CONSENT_DOES_NOT_COVER_TRANSFER", consent_covers_transfer), ("DATA_FLOW_NOT_APPROVED", data_flow_approved), ("FORMAT_NOT_PORTABLE", format_portable)) if not ok
        )
        return Handoff("EVIDENCE_TRANSFER", ()) if not missing else Handoff("RECAPTURE", missing)
    return Handoff("RECONCILE", ("STATE_NOT_A_FAILURE",))


# --------------------------------------------------------------------------------------------------------------- coverage and claims


@dataclass(frozen=True, slots=True)
class Cell:
    """One deployable market-method-provider cell, as the registry holds it. A count is only ever made from these."""

    cell_id: str
    issuer_country: str
    specimen_id: str
    method: str
    tier: str  # LABORATORY / CONTRACTED / QUALIFIED / ENABLED / RESTRICTED / DEGRADED
    evidence_ref: str | None
    approved: bool
    expires_at: datetime | None


TIERS = ("LABORATORY", "CONTRACTED", "QUALIFIED", "ENABLED", "RESTRICTED", "DEGRADED")


def cell_status(c: Cell, now: datetime) -> str:
    """UNKNOWN unless the evidence is present, approved and current. Never AVAILABLE for an expired or unapproved cell."""
    if c.tier not in TIERS:
        return "UNKNOWN"
    if not c.evidence_ref or not c.approved:
        return "UNKNOWN" if c.tier in ("ENABLED", "QUALIFIED") else c.tier
    if c.expires_at is None or c.expires_at <= now:
        return "RESTRICTED" if c.tier in ("ENABLED", "QUALIFIED") else c.tier
    return c.tier


def coverage_counts(cells: Iterable[Cell], now: datetime, *, method: str | None = None) -> dict[str, Any]:
    """Deduplicated countries and specimens per tier, from cells only. A country counts when at least one approved, current cell exists for the method."""
    by_tier: dict[str, dict[str, set[str]]] = {t: {"countries": set(), "specimens": set()} for t in TIERS}
    unknown = 0
    for c in cells:
        if method and c.method != method:
            continue
        st = cell_status(c, now)
        if st == "UNKNOWN":
            unknown += 1
            continue
        by_tier[st]["countries"].add(c.issuer_country.upper())
        by_tier[st]["specimens"].add(f"{c.issuer_country.upper()}:{c.specimen_id}")
    return {"as_of": now.isoformat(), "method": method, "unknown_cells": unknown, "tiers": {t: {"countries": len(v["countries"]), "specimens": len(v["specimens"])} for t, v in by_tier.items()}}


def claim_problems(cells: Sequence[Cell], now: datetime) -> list[str]:
    """What stops a public coverage number: any counted cell that is not ENABLED with approved, current evidence. Empty means the claim may be derived."""
    problems = []
    for c in cells:
        if cell_status(c, now) != "ENABLED":
            problems.append(f"CELL_NOT_PUBLISHABLE:{c.cell_id}")
    return problems


def public_claim(cells: Sequence[Cell], now: datetime) -> dict[str, Any]:
    """The public number, from released cells only. Raises ValueError when any cell offered for counting lacks approved evidence."""
    problems = claim_problems(cells, now)
    if problems:
        raise ValueError("COUNTED_CELL_LACKS_APPROVED_EVIDENCE:" + ",".join(problems[:5]))
    counts = coverage_counts(cells, now)
    return {"as_of": counts["as_of"], "countries": counts["tiers"]["ENABLED"]["countries"], "specimens": counts["tiers"]["ENABLED"]["specimens"], "basis": "released, approved, current cells only"}


# ---------------------------------------------------------------------------------------------------------------------- release governance


@dataclass(frozen=True, slots=True)
class ReleaseDraft:
    release_id: str
    maker: str
    risk: str  # LOW / HIGH
    dependency_digest: str
    scope: Mapping[str, str]  # canary scope: tenant, region, method


def required_approvers(risk: str) -> frozenset[str]:
    """Approver roles by risk, never a shared super-administrator bypass."""
    return frozenset({"PRODUCT", "SECURITY", "PRIVACY", "LEGAL"}) if risk == "HIGH" else frozenset({"PRODUCT", "SECURITY"})


def publish_problems(draft: ReleaseDraft, approvals: Sequence[Mapping[str, str]], *, current_dependency_digest: str) -> list[str]:
    """Why this release may not go live. Maker is never an approver; each required role signs the exact dependency digest; a changed dependency voids approvals."""
    problems: list[str] = []
    valid_roles: set[str] = set()
    seen: set[str] = set()
    for a in approvals:
        who, role, signed = a.get("approver", ""), a.get("role", ""), a.get("dependency_digest", "")
        if who == draft.maker:
            problems.append("MAKER_CANNOT_APPROVE_OWN_RELEASE")
            continue
        if who in seen:
            problems.append("ONE_PERSON_CANNOT_HOLD_TWO_ROLES")
            continue
        if signed != current_dependency_digest:
            problems.append("APPROVAL_FOR_A_DIFFERENT_DEPENDENCY_SET")
            continue
        seen.add(who)
        valid_roles.add(role)
    for role in sorted(required_approvers(draft.risk) - valid_roles):
        problems.append(f"MISSING_APPROVAL:{role}")
    if draft.dependency_digest != current_dependency_digest:
        problems.append("DEPENDENCIES_CHANGED_SINCE_PREFLIGHT")
    return sorted(set(problems))


def pinned_version(session_pin: str | None, live_version: str) -> str:
    """A session keeps the route version it started with. A rollback or a new release never changes an in-flight session, or a committed decision."""
    return session_pin or live_version


# -------------------------------------------------------------------------------------------------------------- cost and reconciliation


@dataclass(frozen=True, slots=True)
class VendorLine:
    provider_id: str
    external_operation_id: str
    amount_minor: int
    currency: str


def reconcile_costs(attempts: Sequence[Mapping[str, Any]], vendor_lines: Sequence[VendorLine]) -> dict[str, Any]:
    """Match the provider's invoice lines to the attempt ledger. A line with no attempt, an attempt billed twice, an unknown-outcome attempt or an amount that
    differs is HELD for a person; nothing is adjusted automatically."""
    ledger = {str(a["external_operation_id"]): a for a in attempts if a.get("external_operation_id")}
    seen: dict[str, int] = {}
    held: list[dict[str, str]] = []
    matched = 0
    for ln in vendor_lines:
        seen[ln.external_operation_id] = seen.get(ln.external_operation_id, 0) + 1
        a = ledger.get(ln.external_operation_id)
        if a is None:
            held.append({"operation": ln.external_operation_id, "reason": "VENDOR_LINE_WITHOUT_ATTEMPT"})
        elif seen[ln.external_operation_id] > 1:
            held.append({"operation": ln.external_operation_id, "reason": "DUPLICATE_VENDOR_LINE"})
        elif a.get("state") in ("OUTCOME_UNKNOWN", "RECONCILING"):
            held.append({"operation": ln.external_operation_id, "reason": "ATTEMPT_OUTCOME_UNKNOWN"})
        elif int(a.get("expected_cost_minor", -1)) != ln.amount_minor or a.get("currency") != ln.currency:
            held.append({"operation": ln.external_operation_id, "reason": "AMOUNT_DIFFERS_FROM_LEDGER"})
        else:
            matched += 1
    unbilled = [op for op, a in ledger.items() if op not in seen and a.get("state") in ("DISPATCHED", "PENDING", "RESULT_RECEIVED", "NORMALIZED", "FINALIZED")]
    return {"matched": matched, "held": held, "attempts_without_vendor_line": sorted(unbilled), "adjusts_anything": False}


def slo_view(observed_p95_ms: int | None, contractual_p95_ms: int | None, vendor_marketing_ms: int | None) -> dict[str, Any]:
    """Observed, contractual and marketed service quality are three fields. The marketed figure is never shown as ours."""
    return {
        "observed_p95_ms": observed_p95_ms,
        "contractual_p95_ms": contractual_p95_ms,
        "vendor_claim_ms": vendor_marketing_ms,
        "observed_available": observed_p95_ms is not None,
        "note": "The vendor's claimed time is the vendor's claim. Only the observed figure is Zoiko's measurement.",
    }


# ------------------------------------------------------------------------------------------------------------------- data-flow graph

HOP_KINDS = ("INGRESS", "TRANSIT", "STORAGE", "REMOTE_SUPPORT", "ANALYTICS", "TELEMETRY", "BACKUP", "SUBPROCESSOR")
SENSITIVE_CLASSES = ("MEDIA", "TEMPLATE")  # images and biometric templates
DATA_CLASSES = ("MEDIA", "TEMPLATE", "DERIVED", "METADATA")
REQUIRED_KINDS = ("INGRESS", "STORAGE")


def flow_digest(hops: Sequence[Mapping[str, Any]]) -> str:
    """One digest for a whole data-flow graph, whatever order the hops were written in."""
    return digest(sorted(({"kind": h["kind"], "party": h["party"], "region": h["region"], "data_class": h["data_class"]} for h in hops), key=lambda x: (x["kind"], x["party"], x["region"], x["data_class"])))


def flow_shape_problems(hops: Sequence[Mapping[str, Any]]) -> list[str]:
    """What is wrong with the graph itself. A path nobody wrote down cannot be approved, and sensitive media never travels or rests where it is not described."""
    out: list[str] = []
    if not hops:
        return ["DATA_FLOW_EMPTY"]
    for h in hops:
        if h.get("kind") not in HOP_KINDS:
            out.append("DATA_FLOW_HOP_KIND_UNKNOWN")
        if h.get("data_class") not in DATA_CLASSES:
            out.append("DATA_FLOW_DATA_CLASS_UNKNOWN")
        if not str(h.get("party", "")).strip() or not str(h.get("region", "")).strip():
            out.append("DATA_FLOW_HOP_INCOMPLETE")
    kinds = {h.get("kind") for h in hops}
    out += [f"DATA_FLOW_MISSING_{k}" for k in REQUIRED_KINDS if k not in kinds]
    return sorted(set(out))


def flow_problems(hops: Sequence[Mapping[str, Any]] | None, allowed_regions: frozenset[str], *, determination_flow_digest: str | None) -> list[str]:
    """Why a provider's data flow stops it being routed to. Every hop (remote support, analytics, telemetry and onward subprocessors included) must sit in an
    allowed region, and the legal determination must have been made about THIS graph: a changed graph stops the route until legal looks again."""
    if hops is None:
        return ["NO_DATA_FLOW_GRAPH"]
    out = flow_shape_problems(hops)
    for h in hops:
        if h.get("region") not in allowed_regions:
            out.append(f"DATA_FLOW_REGION_NOT_ALLOWED:{h.get('kind')}")
    if not out and determination_flow_digest != flow_digest(hops):
        out.append("DATA_FLOW_CHANGED_SINCE_DETERMINATION")
    return sorted(set(out))


def flow_drift(old: Sequence[Mapping[str, Any]], new: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """What a new graph adds or drops, for the person who must decide whether the route stays suspended (a new subprocessor, a new region)."""

    def key(h: Mapping[str, Any]) -> str:
        return f"{h['kind']}|{h['party']}|{h['region']}|{h['data_class']}"

    a, b = {key(h) for h in old}, {key(h) for h in new}
    return {"added": sorted(b - a), "removed": sorted(a - b), "new_regions": sorted({h["region"] for h in new} - {h["region"] for h in old})}


# ------------------------------------------------------------------------------------------------------------- breaker and quality


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    window_seconds: int = 900
    min_volume: int = 20
    open_failure_bp: int = 3000  # 30 percent of dispatched attempts ending unknown or in a bad result
    cooldown_seconds: int = 300
    quality_min_volume: int = 50
    quality_floor_bp: int = 7000  # fewer than 70 percent valid outcomes quarantines the method, however up the provider is


DEFAULT_BREAKER = BreakerConfig()


def breaker_state(outcomes: Sequence[tuple[datetime, bool]], now: datetime, cfg: BreakerConfig = DEFAULT_BREAKER) -> str:
    """CLOSED, OPEN or HALF_OPEN from measured outcomes only: (time, True when the attempt did not end unknown or in a bad result). Too little traffic is CLOSED:
    a breaker never opens on a handful of attempts. After the cooldown one more look is allowed (HALF_OPEN); a good run closes it again."""
    recent = sorted((t, ok) for t, ok in outcomes if 0 <= (now - t).total_seconds() <= cfg.window_seconds)
    if len(recent) < cfg.min_volume:
        return "CLOSED"
    failures = sum(1 for _, ok in recent if not ok)
    if failures * 10000 < len(recent) * cfg.open_failure_bp:
        return "CLOSED"
    last_failure = max((t for t, ok in recent if not ok), default=None)
    if last_failure is not None and (now - last_failure).total_seconds() >= cfg.cooldown_seconds:
        return "HALF_OPEN"
    return "OPEN"


def quality_quarantined(valid: int, total: int, cfg: BreakerConfig = DEFAULT_BREAKER) -> bool:
    """A method whose measured valid-outcome rate is below the floor is quarantined independent of uptime. Little data never quarantines anything."""
    return total >= cfg.quality_min_volume and valid * 10000 < total * cfg.quality_floor_bp


def throttled(recent_attempts: int, cap_per_minute: int | None) -> bool:
    """A provider with a stated per-minute cap takes no more than that, so a failover burst cannot overload the one that is left."""
    return cap_per_minute is not None and recent_attempts >= cap_per_minute


# --------------------------------------------------------------------------------------------------------------- applicant messages

RESTART_STATES = frozenset({"EXPLICIT_RECAPTURE_APPROVED"})
NOT_SENT_STATES = frozenset({"CONFIRMED_NOT_PROCESSED", "FAILED_BEFORE_DISPATCH", "CANCELLED"})
WAIT_STATES = frozenset({"ELIGIBLE", "ATTEMPT_RESERVED", "DISPATCHED", "PENDING", "OUTCOME_UNKNOWN", "RECONCILING", "RESULT_RECEIVED", "NORMALIZED"})


def applicant_message(state: str) -> dict[str, Any]:
    """What an applicant is told. Never the provider name, never "verification failed" for a technical problem, and always one safe next step. The applicant
    keeps the evidence already given unless asked to capture again, and is told which."""
    base = {"counts_against_applicant": False}
    if state in ("NO_ROUTE", "POLICY_BLOCKED"):
        return {**base, "code": "NOT_AVAILABLE_HERE", "title": "This check is not available right now", "body": "We cannot offer this check for your document or device at the moment. This is not a problem with you or your document.", "next": "TRY_ANOTHER_METHOD_OR_CONTACT_SUPPORT", "keeps_evidence": True}
    if state in RESTART_STATES:
        return {**base, "code": "PLEASE_CAPTURE_AGAIN", "title": "Please take your photos again", "body": "Something on our side means we cannot use what you sent. Nothing was decided about you.", "next": "RESTART_CAPTURE", "keeps_evidence": False}
    if state in NOT_SENT_STATES:
        return {**base, "code": "PLEASE_TRY_AGAIN", "title": "Please try again", "body": "Something on our side stopped your check before it finished. Nothing was decided about you and what you sent is kept.", "next": "RESUME", "keeps_evidence": True}
    if state in ("OUTCOME_UNKNOWN", "RECONCILING", "EXCEPTION_REVIEW"):
        return {**base, "code": "STILL_CHECKING", "title": "We are still checking", "body": "Your check is taking longer than usual. You do not need to do anything and you should not start again. We will tell you the result.", "next": "WAIT", "keeps_evidence": True}
    if state in ("REVIEW_REQUIRED", "EVIDENCE_REJECTED"):
        return {**base, "code": "A_PERSON_WILL_LOOK", "title": "A person will take a look", "body": "A member of our team will review your check. You do not need to do anything now.", "next": "WAIT", "keeps_evidence": True}
    if state in WAIT_STATES:
        return {**base, "code": "IN_PROGRESS", "title": "Your check is in progress", "body": "Please keep this page open or come back to it. Nothing you have sent so far is lost.", "next": "WAIT", "keeps_evidence": True}
    return {**base, "code": "RESULT_OWNED_BY_VERIFIER", "title": "Your result comes from the service you are verifying with", "body": "The final result is given by the service you are verifying with.", "next": "NONE", "keeps_evidence": True}


def utcnow() -> datetime:
    return datetime.now(UTC)

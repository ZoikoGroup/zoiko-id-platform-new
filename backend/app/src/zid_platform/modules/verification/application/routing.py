"""Global coverage and provider routing service (ZID-CP-WF09 v2.0; CR-171): capabilities, resolution, attempts, preflight, releases, coverage claims.

    propose_capability / decide_capability   a provider's capability for a method, versioned; approval needs a second person and dated evidence
    resolve_and_record                       the deterministic resolver on a frozen input, kept once per idempotency key
    reserve_attempt / advance_attempt        the attempt state machine, append-only, one attempt per idempotency key
    record_failure / reroute                 failure classes and the only safe ways to ask another provider
    preflight                                what a proposed change would do, with no persistence and no provider traffic
    create_release / run_preflight / approve_release / publish_release / rollback_release
    coverage / public_claim                  counts and the public number, from released cells only

All of it runs in the caller's transaction. Who may call it, and the audit record of each call, is the API's job.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit

from zid_platform.modules.verification.application import market_releases, suspensions
from zid_platform.modules.verification.domain import market_release as rm
from zid_platform.modules.verification.domain import provider_adapter as pa
from zid_platform.modules.verification.domain import routing as rt
from zid_platform.modules.verification.domain import suspension as sp
from zid_platform.modules.verification.infrastructure import route_entitlement_store as ent_store
from zid_platform.modules.verification.infrastructure import routing_store as store
from zid_platform.modules.verification.infrastructure import workflow_store

MANIFEST_KEYS = ("product", "countries", "channels", "processing_regions", "assurance", "pricing", "observed")


class RoutingError(Exception):
    def __init__(self, code: str, detail: str = "", status: int = 409) -> None:
        super().__init__(detail or code)
        self.code = code
        self.status = status


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            d = datetime.fromisoformat(value)
        except ValueError:
            return None
        return d if d.tzinfo else None
    return None


# --------------------------------------------------------------------------------------------------------------------- capabilities


def manifest_problems(m: Mapping[str, Any]) -> list[str]:
    out = [f"MANIFEST_MISSING:{k}" for k in MANIFEST_KEYS if k not in m]
    if out:
        return out
    if not isinstance(m["countries"], list) or not m["countries"] or any(not isinstance(c, str) or len(c) != 2 for c in m["countries"]):
        out.append("MANIFEST_COUNTRIES_INVALID")
    if not isinstance(m["assurance"], int) or isinstance(m["assurance"], bool) or not 0 <= m["assurance"] <= 10:
        out.append("MANIFEST_ASSURANCE_INVALID")
    for k in ("channels", "processing_regions"):
        if not isinstance(m[k], list) or not m[k] or any(not isinstance(x, str) for x in m[k]):
            out.append(f"MANIFEST_{k.upper()}_INVALID")
    p, o = m["pricing"], m["observed"]
    if not isinstance(p, dict) or not isinstance(p.get("unit_cost_minor"), int) or not isinstance(p.get("review_cost_minor", 0), int) or not isinstance(p.get("currency"), str):
        out.append("MANIFEST_PRICING_INVALID")
    if not isinstance(o, dict) or not isinstance(o.get("valid_outcome_rate_bp"), int) or not 0 <= o["valid_outcome_rate_bp"] <= 10000 or not isinstance(o.get("p95_latency_ms"), int):
        out.append("MANIFEST_OBSERVED_INVALID")
    return out


async def propose_capability(
    conn: AsyncConnection, *, provider_id: str, method: str, manifest: Mapping[str, Any], qualified_until: datetime | None, contract_until: datetime | None,
    legal_determination_ref: str | None, legal_determination_valid_until: datetime | None, by: str,
) -> dict[str, Any]:  # fmt: skip
    problems = manifest_problems(manifest)
    if problems:
        raise RoutingError("CAPABILITY_MANIFEST_INVALID", "; ".join(problems), 422)
    prev = await store.latest_capability(conn, provider_id, method)
    version = (prev.version if prev else 0) + 1
    await store.insert_capability(
        conn,
        {
            "provider_id": provider_id, "method": method, "version": version, "state": "UNDER_REVIEW", "manifest": dict(manifest), "manifest_digest": rt.digest(dict(manifest)),
            "qualified_until": qualified_until, "contract_until": contract_until, "legal_determination_ref": legal_determination_ref,
            "legal_determination_valid_until": legal_determination_valid_until, "proposed_by": by, "approved_by": None, "reason": None,
        },
    )  # fmt: skip
    return {"provider_id": provider_id, "method": method, "version": version, "state": "UNDER_REVIEW", "manifest_digest": rt.digest(dict(manifest))}


async def decide_capability(conn: AsyncConnection, tenant_id: str, *, provider_id: str, method: str, action: str, by: str, reason: str) -> dict[str, Any]:
    """APPROVE (second person only), SUSPEND or RETIRE. The new version carries the facts of the one it follows; history stays."""
    cur = await store.latest_capability(conn, provider_id, method)
    if cur is None:
        raise RoutingError("CAPABILITY_NOT_FOUND", "no such capability", 404)
    target = {"APPROVE": "APPROVED", "SUSPEND": "SUSPENDED", "RETIRE": "RETIRED"}[action]
    if action == "APPROVE":
        if cur.state not in ("UNDER_REVIEW", "SUSPENDED"):
            raise RoutingError("CAPABILITY_NOT_AWAITING_APPROVAL", f"it is {cur.state}")
        if by == cur.proposed_by:
            raise RoutingError("SECOND_PERSON_REQUIRED", "the person who proposed a capability cannot approve it", 403)
        missing = [k for k in ("qualified_until", "contract_until", "legal_determination_ref", "legal_determination_valid_until") if getattr(cur, k) is None]
        if missing:
            raise RoutingError("CAPABILITY_EVIDENCE_INCOMPLETE", "missing: " + ", ".join(missing), 422)
    elif cur.state in ("RETIRED",):
        raise RoutingError("CAPABILITY_RETIRED", "retired is final")
    row = {
        "provider_id": provider_id, "method": method, "version": cur.version + 1, "state": target, "manifest": dict(cur.manifest), "manifest_digest": cur.manifest_digest,
        "qualified_until": cur.qualified_until, "contract_until": cur.contract_until, "legal_determination_ref": cur.legal_determination_ref,
        "legal_determination_valid_until": cur.legal_determination_valid_until, "proposed_by": cur.proposed_by, "approved_by": by if action == "APPROVE" else cur.approved_by, "reason": reason,
    }  # fmt: skip
    await store.insert_capability(conn, row)
    await _witness(
        conn, tenant_id, event="provider.capability.changed", aggregate_type="provider_capability", aggregate_id=f"{provider_id}/{method}", version=row["version"], target_type="provider_capability", target_ref=f"{provider_id}/{method}",
        outcome="SUCCESS", cls=EvidenceClass.E3, now=datetime.now(UTC), actor=by, details={"provider_id": provider_id, "method": method, "version": row["version"], "state": target, "manifest_digest": cur.manifest_digest, "reason_code": action},
    )  # fmt: skip
    return {"provider_id": provider_id, "method": method, "version": row["version"], "state": target}


def capability_view(r: Any) -> dict[str, Any]:
    m = dict(r.manifest)
    return {
        "provider_id": r.provider_id, "method": r.method, "version": r.version, "state": r.state, "manifest_digest": r.manifest_digest, "product": m.get("product"),
        "countries": m.get("countries"), "channels": m.get("channels"), "regions": m.get("processing_regions"), "assurance": m.get("assurance"),
        "qualified_until": r.qualified_until.isoformat() if r.qualified_until else None, "contract_until": r.contract_until.isoformat() if r.contract_until else None,
        "legal_determination_ref": r.legal_determination_ref, "proposed_by": r.proposed_by, "approved_by": r.approved_by, "reason": r.reason,
        "observed": m.get("observed"), "health": m.get("health", "UNKNOWN"),
    }  # fmt: skip


def candidate_from(r: Any, *, tenant_id: str, state_override: str | None = None) -> rt.Candidate:
    m = dict(r.manifest)
    obs, price = m.get("observed", {}), m.get("pricing", {})
    sdk = m.get("min_sdk_version")
    return rt.Candidate(
        provider_id=r.provider_id, method=r.method, assurance=int(m.get("assurance", 0)), countries=frozenset(str(c).upper() for c in m.get("countries", [])),
        channels=frozenset(m.get("channels", [])), min_sdk_version=tuple(int(x) for x in str(sdk).split(".")) if sdk else None,
        processing_regions=frozenset(m.get("processing_regions", [])), subprocessor_regions=frozenset(m.get("subprocessor_regions", [])),
        legal_determination_ref=r.legal_determination_ref, legal_determination_valid_until=r.legal_determination_valid_until, state=state_override or r.state,
        qualified_until=r.qualified_until, contract_until=r.contract_until, tenant_ids=frozenset(m["tenant_ids"]) if m.get("tenant_ids") is not None else None,
        health=str(m.get("health", "UNKNOWN")), quota_remaining=m.get("quota_remaining"), unit_cost_minor=int(price.get("unit_cost_minor", 0)),
        review_cost_minor=int(price.get("review_cost_minor", 0)), valid_outcome_rate_bp=int(obs.get("valid_outcome_rate_bp", 0)), p95_latency_ms=int(obs.get("p95_latency_ms", 0)),
        measured=bool(obs.get("measured", False)),
    )  # fmt: skip


async def _witness(conn: AsyncConnection, tenant_id: str, *, event: str, aggregate_type: str, aggregate_id: str, version: int, target_type: str, target_ref: str, outcome: str, cls: EvidenceClass, now: datetime, details: Mapping[str, Any], actor: str | None = None) -> None:
    """Every routing fact leaves an outbox event and a protected audit record in the same transaction (ZID-CP-WF08 evidence). Ids, digests and codes only: no applicant data."""
    audit_outcome = {"NO_ROUTE": "DENIED", "CANCELLED": "DENIED", "UNKNOWN": "PARTIAL"}.get(outcome, outcome)  # the ledger knows four outcomes; the routing word is kept as the reason code
    event_id = await emit(conn, name=event, tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id, aggregate_version=version, data=dict(details), classification="INTERNAL")
    await record_audit(
        conn, tenant_id,
        AuditFacts(event_type=event, actor_type="HUMAN" if actor else "SERVICE", action=event.split(".")[-1], target_type=target_type, target_ref=target_ref, outcome=audit_outcome, reason_code=outcome if outcome != audit_outcome else None, evidence_class=cls, occurred_at=now,
                   actor_principal_id=actor, source_service="verification-routing", source_event_id=event_id, details=dict(details)),
    )  # fmt: skip


async def _entitlement_facts(conn: AsyncConnection, c: rt.Candidate, *, country: str, purpose: str, assurance_floor: int, now: datetime) -> tuple[str, ...]:
    """Why THIS company's own entitlement for this provider and method does not cover the request, or nothing when it does. The service code is the method in
    lower case. A provider without a live entitlement is not eligible, and every reason the existing entitlement guard gives is passed on."""
    rows = [r for r in await ent_store.listing(conn, c.provider_id) if r["service_code"] == c.method.lower()]
    if not rows:
        return ("NO_ENTITLEMENT",)
    best: tuple[str, ...] | None = None
    for r in rows:
        e = ent_store.to_domain(r)
        found: set[str] = set()
        for region in sorted(c.processing_regions or {""}):
            found |= {x.value for x in pa.entitlement_refusals(e, pa.RouteRequest(country, c.method.lower(), purpose, region, assurance_floor), now)}
        out = tuple(sorted(found))
        if best is None or len(out) < len(best):
            best = out
    return best or ()


async def _health_facts(conn: AsyncConnection, provider_id: str, now: datetime) -> tuple[str, bool]:
    """The breaker state and the quality quarantine, measured from this platform's own attempts (never from the vendor's word)."""
    cfg = rt.DEFAULT_BREAKER
    rows = await store.dispatched_outcomes(conn, provider_id, now - timedelta(seconds=cfg.window_seconds))
    bad = ("OUTCOME_UNKNOWN", "RECONCILING", "EXCEPTION_REVIEW", "EVIDENCE_REJECTED")
    outcomes = [(r.created_at, r.to_state not in bad) for r in rows if r.to_state not in ("DISPATCHED", "PENDING")]
    wide = await store.dispatched_outcomes(conn, provider_id, now - timedelta(days=7))
    done = [r for r in wide if r.to_state in ("NORMALIZED", "FINALIZED", "REVIEW_REQUIRED", "EVIDENCE_REJECTED", "RESULT_RECEIVED")]
    valid = sum(1 for r in done if r.to_state in ("NORMALIZED", "FINALIZED", "RESULT_RECEIVED"))
    return rt.breaker_state(outcomes, now, cfg), rt.quality_quarantined(valid, len(done), cfg)


async def decorate(conn: AsyncConnection, c: rt.Candidate, *, country: str, purpose: str, allowed_regions: frozenset[str], assurance_floor: int, now: datetime, determination_flow_digest: str | None) -> rt.Candidate:
    flow = await store.latest_data_flow(conn, c.provider_id)
    hops = [dict(h) for h in flow.hops] if flow else None
    breaker, quarantined = await _health_facts(conn, c.provider_id, now)
    return replace(
        c,
        entitlement_refusals=await _entitlement_facts(conn, c, country=country, purpose=purpose, assurance_floor=assurance_floor, now=now),
        data_flow_problems=tuple(rt.flow_problems(hops, allowed_regions, determination_flow_digest=determination_flow_digest)),
        breaker=breaker, quality_quarantined=quarantined,
    )  # fmt: skip


async def candidates_for(conn: AsyncConnection, *, tenant_id: str, country: str, purpose: str, now: datetime, product: str | None = None, allowed_regions: frozenset[str] = frozenset(), assurance_floor: int = 0) -> list[rt.Candidate]:
    """Latest capabilities as candidates, with what the platform itself knows about each: this company's entitlement, the provider's data-flow graph against its
    legal determination, and its measured health. An exact-scope kill switch (the provider suspension) turns a provider SUSPENDED here, at once."""
    out: list[rt.Candidate] = []
    for r in await store.latest_capabilities(conn):
        m = dict(r.manifest)
        if product is not None and m.get("product") != product:
            continue
        blocked = await suspensions.blocking_for(conn, sp.Invocation(provider=r.provider_id, country=country.upper(), purpose=purpose), at=now)
        base = candidate_from(r, tenant_id=tenant_id, state_override="SUSPENDED" if blocked else None)
        out.append(await decorate(conn, base, country=country, purpose=purpose, allowed_regions=allowed_regions, assurance_floor=assurance_floor, now=now, determination_flow_digest=m.get("legal_data_flow_digest")))
    return out


async def record_data_flow(conn: AsyncConnection, tenant_id: str, *, provider_id: str, hops: Sequence[Mapping[str, Any]], by: str, now: datetime | None = None) -> dict[str, Any]:
    """Register a new version of a provider's data-flow graph. If it differs from the one the legal determination covers, routing to that provider stops until a new
    capability version is proposed with the new digest and a second person approves it. The change is announced and audited with what was added."""
    now = now or datetime.now(UTC)
    hops = [dict(h) for h in hops]
    problems = rt.flow_shape_problems(hops)
    if problems:
        raise RoutingError("DATA_FLOW_INVALID", "; ".join(problems), 422)
    prev = await store.latest_data_flow(conn, provider_id)
    version = (prev.version if prev else 0) + 1
    new_digest = rt.flow_digest(hops)
    if prev is not None and prev.graph_digest == new_digest:
        return {"provider_id": provider_id, "version": prev.version, "graph_digest": new_digest, "changed": False}
    await store.insert_data_flow(conn, provider_id, version, hops, new_digest, by)
    drift = rt.flow_drift([dict(h) for h in prev.hops], hops) if prev else {"added": [], "removed": [], "new_regions": []}
    await _witness(
        conn, tenant_id, event="provider.data_flow.changed", aggregate_type="provider_data_flow", aggregate_id=provider_id, version=version, target_type="provider_data_flow", target_ref=provider_id, outcome="SUCCESS",
        cls=EvidenceClass.E3, now=now, actor=by, details={"provider_id": provider_id, "version": version, "graph_digest": new_digest, "previous_digest": prev.graph_digest if prev else None, "added": len(drift["added"]), "new_regions": drift["new_regions"], "routing_effect": "BLOCKED_UNTIL_LEGAL_DETERMINATION_COVERS_THE_NEW_GRAPH" if prev else "NONE"},
    )  # fmt: skip
    return {"provider_id": provider_id, "version": version, "graph_digest": new_digest, "changed": True, "drift": drift, "routing_effect": "provider is not routed to until its legal determination covers this graph" if prev else "recorded"}


# -------------------------------------------------------------------------------------------------------------------- resolution


async def route_input_for(conn: AsyncConnection, *, tenant_id: str, session_id: str, body: Mapping[str, Any], now: datetime) -> rt.RouteInput:
    """The frozen input. Whether the workflow is approved, whether the market is open and which methods this company is entitled to are LOOKED UP here from the
    platform's own records, never taken from the caller (CR-172)."""
    market = await market_releases.market_open(conn, rm.MarketKey(str(body["issuer_country"]).upper(), str(body["product"]), str(body["purpose"])), now=now)
    wf = await workflow_store.load_version(conn, str(body["workflow_id"]), str(body["workflow_version"]))
    entitled = frozenset(str(r["service_code"]).upper() for r in await ent_store.listing(conn, None) if r["status"] == "ACTIVE")
    return rt.RouteInput(
        tenant_id=tenant_id, session_id=session_id, workflow_version=f"{body['workflow_id']}@{body['workflow_version']}", purpose=str(body["purpose"]), issuer_country=str(body["issuer_country"]).upper(),
        residence_country=str(body.get("residence_country") or body["issuer_country"]).upper(), method=str(body["method"]), assurance_floor=int(body.get("assurance_floor", 0)),
        channel=str(body["channel"]), sdk_version=body.get("sdk_version"), allowed_regions=frozenset(body["allowed_regions"]), now=now,
        tenant_entitled_methods=entitled, workflow_approved=bool(wf and wf["status"] == "APPROVED"), market_open=market.open,
        context_verified=True, chip_required=bool(body.get("chip_required", False)),
    )  # fmt: skip


async def resolve_and_record(conn: AsyncConnection, tenant_id: str, *, idempotency_key: str, body: Mapping[str, Any], exclude: Sequence[str] = (), now: datetime | None = None) -> dict[str, Any]:
    """Resolve once per key. The same key and the same input returns the first decision; the same key with a different input is refused."""
    now = now or datetime.now(UTC)
    session_id = str(body["session_id"])
    existing = await store.decision_by_key(conn, idempotency_key)
    live = await store.live_release(conn)
    route_version = f"release:{live.release_id}:v{live.version}" if live else "unreleased"
    fingerprint = rt.digest({k: v for k, v in body.items() if k != "lineage"})  # what the caller asked, so a later change in the platform's own facts never reads as a reused key
    cands = await candidates_for(conn, tenant_id=tenant_id, country=str(body["issuer_country"]), purpose=str(body["purpose"]), now=now, product=str(body["product"]), allowed_regions=frozenset(body["allowed_regions"]), assurance_floor=int(body.get("assurance_floor", 0)))
    inp = await route_input_for(conn, tenant_id=tenant_id, session_id=session_id, body=body, now=now)
    if existing is not None:
        first = dict(existing.body)
        probe = fingerprint
        if existing.session_id != session_id or first.get("input_fingerprint") != probe:
            raise RoutingError("IDEMPOTENCY_KEY_REUSED", "that key was used for a different request")
        return decision_view(existing, replayed=True)
    d = rt.resolve(inp, cands, exclude=exclude)
    dec_id = str(uuid.uuid4())
    payload = {**d.to_dict(), "input_fingerprint": fingerprint, "lineage": body.get("lineage"), "route_version": route_version}
    await store.insert_decision(
        conn,
        {
            "decision_id": dec_id, "tenant_id": tenant_id, "session_id": session_id, "idempotency_key": idempotency_key, "route_version": route_version,
            "algorithm_version": d.algorithm_version, "input_digest": d.input_digest, "snapshot_digest": d.snapshot_digest, "decision_digest": d.decision_digest,
            "selected": d.selected, "status": payload["status"], "body": payload,
        },
    )  # fmt: skip
    row = await store.load_decision(conn, dec_id)
    assert row is not None
    await _witness(
        conn, tenant_id, event="route.selected" if d.selected else "route.no_route", aggregate_type="route_decision", aggregate_id=dec_id, version=1, target_type="route_decision", target_ref=dec_id,
        outcome="SUCCESS" if d.selected else "NO_ROUTE", cls=EvidenceClass.E3, now=now,
        details={"decision_id": dec_id, "session_id": session_id, "selected": d.selected, "reasons": list(d.no_route_reasons), "decision_digest": d.decision_digest, "input_digest": d.input_digest, "snapshot_digest": d.snapshot_digest, "algorithm_version": d.algorithm_version, "route_version": route_version, "rejected": len(d.rejected)},
    )  # fmt: skip
    return decision_view(row, replayed=False)


def decision_view(row: Any, *, replayed: bool) -> dict[str, Any]:
    b = dict(row.body)
    return {
        "decision_id": str(row.decision_id), "session_id": row.session_id, "status": row.status, "selected": row.selected, "route_version": row.route_version,
        "algorithm_version": row.algorithm_version, "input_digest": row.input_digest, "snapshot_digest": row.snapshot_digest, "decision_digest": row.decision_digest,
        "ranked": b.get("ranked", []), "rejected": b.get("rejected", []), "no_route_reasons": b.get("no_route_reasons", []), "notes": b.get("notes", []),
        "lineage": b.get("lineage"), "caveat": b.get("caveat"), "replayed": replayed, "created_at": row.created_at.isoformat(),
    }  # fmt: skip


def replay_matches(row: Any, inp: rt.RouteInput, candidates: Sequence[rt.Candidate]) -> bool:
    """Re-run the resolver on the recorded snapshot's inputs and say whether it chooses the same provider with the same digest."""
    return rt.resolve(inp, candidates).decision_digest == row.decision_digest


# ---------------------------------------------------------------------------------------------------------------------- attempts


async def reserve_attempt(conn: AsyncConnection, tenant_id: str, *, decision_id: str, idempotency_key: str, body: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Make the provider attempt for a selected route. Quota, contract and legality are checked again NOW: an attempt that no longer passes is CANCELLED and no
    vendor call is possible. A repeated key returns the same attempt."""
    now = now or datetime.now(UTC)
    dec = await store.load_decision(conn, decision_id)
    if dec is None:
        raise RoutingError("DECISION_NOT_FOUND", "no such route decision", 404)
    if dec.status != "ROUTE_SELECTED" or not dec.selected:
        raise RoutingError("NO_ROUTE_TO_RESERVE", "this decision selected no provider")
    first = await store.first_event_for_key(conn, dec.session_id, dec.selected, idempotency_key)
    if first is not None:
        return await attempt_view(conn, str(first.attempt_id))
    inp = await route_input_for(conn, tenant_id=tenant_id, session_id=dec.session_id, body=body, now=now)
    cap = next((c for c in await candidates_for(conn, tenant_id=tenant_id, country=inp.issuer_country, purpose=inp.purpose, now=now, product=str(body["product"]), allowed_regions=inp.allowed_regions, assurance_floor=inp.assurance_floor) if c.provider_id == dec.selected and c.method == inp.method), None)
    fails = rt.input_failures(inp) + (rt.candidate_failures(cap, inp) if cap else [(rt.Gate.QUALIFICATION, "PROVIDER_NO_LONGER_REGISTERED")])
    cap_row = await store.latest_capability(conn, dec.selected, inp.method)
    per_minute = dict(cap_row.manifest).get("max_attempts_per_minute") if cap_row else None
    if rt.throttled(await store.recent_attempts(conn, dec.selected, now - timedelta(seconds=60)), per_minute):
        fails.append((rt.Gate.CAPACITY, "PROVIDER_THROTTLED"))  # a failover burst never overloads the provider that is left
    attempt_id = str(uuid.uuid4())
    cost = cap.unit_cost_minor if cap else None
    common = {"tenant_id": tenant_id, "attempt_id": attempt_id, "decision_id": decision_id, "session_id": dec.session_id, "provider_id": dec.selected, "idempotency_key": idempotency_key, "external_operation_id": None}
    if not await store.insert_event(conn, {**common, "seq": 1, "from_state": None, "to_state": rt.AttemptState.ELIGIBLE.value, "actor": "SYSTEM", "reason": None, "expected_cost_minor": cost, "currency": body.get("currency")}):
        again = await store.first_event_for_key(conn, dec.session_id, dec.selected, idempotency_key)
        assert again is not None
        return await attempt_view(conn, str(again.attempt_id))  # lost a race with an identical request: the winner's attempt
    if fails:
        reasons = ",".join(sorted({r for _, r in fails}))[:280]
        await store.insert_event(conn, {**common, "seq": 2, "from_state": "ELIGIBLE", "to_state": rt.AttemptState.CANCELLED.value, "actor": "SYSTEM", "reason": reasons, "expected_cost_minor": cost, "currency": body.get("currency")})
        await _witness(conn, tenant_id, event="route.attempt.cancelled", aggregate_type="route_attempt", aggregate_id=attempt_id, version=2, target_type="route_attempt", target_ref=attempt_id, outcome="CANCELLED", cls=EvidenceClass.E2, now=now, details={"attempt_id": attempt_id, "decision_id": decision_id, "provider_id": dec.selected, "reasons": sorted({r for _, r in fails})})
    else:
        await store.insert_event(conn, {**common, "seq": 2, "from_state": "ELIGIBLE", "to_state": rt.AttemptState.ATTEMPT_RESERVED.value, "actor": "SYSTEM", "reason": None, "expected_cost_minor": cost, "currency": body.get("currency")})
        await _witness(conn, tenant_id, event="route.attempt.reserved", aggregate_type="route_attempt", aggregate_id=attempt_id, version=2, target_type="route_attempt", target_ref=attempt_id, outcome="SUCCESS", cls=EvidenceClass.E2, now=now, details={"attempt_id": attempt_id, "decision_id": decision_id, "provider_id": dec.selected})
    return await attempt_view(conn, attempt_id)


async def advance_attempt(conn: AsyncConnection, tenant_id: str, attempt_id: str, target: str, *, actor: str, reason: str | None = None, external_operation_id: str | None = None) -> dict[str, Any]:
    events = await store.events_of(conn, attempt_id)
    if not events:
        raise RoutingError("ATTEMPT_NOT_FOUND", "no such attempt", 404)
    last = events[-1]
    try:
        to = rt.transition(rt.AttemptState(last.to_state), rt.AttemptState(target), actor=actor)
    except ValueError as e:  # an unknown state name, or a TransitionError
        code = getattr(e, "code", "ATTEMPT_STATE_UNKNOWN")
        raise RoutingError(code, str(e), 403 if code == "ONLY_THE_OWNING_VERIFIER_FINALISES" else 409) from e
    if to is rt.AttemptState.DISPATCHED and not external_operation_id:
        raise RoutingError("EXTERNAL_OPERATION_ID_REQUIRED", "a dispatch must name the provider's own operation id", 422)
    ext = external_operation_id or last.external_operation_id
    ok = await store.insert_event(
        conn,
        {
            "tenant_id": tenant_id, "attempt_id": attempt_id, "seq": last.seq + 1, "decision_id": str(last.decision_id), "session_id": last.session_id, "provider_id": last.provider_id,
            "idempotency_key": last.idempotency_key, "external_operation_id": ext, "from_state": last.to_state, "to_state": to.value, "actor": actor, "reason": reason,
            "expected_cost_minor": last.expected_cost_minor, "currency": last.currency,
        },
    )  # fmt: skip
    if not ok:
        raise RoutingError("ATTEMPT_CHANGED_CONCURRENTLY", "another change was recorded first; read the attempt again")
    unknown = to is rt.AttemptState.OUTCOME_UNKNOWN
    await _witness(
        conn, tenant_id, event="route.attempt.unknown" if unknown else "route.attempt.advanced", aggregate_type="route_attempt", aggregate_id=attempt_id, version=last.seq + 1, target_type="route_attempt", target_ref=attempt_id,
        outcome="UNKNOWN" if unknown else "SUCCESS", cls=EvidenceClass.E3 if unknown or to is rt.AttemptState.FINALIZED else EvidenceClass.E2, now=datetime.now(UTC),
        details={"attempt_id": attempt_id, "decision_id": str(last.decision_id), "provider_id": last.provider_id, "from": last.to_state, "to": to.value, "actor": actor, "reason_code": (reason or "")[:60] or None},
    )  # fmt: skip
    return await attempt_view(conn, attempt_id)


async def record_failure(conn: AsyncConnection, tenant_id: str, attempt_id: str, **flags: bool) -> dict[str, Any]:
    events = await store.events_of(conn, attempt_id)
    if not events:
        raise RoutingError("ATTEMPT_NOT_FOUND", "no such attempt", 404)
    dispatched = events[-1].to_state not in ("ELIGIBLE", "ATTEMPT_RESERVED")
    cls = rt.classify_failure(dispatched=dispatched, **flags)
    out = await advance_attempt(conn, tenant_id, attempt_id, rt.FAILURE_NEXT[cls].value, actor="SYSTEM", reason=f"FAILURE:{cls}")
    return {**out, "failure_class": cls}


async def attempt_view(conn: AsyncConnection, attempt_id: str) -> dict[str, Any]:
    events = await store.events_of(conn, attempt_id)
    last = events[-1]
    st = rt.AttemptState(last.to_state)
    return {
        "attempt_id": attempt_id, "decision_id": str(last.decision_id), "session_id": last.session_id, "provider_id": last.provider_id, "state": last.to_state, "terminal": st in rt.TERMINAL,
        "may_ask_another_provider": rt.may_ask_another_provider(st), "may_retry_same_provider": st in rt.SAFE_TO_RETRY_SAME_PROVIDER, "external_operation_id": last.external_operation_id,
        "history": [{"seq": e.seq, "from": e.from_state, "to": e.to_state, "actor": e.actor, "reason": e.reason, "at": e.created_at.isoformat()} for e in events],
        "status_note": "Routing selected, provider submitted, result pending and the customer's final outcome are separate. A provider result is evidence, not a verified identity.",
        "applicant": rt.applicant_message(last.to_state),
    }  # fmt: skip


async def reroute(conn: AsyncConnection, tenant_id: str, *, failed_attempt_id: str, idempotency_key: str, body: Mapping[str, Any], downgrade_approval_ref: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Ask another provider, only when the failed attempt provably sent nothing or was provably not processed. Every gate is re-evaluated for the others, and a
    weaker provider needs a named approval."""
    view = await attempt_view(conn, failed_attempt_id)
    if not view["may_ask_another_provider"]:
        raise RoutingError("REROUTE_REFUSED_OUTCOME_NOT_KNOWN", f"the attempt is {view['state']}; an unknown or pending outcome is reconciled, never retried elsewhere")
    now = now or datetime.now(UTC)
    dec = await store.load_decision(conn, view["decision_id"])
    assert dec is not None
    cands = await candidates_for(conn, tenant_id=tenant_id, country=str(body["issuer_country"]), purpose=str(body["purpose"]), now=now, product=str(body["product"]), allowed_regions=frozenset(body["allowed_regions"]), assurance_floor=int(body.get("assurance_floor", 0)))
    failed = next((c for c in cands if c.provider_id == view["provider_id"] and c.method == str(body["method"])), None)
    out = await resolve_and_record(
        conn, tenant_id, idempotency_key=idempotency_key, exclude=[view["provider_id"]],
        body={**body, "session_id": dec.session_id, "lineage": {"previous_decision": view["decision_id"], "failed_provider": view["provider_id"], "failed_attempt": failed_attempt_id}}, now=now,
    )  # fmt: skip
    chosen = next((c for c in cands if c.provider_id == out["selected"] and c.method == str(body["method"])), None)
    if failed and chosen and chosen.assurance < failed.assurance and not (downgrade_approval_ref or "").strip():
        raise RoutingError("FAILOVER_WOULD_LOWER_ASSURANCE", "a weaker provider needs a named approval", 403)
    return out


# --------------------------------------------------------------------------------------------------------------------- preflight


async def preflight(conn: AsyncConnection, tenant_id: str, *, scenarios: Sequence[Mapping[str, Any]], proposed: Sequence[Mapping[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """What a proposed capability change would do to each scenario. Reads only: nothing is stored, no provider is called, no customer is told."""
    now = now or datetime.now(UTC)
    live_rows = await store.latest_capabilities(conn)
    rows = {(r.provider_id, r.method): r for r in live_rows}
    after_rows = dict(rows)
    for p in proposed:
        key = (str(p["provider_id"]), str(p["method"]))
        base = rows.get(key)
        manifest = dict(p.get("manifest") or (base.manifest if base else {}))
        after_rows[key] = _Draft(key, manifest, p, base)
    out_rows: list[dict[str, Any]] = []
    cohorts: dict[str, dict[str, int]] = {}
    for sc in scenarios:
        inp = await route_input_for(conn, tenant_id=tenant_id, session_id=str(sc.get("session_id", "preflight")), body=sc, now=now)
        before = rt.resolve(inp, await _decorated(conn, rows.values(), inp, sc, tenant_id, now))
        after = rt.resolve(inp, await _decorated(conn, after_rows.values(), inp, sc, tenant_id, now))
        change = "SAME" if before.selected == after.selected else "LOSES_COVERAGE" if before.selected and not after.selected else "GAINS_COVERAGE" if after.selected and not before.selected else "SWITCHES_PROVIDER"
        cohort = f"{inp.issuer_country}/{inp.channel}"
        c = cohorts.setdefault(cohort, {"SAME": 0, "LOSES_COVERAGE": 0, "GAINS_COVERAGE": 0, "SWITCHES_PROVIDER": 0})
        c[change] += 1
        out_rows.append({"scenario": {k: sc[k] for k in ("issuer_country", "channel", "method", "product", "purpose")}, "before": before.selected, "after": after.selected, "change": change, "required": bool(sc.get("required", False)), "after_no_route_reasons": list(after.no_route_reasons)})
    blocking = [r for r in out_rows if r["change"] == "LOSES_COVERAGE" and r["required"]]
    return {
        "rows": out_rows, "cohorts": cohorts, "blocking": len(blocking), "passes": not blocking, "stored": False, "provider_calls": 0,
        "note": "Costs here are projections from the registered prices. A switch of provider mid-session may need the applicant to capture again; that is shown as SWITCHES_PROVIDER, not hidden.",
    }  # fmt: skip


async def _decorated(conn: AsyncConnection, rows: Any, inp: rt.RouteInput, sc: Mapping[str, Any], tenant_id: str, now: datetime) -> list[rt.Candidate]:
    return [
        await decorate(conn, candidate_from(r, tenant_id=tenant_id), country=inp.issuer_country, purpose=inp.purpose, allowed_regions=inp.allowed_regions, assurance_floor=inp.assurance_floor, now=now, determination_flow_digest=dict(r.manifest).get("legal_data_flow_digest"))
        for r in rows
        if dict(r.manifest).get("product") == sc["product"]
    ]


class _Draft:
    """A capability row as it would be after a proposed change (never stored)."""

    def __init__(self, key: tuple[str, str], manifest: dict[str, Any], p: Mapping[str, Any], base: Any) -> None:
        self.provider_id, self.method = key
        self.manifest = manifest
        self.state = str(p.get("state") or (base.state if base else "UNDER_REVIEW"))
        self.qualified_until = _utc(p.get("qualified_until")) or (base.qualified_until if base else None)
        self.contract_until = _utc(p.get("contract_until")) or (base.contract_until if base else None)
        self.legal_determination_ref = p.get("legal_determination_ref") or (base.legal_determination_ref if base else None)
        self.legal_determination_valid_until = _utc(p.get("legal_determination_valid_until")) or (base.legal_determination_valid_until if base else None)


# ---------------------------------------------------------------------------------------------------------------------- releases


async def _dependencies(conn: AsyncConnection) -> dict[str, Any]:
    caps = {f"{r.provider_id}/{r.method}": {"version": r.version, "state": r.state, "digest": r.manifest_digest} for r in await store.latest_capabilities(conn)}
    return {"capabilities": caps, "algorithm_version": rt.ALGORITHM_VERSION}


async def create_release(conn: AsyncConnection, *, maker: str, risk: str, scope: Mapping[str, str]) -> dict[str, Any]:
    deps = await _dependencies(conn)
    rid = str(uuid.uuid4())
    await store.insert_release(conn, {"release_id": rid, "version": 1, "state": "DRAFT", "maker": maker, "risk": risk, "dependency_digest": rt.digest(deps), "dependencies": deps, "scope": dict(scope), "preflight": None, "reason": None, "recorded_by": maker})
    return {"release_id": rid, "version": 1, "state": "DRAFT", "dependency_digest": rt.digest(deps)}


def release_view(r: Any, approvals: Sequence[Any] = ()) -> dict[str, Any]:
    return {
        "release_id": str(r.release_id), "version": r.version, "state": r.state, "maker": r.maker, "risk": r.risk, "dependency_digest": r.dependency_digest, "scope": dict(r.scope),
        "preflight": dict(r.preflight) if r.preflight else None, "required_approvers": sorted(rt.required_approvers(r.risk)), "recorded_at": r.recorded_at.isoformat(),
        "approvals": [{"approver": a.approver, "role": a.role, "dependency_digest": a.dependency_digest} for a in approvals],
    }  # fmt: skip


async def record_preflight(conn: AsyncConnection, release_id: str, *, result: Mapping[str, Any], by: str) -> dict[str, Any]:
    cur = await store.latest_release(conn, release_id)
    if cur is None:
        raise RoutingError("RELEASE_NOT_FOUND", "no such release", 404)
    deps = await _dependencies(conn)
    state = "PREFLIGHT_PASSED" if result.get("passes") else "DRAFT"
    row = {"release_id": release_id, "version": cur.version + 1, "state": state, "maker": cur.maker, "risk": cur.risk, "dependency_digest": rt.digest(deps), "dependencies": deps, "scope": dict(cur.scope), "preflight": dict(result), "reason": None if result.get("passes") else "PREFLIGHT_BLOCKED_BY_REQUIRED_COHORT", "recorded_by": by}
    if not result.get("passes"):
        row["preflight"] = dict(result)
    await store.insert_release(conn, row)
    return release_view(await _must(conn, release_id), await store.approvals_of(conn, release_id))


async def _must(conn: AsyncConnection, release_id: str) -> Any:
    r = await store.latest_release(conn, release_id)
    if r is None:
        raise RoutingError("RELEASE_NOT_FOUND", "no such release", 404)
    return r


async def approve_release(conn: AsyncConnection, release_id: str, *, approver: str, role: str) -> dict[str, Any]:
    cur = await _must(conn, release_id)
    if cur.state not in ("PREFLIGHT_PASSED", "APPROVED"):
        raise RoutingError("RELEASE_NOT_READY_FOR_APPROVAL", f"it is {cur.state}; a release is approved only after its preflight passed")
    if approver == cur.maker:
        raise RoutingError("MAKER_CANNOT_APPROVE_OWN_RELEASE", "the maker cannot approve", 403)
    if role not in rt.required_approvers(cur.risk):
        raise RoutingError("ROLE_NOT_REQUIRED_FOR_THIS_RELEASE", f"{role} is not one of {sorted(rt.required_approvers(cur.risk))}", 422)
    if not await store.insert_approval(conn, release_id, approver, role, cur.dependency_digest):
        raise RoutingError("ALREADY_APPROVED_BY_THIS_PERSON", "one person signs a release once", 409)
    return release_view(cur, await store.approvals_of(conn, release_id))


async def publish_release(conn: AsyncConnection, release_id: str, *, by: str, tenant_id: str | None = None) -> dict[str, Any]:
    cur = await _must(conn, release_id)
    if cur.state not in ("PREFLIGHT_PASSED", "APPROVED"):
        raise RoutingError("RELEASE_NOT_READY", f"it is {cur.state}")
    deps = await _dependencies(conn)
    approvals = await store.approvals_of(conn, release_id)
    draft = rt.ReleaseDraft(release_id, cur.maker, cur.risk, cur.dependency_digest, dict(cur.scope))
    problems = rt.publish_problems(draft, [{"approver": a.approver, "role": a.role, "dependency_digest": a.dependency_digest} for a in approvals], current_dependency_digest=rt.digest(deps))
    if problems:
        raise RoutingError("RELEASE_CANNOT_GO_LIVE", "; ".join(problems), 409)
    if by == cur.maker:
        raise RoutingError("MAKER_CANNOT_PUBLISH_OWN_RELEASE", "someone other than the maker publishes", 403)
    await store.insert_release(conn, {"release_id": release_id, "version": cur.version + 1, "state": "LIVE", "maker": cur.maker, "risk": cur.risk, "dependency_digest": cur.dependency_digest, "dependencies": dict(cur.dependencies), "scope": dict(cur.scope), "preflight": dict(cur.preflight), "reason": None, "recorded_by": by})
    if tenant_id:
        await _witness(conn, tenant_id, event="route.release.live", aggregate_type="route_release", aggregate_id=release_id, version=cur.version + 1, target_type="route_release", target_ref=release_id, outcome="SUCCESS", cls=EvidenceClass.E3, now=datetime.now(UTC), actor=by, details={"release_id": release_id, "dependency_digest": cur.dependency_digest, "risk": cur.risk})
    return release_view(await _must(conn, release_id), approvals)


async def rollback_release(conn: AsyncConnection, release_id: str, *, by: str, reason: str, tenant_id: str | None = None) -> dict[str, Any]:
    cur = await _must(conn, release_id)
    if cur.state != "LIVE":
        raise RoutingError("RELEASE_NOT_LIVE", f"it is {cur.state}")
    await store.insert_release(conn, {"release_id": release_id, "version": cur.version + 1, "state": "ROLLED_BACK", "maker": cur.maker, "risk": cur.risk, "dependency_digest": cur.dependency_digest, "dependencies": dict(cur.dependencies), "scope": dict(cur.scope), "preflight": dict(cur.preflight) if cur.preflight else None, "reason": reason, "recorded_by": by})
    if tenant_id:
        await _witness(conn, tenant_id, event="route.quarantined" if "QUARANTINE" in reason.upper() else "route.release.rolled_back", aggregate_type="route_release", aggregate_id=release_id, version=cur.version + 1, target_type="route_release", target_ref=release_id, outcome="SUCCESS", cls=EvidenceClass.E3, now=datetime.now(UTC), actor=by, details={"release_id": release_id, "reason_code": "ROLLED_BACK"})
    return release_view(await _must(conn, release_id), await store.approvals_of(conn, release_id))


# ----------------------------------------------------------------------------------------------------------------------- coverage


async def cells(conn: AsyncConnection, now: datetime) -> list[rt.Cell]:
    out: list[rt.Cell] = []
    for r in await store.latest_capabilities(conn):
        m = dict(r.manifest)
        product = str(m.get("product", ""))
        purpose = str(m.get("purpose", "kyc"))
        specimens = m.get("specimens", {}) or {}
        for country in m.get("countries", []):
            c = str(country).upper()
            market = await market_releases.market_open(conn, rm.MarketKey(c, product, purpose), now=now)
            valid = r.state == "APPROVED" and r.qualified_until is not None and r.qualified_until > now and r.contract_until is not None and r.contract_until > now
            tier = "DEGRADED" if valid and m.get("health") == "DEGRADED" else "ENABLED" if valid and market.open else "QUALIFIED" if valid else "RESTRICTED" if r.state in ("SUSPENDED", "EXPIRED") else "CONTRACTED" if r.contract_until else "LABORATORY"
            for spec in specimens.get(c) or specimens.get(c.lower()) or ["unspecified"]:
                out.append(rt.Cell(f"{r.provider_id}/{r.method}/{c}/{spec}", c, str(spec), r.method, tier, f"{r.provider_id}:{r.manifest_digest}" if r.approved_by else None, r.state == "APPROVED", min(x for x in (r.qualified_until, r.contract_until, r.legal_determination_valid_until) if x is not None) if r.qualified_until and r.contract_until and r.legal_determination_valid_until else None))
    return out


async def coverage(conn: AsyncConnection, now: datetime | None = None, *, method: str | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    return rt.coverage_counts(await cells(conn, now), now, method=method)


async def public_claim(conn: AsyncConnection, now: datetime | None = None) -> dict[str, Any]:
    """The only source of an external coverage number: released cells. Refuses (RoutingError) when a cell offered for counting lacks approved, current evidence."""
    now = now or datetime.now(UTC)
    released = [c for c in await cells(conn, now) if rt.cell_status(c, now) == "ENABLED"]
    try:
        out = rt.public_claim(released, now)
    except ValueError as e:
        raise RoutingError("CLAIM_BLOCKED", str(e)) from e
    return {**out, "caveat": "Counts markets that are launched, approved and current today. It is not a promise of any country, document or method not listed, and no certification is implied."}


async def reconciliation(conn: AsyncConnection, lines: Sequence[rt.VendorLine]) -> dict[str, Any]:
    ledger = [
        {"external_operation_id": r.external_operation_id, "state": r.to_state, "expected_cost_minor": r.expected_cost_minor if r.expected_cost_minor is not None else -1, "currency": r.currency}
        for r in await store.ledger_rows(conn)
        if r.external_operation_id
    ]
    return rt.reconcile_costs(ledger, lines)


async def decision_evidence(conn: AsyncConnection, decision_id: str) -> list[dict[str, Any]]:
    """The audit records written for this decision, so a reviewer can see the trace matches what the evidence ledger holds (digests and codes only)."""
    from zid_platform.modules.evidence.ports import audit_timeline

    return [dict(x) for x in await audit_timeline(conn, "route_decision", decision_id)]

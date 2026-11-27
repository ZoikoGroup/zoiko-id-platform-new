"""The fraud graph, as the company's own analysts use it (CR-168; ZID-CP-WF05 v2.0).

It is a PROJECTION over the company's own sessions: nothing here is a second person store, a second case system or a second decision. A refresh reads the sessions, records sourced observations, derives typed links and lets a
deterministic rule open leads. A lead is a place to look. Only a person's adjudicated review case (the existing WF04 queue) lets a finding be recorded, and only a confirmed finding lets someone PROPOSE an exact-target action,
which a different person approves. Nothing in this module changes a session, a decision, a webhook or a customer's status.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verify_api.application import review_service
from zid_platform.modules.verify_api.domain import cases as case_domain
from zid_platform.modules.verify_api.domain import fraud as f
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain import review as rv
from zid_platform.modules.verify_api.domain import sessions as sd
from zid_platform.modules.verify_api.infrastructure import fraud_store as db
from zid_platform.modules.verify_api.infrastructure import review_store as rdb
from zid_platform.modules.verify_api.infrastructure import session_store as sdb

INGEST_LIMIT = 1000
LIST_LIMIT = 100


def _iso(x: datetime | None) -> str | None:
    return x.isoformat() if x else None


def _id(prefix: str, *parts: str) -> str:
    return prefix + hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _active(e: dict[str, Any], now: datetime) -> bool:
    return e["status"] == "ACTIVE" and e["expires_at"] > now  # type: ignore[no-any-return]


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    scope: str,
    kind: str,
    actor: str,
    now: datetime,
    *,
    reason: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    await db.append_event(
        conn,
        scope_id=scope,
        tenant_id=tenant_id,
        event_type=kind,
        actor=actor,
        reason=reason,
        data=data or {},
        now=now,
    )


# ------------------------------------------------------------------------------- observations (what the graph is made of)


async def record_observation(
    conn: AsyncConnection, tenant_id: str, *, source: str, kind: str, session_id: str, source_event_id: str, token: str | None, observed_at: datetime,
    environment: str, region: str, purpose: str, now: datetime,
) -> str:  # fmt: skip
    """One sourced fact, idempotent on its source event. A source that is not enabled, or a kind it does not produce, is kept as QUARANTINED with its reason and NO token, so it can never make a link."""
    problem = f.source_problem(source, kind)
    obs_id = _id("obs_", tenant_id, source, kind, source_event_id)
    new = await db.insert_observation(
        conn,
        tenant_id=tenant_id,
        o={
            "id": obs_id, "env": environment, "reg": region, "pur": purpose, "sid": session_id, "kind": kind, "src": source, "sev": source_event_id,
            "tok": None if problem else token, "val": None, "obs": observed_at, "exp": observed_at + f.OBSERVATION_TTL,
            "rel": "COMPANY_SUPPLIED", "cls": "STANDARD", "dg": f.observation_digest(source, source_event_id, kind, token, observed_at),
            "st": "QUARANTINED" if problem else "ACTIVE", "qr": problem,
        },
    )  # fmt: skip
    return obs_id if new else ("quarantined" if problem else "known")


async def _ingest_sessions(conn: AsyncConnection, tenant_id: str, now: datetime) -> dict[str, int]:
    seen = quarantined = 0
    for row in await db.sessions_for_ingest(conn, limit=INGEST_LIMIT):
        ref = str(row["customer_reference"] or "")
        if not ref or ref.startswith("erased-"):
            continue  # an erased reference makes no observation
        token = f.reference_token(case_domain.reference_hash(tenant_id, ref))
        got = await record_observation(
            conn, tenant_id, source="verify_sessions", kind="CUSTOMER_REFERENCE", session_id=row["session_id"], source_event_id=row["session_id"], token=token,
            observed_at=row["created_at"], environment=row["environment"], region=row["region"], purpose=row["purpose_code"], now=now,
        )  # fmt: skip
        seen += got.startswith("obs_")
        quarantined += got == "quarantined"
    return {"new_observations": seen, "quarantined": quarantined}


# ------------------------------------------------------------------------------- links and leads


async def _derive_group(
    conn: AsyncConnection, tenant_id: str, token: str, now: datetime
) -> tuple[list[dict[str, Any]], bool]:
    """Typed links for one token: every other session is linked to the earliest one. Bounded, and says so when it cuts."""
    obs = await db.observations_for_token(conn, token, limit=f.GROUP_MAX + 1)
    truncated = len(obs) > f.GROUP_MAX
    obs = obs[: f.GROUP_MAX]
    if len(obs) < 2:
        return [], truncated
    anchor = obs[0]
    kind = f.EDGE_KINDS["EXACT_REFERENCE"]
    edges = []
    for other in obs[1:]:
        if other["session_id"] == anchor["session_id"]:
            continue
        a, b = f.order_pair(anchor["session_id"], other["session_id"])
        edges.append(
            await db.upsert_edge(
                conn,
                tenant_id=tenant_id,
                e={
                    "id": _id("edg_", tenant_id, a, b, kind.name), "a": a, "b": b, "ty": kind.name, "st": kind.strength.value, "me": kind.method, "cc": kind.strength.value,
                    "rv": f.RULES_VERSION, "ev": [anchor["observation_id"], other["observation_id"]], "tg": token,
                    "fs": min(anchor["observed_at"], other["observed_at"]), "ls": max(anchor["observed_at"], other["observed_at"]),
                    "ex": min(anchor["expires_at"], other["expires_at"]),
                },
            )
        )  # fmt: skip
    return edges, truncated


async def _group(
    conn: AsyncConnection, token: str, now: datetime
) -> tuple[list[dict[str, Any]], list[f.SessionFact]]:
    """One token's ACTIVE links (a person's exclusion counts) and the facts of the sessions they join."""
    edges = [e for e in await db.edges_for_group(conn, token) if _active(e, now)]
    nodes = sorted({n for e in edges for n in (e["node_a"], e["node_b"])})
    info = await db.outcomes_for_sessions(conn, nodes) if nodes else {}
    return edges, [
        f.SessionFact(s, str(i["application_id"]), i["created_at"], i["outcome"])
        for s, i in info.items()
    ]


async def _open_lead(
    conn: AsyncConnection, tenant_id: str, token: str, cand: f.Candidate, edges: Sequence[dict[str, Any]], rule_version: str, now: datetime, actor: str
) -> str:  # fmt: skip
    """Open or refresh the lead for one group under one rule. Returns the lead's group key."""
    key = f.group_key(cand.rule_id, token)
    lead_id = _id("led_", tenant_id, cand.rule_id, key)
    inside = set(cand.nodes)
    edge_ids = sorted(
        e["edge_id"] for e in edges if e["node_a"] in inside and e["node_b"] in inside
    )
    snap = f.snapshot_hash(cand.nodes, edge_ids)
    before = await db.get_lead(conn, lead_id)
    row = await db.upsert_lead(
        conn,
        tenant_id=tenant_id,
        lead={
            "lead_id": lead_id, "environment": await _environment(conn, cand.nodes[0]), "rule_id": cand.rule_id, "rule_version": rule_version, "group_key": key, "group_token": token,
            "risk_class": cand.risk_class, "seed_session": cand.nodes[0], "nodes": list(cand.nodes), "edge_ids": edge_ids, "snapshot_hash": snap, "uncertain": list(cand.uncertain),
            "summary": cand.summary, "expires_at": now + f.LEAD_EXPIRY,
        },
    )  # fmt: skip
    if row is not None and (
        before is None or before["snapshot_hash"] != snap or before["status"] == "EXPIRED"
    ):
        kind = (
            "lead_opened"
            if before is None
            else ("lead_reopened" if before["status"] == "EXPIRED" else "lead_snapshot_changed")
        )
        await _audit(
            conn,
            tenant_id,
            lead_id,
            kind,
            actor,
            now,
            data={"rule": cand.rule_id, "nodes": len(cand.nodes), "snapshot": snap},
        )
    return key


async def _evaluate(
    conn: AsyncConnection, tenant_id: str, token: str, truncated: bool, now: datetime, actor: str
) -> str | None:
    """Judge one group by the built-in rules and open or refresh its lead. Returns the lead's group key when a rule applies."""
    edges, facts = await _group(conn, token, now)
    cand = f.evaluate_group(facts, truncated=truncated) if edges else None
    return (
        await _open_lead(conn, tenant_id, token, cand, edges, f.RULES_VERSION, now, actor)
        if cand
        else None
    )


async def _evaluate_query(
    conn: AsyncConnection,
    tenant_id: str,
    token: str,
    q: dict[str, Any],
    truncated: bool,
    now: datetime,
    actor: str,
) -> str | None:
    edges, facts = await _group(conn, token, now)
    mine = [e for e in edges if e["edge_type"] in q["params"]["edge_types"]]
    cand = (
        f.evaluate_query(
            q["params"],
            q["query_key"],
            facts,
            f.best_strength(e["strength"] for e in mine),
            now=now,
            truncated=truncated,
        )
        if mine
        else None
    )
    return (
        await _open_lead(
            conn,
            tenant_id,
            token,
            cand,
            mine,
            f"{f.RULES_VERSION}+{q['query_key']}@v{q['version']}",
            now,
            actor,
        )
        if cand
        else None
    )


async def _environment(conn: AsyncConnection, session_id: str) -> str:
    row = await sdb.get_session_any(conn, session_id)
    return str(row["environment"]) if row else "SANDBOX"


async def refresh(
    conn: AsyncConnection, tenant_id: str, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """Bring the projection up to date with the sessions. Idempotent: the same sessions give the same observations, links and leads. A lead that no longer qualifies (a link was excluded, a reference erased) and that nobody has acted on expires."""
    now = now or datetime.now(UTC)
    expired_obs, expired_edges = await db.expire_due(conn, now)
    await db.expire_actions(conn, now)
    ingested = await _ingest_sessions(conn, tenant_id, now)
    live: set[str] = set()
    groups = 0
    queries = await db.published_queries(conn)
    for token, _n in await db.active_tokens(conn):
        groups += 1
        _edges, truncated = await _derive_group(conn, tenant_id, token, now)
        for key in [
            await _evaluate(conn, tenant_id, token, truncated, now, actor),
            *[
                await _evaluate_query(conn, tenant_id, token, q, truncated, now, actor)
                for q in queries
            ],
        ]:
            if key:
                live.add(key)
    await db.expire_entries(conn, now)
    stale = [x["group_key"] for x in await db.open_leads(conn) if x["group_key"] not in live]
    gone = await db.expire_leads_for_keys(conn, stale, now) if stale else 0
    await _audit(
        conn,
        tenant_id,
        f"refresh:{tenant_id}",
        "graph_refreshed",
        actor,
        now,
        data={
            **ingested,
            "groups": groups,
            "leads_expired": gone,
            "observations_expired": expired_obs,
            "edges_expired": expired_edges,
        },
    )
    return {
        **ingested,
        "groups": groups,
        "leads_expired": gone,
        "observations_expired": expired_obs,
        "edges_expired": expired_edges,
        "as_of": now.isoformat(),
    }


# ------------------------------------------------------------------------------- views


def edge_view(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "edge_id": e["edge_id"],
        "type": e["edge_type"],
        "nodes": [e["node_a"], e["node_b"]],
        "strength": e["strength"],
        "method": e["method"],
        "confidence_class": e["confidence_class"],
        "rule_version": e["rule_version"],
        "evidence_ref": list(e["evidence"]),
        "first_seen": _iso(e["first_seen"]),
        "last_seen": _iso(e["last_seen"]),
        "expires_at": _iso(e["expires_at"]),
        "status": e["status"],
        "edge_version": e["edge_version"],
        "weak_only": f.only_weak([e["strength"]]),
        "fraud_finding": False,  # an edge is never a finding
    }


def lead_view(
    x: dict[str, Any],
    *,
    current_edges: Sequence[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    out = {
        "lead_id": x["lead_id"],
        "rule_id": x["rule_id"],
        "rule_version": x["rule_version"],
        "risk_class": x["risk_class"],
        "status": x["status"],
        "finding": x["finding"],
        "seed_session": x["seed_session"],
        "sessions": len(x["nodes"]),
        "summary": x["summary"],
        "uncertain_facts": x["uncertain"],
        "snapshot_hash": x["snapshot_hash"],
        "review_case_id": x["review_case_id"],
        "lead_version": x["lead_version"],
        "created_at": _iso(x["created_at"]),
        "expires_at": _iso(x["expires_at"]),
        "environment": x["environment"],
        "sla": f.lead_sla(
            status=x["status"],
            finding=x["finding"],
            risk=x["risk_class"],
            created_at=x["created_at"],
            now=now or datetime.now(UTC),
        ),
    }
    if current_edges is not None:
        ids = [e["edge_id"] for e in current_edges]
        out["snapshot_current"] = f.snapshot_hash(x["nodes"], ids) == x["snapshot_hash"]
        out["strengths"] = {
            s.value: sum(1 for e in current_edges if e["strength"] == s.value) for s in f.Strength
        }
    return out


async def _current_edges(
    conn: AsyncConnection, lead: dict[str, Any], now: datetime
) -> list[dict[str, Any]]:
    return [e for e in await db.edges_by_ids(conn, lead["edge_ids"]) if _active(e, now)]


async def _load_lead(conn: AsyncConnection, lead_id: str, *, lock: bool = False) -> dict[str, Any]:
    x = await db.get_lead(conn, lead_id, lock=lock)
    if x is None:
        raise m.PartnerError(
            "GRAPH_ACCESS_DENIED", "No such lead."
        )  # the same answer for another company's
    return x


async def list_leads(
    conn: AsyncConnection, *, status: str | None, finding: str | None, limit: int = LIST_LIMIT
) -> dict[str, Any]:
    if status is not None and status not in {x.value for x in f.LeadStatus}:
        raise m.PartnerError("VALIDATION_FAILED", "Unknown lead state.")
    if finding is not None and finding not in {x.value for x in f.Finding}:
        raise m.PartnerError("VALIDATION_FAILED", "Unknown finding.")
    return {
        "items": [
            lead_view(x)
            for x in await db.list_leads(
                conn, status=status, finding=finding, limit=min(limit, LIST_LIMIT)
            )
        ]
    }


async def get_lead(
    conn: AsyncConnection, tenant_id: str, actor: str, lead_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    x = await _load_lead(conn, lead_id)
    edges = await _current_edges(conn, x, now)
    out = lead_view(x, current_edges=edges, now=now)
    out["edges"] = [edge_view(e) for e in edges]
    out["notes"] = [
        {
            "note_id": str(n["note_id"]),
            "edge_id": n["edge_id"],
            "kind": n["kind"],
            "body": n["body"],
            "source_ref": n["source_ref"],
            "author": str(n["author"]),
            "at": _iso(n["created_at"]),
        }
        for n in await db.notes_for(conn, lead_id)
    ]
    out["actions"] = [
        action_view(a) for a in await db.list_actions(conn, lead_id=lead_id, limit=20)
    ]
    await _audit(conn, tenant_id, lead_id, "lead_viewed", actor, now)
    return out


# ------------------------------------------------------------------------------- reading the graph (bounded, masked, audited)


async def graph(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    seed: str,
    *,
    depth: int,
    limit: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    f.check_read_limits(depth, limit)
    if await sdb.get_session_any(conn, seed) is None:
        raise m.PartnerError(
            "GRAPH_ACCESS_DENIED", "No such session."
        )  # the same answer for one that is not this company's
    edges: dict[str, dict[str, Any]] = {}
    frontier, known = [seed], {seed}
    for _ in range(depth):
        if not frontier or len(known) > limit * 2:
            break
        got = [e for e in await db.edges_touching(conn, frontier, now)]
        nxt: list[str] = []
        for e in got:
            edges[e["edge_id"]] = e
            for n in (e["node_a"], e["node_b"]):
                if n not in known:
                    known.add(n)
                    nxt.append(n)
        frontier = sorted(nxt)
    order, kept, more = f.expand(
        seed,
        [(e["edge_id"], e["node_a"], e["node_b"]) for e in edges.values()],
        depth=depth,
        limit=limit,
    )
    info = await db.outcomes_for_sessions(conn, order)
    nodes = [
        {
            "id": n,
            "type": "session",
            "outcome": (info.get(n) or {}).get("outcome") or "undecided",
            "country": (info.get(n) or {}).get("country"),
            "is_seed": n == seed,
        }
        for n in order
    ]
    gap = await db.retracted_touching(conn, order) > 0
    out = {
        "graph_snapshot_id": f.snapshot_hash(order, kept),
        "tenant_scope": "derived_from_principal",
        "purpose": "fraud_investigation",
        "query_version": f.RULES_VERSION,
        "nodes": nodes,
        "edges": [edge_view(edges[i]) for i in kept],
        "has_more": more,
        "masked": True,
        "source_gap": gap,
    }
    await _audit(
        conn,
        tenant_id,
        f"seed:{seed}",
        "graph_viewed",
        actor,
        now,
        data={"depth": depth, "limit": limit, "nodes": len(nodes), "edges": len(kept)},
    )
    return out


async def get_edge(
    conn: AsyncConnection, tenant_id: str, actor: str, edge_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    e = await db.get_edge(conn, edge_id)
    if e is None:
        raise m.PartnerError("GRAPH_ACCESS_DENIED", "No such link.")
    out = edge_view(e)
    kind = f.EDGE_KINDS[e["edge_type"]]
    out["explanation"] = f.explain_edge(
        e["edge_type"], first_seen=e["first_seen"], last_seen=e["last_seen"], n=len(e["evidence"])
    )
    out["does_not_show"] = kind.note
    out["source"] = kind.source
    out["status_reason"] = e["status_reason"]
    out["observations"] = [
        {"observation_id": o["observation_id"], "session_id": o["session_id"], "source": o["source"], "kind": o["kind"], "observed_at": _iso(o["observed_at"]), "reliability": o["reliability"],
         "digest": o["digest"], "status": o["status"]}
        for o in await db.observations_by_ids(conn, e["evidence"])
    ]  # fmt: skip
    await _audit(conn, tenant_id, f"edge:{edge_id}", "edge_viewed", actor, now)
    return out


async def challenge_edge(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    edge_id: str,
    action: str,
    reason: str | None,
    expected_version: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Dispute a link without deleting its history: it is left out of every current analysis, and can be put back. The reason is kept."""
    now = now or datetime.now(UTC)
    action, clean = f.check_challenge(action, reason)
    e = await db.get_edge(conn, edge_id, lock=True)
    if e is None:
        raise m.PartnerError("GRAPH_ACCESS_DENIED", "No such link.")
    if e["status"] == "RETRACTED":
        raise m.PartnerError(
            "EDGE_SOURCE_UNAVAILABLE", "The data behind this link was erased or has expired."
        )
    f.check_version(e["edge_version"], expected_version)
    want = "EXCLUDED" if action == "exclude" else "ACTIVE"
    if e["status"] == want:
        raise m.PartnerError(
            "EDGE_STATE_INVALID",
            f"This link is already {'excluded' if action == 'exclude' else 'active'}.",
        )
    if not await db.set_edge_status(
        conn, edge_id, expected=e["edge_version"], status=want, reason=clean, by=actor
    ):
        raise m.PartnerError("GRAPH_SNAPSHOT_STALE", "It changed since you looked.")
    await _audit(
        conn,
        tenant_id,
        f"edge:{edge_id}",
        "edge_excluded" if action == "exclude" else "edge_restored",
        actor,
        now,
        reason=clean,
    )
    return await get_edge(conn, tenant_id, actor, edge_id, now)


# ------------------------------------------------------------------------------- working a lead


async def triage(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    lead_id: str,
    status: str,
    note: str | None,
    expected_version: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    x = await _load_lead(conn, lead_id, lock=True)
    f.check_version(x["lead_version"], expected_version)
    try:
        target = f.LeadStatus(status)
    except ValueError as e:
        raise m.PartnerError("VALIDATION_FAILED", "Choose IN_ANALYSIS or CLEARED.") from e
    if target not in (f.LeadStatus.IN_ANALYSIS, f.LeadStatus.CLEARED):
        raise m.PartnerError("VALIDATION_FAILED", "Choose IN_ANALYSIS or CLEARED.")
    f.check_lead_transition(f.LeadStatus(x["status"]), target)
    clean = f.check_note(note, required=target is f.LeadStatus.CLEARED)
    await db.set_lead(conn, lead_id, expected=x["lead_version"], status=target.value, now=now)
    await _audit(
        conn, tenant_id, lead_id, f"lead_{target.value.lower()}", actor, now, reason=clean or None
    )
    return await get_lead(conn, tenant_id, actor, lead_id, now)


async def add_note(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    lead_id: str,
    *,
    kind: str,
    body: str | None,
    edge_id: str | None,
    source_ref: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """An investigator's note, or a fact that argues AGAINST the link (a household, a shared device) with where it came from."""
    now = now or datetime.now(UTC)
    x = await _load_lead(conn, lead_id)
    if kind not in ("EXCULPATORY", "NOTE"):
        raise m.PartnerError("VALIDATION_FAILED", "A note is NOTE or EXCULPATORY.")
    clean = f.check_note(body)
    ref = " ".join((source_ref or "").split())[:200] or None
    if kind == "EXCULPATORY" and ref is None:
        raise m.PartnerError("NOTE_REQUIRED", "Say where the exculpatory fact comes from.")
    if edge_id is not None and edge_id not in x["edge_ids"]:
        raise m.PartnerError("VALIDATION_FAILED", "That link is not part of this lead.")
    nid = await db.insert_note(
        conn,
        tenant_id=tenant_id,
        lead_id=lead_id,
        edge_id=edge_id,
        kind=kind,
        body=clean,
        source_ref=ref,
        author=actor,
    )
    await _audit(
        conn, tenant_id, lead_id, "note_added", actor, now, data={"kind": kind, "note_id": nid}
    )
    return await get_lead(conn, tenant_id, actor, lead_id, now)


async def escalate(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    lead_id: str,
    expected_version: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Hand the lead to the review queue (WF04), where a person with the right duty looks. It never decides a session: it opens the case that session already needs (or finds the one it has) and adds the lead to its evidence. One lead,
    one case, however often this is retried."""
    now = now or datetime.now(UTC)
    x = await _load_lead(conn, lead_id, lock=True)
    if x["review_case_id"]:
        return await get_lead(conn, tenant_id, actor, lead_id, now)  # a retry: the same case
    f.check_version(x["lead_version"], expected_version)
    f.check_lead_transition(f.LeadStatus(x["status"]), f.LeadStatus.ESCALATED)
    chosen: dict[str, Any] | None = None
    for sid in x["nodes"][: f.GROUP_MAX]:
        row = await sdb.get_session_any(conn, sid)
        if row is not None and row["status"] == sd.Status.MANUAL_REVIEW.value:
            chosen = row
            break
    if chosen is None:
        raise m.PartnerError(
            "LEAD_NOT_ELIGIBLE",
            "None of this lead's sessions is waiting for a person to review. A lead can only be handed to the review queue through a session that needs review; otherwise keep working it here.",
        )
    existing = await rdb.case_for_session(conn, chosen["session_id"])
    case_id = (
        existing["case_id"]
        if existing
        else (
            await review_service.open_case(conn, tenant_id, actor, chosen["session_id"], now=now)
        )["case_id"]
    )
    edges = await _current_edges(conn, x, now)
    await review_service.attach_evidence(
        conn, case_id, actor, kind="fraud_lead", label="Fraud graph lead (a lead, not a finding)",
        summary={"lead_id": lead_id, "rule": x["rule_id"], "risk_class": x["risk_class"], "linked_sessions": len(x["nodes"]),
                 "link_strengths": {s.value: sum(1 for e in edges if e["strength"] == s.value) for s in f.Strength}, "snapshot": x["snapshot_hash"], "is_a_finding": False},
        now=now,
    )  # fmt: skip
    await db.set_lead(
        conn,
        lead_id,
        expected=x["lead_version"],
        status=f.LeadStatus.ESCALATED.value,
        case_id=case_id,
        now=now,
    )
    await _audit(conn, tenant_id, lead_id, "lead_escalated", actor, now, data={"case_id": case_id})
    return await get_lead(conn, tenant_id, actor, lead_id, now)


async def record_finding(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    lead_id: str,
    finding: str,
    note: str | None,
    expected_version: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A finding rests on the review case: it must be final, and the person recording it took no part in it. Nothing promotes a lead to a finding by itself."""
    now = now or datetime.now(UTC)
    chosen = f.check_finding(finding)
    clean = f.check_note(note)
    x = await _load_lead(conn, lead_id, lock=True)
    f.check_version(x["lead_version"], expected_version)
    case = await rdb.get_case(conn, x["review_case_id"]) if x["review_case_id"] else None
    people = (await rdb.reviewers_of(conn, case["case_id"])) if case else set()
    problems = f.finding_problems(
        lead_status=f.LeadStatus(x["status"]),
        case_final=bool(case and case["status"] == "APPROVED_FINAL"),
        person=actor,
        case_people=people,
    )
    if problems:
        code = {"LEAD_NOT_ESCALATED": "LEAD_STATE_INVALID"}.get(problems[0], problems[0])
        raise m.PartnerError(
            code,
            "A finding needs an escalated lead whose review case is final, recorded by someone who did not take part in it.",
        )
    if x["finding"] != f.Finding.UNREVIEWED.value:
        raise m.PartnerError(
            "LEAD_STATE_INVALID",
            "This lead already has a finding. A correction is a new lead, so the earlier record stays.",
        )
    await db.set_lead(
        conn, lead_id, expected=x["lead_version"], finding=chosen.value, finding_by=actor, now=now
    )
    await _audit(
        conn,
        tenant_id,
        lead_id,
        "finding_recorded",
        actor,
        now,
        reason=clean,
        data={"finding": chosen.value, "case_id": x["review_case_id"]},
    )
    return await get_lead(conn, tenant_id, actor, lead_id, now)


# ------------------------------------------------------------------------------- action proposals (exact targets, a second person)


def action_view(
    a: dict[str, Any], targets: Sequence[dict[str, Any]] | None = None
) -> dict[str, Any]:
    out = {
        "action_id": a["action_id"],
        "lead_id": a["lead_id"],
        "proposal_type": a["proposal_type"],
        "targets": list(a["targets"]),
        "basis_ref": a["basis_ref"],
        "impact_ref": a["impact_ref"],
        "impact": a["impact"],
        "manifest_checksum": a["manifest_checksum"],
        "status": a["status"],
        "maker": str(a["maker"]),
        "decided_by": str(a["decided_by"]) if a["decided_by"] else None,
        "decided_at": _iso(a["decided_at"]),
        "decision_note": a["decision_note"],
        "expires_at": _iso(a["expires_at"]),
        "created_at": _iso(a["created_at"]),
        "action_version": a["action_version"],
        "executes_changes": False,  # a proposal and its approval change no session, decision or customer
    }
    if targets is not None:
        out["receipt"] = [
            {
                "position": t["position"],
                "target": t["target"],
                "strength": t["strength"],
                "state": t["state"],
            }
            for t in targets
        ]
    return out


async def _target_facts(
    conn: AsyncConnection, lead: dict[str, Any], now: datetime
) -> tuple[dict[str, f.TargetFacts], list[dict[str, Any]]]:
    edges = await _current_edges(conn, lead, now)
    by: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        by[e["node_a"]].append(e["strength"])
        by[e["node_b"]].append(e["strength"])
    return {n: f.TargetFacts(n, True, tuple(by.get(n, []))) for n in lead["nodes"]}, edges


def _first_problem(problems: Sequence[str], text: str) -> m.PartnerError:
    return m.PartnerError(problems[0].split(":")[0], f"{text} ({', '.join(problems)})")


async def propose_action(
    conn: AsyncConnection, tenant_id: str, actor: str, lead_id: str, *, proposal_type: str, targets: Sequence[str], basis_ref: str | None, impact_ref: str | None,
    expires_at: datetime, idempotency_key: str, expected_lead_version: int | None, now: datetime | None = None,
) -> dict[str, Any]:  # fmt: skip
    now = now or datetime.now(UTC)
    key = (idempotency_key or "").strip()
    if not 8 <= len(key) <= 80:
        raise m.PartnerError(
            "VALIDATION_FAILED", "An idempotency key of 8 to 80 characters is required."
        )
    again = await db.action_by_key(conn, key)
    if again is not None:
        return action_view(again, await db.action_targets(conn, again["action_id"]))
    basis = f.check_basis(basis_ref, "legal basis or policy reference")
    impact_note = f.check_basis(impact_ref, "impact assessment reference")
    x = await _load_lead(conn, lead_id, lock=True)
    f.check_version(x["lead_version"], expected_lead_version)
    facts, _edges = await _target_facts(conn, x, now)
    problems = f.proposal_problems(
        finding=f.Finding(x["finding"]),
        targets=list(targets),
        facts=facts,
        proposal_type=proposal_type,
        expires_at=expires_at,
        now=now,
    )
    if problems:
        raise _first_problem(problems, "This proposal is refused")
    chosen = {t: facts[t] for t in targets}
    info = await db.outcomes_for_sessions(conn, list(targets))
    rows = [
        {
            "session_id": t,
            "strength": (f.best_strength(chosen[t].strengths) or f.Strength.WEAK).value,
            "edges": len(chosen[t].strengths),
            "outcome": (info.get(t) or {}).get("outcome") or "undecided",
            "reversible": True,
        }
        for t in targets
    ]
    impact = {
        "targets": rows,
        "by_strength": {
            s.value: sum(1 for r in rows if r["strength"] == s.value) for s in f.Strength
        },
        "total": len(rows),
        "reversible": True,
    }
    action_id = "act_" + uuid.uuid4().hex[:24]
    checksum = f.manifest_checksum(
        tenant_id=tenant_id,
        lead_id=lead_id,
        proposal_type=proposal_type,
        targets=list(targets),
        basis_ref=basis,
        impact_ref=impact_note,
        snapshot=x["snapshot_hash"],
        expires_at=expires_at,
    )
    made = await db.insert_action(
        conn,
        tenant_id=tenant_id,
        a={"action_id": action_id, "lead_id": lead_id, "proposal_type": proposal_type, "targets": list(targets), "basis_ref": basis, "impact_ref": impact_note, "impact": impact,
           "snapshot_hash": x["snapshot_hash"], "manifest_checksum": checksum, "expires_at": expires_at, "maker": actor, "idempotency_key": key},
        targets=rows,
    )  # fmt: skip
    if made is None:  # lost a race with the same key
        made = await db.action_by_key(conn, key)
        assert made is not None
    else:
        await _audit(
            conn,
            tenant_id,
            made["action_id"],
            "action_proposed",
            actor,
            now,
            reason=basis,
            data={"targets": len(targets), "checksum": checksum},
        )
        await _audit(
            conn,
            tenant_id,
            lead_id,
            "action_proposed",
            actor,
            now,
            data={"action_id": made["action_id"]},
        )
    return action_view(made, await db.action_targets(conn, made["action_id"]))


async def get_action(conn: AsyncConnection, action_id: str) -> dict[str, Any]:
    a = await db.get_action(conn, action_id)
    if a is None:
        raise m.PartnerError("GRAPH_ACCESS_DENIED", "No such proposal.")
    return action_view(a, await db.action_targets(conn, action_id))


async def list_actions(
    conn: AsyncConnection, *, lead_id: str | None, now: datetime | None = None
) -> dict[str, Any]:
    await db.expire_actions(conn, now or datetime.now(UTC))
    return {
        "items": [
            action_view(a) for a in await db.list_actions(conn, lead_id=lead_id, limit=LIST_LIMIT)
        ]
    }


async def approve_action(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    action_id: str,
    manifest_checksum: str | None,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The second person approves THE manifest they looked at: the same targets in the same order, on the same lead snapshot, with every target still linked by a link that supports a step."""
    now = now or datetime.now(UTC)
    a = await db.get_action(conn, action_id, lock=True)
    if a is None:
        raise m.PartnerError("GRAPH_ACCESS_DENIED", "No such proposal.")
    x = await _load_lead(conn, a["lead_id"], lock=True)
    facts, _ = await _target_facts(conn, x, now)
    linked = (
        x["snapshot_hash"] == a["snapshot_hash"]
        and x["finding"] == f.Finding.CONFIRMED.value
        and not any(
            f.only_weak(facts[t].strengths) or not facts[t].strengths
            for t in a["targets"]
            if t in facts
        )
        and all(t in facts for t in a["targets"])
    )
    problems = f.approval_problems(
        status=a["status"],
        maker=str(a["maker"]),
        approver=actor,
        expires_at=a["expires_at"],
        now=now,
        supplied=manifest_checksum,
        stored=a["manifest_checksum"],
        still_linked=linked,
    )
    if problems:
        raise _first_problem(problems, "This approval is refused")
    clean = f.check_note(note, required=False)
    if not await db.set_action(
        conn,
        action_id,
        expected=a["action_version"],
        status="APPROVED",
        by=actor,
        note=clean or None,
        now=now,
    ):
        raise m.PartnerError("GRAPH_SNAPSHOT_STALE", "It changed since you looked.")
    await _audit(
        conn,
        tenant_id,
        action_id,
        "action_approved",
        actor,
        now,
        reason=clean or None,
        data={"checksum": a["manifest_checksum"]},
    )
    return await get_action(conn, action_id)


async def _close_action(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    action_id: str,
    *,
    to: str,
    allowed: tuple[str, ...],
    note: str | None,
    now: datetime | None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    a = await db.get_action(conn, action_id, lock=True)
    if a is None:
        raise m.PartnerError("GRAPH_ACCESS_DENIED", "No such proposal.")
    if a["status"] not in allowed:
        raise m.PartnerError(
            "ACTION_NOT_PROPOSED",
            f"A proposal that is {a['status'].lower()} cannot be {to.lower()}.",
        )
    clean = f.check_note(note)
    by = (
        None if str(a["maker"]) == actor else actor
    )  # a maker may withdraw their own proposal; they are never recorded as its decider
    if not await db.set_action(
        conn, action_id, expected=a["action_version"], status=to, by=by, note=clean, now=now
    ):
        raise m.PartnerError("GRAPH_SNAPSHOT_STALE", "It changed since you looked.")
    await _audit(conn, tenant_id, action_id, f"action_{to.lower()}", actor, now, reason=clean)
    return await get_action(conn, action_id)


async def reject_action(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    action_id: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    return await _close_action(
        conn, tenant_id, actor, action_id, to="REJECTED", allowed=("PROPOSED",), note=note, now=now
    )


async def revoke_action(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    action_id: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Take an approval back. The earlier approval stays in the record; nothing is erased."""
    return await _close_action(
        conn, tenant_id, actor, action_id, to="REVOKED", allowed=("APPROVED",), note=note, now=now
    )


# ------------------------------------------------------------------------------- erasure, the overview, sources and the trail


async def purge_reference(
    conn: AsyncConnection, tenant_id: str, case_hash: bytes, now: datetime | None = None
) -> dict[str, int]:
    """Called when a customer reference is erased: its token leaves every observation, the links made from it are retracted, an entry for it on the blocklist is removed, and a lead nobody acted on expires. A lead that was already escalated keeps its record of what was done."""
    now = now or datetime.now(UTC)
    token = f.reference_token(case_hash)
    expired = await db.expire_leads_for_token(
        conn, token, now
    )  # before the token is wiped, or the leads cannot be found
    obs, edges = await db.erase_token(conn, token)
    if obs or edges:
        await _audit(
            conn,
            tenant_id,
            f"erasure:{tenant_id}",
            "reference_erased",
            "system",
            now,
            data={"observations": obs, "edges": edges, "leads_expired": expired},
        )
    return {"observations": obs, "edges": edges, "leads_expired": expired}


def _totals(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    """The one place the lead numbers are added up. The overview, the analytics and the metric list all read it, so no screen can show a different total."""
    n = lambda pred: sum(r["n"] for r in rows if pred(r))
    return {
        "leads_raised": sum(r["n"] for r in rows),
        "under_investigation": n(lambda r: r["status"] in ("IN_ANALYSIS", "ESCALATED")),  # type: ignore[no-untyped-call]
        "confirmed_findings": n(lambda r: r["finding"] == "CONFIRMED"),  # type: ignore[no-untyped-call]
        "cleared": n(lambda r: r["status"] == "CLEARED"),  # type: ignore[no-untyped-call]
        "no_outcome_yet": n(  # type: ignore[no-untyped-call]
            lambda r: (
                r["status"] in ("OPEN", "IN_ANALYSIS")
                or (r["status"] == "ESCALATED" and r["finding"] == "UNREVIEWED")
            )
        ),
        "no_finding": n(lambda r: r["finding"] == "NO_FINDING"),  # type: ignore[no-untyped-call]
        "inconclusive": n(lambda r: r["finding"] == "INCONCLUSIVE"),  # type: ignore[no-untyped-call]
        "unreviewed": n(lambda r: r["finding"] == "UNREVIEWED"),  # type: ignore[no-untyped-call]
    }


async def _queue(
    conn: AsyncConnection, now: datetime, *, data_missing: bool = False
) -> dict[str, Any]:
    """Leads still waiting, by where they stand against their time. Age is from the time each lead was opened: the same basis the review queue uses, so the two agree to the second."""
    rows = await db.list_leads(conn, status=None, finding=None, limit=500)
    states: dict[str, int] = {}
    oldest = 0
    for x in rows:
        s = f.lead_sla(
            status=x["status"],
            finding=x["finding"],
            risk=x["risk_class"],
            created_at=x["created_at"],
            now=now,
            data_missing=data_missing,
        )
        states[s["state"]] = states.get(s["state"], 0) + 1
        if s["state"] in ("RUNNING", "DUE_SOON", "BREACHED", "UNKNOWN"):
            oldest = max(oldest, s["age_seconds"])
    return {
        "by_state": states,
        "breached": states.get("BREACHED", 0),
        "with_review": states.get("WITH_REVIEW", 0),
        "oldest_waiting_seconds": oldest,
        "counted": len(rows),
        "basis": "from the time each lead was opened",
    }


async def overview(conn: AsyncConnection, now: datetime | None = None) -> dict[str, Any]:
    """Alerts, investigations and confirmed findings are different numbers, kept apart, each with its denominator. Missing data is shown as missing, never as 'safe'."""
    now = now or datetime.now(UTC)
    rows = await db.lead_counts(conn)
    totals = _totals(rows)
    sessions = await db.count_sessions(conn)
    observed = await db.observed_sessions(conn)
    health = await db.observation_health(conn)
    edges = await db.edge_counts(conn)
    return {
        "as_of": now.isoformat(),
        "scope": {"tenant": "derived_from_principal", "purpose": "fraud_investigation"},
        "leads": {
            "raised": totals["leads_raised"],
            "under_investigation": totals["under_investigation"],
            "confirmed_findings": totals["confirmed_findings"],
            "cleared": totals["cleared"],
            "no_outcome_yet": totals["no_outcome_yet"],
            "by_risk": {k: sum(r["n"] for r in rows if r["risk_class"] == k) for k in f.RISK},
        },
        "queue": await _queue(conn, now, data_missing=sessions > 0 and observed == 0),
        "links": {
            "active": edges.get("ACTIVE", 0),
            "excluded": edges.get("EXCLUDED", 0),
            "retracted": edges.get("RETRACTED", 0),
        },
        "coverage": {
            "sessions": sessions,
            "sessions_with_observation": observed,
            "sessions_without": max(0, sessions - observed),
            "note": "Sessions without an observation have no customer reference left (erased) or have not been read in yet. Run a refresh.",
            "sources": [
                {
                    "source": h["source"],
                    "status": h["status"],
                    "observations": h["n"],
                    "last_ingested": _iso(h["last_ingested"]),
                    "last_observed": _iso(h["last_observed"]),
                }
                for h in health
            ],
        },
        "definitions": {
            "raised": "Leads a rule opened. A lead is a place to look, not a finding. Denominator: all leads, whole retention window.",
            "confirmed_findings": "Leads whose finding was recorded after a final review case, by someone who took no part in it.",
            "no_outcome_yet": "Open, in analysis, or escalated without a finding. Never counted as cleared or as confirmed.",
        },
    }


async def metrics(conn: AsyncConnection, now: datetime | None = None) -> dict[str, Any]:
    """Every fraud number, defined once, read from one place. There is no second total anywhere: the overview and the analytics read the same function."""
    now = now or datetime.now(UTC)
    totals = _totals(await db.lead_counts(conn))
    q = await _queue(conn, now)
    label = f.label_quality(totals["confirmed_findings"], totals["no_finding"])
    values = {
        "leads_raised": totals["leads_raised"], "under_investigation": totals["under_investigation"], "confirmed_findings": totals["confirmed_findings"], "cleared": totals["cleared"],
        "no_outcome_yet": totals["no_outcome_yet"], "labelled": label["labelled"], "precision": label["precision"], "queue_breached": q["breached"], "queue_with_review": q["with_review"],
        "oldest_waiting_seconds": q["oldest_waiting_seconds"],
    }  # fmt: skip
    return {
        "as_of": now.isoformat(),
        "dimensions": {"company": "derived_from_principal", "purpose": "fraud_investigation", "window": "retention window"},
        "values": values,
        "definitions": f.METRIC_DEFINITIONS,
        "source": "verify.api_fraud_lead, read by one function",
        "reconciliation": "The overview, the analytics and this list add the same rows, so they cannot disagree. No Command Center metric source exists in this build to reconcile with; when one does, it should read this list rather than count again.",
        "reliable": label["reliable"],
    }  # fmt: skip


def sources() -> dict[str, Any]:
    return {
        "rules_version": f.RULES_VERSION,
        "sources": [
            {
                "source": s.name,
                "enabled": s.enabled,
                "qualified": s.qualified,
                "purpose": s.purpose,
                "reason": s.reason,
            }
            for s in f.SOURCES.values()
        ],
        "edge_types": [
            {
                "type": k.name,
                "strength": k.strength.value,
                "method": k.method,
                "source": k.source,
                "does_not_show": k.note,
                "active": f.SOURCES[k.source].enabled,
            }
            for k in f.EDGE_KINDS.values()
        ],
        "gates": f.gates(),
        "limits": {
            "graph_nodes": f.GRAPH_LIMIT_MAX,
            "graph_depth": f.GRAPH_DEPTH_MAX,
            "group_size": f.GROUP_MAX,
            "action_targets": f.ACTION_MAX_TARGETS,
        },
    }


async def trail(conn: AsyncConnection, scope: str) -> dict[str, Any]:
    ev = await db.events(conn, scope)
    problems = rv.chain_problems(ev)
    return {
        "scope": scope,
        "chain_intact": not problems,
        "problems": problems,
        "events": [
            {
                "seq": e["seq"],
                "type": e["event_type"],
                "actor": e["actor"],
                "reason": e["reason"],
                "data": e["data"],
                "digest": e["digest"],
                "at": _iso(e["created_at"]),
            }
            for e in ev
        ],
    }


# ------------------------------------------------------------------------------- the query builder (FG-09)


def query_view(q: dict[str, Any]) -> dict[str, Any]:
    return {
        "query_id": q["query_id"], "key": q["query_key"], "version": q["version"], "params": q["params"], "checksum": q["checksum"], "status": q["status"],
        "replay": q["replay"], "replayed_at": _iso(q["replayed_at"]), "replay_current": q["replay_checksum"] == q["checksum"],
        "maker": str(q["maker"]), "approver": str(q["approver"]) if q["approver"] else None, "decision_note": q["decision_note"],
        "created_at": _iso(q["created_at"]), "updated_at": _iso(q["updated_at"]),
    }  # fmt: skip


async def _load_query(
    conn: AsyncConnection, query_id: str, *, lock: bool = False
) -> dict[str, Any]:
    q = await db.get_query(conn, query_id, lock=lock)
    if q is None:
        raise m.PartnerError("GRAPH_ACCESS_DENIED", "No such query.")
    return q


async def create_query(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    key: str,
    params: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """A new DRAFT version of a query. It does nothing until it has been replayed and a different person has published it."""
    key = (key or "").strip().lower()
    if not f.re_key(key):
        raise m.PartnerError(
            "VALIDATION_FAILED", "A query key is 3 to 40 lower-case letters, numbers and hyphens."
        )
    clean = f.compile_query(params)
    q = await db.insert_query(
        conn, tenant_id=tenant_id,
        q={"query_id": "qry_" + uuid.uuid4().hex[:24], "query_key": key, "version": await db.next_version(conn, key), "params": clean, "checksum": f.query_checksum(clean), "maker": actor},
    )  # fmt: skip
    await _audit(
        conn,
        tenant_id,
        q["query_id"],
        "query_drafted",
        actor,
        now or datetime.now(UTC),
        data={"checksum": q["checksum"], "version": q["version"]},
    )
    return query_view(q)


async def list_queries(conn: AsyncConnection, status: str | None) -> dict[str, Any]:
    if status is not None and status not in f.QUERY_STATUSES:
        raise m.PartnerError("VALIDATION_FAILED", "Unknown query state.")
    return {"items": [query_view(q) for q in await db.list_queries(conn, status=status)]}


async def replay_query(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    query_id: str,
    now: datetime | None = None,
    *,
    budget_s: float = f.QUERY_TIME_BUDGET,
) -> dict[str, Any]:
    """Run the query over the company's current links and say what it WOULD do. It opens no lead, calls no provider, charges nothing and changes no decision; the result is stored against this exact version."""
    now = now or datetime.now(UTC)
    q = await _load_query(conn, query_id, lock=True)
    if q["status"] not in ("DRAFT", "IN_REVIEW", "PUBLISHED"):
        raise m.PartnerError("QUERY_STATE_INVALID", "A rejected or retired query is not replayed.")
    tokens = await db.active_tokens(conn)
    if len(tokens) > f.QUERY_SCAN_MAX:
        raise m.PartnerError(
            "QUERY_LIMIT_EXCEEDED",
            f"This query would look at more than {f.QUERY_SCAN_MAX} groups. Narrow it first.",
        )
    cands: list[f.Candidate | None] = []
    started, read = time.monotonic(), 0
    try:
        await conn.execute(
            text("SET LOCAL statement_timeout = 10000")
        )  # a runaway statement is ended by the database, not left to finish
        for token, _n in tokens:
            edges, facts = await _group(conn, token, now)
            read += len(edges)
            over = f.budget_exceeded(started, time.monotonic(), budget_s, read)
            if over:
                raise m.PartnerError(
                    "QUERY_LIMIT_EXCEEDED",
                    "This query used up its "
                    + ("time" if over == "TIME" else "link")
                    + " budget and was stopped. Nothing was recorded. Narrow it and replay again.",
                )
            mine = [e for e in edges if e["edge_type"] in q["params"]["edge_types"]]
            cands.append(
                f.evaluate_query(
                    q["params"],
                    q["query_key"],
                    facts,
                    f.best_strength(e["strength"] for e in mine),
                    now=now,
                )
                if mine
                else None
            )
    except DBAPIError as e:
        if "statement timeout" in str(e).lower():
            raise m.PartnerError(
                "QUERY_LIMIT_EXCEEDED",
                "This query took too long and was stopped. Nothing was recorded.",
            ) from e
        raise
    result = {
        **f.replay_summary(cands, len(tokens)),
        "as_of": now.isoformat(),
        "checksum": q["checksum"],
    }
    await db.set_query(
        conn, query_id, status=q["status"], now=now, replay=result, replay_checksum=q["checksum"]
    )
    await _audit(
        conn,
        tenant_id,
        query_id,
        "query_replayed",
        actor,
        now,
        data={"would_open": result["would_open"], "groups": len(tokens)},
    )
    return query_view(await _load_query(conn, query_id))


async def submit_query(
    conn: AsyncConnection, tenant_id: str, actor: str, query_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    q = await _load_query(conn, query_id, lock=True)
    if q["status"] != "DRAFT":
        raise m.PartnerError("QUERY_STATE_INVALID", "Only a draft is sent for approval.")
    if q["replay_checksum"] != q["checksum"]:
        raise m.PartnerError(
            "REPLAY_REQUIRED",
            "Replay this exact version first, so the person who approves can see what it would do.",
        )
    problems = f.publish_problems(q["params"])
    if problems:
        raise m.PartnerError(
            "QUERY_SOURCE_NOT_ENABLED",
            "This query names a link type nothing produces yet (" + ", ".join(problems) + ").",
        )
    await db.set_query(conn, query_id, status="IN_REVIEW", now=now)
    await _audit(conn, tenant_id, query_id, "query_submitted", actor, now)
    return query_view(await _load_query(conn, query_id))


async def decide_query(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    query_id: str,
    approve: bool,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The second person publishes it (and the earlier version of the same key is retired in the same step) or rejects it with a reason. The maker never decides."""
    now = now or datetime.now(UTC)
    q = await _load_query(conn, query_id, lock=True)
    if q["status"] != "IN_REVIEW":
        raise m.PartnerError("QUERY_STATE_INVALID", "Only a query waiting for approval is decided.")
    if str(q["maker"]) == actor:
        raise m.PartnerError(
            "ACTION_APPROVAL_REQUIRED", "Someone else must approve a query you wrote."
        )
    clean = f.check_note(note, required=not approve)
    if approve:
        await db.retire_other_versions(
            conn, q["query_key"], query_id, now
        )  # first: only one version of a key is ever published
    await db.set_query(
        conn,
        query_id,
        status="PUBLISHED" if approve else "REJECTED",
        now=now,
        approver=actor,
        note=clean or None,
    )
    await _audit(
        conn,
        tenant_id,
        query_id,
        "query_published" if approve else "query_rejected",
        actor,
        now,
        reason=clean or None,
        data={"checksum": q["checksum"]},
    )
    return query_view(await _load_query(conn, query_id))


async def retire_query(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    query_id: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    q = await _load_query(conn, query_id, lock=True)
    if q["status"] != "PUBLISHED":
        raise m.PartnerError("QUERY_STATE_INVALID", "Only a published query is retired.")
    clean = f.check_note(note)
    await db.set_query(conn, query_id, status="RETIRED", now=now, note=clean)
    await _audit(conn, tenant_id, query_id, "query_retired", actor, now, reason=clean)
    return query_view(await _load_query(conn, query_id))


# ------------------------------------------------------------------------------- the company's own blocklist (FG-11)


def entry_view(e: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "entry_id": e["entry_id"], "lead_id": e["lead_id"], "reason_code": e["reason_code"], "basis_ref": e["basis_ref"], "status": f.block_effective(e["status"], e["expires_at"], now),
        "entry_version": e["entry_version"], "expires_at": _iso(e["expires_at"]), "review_due": e["status"] == "ACTIVE" and e["review_due_at"] <= now, "review_due_at": _iso(e["review_due_at"]),
        "maker": str(e["maker"]), "approver": str(e["approver"]) if e["approver"] else None, "challenge_note": e["challenge_note"], "outcome_note": e["outcome_note"],
        "created_at": _iso(e["created_at"]), "scope": "this company only", "matches_by": "exact reference only",
    }  # fmt: skip


async def _load_entry(
    conn: AsyncConnection, entry_id: str, *, lock: bool = False
) -> dict[str, Any]:
    e = await db.get_entry(conn, entry_id, lock=lock)
    if e is None:
        raise m.PartnerError("GRAPH_ACCESS_DENIED", "No such entry.")
    return e


async def propose_entry(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    lead_id: str,
    reason_code: str | None,
    basis_ref: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    reason = f.check_block_reason(reason_code)
    basis = f.check_basis(basis_ref, "policy reference")
    x = await _load_lead(conn, lead_id, lock=True)
    token = x["group_token"]
    edges = await _current_edges(conn, x, now)
    problems = f.admission_problems(
        finding=f.Finding(x["finding"]), lead_status=f.LeadStatus(x["status"]), has_token=bool(token), strengths=[e["strength"] for e in edges],
        already_listed=bool(token and await db.entry_for_token(conn, token)),
    )  # fmt: skip
    if problems:
        raise _first_problem(problems, "This entry is refused")
    made = await db.insert_entry(
        conn, tenant_id=tenant_id,
        e={"entry_id": "blk_" + uuid.uuid4().hex[:24], "token": token, "lead_id": lead_id, "reason_code": reason, "basis_ref": basis, "expires_at": now + timedelta(days=f.BLOCK_REASONS[reason]),
           "review_due_at": now + f.BLOCK_REVIEW_EVERY, "maker": actor},
    )  # fmt: skip
    if made is None:
        raise m.PartnerError("ALREADY_LISTED", "This reference is already on the list.")
    await _audit(
        conn,
        tenant_id,
        made["entry_id"],
        "entry_proposed",
        actor,
        now,
        reason=basis,
        data={"lead_id": lead_id, "reason_code": reason},
    )
    return entry_view(made, now)


async def list_entries(
    conn: AsyncConnection, status: str | None, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if status is not None and status not in f.BLOCK_STATUSES:
        raise m.PartnerError("VALIDATION_FAILED", "Unknown entry state.")
    await db.expire_entries(conn, now)
    counts = await db.entry_counts(conn, now)
    return {
        "items": [entry_view(e, now) for e in await db.list_entries(conn, status=status)],
        "counts": counts,
        "scope": "this company only",
    }


async def decide_entry(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    entry_id: str,
    approve: bool,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    e = await _load_entry(conn, entry_id, lock=True)
    if e["status"] != "PROPOSED":
        raise m.PartnerError(
            "ENTRY_STATE_INVALID", "Only a proposed entry is approved or rejected."
        )
    if str(e["maker"]) == actor:
        raise m.PartnerError(
            "ACTION_APPROVAL_REQUIRED", "Someone else must approve an entry you proposed."
        )
    clean = f.check_note(note, required=not approve)
    if not await db.set_entry(
        conn,
        entry_id,
        expected=e["entry_version"],
        status="ACTIVE" if approve else "REJECTED",
        now=now,
        approver=actor,
        note=clean or None,
    ):
        raise m.PartnerError("GRAPH_SNAPSHOT_STALE", "It changed since you looked.")
    await _audit(
        conn,
        tenant_id,
        entry_id,
        "entry_approved" if approve else "entry_rejected",
        actor,
        now,
        reason=clean or None,
    )
    return entry_view(await _load_entry(conn, entry_id), now)


async def challenge_entry(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    entry_id: str,
    note: str | None,
    expected_version: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Staff record a challenge on behalf of a person who says they were wrongly listed. While it is open the entry does not match, and nothing about the restricted evidence is shared."""
    now = now or datetime.now(UTC)
    e = await _load_entry(conn, entry_id, lock=True)
    f.check_version(e["entry_version"], expected_version)
    if f.block_effective(e["status"], e["expires_at"], now) != "ACTIVE":
        raise m.PartnerError("ENTRY_STATE_INVALID", "Only an active entry is challenged.")
    clean = f.check_note(note)
    await db.set_entry(
        conn, entry_id, expected=e["entry_version"], status="CHALLENGED", now=now, challenge=clean
    )
    await _audit(conn, tenant_id, entry_id, "entry_challenged", actor, now, reason=clean)
    return entry_view(await _load_entry(conn, entry_id), now)


async def resolve_challenge(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    entry_id: str,
    uphold: bool,
    note: str | None,
    expected_version: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Someone who did not place the entry decides: it stays (and its clock restarts) or it comes off. The outcome is a new version of the entry, with the earlier ones kept in the trail."""
    now = now or datetime.now(UTC)
    e = await _load_entry(conn, entry_id, lock=True)
    f.check_version(e["entry_version"], expected_version)
    if e["status"] != "CHALLENGED":
        raise m.PartnerError("ENTRY_STATE_INVALID", "Only a challenged entry is resolved.")
    if actor in (str(e["maker"]), str(e["approver"])):
        raise m.PartnerError(
            "REVIEWER_NOT_INDEPENDENT",
            "Someone who took part in listing this reference cannot decide its challenge.",
        )
    clean = f.check_note(note)
    if uphold:
        await db.set_entry(
            conn,
            entry_id,
            expected=e["entry_version"],
            status="ACTIVE",
            now=now,
            outcome=clean,
            review_due_at=now + f.BLOCK_REVIEW_EVERY,
        )
    else:
        await db.set_entry(
            conn, entry_id, expected=e["entry_version"], status="REMOVED", now=now, outcome=clean
        )
    await _audit(
        conn,
        tenant_id,
        entry_id,
        "challenge_upheld" if uphold else "entry_removed",
        actor,
        now,
        reason=clean,
    )
    return entry_view(await _load_entry(conn, entry_id), now)


async def remove_entry(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    entry_id: str,
    note: str | None,
    expected_version: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Take a reference off the list (recovery). The entry and its history stay."""
    now = now or datetime.now(UTC)
    e = await _load_entry(conn, entry_id, lock=True)
    f.check_version(e["entry_version"], expected_version)
    if e["status"] not in ("ACTIVE", "CHALLENGED", "PROPOSED"):
        raise m.PartnerError("ENTRY_STATE_INVALID", "This entry is already off the list.")
    clean = f.check_note(note)
    await db.set_entry(
        conn, entry_id, expected=e["entry_version"], status="REMOVED", now=now, outcome=clean
    )
    await _audit(conn, tenant_id, entry_id, "entry_removed", actor, now, reason=clean)
    return entry_view(await _load_entry(conn, entry_id), now)


async def check_reference(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    customer_reference: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """For the company's own system: is this reference on the company's own list? An exact match only; never a look-alike, never another company's list. It is advice: the company decides what to do."""
    now = now or datetime.now(UTC)
    ref = (customer_reference or "").strip()
    if not 1 <= len(ref) <= 128:
        raise m.PartnerError("VALIDATION_FAILED", "A customer reference is 1 to 128 characters.")
    e = await db.entry_for_token(
        conn, f.reference_token(case_domain.reference_hash(tenant_id, ref))
    )
    state = f.block_effective(e["status"], e["expires_at"], now) if e else None
    listed = state == "ACTIVE"
    await _audit(
        conn,
        tenant_id,
        f"blocklist:{tenant_id}",
        "reference_checked",
        actor,
        now,
        data={"listed": listed, "entry_id": e["entry_id"] if e else None},
    )  # never the reference
    return {
        "listed": listed, "under_review": state == "CHALLENGED", "entry_id": e["entry_id"] if e and state in ("ACTIVE", "CHALLENGED") else None,
        "reason_code": e["reason_code"] if listed and e else None, "expires_at": _iso(e["expires_at"]) if listed and e else None,
        "advisory_only": True, "matched_by": "exact reference", "similarity_used": False, "scope": "this company only",
    }  # fmt: skip


# ------------------------------------------------------------------------------- the timeline and the as-of picture (FG-10)


async def timeline(
    conn: AsyncConnection, tenant_id: str, actor: str, lead_id: str, now: datetime | None = None
) -> dict[str, Any]:
    """One chronology for a lead: what the graph recorded, what the review case recorded, and the proposals made from it, in the order they happened. It also says whether the lead's exact snapshot can still be rebuilt."""
    now = now or datetime.now(UTC)
    x = await _load_lead(conn, lead_id)
    scopes = [
        lead_id,
        *[a["action_id"] for a in await db.list_actions(conn, lead_id=lead_id, limit=50)],
    ]
    items = [
        {
            "at": e["created_at"],
            "source": "fraud_graph",
            "scope": e["scope_id"],
            "seq": e["seq"],
            "type": e["event_type"],
            "actor": e["actor"],
            "reason": e["reason"],
            "digest": e["digest"],
        }
        for e in await db.events_for_scopes(conn, scopes)
    ]
    if x["review_case_id"]:
        items += [
            {
                "at": e["created_at"],
                "source": "review_case",
                "scope": x["review_case_id"],
                "seq": e["seq"],
                "type": e["event_type"],
                "actor": e["actor"],
                "reason": e["reason"],
                "digest": e["digest"],
            }
            for e in await rdb.events(conn, x["review_case_id"])
        ]
    items.sort(key=lambda i: (i["at"], i["source"], i["seq"]))
    edges = await db.edges_by_ids(conn, x["edge_ids"])
    rebuilt = f.snapshot_hash(x["nodes"], [e["edge_id"] for e in edges]) == x["snapshot_hash"]
    gaps = sorted(
        {
            e["edge_id"]
            for e in edges
            if e["status"] == "RETRACTED" and e["status_reason"] == "ERASED"
        }
    )
    await _audit(conn, tenant_id, lead_id, "timeline_viewed", actor, now)
    return {
        "lead_id": lead_id, "snapshot_hash": x["snapshot_hash"], "reproducible": rebuilt and not gaps,
        "not_reproducible_because": None if rebuilt and not gaps else ("some of its data was erased and is not rebuilt" if gaps else "its links no longer match the snapshot"),
        "erased_links": gaps,
        "events": [{**i, "at": _iso(i["at"])} for i in items],
        "final_receipt": None,  # a lead is never a signed finding: a receipt exists only for a decision
    }  # fmt: skip


async def graph_as_of(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    seed: str,
    at: datetime,
    *,
    depth: int,
    limit: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The picture the company could see around a session at an earlier time, from what was recorded then: when each link was learned, each exclusion and restoration, each expiry. Erased data is not rebuilt, and the picture says so."""
    now = now or datetime.now(UTC)
    f.check_read_limits(depth, limit)
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    if at > now:
        raise m.PartnerError("VALIDATION_FAILED", "Choose a time in the past.")
    if await sdb.get_session_any(conn, seed) is None:
        raise m.PartnerError("GRAPH_ACCESS_DENIED", "No such session.")
    # every link that could have touched the neighbourhood, whatever its state today
    edges: dict[str, dict[str, Any]] = {}
    frontier, known = [seed], {seed}
    for _ in range(depth):
        if not frontier:
            break
        got = await db.edges_touching_any(conn, frontier)
        nxt: list[str] = []
        for e in got:
            edges[e["edge_id"]] = e
            for n in (e["node_a"], e["node_b"]):
                if n not in known:
                    known.add(n)
                    nxt.append(n)
        frontier = sorted(nxt)
    hist = await db.edge_history(conn, list(edges))
    alive = {i: e for i, e in edges.items() if f.edge_active_at(e, hist.get(i, []), at)}
    order, kept, more = f.expand(
        seed, [(i, e["node_a"], e["node_b"]) for i, e in alive.items()], depth=depth, limit=limit
    )
    erased = sum(
        1 for e in edges.values() if e["status"] == "RETRACTED" and e["status_reason"] == "ERASED"
    )
    await _audit(
        conn,
        tenant_id,
        f"seed:{seed}",
        "graph_viewed_as_of",
        actor,
        now,
        data={"as_of": at.isoformat(), "nodes": len(order), "edges": len(kept)},
    )
    return {
        "as_of": at.isoformat(), "graph_snapshot_id": f.snapshot_hash(order, kept), "nodes": [{"id": n, "type": "session", "is_seed": n == seed} for n in order],
        "edges": [edge_view(alive[i]) for i in kept], "has_more": more, "masked": True, "source_gap": erased > 0,
        "note": "Rebuilt from what was recorded. Links whose data was erased are not rebuilt." if erased else "Rebuilt from what was recorded.",
    }  # fmt: skip


# ------------------------------------------------------------------------------- data quality (FG-13) and honest numbers (FG-12)


async def quality(conn: AsyncConnection, now: datetime | None = None) -> dict[str, Any]:
    """Is the graph being fed? A source that went quiet while sessions kept arriving is MISSING DATA: it is never shown as the risk going down."""
    now = now or datetime.now(UTC)
    day = now - f.FRESH_FOR
    sessions_recent, observed_recent = (
        await db.recent_sessions(conn, day),
        await db.recent_observations(conn, day),
    )
    health = await db.observation_health(conn)
    rows = []
    for name, src in f.SOURCES.items():
        mine = [h for h in health if h["source"] == name]
        last = max((h["last_ingested"] for h in mine if h["last_ingested"]), default=None)
        state = (
            f.freshness(last, now, sessions_recent=sessions_recent)
            if src.enabled
            else "NOT_ENABLED"
        )
        rows.append(
            {
                "source": name,
                "enabled": src.enabled,
                "state": state,
                "last_ingested": _iso(last),
                "observations": {h["status"]: h["n"] for h in mine},
            }
        )
    stale = any(
        r["state"] in ("STALE", "NO_DATA") and r["enabled"] and sessions_recent for r in rows
    )
    sessions, observed = await db.count_sessions(conn), await db.observed_sessions(conn)
    return {
        "as_of": now.isoformat(), "sources": rows,
        "coverage": {"sessions": sessions, "with_observation": observed, "without": max(0, sessions - observed), "share_observed": round(observed / sessions, 3) if sessions else None},
        "last_24h": {"sessions": sessions_recent, "observations_read": observed_recent},
        "warning": "MISSING_DATA: sessions arrived but the graph was not refreshed. Fewer leads now does not mean less risk." if stale else None,
        "quarantined": [{"source": q["source"], "reason": q["quarantine_reason"], "count": q["n"]} for q in await db.quarantine_reasons(conn)],
        "retention": {
            "observation_days": f.OBSERVATION_TTL.days, "lead_days": f.LEAD_EXPIRY.days, "blocklist_days": f.BLOCK_REASONS,
            "expiring_within_30_days": await db.expiring_observations(conn, now, now + timedelta(days=30)),
            "note": "Each market sets its own period; there is no single global one. An erased reference is removed at once, whatever these say.",
        },
    }  # fmt: skip


async def analytics(conn: AsyncConnection, now: datetime | None = None) -> dict[str, Any]:
    """What the graph has actually been right and wrong about, with denominators. Precision uses only leads a person adjudicated; small groups are hidden; a rate under thirty labels is flagged unreliable; no loss figure is invented."""
    now = now or datetime.now(UTC)
    totals = _totals(await db.lead_counts(conn))
    confirmed, cleared, inconclusive = (
        totals["confirmed_findings"],
        totals["no_finding"],
        totals["inconclusive"],
    )
    handled = sorted(await db.handle_times(conn))

    def cells(dimension: str) -> list[dict[str, Any]]:
        by: dict[str, dict[str, int]] = {}
        for r in raw[dimension]:
            by.setdefault(str(r["k"] or "unknown"), {})[r["finding"]] = int(r["n"])
        return [
            {
                "group": k,
                "labelled": f.shown(sum(v.values())),
                **(
                    {"confirmed_share": round(v.get("CONFIRMED", 0) / sum(v.values()), 3)}
                    if sum(v.values()) >= f.MIN_CELL
                    else {"confirmed_share": "not shown"}
                ),
            }
            for k, v in sorted(by.items())
        ]

    raw = {
        "rule": await db.labelled_by(conn, "rule"),
        "country": await db.labelled_by(conn, "country"),
    }
    return {
        "as_of": now.isoformat(), "window": "whole retention window",
        "quality": f.label_quality(confirmed, cleared), "inconclusive": inconclusive,
        "unreviewed": totals["unreviewed"],
        "mean_handling_hours": round(sum(handled) / len(handled) / 3600, 2) if handled else None, "handled_leads": len(handled),
        "by_rule": cells("rule"), "by_country": cells("country"),
        "fairness_note": "Groups with fewer than %d adjudicated leads are not shown, because a figure that small singles people out. No automatic step depends on any of these numbers." % f.MIN_CELL,  # noqa: UP031
        "prevented_loss": None, "prevented_loss_note": "Not reported. There is no audited method for it; a fraud-saved figure would be invented.",
    }  # fmt: skip


# ------------------------------------------------------------------------------- the masked export (WF05-02-A05)


async def export_graph(
    conn: AsyncConnection, tenant_id: str, actor: str, seed: str, *, fmt: str | None, purpose: str | None, reason: str | None, depth: int, limit: int, now: datetime | None = None,
) -> dict[str, Any]:  # fmt: skip
    """A file of the same masked, bounded picture the screen shows: no customer reference, only this company's sessions, a stated purpose and reason, a do-not-keep-after time, and a limit per person per day. Every export is on the record.
    The time is a request the file carries, not something the file can enforce: the company is told so."""
    now = now or datetime.now(UTC)
    fmt, purpose, why = f.check_export(fmt, purpose, reason)
    scope = f"export:{actor}"
    if await db.count_events_since(conn, scope, now - timedelta(days=1)) >= f.EXPORT_PER_DAY:
        raise m.PartnerError(
            "RATE_LIMITED",
            f"At most {f.EXPORT_PER_DAY} exports a day for one person. This is a limit on copying the graph, not on reading it.",
        )
    g = await graph(conn, tenant_id, actor, seed, depth=depth, limit=limit, now=now)
    expires = now + f.EXPORT_TTL
    meta = {
        "purpose": purpose, "reason": why, "company": "this company only", "generated_at": now.isoformat(), "do_not_keep_after": expires.isoformat(),
        "graph_snapshot_id": g["graph_snapshot_id"], "masked": "no customer reference; links are hypotheses, not findings", "has_more": g["has_more"], "source_gap": g["source_gap"],
    }  # fmt: skip
    body = f.render_csv(g, meta) if fmt == "csv" else f.render_svg(g, meta)
    await _audit(
        conn,
        tenant_id,
        scope,
        "graph_exported",
        actor,
        now,
        reason=why,
        data={
            "seed": seed,
            "format": fmt,
            "purpose": purpose,
            "nodes": len(g["nodes"]),
            "edges": len(g["edges"]),
            "snapshot": g["graph_snapshot_id"],
            "do_not_keep_after": expires.isoformat(),
        },
    )
    return {
        "content": body,
        "content_type": "text/csv; charset=utf-8" if fmt == "csv" else "image/svg+xml",
        "filename": f"link-picture-{seed[-8:]}.{fmt}",
        "expires_at": expires.isoformat(),
        "purpose": purpose,
    }

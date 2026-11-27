"""SQL for the fraud graph (CR-168). Every statement runs inside the company's scope (row-level security); nothing here decides anything."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verify_api.domain import review as r


def _row(x: Any) -> dict[str, Any]:
    return dict(x._mapping)


def _t(tenant_id: str) -> uuid.UUID:
    return uuid.UUID(tenant_id)


# ------------------------------------------------------------------------------- sources: the company's own sessions


async def sessions_for_ingest(conn: AsyncConnection, *, limit: int) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT s.session_id, s.tenant_id, s.application_id, s.environment, s.customer_reference, s.region, s.purpose_code, s.created_at, s.status, "
            "(SELECT d.outcome FROM verify.api_decision d WHERE d.session_id = s.session_id ORDER BY d.decision_version DESC LIMIT 1) AS outcome "
            "FROM verify.api_session s ORDER BY s.created_at DESC, s.session_id DESC LIMIT :n"
        ),
        {"n": limit},
    )
    return [_row(x) for x in rows]


async def count_sessions(conn: AsyncConnection) -> int:
    return int((await conn.execute(text("SELECT count(*) FROM verify.api_session"))).scalar_one())


# ------------------------------------------------------------------------------- observations


async def insert_observation(conn: AsyncConnection, *, tenant_id: str, o: dict[str, Any]) -> bool:
    """Idempotent on (company, source, kind, source event). True when it was new."""
    res = await conn.execute(
        text(
            "INSERT INTO verify.api_fraud_observation (observation_id, tenant_id, environment, region, purpose_code, session_id, kind, source, source_event_id, token, value, "
            "observed_at, expires_at, reliability, classification, digest, status, quarantine_reason) "
            "VALUES (:id, :t, :env, :reg, :pur, :sid, :kind, :src, :sev, :tok, :val, :obs, :exp, :rel, :cls, :dg, :st, :qr) "
            "ON CONFLICT (tenant_id, source, kind, source_event_id) DO NOTHING"
        ),
        {"t": _t(tenant_id), **o},
    )
    return bool(res.rowcount)


async def observations_for_token(
    conn: AsyncConnection, token: str, *, limit: int
) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT observation_id, session_id, observed_at, expires_at FROM verify.api_fraud_observation "
            "WHERE token = :k AND status = 'ACTIVE' ORDER BY observed_at, session_id LIMIT :n"
        ),
        {"k": token, "n": limit},
    )
    return [_row(x) for x in rows]


async def active_tokens(conn: AsyncConnection) -> list[tuple[str, int]]:
    """Tokens carried by two or more sessions: the only ones that make links."""
    rows = await conn.execute(
        text(
            "SELECT token, count(DISTINCT session_id) AS n FROM verify.api_fraud_observation "
            "WHERE kind = 'CUSTOMER_REFERENCE' AND status = 'ACTIVE' AND token IS NOT NULL GROUP BY token HAVING count(DISTINCT session_id) >= 2 ORDER BY token"
        )
    )
    return [(x.token, int(x.n)) for x in rows]


async def outcomes_for_sessions(
    conn: AsyncConnection, session_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT s.session_id, s.application_id, s.created_at, s.country, s.status, "
            "(SELECT d.outcome FROM verify.api_decision d WHERE d.session_id = s.session_id ORDER BY d.decision_version DESC LIMIT 1) AS outcome "
            "FROM verify.api_session s WHERE s.session_id = ANY(:ids)"
        ),
        {"ids": list(session_ids)},
    )
    return {x.session_id: _row(x) for x in rows}


async def erase_token(conn: AsyncConnection, token: str) -> tuple[int, int]:
    """An erasure request: the token leaves every observation, and every link made from it is retracted and loses its group. Returns (observations, edges)."""
    o = await conn.execute(
        text(
            "UPDATE verify.api_fraud_observation SET token = NULL, status = 'ERASED' WHERE token = :k AND status <> 'ERASED'"
        ),
        {"k": token},
    )
    await conn.execute(
        text("UPDATE verify.api_fraud_lead SET group_token = NULL WHERE group_token = :k"),
        {"k": token},
    )
    await conn.execute(
        text(
            "UPDATE verify.api_fraud_blocklist SET token = NULL, status = 'REMOVED', outcome_note = 'The reference was erased.', entry_version = entry_version + 1, updated_at = now() WHERE token = :k"
        ),
        {"k": token},
    )
    e = await conn.execute(
        text(
            "UPDATE verify.api_fraud_edge SET status = 'RETRACTED', status_reason = 'ERASED', token_group = NULL, edge_version = edge_version + 1, updated_at = now() "
            "WHERE token_group = :k AND status <> 'RETRACTED'"
        ),
        {"k": token},
    )
    return int(o.rowcount or 0), int(e.rowcount or 0)


async def expire_due(conn: AsyncConnection, now: datetime) -> tuple[int, int]:
    o = await conn.execute(
        text(
            "UPDATE verify.api_fraud_observation SET token = NULL, status = 'EXPIRED' WHERE expires_at <= :n AND status = 'ACTIVE'"
        ),
        {"n": now},
    )
    e = await conn.execute(
        text(
            "UPDATE verify.api_fraud_edge SET status = 'RETRACTED', status_reason = 'EXPIRED', token_group = NULL, edge_version = edge_version + 1, updated_at = now() "
            "WHERE expires_at <= :n AND status <> 'RETRACTED'"
        ),
        {"n": now},
    )
    return int(o.rowcount or 0), int(e.rowcount or 0)


# ------------------------------------------------------------------------------- edges


_EDGE = "edge_id, node_a, node_b, edge_type, strength, method, confidence_class, rule_version, evidence, first_seen, last_seen, expires_at, status, status_reason, changed_by, edge_version, created_at, updated_at"


async def upsert_edge(
    conn: AsyncConnection, *, tenant_id: str, e: dict[str, Any]
) -> dict[str, Any]:
    """New edge, or a refresh of an existing one's evidence and times. A person's exclusion is never undone by a refresh."""
    row = (
        await conn.execute(
            text(
                "INSERT INTO verify.api_fraud_edge (edge_id, tenant_id, node_a, node_b, edge_type, strength, method, confidence_class, rule_version, evidence, token_group, first_seen, last_seen, expires_at) "
                "VALUES (:id, :t, :a, :b, :ty, :st, :me, :cc, :rv, :ev, :tg, :fs, :ls, :ex) "
                "ON CONFLICT (tenant_id, node_a, node_b, edge_type) DO UPDATE SET "
                "evidence = (SELECT array_agg(DISTINCT x) FROM unnest(verify.api_fraud_edge.evidence || EXCLUDED.evidence) AS x), "
                "first_seen = LEAST(verify.api_fraud_edge.first_seen, EXCLUDED.first_seen), last_seen = GREATEST(verify.api_fraud_edge.last_seen, EXCLUDED.last_seen), "
                "expires_at = GREATEST(verify.api_fraud_edge.expires_at, EXCLUDED.expires_at), "
                "token_group = CASE WHEN verify.api_fraud_edge.status = 'RETRACTED' THEN verify.api_fraud_edge.token_group ELSE EXCLUDED.token_group END, updated_at = now() "
                f"RETURNING {_EDGE}, (xmax = 0) AS inserted"
            ),
            {"t": _t(tenant_id), **e},
        )
    ).one()
    return _row(row)


async def get_edge(
    conn: AsyncConnection, edge_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_EDGE} FROM verify.api_fraud_edge WHERE edge_id = :i{' FOR UPDATE' if lock else ''}"
            ),
            {"i": edge_id},
        )
    ).first()
    return _row(row) if row else None


async def edges_touching(
    conn: AsyncConnection, nodes: Sequence[str], now: datetime, *, limit: int = 5000
) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            f"SELECT {_EDGE} FROM verify.api_fraud_edge WHERE status = 'ACTIVE' AND expires_at > :n AND (node_a = ANY(:ns) OR node_b = ANY(:ns)) ORDER BY edge_id LIMIT :l"
        ),
        {"n": now, "ns": list(nodes), "l": limit},
    )
    return [_row(x) for x in rows]


async def edges_touching_any(
    conn: AsyncConnection, nodes: Sequence[str], *, limit: int = 5000
) -> list[dict[str, Any]]:
    """Whatever state they are in today: for rebuilding an earlier picture."""
    rows = await conn.execute(
        text(
            f"SELECT {_EDGE} FROM verify.api_fraud_edge WHERE node_a = ANY(:ns) OR node_b = ANY(:ns) ORDER BY edge_id LIMIT :l"
        ),
        {"ns": list(nodes), "l": limit},
    )
    return [_row(x) for x in rows]


async def edges_by_ids(conn: AsyncConnection, ids: Sequence[str]) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(f"SELECT {_EDGE} FROM verify.api_fraud_edge WHERE edge_id = ANY(:i) ORDER BY edge_id"),
        {"i": list(ids)},
    )
    return [_row(x) for x in rows]


async def set_edge_status(
    conn: AsyncConnection, edge_id: str, *, expected: int, status: str, reason: str, by: str
) -> bool:
    res = await conn.execute(
        text(
            "UPDATE verify.api_fraud_edge SET status = :s, status_reason = :r, changed_by = :b, edge_version = edge_version + 1, updated_at = now() "
            "WHERE edge_id = :i AND edge_version = :v AND status <> 'RETRACTED'"
        ),
        {"s": status, "r": reason, "b": by, "i": edge_id, "v": expected},
    )
    return bool(res.rowcount)


async def edge_counts(conn: AsyncConnection) -> dict[str, int]:
    rows = await conn.execute(
        text("SELECT status, count(*) AS n FROM verify.api_fraud_edge GROUP BY status")
    )
    return {x.status: int(x.n) for x in rows}


# ------------------------------------------------------------------------------- leads


_LEAD = "lead_id, environment, rule_id, rule_version, group_key, group_token, risk_class, seed_session, nodes, edge_ids, snapshot_hash, uncertain, summary, status, finding, finding_by, review_case_id, lead_version, created_at, updated_at, expires_at"


async def upsert_lead(
    conn: AsyncConnection, *, tenant_id: str, lead: dict[str, Any]
) -> dict[str, Any] | None:
    """A new lead, or an update of one nobody has acted on yet; an expired one that qualifies again (a link was restored) reopens. A cleared or escalated lead is left exactly as it is."""
    row = (
        await conn.execute(
            text(
                "INSERT INTO verify.api_fraud_lead (lead_id, tenant_id, environment, rule_id, rule_version, group_key, group_token, risk_class, seed_session, nodes, edge_ids, snapshot_hash, uncertain, summary, expires_at) "
                "VALUES (:id, :t, :env, :rule, :rv, :gk, :gt, :risk, :seed, :nodes, :edges, :snap, CAST(:unc AS jsonb), CAST(:sum AS jsonb), :ex) "
                "ON CONFLICT (tenant_id, rule_id, group_key) DO UPDATE SET risk_class = EXCLUDED.risk_class, nodes = EXCLUDED.nodes, edge_ids = EXCLUDED.edge_ids, snapshot_hash = EXCLUDED.snapshot_hash, "
                "uncertain = EXCLUDED.uncertain, summary = EXCLUDED.summary, rule_version = EXCLUDED.rule_version, "
                "status = CASE WHEN verify.api_fraud_lead.status = 'EXPIRED' THEN 'OPEN' ELSE verify.api_fraud_lead.status END, "
                "lead_version = CASE WHEN verify.api_fraud_lead.snapshot_hash <> EXCLUDED.snapshot_hash OR verify.api_fraud_lead.status = 'EXPIRED' THEN verify.api_fraud_lead.lead_version + 1 ELSE verify.api_fraud_lead.lead_version END, updated_at = now() "
                "WHERE verify.api_fraud_lead.status IN ('OPEN', 'IN_ANALYSIS', 'EXPIRED') "
                f"RETURNING {_LEAD}, (xmax = 0) AS inserted"
            ),
            {
                "t": _t(tenant_id),
                "id": lead["lead_id"],
                "env": lead["environment"],
                "rule": lead["rule_id"],
                "rv": lead["rule_version"],
                "gk": lead["group_key"],
                "gt": lead.get("group_token"),
                "risk": lead["risk_class"],
                "seed": lead["seed_session"],
                "nodes": lead["nodes"],
                "edges": lead["edge_ids"],
                "snap": lead["snapshot_hash"],
                "unc": json.dumps(lead["uncertain"]),
                "sum": json.dumps(lead["summary"]),
                "ex": lead["expires_at"],
            },
        )
    ).first()
    return _row(row) if row else None


async def get_lead(
    conn: AsyncConnection, lead_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_LEAD} FROM verify.api_fraud_lead WHERE lead_id = :i{' FOR UPDATE' if lock else ''}"
            ),
            {"i": lead_id},
        )
    ).first()
    return _row(row) if row else None


async def list_leads(
    conn: AsyncConnection, *, status: str | None, finding: str | None, limit: int
) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            f"SELECT {_LEAD} FROM verify.api_fraud_lead WHERE (CAST(:s AS text) IS NULL OR status = :s) AND (CAST(:f AS text) IS NULL OR finding = :f) "
            "ORDER BY created_at DESC, lead_id DESC LIMIT :n"
        ),
        {"s": status, "f": finding, "n": limit},
    )
    return [_row(x) for x in rows]


async def open_leads(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(f"SELECT {_LEAD} FROM verify.api_fraud_lead WHERE status IN ('OPEN', 'IN_ANALYSIS')")
    )
    return [_row(x) for x in rows]


async def set_lead(
    conn: AsyncConnection,
    lead_id: str,
    *,
    expected: int | None,
    status: str | None = None,
    finding: str | None = None,
    finding_by: str | None = None,
    case_id: str | None = None,
    now: datetime,
) -> bool:
    res = await conn.execute(
        text(
            "UPDATE verify.api_fraud_lead SET status = COALESCE(:s, status), finding = COALESCE(:f, finding), finding_by = COALESCE(CAST(:fb AS uuid), finding_by), "
            "review_case_id = COALESCE(:c, review_case_id), lead_version = lead_version + 1, updated_at = :n "
            "WHERE lead_id = :i AND (CAST(:v AS integer) IS NULL OR lead_version = :v)"
        ),
        {
            "s": status,
            "f": finding,
            "fb": finding_by,
            "c": case_id,
            "n": now,
            "i": lead_id,
            "v": expected,
        },
    )
    return bool(res.rowcount)


async def lead_counts(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT status, finding, risk_class, count(*) AS n FROM verify.api_fraud_lead GROUP BY status, finding, risk_class"
        )
    )
    return [_row(x) for x in rows]


# ------------------------------------------------------------------------------- notes


async def insert_note(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    lead_id: str,
    edge_id: str | None,
    kind: str,
    body: str,
    source_ref: str | None,
    author: str,
) -> str:
    nid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO verify.api_fraud_note (note_id, tenant_id, lead_id, edge_id, kind, body, source_ref, author) VALUES (:n, :t, :l, :e, :k, :b, :s, :a)"
        ),
        {
            "n": nid,
            "t": _t(tenant_id),
            "l": lead_id,
            "e": edge_id,
            "k": kind,
            "b": body,
            "s": source_ref,
            "a": uuid.UUID(author),
        },
    )
    return str(nid)


async def notes_for(conn: AsyncConnection, lead_id: str) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT note_id, edge_id, kind, body, source_ref, author, created_at FROM verify.api_fraud_note WHERE lead_id = :l ORDER BY created_at, note_id"
        ),
        {"l": lead_id},
    )
    return [_row(x) for x in rows]


# ------------------------------------------------------------------------------- action proposals


_ACTION = "action_id, lead_id, proposal_type, targets, basis_ref, impact_ref, impact, snapshot_hash, manifest_checksum, expires_at, status, maker, decided_by, decided_at, decision_note, idempotency_key, action_version, created_at"


async def insert_action(
    conn: AsyncConnection, *, tenant_id: str, a: dict[str, Any], targets: Sequence[dict[str, Any]]
) -> dict[str, Any] | None:
    """None when this idempotency key was already used: the caller returns the earlier proposal."""
    res = await conn.execute(
        text(
            "INSERT INTO verify.api_fraud_action (action_id, tenant_id, lead_id, proposal_type, targets, basis_ref, impact_ref, impact, snapshot_hash, manifest_checksum, expires_at, maker, idempotency_key) "
            "VALUES (:id, :t, :l, :ty, :tg, :b, :i, CAST(:im AS jsonb), :sn, :mc, :ex, :mk, :ik) ON CONFLICT (tenant_id, idempotency_key) DO NOTHING"
        ),
        {
            "id": a["action_id"], "t": _t(tenant_id), "l": a["lead_id"], "ty": a["proposal_type"], "tg": a["targets"], "b": a["basis_ref"], "i": a["impact_ref"],
            "im": json.dumps(a["impact"]), "sn": a["snapshot_hash"], "mc": a["manifest_checksum"], "ex": a["expires_at"], "mk": uuid.UUID(a["maker"]), "ik": a["idempotency_key"],
        },
    )  # fmt: skip
    if not res.rowcount:
        return None
    for pos, t in enumerate(targets):
        await conn.execute(
            text(
                "INSERT INTO verify.api_fraud_action_target (action_id, tenant_id, position, target, strength) VALUES (:a, :t, :p, :g, :s)"
            ),
            {
                "a": a["action_id"],
                "t": _t(tenant_id),
                "p": pos,
                "g": t["session_id"],
                "s": t["strength"],
            },
        )
    return await get_action(conn, a["action_id"])


async def get_action(
    conn: AsyncConnection, action_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_ACTION} FROM verify.api_fraud_action WHERE action_id = :i{' FOR UPDATE' if lock else ''}"
            ),
            {"i": action_id},
        )
    ).first()
    return _row(row) if row else None


async def action_by_key(conn: AsyncConnection, key: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(f"SELECT {_ACTION} FROM verify.api_fraud_action WHERE idempotency_key = :k"),
            {"k": key},
        )
    ).first()
    return _row(row) if row else None


async def list_actions(
    conn: AsyncConnection, *, lead_id: str | None, limit: int
) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            f"SELECT {_ACTION} FROM verify.api_fraud_action WHERE (CAST(:l AS text) IS NULL OR lead_id = :l) ORDER BY created_at DESC, action_id DESC LIMIT :n"
        ),
        {"l": lead_id, "n": limit},
    )
    return [_row(x) for x in rows]


async def action_targets(conn: AsyncConnection, action_id: str) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT position, target, strength, state, updated_at FROM verify.api_fraud_action_target WHERE action_id = :a ORDER BY position"
        ),
        {"a": action_id},
    )
    return [_row(x) for x in rows]


async def set_action(
    conn: AsyncConnection,
    action_id: str,
    *,
    expected: int,
    status: str,
    by: str | None,
    note: str | None,
    now: datetime,
) -> bool:
    res = await conn.execute(
        text(
            "UPDATE verify.api_fraud_action SET status = :s, decided_by = COALESCE(CAST(:b AS uuid), decided_by), decided_at = :n, decision_note = COALESCE(:note, decision_note), action_version = action_version + 1 "
            "WHERE action_id = :i AND action_version = :v"
        ),
        {"s": status, "b": by, "n": now, "note": note, "i": action_id, "v": expected},
    )
    if res.rowcount:
        await conn.execute(
            text(
                "UPDATE verify.api_fraud_action_target SET state = :s, updated_at = :n WHERE action_id = :i"
            ),
            {"s": status, "n": now, "i": action_id},
        )
    return bool(res.rowcount)


async def expire_actions(conn: AsyncConnection, now: datetime) -> int:
    res = await conn.execute(
        text(
            "UPDATE verify.api_fraud_action SET status = 'EXPIRED', decided_at = :n, action_version = action_version + 1 WHERE status = 'PROPOSED' AND expires_at <= :n"
        ),
        {"n": now},
    )
    if res.rowcount:
        await conn.execute(
            text(
                "UPDATE verify.api_fraud_action_target t SET state = 'EXPIRED', updated_at = :n FROM verify.api_fraud_action a WHERE a.action_id = t.action_id AND a.status = 'EXPIRED' AND t.state = 'PROPOSED'"
            ),
            {"n": now},
        )
    return int(res.rowcount or 0)


# ------------------------------------------------------------------------------- the audit chain


async def append_event(
    conn: AsyncConnection,
    *,
    scope_id: str,
    tenant_id: str,
    event_type: str,
    actor: str,
    reason: str | None,
    data: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Changes AND reads. The next number is read and used inside a savepoint; two writers racing for it retry once rather than lose an entry."""
    last_error: Exception | None = None
    for _ in range(3):
        try:
            async with conn.begin_nested():
                last = (
                    await conn.execute(
                        text(
                            "SELECT seq, digest FROM verify.api_fraud_event WHERE scope_id = :s ORDER BY seq DESC LIMIT 1"
                        ),
                        {"s": scope_id},
                    )
                ).first()
                seq, prev = (last.seq + 1, last.digest) if last else (1, r.GENESIS)
                digest = r.event_digest(
                    prev,
                    seq=seq,
                    case_id=scope_id,
                    event_type=event_type,
                    actor=actor,
                    at=now,
                    reason=reason,
                    data=data,
                )
                await conn.execute(
                    text(
                        "INSERT INTO verify.api_fraud_event (scope_id, seq, tenant_id, event_type, actor, reason, data, prev_digest, digest, created_at) "
                        "VALUES (:s, :q, :t, :ty, :a, :r, CAST(:d AS jsonb), :p, :dg, :n)"
                    ),
                    {
                        "s": scope_id,
                        "q": seq,
                        "t": _t(tenant_id),
                        "ty": event_type,
                        "a": actor,
                        "r": reason,
                        "d": json.dumps(data),
                        "p": prev,
                        "dg": digest,
                        "n": now,
                    },
                )
            return {"seq": seq, "digest": digest}
        except Exception as e:
            if "api_fraud_event_pkey" not in str(e) and "duplicate key" not in str(e):
                raise
            last_error = e
    raise last_error  # type: ignore[misc]


async def events(conn: AsyncConnection, scope_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT scope_id AS case_id, seq, event_type, actor, reason, data, prev_digest, digest, created_at FROM verify.api_fraud_event WHERE scope_id = :s ORDER BY seq LIMIT :n"
        ),
        {"s": scope_id, "n": limit},
    )
    return [_row(x) for x in rows]


# ------------------------------------------------------------------------------- overview


async def observation_health(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT source, status, count(*) AS n, max(ingested_at) AS last_ingested, max(observed_at) AS last_observed FROM verify.api_fraud_observation GROUP BY source, status ORDER BY source, status"
        )
    )
    return [_row(x) for x in rows]


async def observed_sessions(conn: AsyncConnection) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(DISTINCT session_id) FROM verify.api_fraud_observation WHERE kind = 'CUSTOMER_REFERENCE'"
                )
            )
        ).scalar_one()
    )


# ------------------------------------------------------------------------------- reads the service needs


async def edges_for_group(conn: AsyncConnection, token: str) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(f"SELECT {_EDGE} FROM verify.api_fraud_edge WHERE token_group = :k ORDER BY edge_id"),
        {"k": token},
    )
    return [_row(x) for x in rows]


async def retracted_touching(conn: AsyncConnection, nodes: Sequence[str]) -> int:
    """Links that once touched these sessions and were taken away (erased, expired): a reader is told the picture has a gap."""
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM verify.api_fraud_edge WHERE status = 'RETRACTED' AND (node_a = ANY(:n) OR node_b = ANY(:n))"
                ),
                {"n": list(nodes)},
            )
        ).scalar_one()
    )


async def observations_by_ids(conn: AsyncConnection, ids: Sequence[str]) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT observation_id, session_id, kind, source, observed_at, ingested_at, expires_at, reliability, classification, digest, status "
            "FROM verify.api_fraud_observation WHERE observation_id = ANY(:i) ORDER BY observed_at, observation_id"
        ),
        {"i": list(ids)},
    )
    return [_row(x) for x in rows]


async def expire_leads_for_keys(conn: AsyncConnection, keys: Sequence[str], now: datetime) -> int:
    res = await conn.execute(
        text(
            "UPDATE verify.api_fraud_lead SET status = 'EXPIRED', lead_version = lead_version + 1, updated_at = :n WHERE group_key = ANY(:k) AND status IN ('OPEN', 'IN_ANALYSIS')"
        ),
        {"k": list(keys), "n": now},
    )
    return int(res.rowcount or 0)


async def expire_leads_for_token(conn: AsyncConnection, token: str, now: datetime) -> int:
    """Whatever rule made them: an open lead built on an erased reference expires."""
    res = await conn.execute(
        text(
            "UPDATE verify.api_fraud_lead SET status = 'EXPIRED', lead_version = lead_version + 1, updated_at = :n WHERE group_token = :k AND status IN ('OPEN', 'IN_ANALYSIS')"
        ),
        {"k": token, "n": now},
    )
    return int(res.rowcount or 0)


# ------------------------------------------------------------------------------- queries


_QUERY = "query_id, query_key, version, params, checksum, status, replay, replay_checksum, replayed_at, maker, approver, decision_note, created_at, updated_at"


async def next_version(conn: AsyncConnection, key: str) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT COALESCE(max(version), 0) + 1 FROM verify.api_fraud_query WHERE query_key = :k"
                ),
                {"k": key},
            )
        ).scalar_one()
    )


async def insert_query(
    conn: AsyncConnection, *, tenant_id: str, q: dict[str, Any]
) -> dict[str, Any]:
    await conn.execute(
        text(
            "INSERT INTO verify.api_fraud_query (query_id, tenant_id, query_key, version, params, checksum, maker) VALUES (:id, :t, :k, :v, CAST(:p AS jsonb), :c, :m)"
        ),
        {
            "id": q["query_id"],
            "t": _t(tenant_id),
            "k": q["query_key"],
            "v": q["version"],
            "p": json.dumps(q["params"]),
            "c": q["checksum"],
            "m": uuid.UUID(q["maker"]),
        },
    )
    return await get_query(conn, q["query_id"])  # type: ignore[return-value]


async def get_query(
    conn: AsyncConnection, query_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_QUERY} FROM verify.api_fraud_query WHERE query_id = :i{' FOR UPDATE' if lock else ''}"
            ),
            {"i": query_id},
        )
    ).first()
    return _row(row) if row else None


async def list_queries(conn: AsyncConnection, *, status: str | None) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            f"SELECT {_QUERY} FROM verify.api_fraud_query WHERE (CAST(:s AS text) IS NULL OR status = :s) ORDER BY query_key, version DESC LIMIT 200"
        ),
        {"s": status},
    )
    return [_row(x) for x in rows]


async def published_queries(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            f"SELECT {_QUERY} FROM verify.api_fraud_query WHERE status = 'PUBLISHED' ORDER BY query_key"
        )
    )
    return [_row(x) for x in rows]


async def set_query(
    conn: AsyncConnection,
    query_id: str,
    *,
    status: str,
    now: datetime,
    approver: str | None = None,
    note: str | None = None,
    replay: dict[str, Any] | None = None,
    replay_checksum: str | None = None,
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_fraud_query SET status = :s, approver = COALESCE(CAST(:a AS uuid), approver), decision_note = COALESCE(:n, decision_note), "
            "replay = COALESCE(CAST(:r AS jsonb), replay), replay_checksum = COALESCE(:rc, replay_checksum), replayed_at = CASE WHEN :rc IS NULL THEN replayed_at ELSE :now END, updated_at = :now "
            "WHERE query_id = :i"
        ),
        {
            "s": status,
            "a": approver,
            "n": note,
            "r": json.dumps(replay) if replay is not None else None,
            "rc": replay_checksum,
            "now": now,
            "i": query_id,
        },
    )


async def retire_other_versions(conn: AsyncConnection, key: str, keep: str, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_fraud_query SET status = 'RETIRED', updated_at = :n WHERE query_key = :k AND status = 'PUBLISHED' AND query_id <> :i"
        ),
        {"n": now, "k": key, "i": keep},
    )


# ------------------------------------------------------------------------------- the blocklist


_BLOCK = "entry_id, token, lead_id, reason_code, basis_ref, status, entry_version, expires_at, review_due_at, maker, approver, decided_at, decision_note, challenge_note, outcome_note, created_at, updated_at"


async def insert_entry(
    conn: AsyncConnection, *, tenant_id: str, e: dict[str, Any]
) -> dict[str, Any] | None:
    res = await conn.execute(
        text(
            "INSERT INTO verify.api_fraud_blocklist (entry_id, tenant_id, token, lead_id, reason_code, basis_ref, expires_at, review_due_at, maker) "
            "VALUES (:id, :t, :k, :l, :r, :b, :x, :d, :m) ON CONFLICT DO NOTHING"
        ),
        {
            "id": e["entry_id"],
            "t": _t(tenant_id),
            "k": e["token"],
            "l": e["lead_id"],
            "r": e["reason_code"],
            "b": e["basis_ref"],
            "x": e["expires_at"],
            "d": e["review_due_at"],
            "m": uuid.UUID(e["maker"]),
        },
    )
    return await get_entry(conn, e["entry_id"]) if res.rowcount else None


async def get_entry(
    conn: AsyncConnection, entry_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_BLOCK} FROM verify.api_fraud_blocklist WHERE entry_id = :i{' FOR UPDATE' if lock else ''}"
            ),
            {"i": entry_id},
        )
    ).first()
    return _row(row) if row else None


async def entry_for_token(conn: AsyncConnection, token: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_BLOCK} FROM verify.api_fraud_blocklist WHERE token = :k AND status IN ('PROPOSED', 'ACTIVE', 'CHALLENGED') LIMIT 1"
            ),
            {"k": token},
        )
    ).first()
    return _row(row) if row else None


async def list_entries(conn: AsyncConnection, *, status: str | None) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            f"SELECT {_BLOCK} FROM verify.api_fraud_blocklist WHERE (CAST(:s AS text) IS NULL OR status = :s) ORDER BY created_at DESC, entry_id DESC LIMIT 200"
        ),
        {"s": status},
    )
    return [_row(x) for x in rows]


async def set_entry(
    conn: AsyncConnection, entry_id: str, *, expected: int, status: str, now: datetime, approver: str | None = None, note: str | None = None, challenge: str | None = None, outcome: str | None = None,
    expires_at: datetime | None = None, review_due_at: datetime | None = None,
) -> bool:  # fmt: skip
    res = await conn.execute(
        text(
            "UPDATE verify.api_fraud_blocklist SET status = :s, approver = COALESCE(CAST(:a AS uuid), approver), decision_note = COALESCE(:n, decision_note), challenge_note = COALESCE(:c, challenge_note), "
            "outcome_note = COALESCE(:o, outcome_note), expires_at = COALESCE(:x, expires_at), review_due_at = COALESCE(:d, review_due_at), "
            "decided_at = CASE WHEN CAST(:a AS uuid) IS NULL THEN decided_at ELSE :now END, entry_version = entry_version + 1, updated_at = :now WHERE entry_id = :i AND entry_version = :v"
        ),
        {
            "s": status,
            "a": approver,
            "n": note,
            "c": challenge,
            "o": outcome,
            "x": expires_at,
            "d": review_due_at,
            "now": now,
            "i": entry_id,
            "v": expected,
        },
    )
    return bool(res.rowcount)


async def expire_entries(conn: AsyncConnection, now: datetime) -> int:
    res = await conn.execute(
        text(
            "UPDATE verify.api_fraud_blocklist SET status = 'EXPIRED', entry_version = entry_version + 1, updated_at = :n WHERE status IN ('ACTIVE', 'CHALLENGED') AND expires_at <= :n"
        ),
        {"n": now},
    )
    return int(res.rowcount or 0)


async def entry_counts(conn: AsyncConnection, now: datetime) -> dict[str, int]:
    rows = await conn.execute(
        text(
            "SELECT status, count(*) AS n, count(*) FILTER (WHERE review_due_at <= :n AND status = 'ACTIVE') AS due FROM verify.api_fraud_blocklist GROUP BY status"
        ),
        {"n": now},
    )
    out: dict[str, int] = {}
    for x in rows:
        out[x.status] = int(x.n)
        out["review_due"] = out.get("review_due", 0) + int(x.due)
    return out


# ------------------------------------------------------------------------------- timeline, quality and honest numbers


async def events_for_scopes(conn: AsyncConnection, scopes: Sequence[str]) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT scope_id, seq, event_type, actor, reason, data, digest, created_at FROM verify.api_fraud_event WHERE scope_id = ANY(:s) ORDER BY created_at, scope_id, seq LIMIT 1000"
        ),
        {"s": list(scopes)},
    )
    return [_row(x) for x in rows]


async def edge_history(
    conn: AsyncConnection, edge_ids: Sequence[str]
) -> dict[str, list[tuple[datetime, str]]]:
    rows = await conn.execute(
        text(
            "SELECT scope_id, event_type, created_at FROM verify.api_fraud_event WHERE scope_id = ANY(:s) AND event_type IN ('edge_excluded', 'edge_restored') ORDER BY created_at, seq"
        ),
        {"s": [f"edge:{i}" for i in edge_ids]},
    )
    out: dict[str, list[tuple[datetime, str]]] = {}
    for x in rows:
        out.setdefault(x.scope_id[5:], []).append((x.created_at, x.event_type))
    return out


async def recent_sessions(conn: AsyncConnection, since: datetime) -> int:
    return int(
        (
            await conn.execute(
                text("SELECT count(*) FROM verify.api_session WHERE created_at >= :s"), {"s": since}
            )
        ).scalar_one()
    )


async def recent_observations(conn: AsyncConnection, since: datetime) -> int:
    return int(
        (
            await conn.execute(
                text("SELECT count(*) FROM verify.api_fraud_observation WHERE ingested_at >= :s"),
                {"s": since},
            )
        ).scalar_one()
    )


async def quarantine_reasons(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT source, quarantine_reason, count(*) AS n FROM verify.api_fraud_observation WHERE status = 'QUARANTINED' GROUP BY source, quarantine_reason ORDER BY source"
        )
    )
    return [_row(x) for x in rows]


async def expiring_observations(conn: AsyncConnection, now: datetime, soon: datetime) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM verify.api_fraud_observation WHERE status = 'ACTIVE' AND expires_at > :n AND expires_at <= :s"
                ),
                {"n": now, "s": soon},
            )
        ).scalar_one()
    )


async def labelled_by(conn: AsyncConnection, dimension: str) -> list[dict[str, Any]]:
    """Adjudicated leads only, grouped by rule or by the seed session's country."""
    if dimension == "rule":
        sql = (
            "SELECT l.rule_id AS k, l.finding, count(*) AS n FROM verify.api_fraud_lead l JOIN verify.api_session s ON s.session_id = l.seed_session "
            "WHERE l.finding IN ('CONFIRMED', 'NO_FINDING') GROUP BY 1, 2 ORDER BY 1"
        )
    else:
        sql = (
            "SELECT s.country AS k, l.finding, count(*) AS n FROM verify.api_fraud_lead l JOIN verify.api_session s ON s.session_id = l.seed_session "
            "WHERE l.finding IN ('CONFIRMED', 'NO_FINDING') GROUP BY 1, 2 ORDER BY 1"
        )
    return [_row(x) for x in await conn.execute(text(sql))]


async def handle_times(conn: AsyncConnection) -> list[float]:
    rows = await conn.execute(
        text(
            "SELECT extract(epoch FROM (e.created_at - l.created_at)) AS secs FROM verify.api_fraud_lead l JOIN verify.api_fraud_event e ON e.scope_id = l.lead_id AND e.event_type = 'finding_recorded'"
        )
    )
    return [float(x.secs) for x in rows]


async def count_events_since(conn: AsyncConnection, scope_id: str, since: datetime) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM verify.api_fraud_event WHERE scope_id = :s AND created_at >= :t AND event_type = 'graph_exported'"
                ),
                {"s": scope_id, "t": since},
            )
        ).scalar_one()
    )

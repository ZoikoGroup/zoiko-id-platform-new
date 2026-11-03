"""Persistence for sanctions lists, screening cases and evidence (ZID-ED-04; CR-043).

The list tables are public reference data and not tenant-scoped. Every other table is tenant-scoped (row-level security) and
holds restricted records: party names and birth years live here and nowhere else, never in an event, a log or a safe view."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.screening.domain.decision import (
    RegisterEvidence,
    RegisterPerson,
    Role,
    ScreeningError,
    ScreeningStatus,
)
from zid_platform.modules.screening.domain.lists import Entry, ListId, PartyKind, Snapshot
from zid_platform.modules.screening.domain.matching import Match, Party
from zid_platform.modules.screening.domain.ownership import Control, Edge, Graph, Holder, HolderKind

# ---- lists -----------------------------------------------------------------------------------------------------------


async def put_list(
    conn: AsyncConnection,
    list_id: ListId,
    *,
    version: str,
    content_hash: str,
    entries: list[Entry],
    source_ref: str,
    fetched_at: datetime,
    now: datetime,
) -> str:
    """'UNCHANGED' (the same file again: only its fetch time moves, so it stays fresh) or 'LOADED'."""
    same = (
        await conn.execute(
            text(
                "SELECT version_id, active FROM verify.sanction_list_version WHERE list_id = :l AND content_hash = :h"
            ),
            {"l": list_id.value, "h": content_hash},
        )
    ).first()
    if same is not None and same.active:
        await conn.execute(
            text("UPDATE verify.sanction_list_version SET fetched_at = :f WHERE version_id = :v"),
            {"f": fetched_at, "v": same.version_id},
        )
        return "UNCHANGED"
    await conn.execute(
        text(
            "UPDATE verify.sanction_list_version SET active = false WHERE list_id = :l AND active"
        ),
        {"l": list_id.value},
    )
    if same is not None:  # an older file published again: it becomes the active one
        await conn.execute(
            text(
                "UPDATE verify.sanction_list_version SET active = true, fetched_at = :f WHERE version_id = :v"
            ),
            {"f": fetched_at, "v": same.version_id},
        )
        return "LOADED"
    version_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO verify.sanction_list_version (version_id, list_id, version, content_hash, entry_count, "
            "source_ref, fetched_at, created_at) VALUES (:v, :l, :n, :h, :c, :s, :f, :now)"
        ),
        {
            "v": version_id,
            "l": list_id.value,
            "n": version,
            "h": content_hash,
            "c": len(entries),
            "s": source_ref,
            "f": fetched_at,
            "now": now,
        },
    )
    rows = [
        {
            "v": version_id,
            "r": e.source_ref,
            "k": e.kind.value,
            "n": e.primary_name,
            "d": json.dumps(
                {
                    "aliases": list(e.aliases),
                    "birth": list(e.birth),
                    "countries": list(e.countries),
                    "identifiers": list(e.identifiers),
                    "programs": list(e.programs),
                }
            ),
        }
        for e in {e.source_ref: e for e in entries}.values()
    ]
    for start in range(0, len(rows), 1000):
        await conn.execute(
            text(
                "INSERT INTO verify.sanction_list_entry (version_id, source_ref, kind, primary_name, data) "
                "VALUES (:v, :r, :k, :n, CAST(:d AS jsonb))"
            ),
            rows[start : start + 1000],
        )
    return "LOADED"


async def snapshots(conn: AsyncConnection) -> dict[ListId, Snapshot]:
    out: dict[ListId, Snapshot] = {}
    for v in (
        await conn.execute(
            text(
                "SELECT version_id, list_id, version, content_hash, fetched_at FROM verify.sanction_list_version WHERE active"
            )
        )
    ).all():
        entries = []
        for r in (
            await conn.execute(
                text(
                    "SELECT source_ref, kind, primary_name, data FROM verify.sanction_list_entry WHERE version_id = :v"
                ),
                {"v": v.version_id},
            )
        ).all():
            d = r.data
            entries.append(
                Entry(
                    list_id=ListId(v.list_id),
                    source_ref=r.source_ref,
                    kind=PartyKind(r.kind),
                    primary_name=r.primary_name,
                    aliases=tuple(d.get("aliases", [])),
                    birth=tuple(d.get("birth", [])),
                    countries=tuple(d.get("countries", [])),
                    identifiers=tuple(d.get("identifiers", [])),
                    programs=tuple(d.get("programs", [])),
                )
            )
        out[ListId(v.list_id)] = Snapshot(
            ListId(v.list_id), v.version, v.content_hash, v.fetched_at, tuple(entries)
        )
    return out


# ---- cases -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaseRow:
    case_id: str
    tenant_id: str
    kyb_case_id: str | None
    country: str
    status: ScreeningStatus
    reasons: tuple[str, ...]
    elevated_risk: bool
    authority: dict[str, Any]
    requested_by: str
    current_version: int
    expiry_at: datetime | None
    idempotency_key: str


_CASE = (
    "case_id, tenant_id, kyb_case_id, country, status, reasons, elevated_risk, authority, requested_by, "
    "current_version, expiry_at, idempotency_key"
)


def _case(r: Any) -> CaseRow:
    return CaseRow(
        case_id=str(r.case_id),
        tenant_id=str(r.tenant_id),
        kyb_case_id=str(r.kyb_case_id) if r.kyb_case_id else None,
        country=r.country,
        status=ScreeningStatus(r.status),
        reasons=tuple(r.reasons or ()),
        elevated_risk=bool(r.elevated_risk),
        authority=dict(r.authority),
        requested_by=str(r.requested_by),
        current_version=int(r.current_version),
        expiry_at=r.expiry_at,
        idempotency_key=r.idempotency_key,
    )


async def case_by_key(conn: AsyncConnection, tenant_id: str, key: str) -> CaseRow | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_CASE} FROM verify.screening_case WHERE tenant_id = :t AND idempotency_key = :k"
            ),
            {"t": uuid.UUID(tenant_id), "k": key},
        )
    ).first()
    return _case(row) if row else None


async def case_for_kyb(conn: AsyncConnection, tenant_id: str, kyb_case_id: str) -> CaseRow | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_CASE} FROM verify.screening_case WHERE tenant_id = :t AND kyb_case_id = :k "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"t": uuid.UUID(tenant_id), "k": uuid.UUID(kyb_case_id)},
        )
    ).first()
    return _case(row) if row else None


async def load_case(
    conn: AsyncConnection, tenant_id: str, case_id: str, *, lock: bool = False
) -> CaseRow:
    row = (
        await conn.execute(
            text(
                f"SELECT {_CASE} FROM verify.screening_case WHERE tenant_id = :t AND case_id = :c"
                + (" FOR UPDATE" if lock else "")
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id)},
        )
    ).first()
    if row is None:
        raise ScreeningError("CASE_NOT_FOUND")
    return _case(row)


async def create_case(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    case_id: str,
    kyb_case_id: str | None,
    country: str,
    idempotency_key: str,
    elevated_risk: bool,
    authority: dict[str, Any],
    requested_by: str,
    now: datetime,
) -> bool:
    created = await conn.execute(
        text(
            "INSERT INTO verify.screening_case (case_id, tenant_id, kyb_case_id, country, idempotency_key, status, "
            "elevated_risk, authority, requested_by, created_at, updated_at) VALUES (:c, :t, :k, :n, :i, 'GATHERING', "
            ":e, CAST(:a AS jsonb), :r, :now, :now) ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING case_id"
        ),
        {
            "c": uuid.UUID(case_id),
            "t": uuid.UUID(tenant_id),
            "k": uuid.UUID(kyb_case_id) if kyb_case_id else None,
            "n": country,
            "i": idempotency_key,
            "e": elevated_risk,
            "a": json.dumps(authority),
            "r": uuid.UUID(requested_by),
            "now": now,
        },
    )
    return created.first() is not None


async def put_parties(
    conn: AsyncConnection, tenant_id: str, case_id: str, parties: list[tuple[Role, Party]]
) -> None:
    for role, p in parties:
        await conn.execute(
            text(
                "INSERT INTO verify.screening_party (tenant_id, case_id, ref, role, kind, name, birth, countries, identifiers) "
                "VALUES (:t, :c, :r, :ro, :k, :n, :b, CAST(:co AS jsonb), CAST(:i AS jsonb))"
            ),
            {
                "t": uuid.UUID(tenant_id),
                "c": uuid.UUID(case_id),
                "r": p.ref,
                "ro": role.value,
                "k": p.kind.value,
                "n": p.name,
                "b": p.birth,
                "co": json.dumps(list(p.countries)),
                "i": json.dumps(list(p.identifiers)),
            },
        )


async def load_parties(
    conn: AsyncConnection, tenant_id: str, case_id: str
) -> list[tuple[Role, Party]]:
    rows = (
        await conn.execute(
            text(
                "SELECT ref, role, kind, name, birth, countries, identifiers FROM verify.screening_party "
                "WHERE tenant_id = :t AND case_id = :c ORDER BY role, ref"
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id)},
        )
    ).all()
    return [
        (
            Role(r.role),
            Party(
                r.ref,
                PartyKind(r.kind),
                r.name,
                r.birth,
                tuple(r.countries or ()),
                tuple(r.identifiers or ()),
            ),
        )
        for r in rows
    ]


def _graph_json(g: Graph) -> dict[str, Any]:
    return {
        "subject": g.subject,
        "holders": [
            {"ref": h.ref, "kind": h.kind.value, "terminal": h.terminal} for h in g.holders.values()
        ],
        "edges": [
            {
                "owner": e.owner,
                "owned": e.owned,
                "shares": str(e.shares_pct) if e.shares_pct is not None else None,
                "votes": str(e.votes_pct) if e.votes_pct is not None else None,
                "controls": sorted(c.value for c in e.controls),
                "verified": e.verified,
            }
            for e in g.edges
        ],
    }


def _graph(d: dict[str, Any]) -> Graph:
    holders = {
        h["ref"]: Holder(h["ref"], HolderKind(h["kind"]), bool(h.get("terminal")))
        for h in d["holders"]
    }
    edges = tuple(
        Edge(
            owner=e["owner"],
            owned=e["owned"],
            shares_pct=Decimal(e["shares"]) if e.get("shares") is not None else None,
            votes_pct=Decimal(e["votes"]) if e.get("votes") is not None else None,
            controls=frozenset(Control(c) for c in e.get("controls", [])),
            verified=bool(e.get("verified", True)),
        )
        for e in d["edges"]
    )
    return Graph(d["subject"], holders, edges)


async def put_graph(conn: AsyncConnection, tenant_id: str, case_id: str, graph: Graph) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.screening_ownership (tenant_id, case_id, graph) VALUES (:t, :c, CAST(:g AS jsonb)) "
            "ON CONFLICT (case_id) DO UPDATE SET graph = EXCLUDED.graph"
        ),
        {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id), "g": json.dumps(_graph_json(graph))},
    )


async def load_graph(conn: AsyncConnection, tenant_id: str, case_id: str) -> Graph | None:
    row = (
        await conn.execute(
            text(
                "SELECT graph FROM verify.screening_ownership WHERE tenant_id = :t AND case_id = :c"
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id)},
        )
    ).first()
    return _graph(row.graph) if row else None


async def append_evidence(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    case_id: str,
    version: int,
    status: ScreeningStatus,
    reasons: tuple[str, ...],
    body: dict[str, Any],
    evidence_hash: str,
    jws: str,
    decided_by: str | None,
    rationale: str | None,
    expiry_at: datetime | None,
    matches: tuple[Match, ...],
    now: datetime,
) -> str:
    evidence_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO verify.screening_evidence (evidence_id, tenant_id, case_id, version, decision_status, reasons, "
            "body, evidence_hash, jws, decided_by, rationale, expiry_at, created_at) VALUES (:e, :t, :c, :v, :s, "
            "CAST(:rs AS jsonb), CAST(:b AS jsonb), :h, :j, :d, :ra, :x, :now)"
        ),
        {
            "e": uuid.UUID(evidence_id),
            "t": uuid.UUID(tenant_id),
            "c": uuid.UUID(case_id),
            "v": version,
            "s": status.value,
            "rs": json.dumps(list(reasons)),
            "b": json.dumps(body, default=str),
            "h": evidence_hash,
            "j": jws,
            "d": uuid.UUID(decided_by) if decided_by else None,
            "ra": rationale,
            "x": expiry_at,
            "now": now,
        },
    )
    for m in matches:
        await conn.execute(
            text(
                "INSERT INTO verify.screening_match (match_id, tenant_id, case_id, evidence_version, party_ref, list_id, "
                "list_version, source_ref, matched_name, score, strength, reasons, created_at) VALUES (:i, :t, :c, :v, "
                ":p, :l, :lv, :sr, :mn, :sc, :st, CAST(:rs AS jsonb), :now)"
            ),
            {
                "i": uuid.uuid4(),
                "t": uuid.UUID(tenant_id),
                "c": uuid.UUID(case_id),
                "v": version,
                "p": m.party_ref,
                "l": m.list_id.value,
                "lv": m.list_version,
                "sr": m.source_ref,
                "mn": m.matched_name,
                "sc": m.score,
                "st": m.strength.value,
                "rs": json.dumps(list(m.reasons)),
                "now": now,
            },
        )
    await conn.execute(
        text(
            "UPDATE verify.screening_case SET status = :s, reasons = CAST(:rs AS jsonb), current_version = :v, "
            "expiry_at = :x, updated_at = :now WHERE tenant_id = :t AND case_id = :c"
        ),
        {
            "s": status.value,
            "rs": json.dumps(list(reasons)),
            "v": version,
            "x": expiry_at,
            "now": now,
            "t": uuid.UUID(tenant_id),
            "c": uuid.UUID(case_id),
        },
    )
    return evidence_id


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    version: int
    decision_status: ScreeningStatus
    reasons: tuple[str, ...]
    body: dict[str, Any]
    evidence_hash: str
    jws: str
    decided_by: str | None
    rationale: str | None
    expiry_at: datetime | None


async def history(conn: AsyncConnection, tenant_id: str, case_id: str) -> list[EvidenceRow]:
    rows = (
        await conn.execute(
            text(
                "SELECT version, decision_status, reasons, body, evidence_hash, jws, decided_by, rationale, expiry_at "
                "FROM verify.screening_evidence WHERE tenant_id = :t AND case_id = :c ORDER BY version"
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id)},
        )
    ).all()
    return [
        EvidenceRow(
            int(r.version),
            ScreeningStatus(r.decision_status),
            tuple(r.reasons or ()),
            dict(r.body),
            r.evidence_hash,
            r.jws,
            str(r.decided_by) if r.decided_by else None,
            r.rationale,
            r.expiry_at,
        )
        for r in rows
    ]


async def matches_for(
    conn: AsyncConnection, tenant_id: str, case_id: str, version: int
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT party_ref, list_id, list_version, source_ref, matched_name, score, strength, reasons "
                "FROM verify.screening_match WHERE tenant_id = :t AND case_id = :c AND evidence_version = :v "
                "ORDER BY score DESC"
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id), "v": version},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


# ---- register evidence, triggers, appeals (migration screening_0002) ---------------------------------------------------------


async def put_register(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    ev: RegisterEvidence,
    *,
    source: str,
    now: datetime,
) -> int:
    """Record what the official register said, as the case's next numbered snapshot; returns its number."""
    t, c = uuid.UUID(tenant_id), uuid.UUID(case_id)
    seq = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(seq), 0) + 1 FROM verify.screening_register_snapshot WHERE tenant_id = :t AND case_id = :c"
                ),
                {"t": t, "c": c},
            )
        ).scalar_one()
    )
    await conn.execute(
        text(
            "INSERT INTO verify.screening_register_snapshot (tenant_id, case_id, seq, status, content_hash, source, observed_at) "
            "VALUES (:t, :c, :s, :st, :h, :src, :at)"
        ),
        {"t": t, "c": c, "s": seq, "st": ev.status, "h": ev.content_hash, "src": source, "at": now},
    )
    for p in ev.people:
        await conn.execute(
            text(
                "INSERT INTO verify.screening_register_person (tenant_id, case_id, seq, ref, kind, party_kind, name, birth, natures, ceased) "
                "VALUES (:t, :c, :s, :r, :k, :pk, :n, :b, CAST(:na AS jsonb), :ce)"
            ),
            {
                "t": t, "c": c, "s": seq, "r": p.ref, "k": p.kind, "pk": p.party.kind.value, "n": p.party.name,
                "b": p.party.birth, "na": json.dumps(list(p.natures)), "ce": p.ceased,
            },
        )  # fmt: skip
    return seq


async def load_register(
    conn: AsyncConnection, tenant_id: str, case_id: str, *, seq: int | None = None
) -> RegisterEvidence | None:
    """The snapshot with this number, or the latest; None when the case never saw one."""
    t, c = uuid.UUID(tenant_id), uuid.UUID(case_id)
    snap = (
        await conn.execute(
            text(
                "SELECT seq, status, content_hash FROM verify.screening_register_snapshot WHERE tenant_id = :t AND case_id = :c "
                + ("AND seq = :s" if seq is not None else "ORDER BY seq DESC LIMIT 1")
            ),
            {"t": t, "c": c, **({"s": seq} if seq is not None else {})},
        )
    ).first()
    if snap is None:
        return None
    rows = (
        await conn.execute(
            text(
                "SELECT ref, kind, party_kind, name, birth, natures, ceased FROM verify.screening_register_person "
                "WHERE tenant_id = :t AND case_id = :c AND seq = :s ORDER BY ref"
            ),
            {"t": t, "c": c, "s": snap.seq},
        )
    ).all()
    people = tuple(
        RegisterPerson(
            ref=r.ref,
            kind=r.kind,
            party=Party(r.ref, PartyKind(r.party_kind), r.name, r.birth),
            natures=tuple(r.natures or ()),
            ceased=bool(r.ceased),
        )
        for r in rows
    )
    return RegisterEvidence(snap.status, people, snap.content_hash, int(snap.seq))


async def add_trigger(
    conn: AsyncConnection, tenant_id: str, case_id: str, reason: str, now: datetime
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.screening_trigger (trigger_id, tenant_id, case_id, reason, requested_at) "
            "VALUES (:i, :t, :c, :r, :at)"
        ),
        {
            "i": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "c": uuid.UUID(case_id),
            "r": reason,
            "at": now,
        },
    )


async def open_triggers(conn: AsyncConnection, tenant_id: str) -> dict[str, list[str]]:
    rows = (
        await conn.execute(
            text(
                "SELECT case_id, reason FROM verify.screening_trigger WHERE tenant_id = :t AND handled_version IS NULL "
                "ORDER BY requested_at"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).all()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(str(r.case_id), []).append(r.reason)
    return out


async def close_triggers(conn: AsyncConnection, tenant_id: str, case_id: str, version: int) -> None:
    await conn.execute(
        text(
            "UPDATE verify.screening_trigger SET handled_version = :v "
            "WHERE tenant_id = :t AND case_id = :c AND handled_version IS NULL"
        ),
        {"v": version, "t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id)},
    )


async def add_appeal(
    conn: AsyncConnection, tenant_id: str, case_id: str, *, kind: str, statement: str, statement_hash: str,
    requested_by: str, evidence_version: int, now: datetime,
) -> str:  # fmt: skip
    appeal_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO verify.screening_appeal (appeal_id, tenant_id, case_id, kind, statement, statement_hash, requested_by, "
            "created_at, evidence_version) VALUES (:i, :t, :c, :k, :s, :h, :r, :at, :v)"
        ),
        {
            "i": uuid.UUID(appeal_id), "t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id), "k": kind, "s": statement,
            "h": statement_hash, "r": uuid.UUID(requested_by), "at": now, "v": evidence_version,
        },
    )  # fmt: skip
    return appeal_id


async def appeals_for(conn: AsyncConnection, tenant_id: str, case_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT appeal_id, kind, statement, created_at, evidence_version FROM verify.screening_appeal "
                "WHERE tenant_id = :t AND case_id = :c ORDER BY created_at"
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id)},
        )
    ).all()
    return [
        {
            "appeal_id": str(r.appeal_id), "kind": r.kind, "statement": r.statement,
            "requested_at": r.created_at.isoformat(), "evidence_version": int(r.evidence_version),
        }
        for r in rows
    ]  # fmt: skip


async def count_cases_for_kyb(conn: AsyncConnection, tenant_id: str, kyb_case_id: str) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM verify.screening_case WHERE tenant_id = :t AND kyb_case_id = :k"
                ),
                {"t": uuid.UUID(tenant_id), "k": uuid.UUID(kyb_case_id)},
            )
        ).scalar_one()
    )


async def snapshot_for(
    conn: AsyncConnection, list_id: ListId, content_hash: str
) -> Snapshot | None:
    """A list exactly as it was when a decision named it (old versions are kept), for replaying that decision."""
    v = (
        await conn.execute(
            text(
                "SELECT version_id, list_id, version, content_hash, fetched_at FROM verify.sanction_list_version "
                "WHERE list_id = :l AND content_hash = :h"
            ),
            {"l": list_id.value, "h": content_hash},
        )
    ).first()
    if v is None:
        return None
    entries = []
    for r in (
        await conn.execute(
            text(
                "SELECT source_ref, kind, primary_name, data FROM verify.sanction_list_entry WHERE version_id = :v"
            ),
            {"v": v.version_id},
        )
    ).all():
        d = r.data
        entries.append(
            Entry(
                list_id=ListId(v.list_id), source_ref=r.source_ref, kind=PartyKind(r.kind), primary_name=r.primary_name,
                aliases=tuple(d.get("aliases", [])), birth=tuple(d.get("birth", [])), countries=tuple(d.get("countries", [])),
                identifiers=tuple(d.get("identifiers", [])), programs=tuple(d.get("programs", [])),
            )
        )  # fmt: skip
    return Snapshot(ListId(v.list_id), v.version, v.content_hash, v.fetched_at, tuple(entries))

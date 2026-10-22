"""PostgreSQL persistence of incidents, PIR action items, readiness reviews and change freezes
(G-W8), plus the service catalogue file loader."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.operations.domain.catalogue import Catalogue, parse
from zid_platform.modules.operations.domain.incidents import (
    ActionItem,
    GateResult,
    GateStatus,
    IncidentStatus,
    OperationsError,
    Severity,
)

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
CATALOGUE_FILE = CONFIG / "operations" / "service-catalogue.yaml"


@cache
def catalogue(path: Path = CATALOGUE_FILE) -> Catalogue:
    return parse(yaml.safe_load(path.read_text(encoding="utf-8")))


@dataclass(frozen=True, slots=True)
class Incident:
    incident_id: str
    title: str
    severity: Severity
    status: IncidentStatus
    security: bool
    services: tuple[str, ...]
    cells: tuple[str, ...]
    roles: dict[str, str]
    declared_by: str
    declared_at: datetime
    mitigated_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    pir_ref: str | None
    version: int


def _incident(r: Any) -> Incident:
    m = r._mapping
    return Incident(
        str(m["incident_id"]),
        m["title"],
        Severity(m["severity"]),
        IncidentStatus(m["status"]),
        bool(m["security"]),
        tuple(m["services"]),
        tuple(m["cells"]),
        dict(m["roles"] or {}),
        str(m["declared_by"]),
        m["declared_at"],
        m["mitigated_at"],
        m["resolved_at"],
        m["closed_at"],
        m["pir_ref"],
        int(m["version"]),
    )


async def insert_incident(conn: AsyncConnection, v: Mapping[str, Any]) -> Incident:
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.incident (incident_id, title, severity, security, services, cells,
                    roles, declared_by)
                VALUES (:id, :title, :sev, :sec, :services, :cells, CAST(:roles AS jsonb), :by) RETURNING *
            """),
            {
                "id": uuid.uuid4(),
                "title": v["title"],
                "sev": v["severity"],
                "sec": v["security"],
                "services": list(v["services"]),
                "cells": list(v["cells"]),
                "roles": json.dumps(v["roles"]),
                "by": uuid.UUID(v["actor"]),
            },
        )
    ).one()
    return _incident(row)


async def load_incident(
    conn: AsyncConnection, incident_id: str, *, lock: bool = False
) -> Incident | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.incident WHERE incident_id = :id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": uuid.UUID(incident_id)},
        )
    ).first()
    return _incident(row) if row else None


async def incidents(conn: AsyncConnection, *, open_only: bool, limit: int = 100) -> list[Incident]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.incident"
                + (" WHERE status NOT IN ('RESOLVED', 'CLOSED')" if open_only else "")
                + " ORDER BY declared_at DESC LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [_incident(r) for r in rows]


async def update_incident(conn: AsyncConnection, incident_id: str, **fields: Any) -> Incident:
    allowed = {
        "severity",
        "status",
        "security",
        "roles",
        "mitigated_at",
        "resolved_at",
        "closed_at",
        "pir_ref",
    }
    assert set(fields) <= allowed
    params = {**fields, "id": uuid.UUID(incident_id)}
    sets = []
    for k in fields:
        if k == "roles":
            sets.append("roles = CAST(:roles AS jsonb)")
            params["roles"] = json.dumps(fields["roles"])
        else:
            sets.append(f"{k} = :{k}")
    row = (
        await conn.execute(
            text(
                f"UPDATE platform.incident SET {', '.join(sets)}, version = version + 1 "
                "WHERE incident_id = :id RETURNING *"
            ),
            params,
        )
    ).one()
    return _incident(row)


async def append_entry(
    conn: AsyncConnection,
    incident_id: str,
    *,
    kind: str,
    actor: str,
    note: str,
    data: Mapping[str, Any],
) -> int:
    seq = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(seq), 0) + 1 FROM platform.incident_entry WHERE incident_id = :id"
                ),
                {"id": uuid.UUID(incident_id)},
            )
        ).scalar_one()
    )
    await conn.execute(
        text("""
            INSERT INTO platform.incident_entry (incident_id, seq, kind, actor, note, data)
            VALUES (:id, :seq, :kind, :actor, :note, CAST(:data AS jsonb))
        """),
        {
            "id": uuid.UUID(incident_id),
            "seq": seq,
            "kind": kind,
            "actor": uuid.UUID(actor),
            "note": note,
            "data": json.dumps(dict(data), default=str),
        },
    )
    return seq


async def timeline(conn: AsyncConnection, incident_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT * FROM platform.incident_entry WHERE incident_id = :id ORDER BY seq"),
            {"id": uuid.UUID(incident_id)},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def add_action_item(
    conn: AsyncConnection, incident_id: str, *, title: str, owner: str | None, due: date | None
) -> str:
    iid = uuid.uuid4()
    await conn.execute(
        text("""
            INSERT INTO platform.incident_action_item (item_id, incident_id, title, owner, due)
            VALUES (:id, :inc, :title, :owner, :due)
        """),
        {
            "id": iid,
            "inc": uuid.UUID(incident_id),
            "title": title,
            "owner": uuid.UUID(owner) if owner else None,
            "due": due,
        },
    )
    return str(iid)


async def verify_action_item(
    conn: AsyncConnection, incident_id: str, item_id: str, verifier: str
) -> None:
    try:
        async with conn.begin_nested():
            r = await conn.execute(
                text("""
                    UPDATE platform.incident_action_item SET verified_by = :v, verified_at = now()
                    WHERE item_id = :id AND incident_id = :inc AND verified_by IS NULL
                """),
                {"v": uuid.UUID(verifier), "id": uuid.UUID(item_id), "inc": uuid.UUID(incident_id)},
            )
    except IntegrityError as e:
        raise OperationsError(
            "PIR_VERIFICATION_NOT_INDEPENDENT", "An item is never verified by its owner."
        ) from e
    if not r.rowcount:
        raise OperationsError("PIR_ACTION_NOT_FOUND", "No such open action item on this incident.")


async def action_items(conn: AsyncConnection, incident_id: str) -> list[tuple[str, ActionItem]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.incident_action_item WHERE incident_id = :id ORDER BY created_at"
            ),
            {"id": uuid.UUID(incident_id)},
        )
    ).all()
    return [
        (
            str(r.item_id),
            ActionItem(
                r.title,
                str(r.owner) if r.owner else None,
                r.due,
                str(r.verified_by) if r.verified_by else None,
            ),
        )
        for r in rows
    ]


async def insert_review(
    conn: AsyncConnection,
    *,
    service_id: str,
    release_ref: str,
    ready: bool,
    actor: str,
    gates: Sequence[GateResult],
) -> str:
    rid = uuid.uuid4()
    await conn.execute(
        text("""
            INSERT INTO platform.readiness_review (review_id, service_id, release_ref, production_ready, recorded_by)
            VALUES (:id, :s, :rel, :ready, :by)
        """),
        {"id": rid, "s": service_id, "rel": release_ref, "ready": ready, "by": uuid.UUID(actor)},
    )
    for g in gates:
        await conn.execute(
            text("""
                INSERT INTO platform.readiness_gate (review_id, gate, status, evidence_ref, risk_owner,
                    risk_expires, compensating_control)
                VALUES (:id, :g, :st, :ev, :owner, :exp, :comp)
            """),
            {
                "id": rid,
                "g": g.gate,
                "st": g.status.value,
                "ev": g.evidence_ref,
                "owner": uuid.UUID(g.risk_owner) if g.risk_owner else None,
                "exp": g.risk_expires,
                "comp": g.compensating_control,
            },
        )
    return str(rid)


async def latest_review(
    conn: AsyncConnection, service_id: str
) -> tuple[dict[str, Any], list[GateResult]] | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.readiness_review WHERE service_id = :s ORDER BY recorded_at DESC LIMIT 1"
            ),
            {"s": service_id},
        )
    ).first()
    if row is None:
        return None
    gates = (
        await conn.execute(
            text("SELECT * FROM platform.readiness_gate WHERE review_id = :id"),
            {"id": row.review_id},
        )
    ).all()
    return dict(row._mapping), [
        GateResult(
            g.gate,
            GateStatus(g.status),
            g.evidence_ref,
            str(g.risk_owner) if g.risk_owner else None,
            g.risk_expires,
            g.compensating_control,
        )
        for g in gates
    ]


async def active_freeze(conn: AsyncConnection, service_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.change_freeze WHERE service_id = :s AND lifted_at IS NULL"
            ),
            {"s": service_id},
        )
    ).first()
    return dict(row._mapping) if row else None


async def freeze(
    conn: AsyncConnection, service_id: str, *, reason: str, incident_id: str | None, actor: str
) -> str:
    fid = uuid.uuid4()
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                    INSERT INTO platform.change_freeze (freeze_id, service_id, reason_code, incident_id, frozen_by)
                    VALUES (:id, :s, :r, :inc, :by)
                """),
                {
                    "id": fid,
                    "s": service_id,
                    "r": reason,
                    "inc": uuid.UUID(incident_id) if incident_id else None,
                    "by": uuid.UUID(actor),
                },
            )
    except IntegrityError as e:
        raise OperationsError(
            "CHANGE_ALREADY_FROZEN", "The service already has an active freeze."
        ) from e
    return str(fid)


async def lift(conn: AsyncConnection, service_id: str, *, actor: str, grant: str) -> str | None:
    row = (
        await conn.execute(
            text("""
                UPDATE platform.change_freeze SET lifted_by = :by, lifted_at = now(), lift_grant_id = :g
                WHERE service_id = :s AND lifted_at IS NULL RETURNING freeze_id
            """),
            {"s": service_id, "by": uuid.UUID(actor), "g": uuid.UUID(grant)},
        )
    ).first()
    return str(row.freeze_id) if row else None

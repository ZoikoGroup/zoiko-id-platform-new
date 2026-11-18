"""Ongoing screening of a subject (ZID-DTV-WF-06 S12, ENG-14; CR-113).

A watch rests on a case the platform already holds, so its subject, market, purpose and policy are that case's, never retyped. It is created inactive; two different people approve it; the release gates must be
signed; only then does it run. `run_due` screens a watch's subject again when its cadence has run out for any source, by opening a new case under a key made from the watch and its run number (so a retry
is the same run). Each source's last attempt and last success are kept apart, and "monitored continuously" is claimable only while every source has a success inside the cadence.

A watch re-screens through the same eligibility and source gates as any case: an unsigned or expired policy, a stale list or a provider outage makes the run UNAVAILABLE, never clear, and records an attempt and
no success.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.screening.application import screening_requests as requests
from zid_platform.modules.screening.domain import coverage as cov
from zid_platform.modules.screening.domain import eligibility as elig
from zid_platform.modules.screening.domain import monitoring_watch as mw
from zid_platform.modules.screening.domain.decision import ScreeningError
from zid_platform.modules.screening.domain.lists import ListId, PartyKind, Snapshot
from zid_platform.modules.screening.domain.matching import MatchPolicy, Party
from zid_platform.modules.screening.infrastructure.market_policies import MarketPack


async def create_watch(
    conn: AsyncConnection,
    request_id: str,
    *,
    created_by: str,
    cadence: timedelta,
    triggers: frozenset[mw.Trigger],
    now: datetime,
) -> dict[str, Any]:
    """A watch on the subject of an existing case: the classes and sources are the ones that case used. It starts inactive."""
    row = (
        await conn.execute(
            text("SELECT tenant_id FROM verify.screening_request WHERE request_id = :r"),
            {"r": uuid.UUID(request_id)},
        )
    ).first()
    if row is None:
        raise ScreeningError("CASE_NOT_FOUND", request_id)
    sources = await requests._sources_of(conn, request_id)
    if not sources:
        raise ScreeningError("WATCH_HAS_NO_SOURCES", "the case used no source to watch")
    watch_id = uuid.uuid4()
    classes = sorted({s.klass.value for s in sources})
    source_ids = sorted(s.source_id for s in sources)
    draft = _draft(
        str(watch_id), str(row.tenant_id), request_id, classes, source_ids, triggers, cadence, ()
    )
    problems = [
        p for p in mw.activation_problems(draft, gates_signed=True) if p != "TWO_APPROVERS_REQUIRED"
    ]
    if problems:
        raise ScreeningError("WATCH_INVALID", ",".join(problems))
    await conn.execute(
        text(
            "INSERT INTO verify.screening_watch (watch_id, tenant_id, request_id, classes, sources, triggers, cadence_seconds, created_by, created_at) "
            "VALUES (:w, :t, :r, CAST(:c AS jsonb), CAST(:s AS jsonb), CAST(:tr AS jsonb), :cad, :by, :at)"
        ),
        {
            "w": watch_id,
            "t": row.tenant_id,
            "r": uuid.UUID(request_id),
            "c": json.dumps(classes),
            "s": json.dumps(source_ids),
            "tr": json.dumps(sorted(t.value for t in triggers)),
            "cad": int(cadence.total_seconds()),
            "by": uuid.UUID(created_by),
            "at": now,
        },
    )
    return {"watch_id": str(watch_id), "active": False, "classes": classes, "sources": source_ids}


def _draft(
    watch_id: str,
    tenant: str,
    request_id: str,
    classes: list[str],
    sources: list[str],
    triggers: frozenset[mw.Trigger],
    cadence: timedelta,
    approved: tuple[str, ...],
    active: bool = False,
) -> mw.Watch:
    return mw.Watch(
        watch_id=watch_id,
        tenant_id=tenant,
        subject_ref=request_id,
        purpose="WATCH",
        classes=frozenset(classes),
        sources=frozenset(sources),
        triggers=triggers,
        cadence=cadence,
        active=active,
        approved_by=approved,
    )


async def _load(conn: AsyncConnection, watch_id: str, *, lock: bool = False) -> Any:
    sql = "SELECT watch_id, tenant_id, request_id, classes, sources, triggers, cadence_seconds, active, approved_by, created_by, runs FROM verify.screening_watch WHERE watch_id = :w"
    row = (
        await conn.execute(text(sql + (" FOR UPDATE" if lock else "")), {"w": uuid.UUID(watch_id)})
    ).first()
    if row is None:
        raise ScreeningError("WATCH_NOT_FOUND", watch_id)
    return row


async def approve_watch(conn: AsyncConnection, watch_id: str, *, approver: str) -> dict[str, Any]:
    """One person's approval. The person who created the watch cannot approve it, and one person approves once."""
    row = await _load(conn, watch_id, lock=True)
    if approver == str(row.created_by):
        raise ScreeningError("APPROVER_CREATED_THE_WATCH")
    people = list(row.approved_by)
    if approver in people:
        raise ScreeningError("APPROVER_ALREADY_APPROVED")
    people.append(approver)
    await conn.execute(
        text(
            "UPDATE verify.screening_watch SET approved_by = CAST(:a AS jsonb) WHERE watch_id = :w"
        ),
        {"a": json.dumps(people), "w": uuid.UUID(watch_id)},
    )
    return {"watch_id": watch_id, "approvals": len(people), "active": bool(row.active)}


async def activate_watch(
    conn: AsyncConnection, watch_id: str, *, gates_signed: bool
) -> dict[str, Any]:
    """Switch a watch on. Refused, naming every control, unless two people approved it and the release gates are signed."""
    row = await _load(conn, watch_id, lock=True)
    watch = _draft(
        watch_id,
        str(row.tenant_id),
        str(row.request_id),
        list(row.classes),
        list(row.sources),
        frozenset(mw.Trigger(t) for t in row.triggers),
        timedelta(seconds=int(row.cadence_seconds)),
        tuple(row.approved_by),
    )
    problems = mw.activation_problems(watch, gates_signed=gates_signed)
    if problems:
        raise ScreeningError("WATCH_NOT_ACTIVATABLE", ",".join(problems))
    await conn.execute(
        text("UPDATE verify.screening_watch SET active = true WHERE watch_id = :w"),
        {"w": uuid.UUID(watch_id)},
    )
    return {"watch_id": watch_id, "active": True}


async def _marks(conn: AsyncConnection, watch_id: str) -> dict[str, mw.Watermark]:
    rows = (
        await conn.execute(
            text(
                "SELECT source_id, last_attempt_at, last_success_at, snapshot FROM verify.screening_watch_mark WHERE watch_id = :w"
            ),
            {"w": uuid.UUID(watch_id)},
        )
    ).all()
    return {
        r.source_id: mw.Watermark(r.last_attempt_at, r.last_success_at, r.snapshot) for r in rows
    }


async def status(conn: AsyncConnection, watch_id: str, *, now: datetime) -> dict[str, Any]:
    """Where a watch stands: its approvals, the attempt and the success of each source, and whether it may say it monitors continuously."""
    row = await _load(conn, watch_id)
    marks = await _marks(conn, watch_id)
    watch = mw.Watch(
        str(row.watch_id), str(row.tenant_id), str(row.request_id), "WATCH", frozenset(row.classes), frozenset(row.sources), frozenset(mw.Trigger(t) for t in row.triggers),
        timedelta(seconds=int(row.cadence_seconds)), bool(row.active), approved_by=tuple(row.approved_by), watermarks=marks,
    )  # fmt: skip
    return {
        "watch_id": watch_id,
        "active": bool(row.active),
        "approvals": len(set(row.approved_by)),
        "cadence_seconds": int(row.cadence_seconds),
        "runs": int(row.runs),
        "continuous": mw.may_claim_continuous(watch, now),
        "sources": {
            s: {
                "last_attempt_at": m.last_attempt_at.isoformat() if m.last_attempt_at else None,
                "last_success_at": m.last_success_at.isoformat() if m.last_success_at else None,
                "snapshot": m.snapshot,
            }
            for s, m in sorted(marks.items())
        },
    }


async def market_of(conn: AsyncConnection, watch_id: str) -> str:
    """The market a watch's case was screened in: where its release gates are read from."""
    row = await _load(conn, watch_id)
    case = await requests._row(conn, str(row.request_id))
    if case is None:
        raise ScreeningError("CASE_NOT_FOUND", str(row.request_id))
    return str(case.market)


async def watch_tenant(conn: AsyncConnection, watch_id: str) -> str | None:
    """The tenant a watch belongs to, for a screening reviewer (the reviewer's view across tenants, set only for this read)."""
    try:
        key = uuid.UUID(watch_id)
    except ValueError:
        return None
    await conn.execute(text("SELECT set_config('app.screening_reviewer', 'on', true)"))
    try:
        row = (
            await conn.execute(
                text("SELECT tenant_id FROM verify.screening_watch WHERE watch_id = :w"), {"w": key}
            )
        ).first()
    finally:
        await conn.execute(text("SELECT set_config('app.screening_reviewer', '', true)"))
    return None if row is None else str(row.tenant_id)


async def request_rescreen_for_source(
    conn: AsyncConnection, source_id: str, *, now: datetime
) -> int:
    """Ask every active watch that depends on this source to screen again, whatever its cadence says. The request is a mark the scheduled pass reads; nothing is screened here. Runs for a screening
    reviewer: the watches are read across tenants, and each is marked under its own."""
    from zid_db import in_tenant

    await conn.execute(text("SELECT set_config('app.screening_reviewer', 'on', true)"))
    try:
        tenants = [
            str(r.tenant_id)
            for r in (
                await conn.execute(
                    text(
                        "SELECT DISTINCT tenant_id FROM verify.screening_watch WHERE active AND sources ? :s"
                    ),
                    {"s": source_id},
                )
            ).all()
        ]
    finally:
        await conn.execute(text("SELECT set_config('app.screening_reviewer', '', true)"))
    marked = 0
    for tenant in tenants:
        async with in_tenant(conn, tenant):
            result = await conn.execute(
                text(
                    "UPDATE verify.screening_watch SET rescreen_requested_at = :at WHERE tenant_id = :t AND active AND sources ? :s"
                ),
                {"at": now, "t": uuid.UUID(tenant), "s": source_id},
            )
            marked += int(result.rowcount or 0)
    return marked


async def due_watches(conn: AsyncConnection, tenant_id: str, *, now: datetime) -> list[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT watch_id, sources, triggers, cadence_seconds, rescreen_requested_at FROM verify.screening_watch WHERE tenant_id = :t AND active"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).all()
    asked = [str(r.watch_id) for r in rows if r.rescreen_requested_at is not None]
    watches = []
    for r in rows:
        marks = await _marks(conn, str(r.watch_id))
        watches.append(
            mw.Watch(
                str(r.watch_id),
                tenant_id,
                "",
                "WATCH",
                frozenset(),
                frozenset(r.sources),
                frozenset(mw.Trigger(t) for t in r.triggers),
                timedelta(seconds=int(r.cadence_seconds)),
                True,
                watermarks=marks,
            )
        )
    return sorted(
        {*mw.due(watches, now), *asked}
    )  # a watch a new list release asked about is due whatever its cadence says


async def run_watch(
    conn: AsyncConnection,
    watch_id: str,
    *,
    pack: MarketPack | None,
    snapshots: Mapping[ListId, Snapshot],
    max_age: timedelta,
    match_policy: MatchPolicy,
    identity: elig.IdentityClaim | None,
    now: datetime,
    registry: Any = None,
    adapters: Any = None,
) -> dict[str, Any]:
    """Screen a watch's subject again. The run is a new case under a key made from the watch and its run number. Every source gets an ATTEMPT mark; only a source that answered (available) gets a SUCCESS."""
    row = await _load(conn, watch_id, lock=True)
    origin = str(row.request_id)
    case = await requests._row(conn, origin)
    subject = (
        await conn.execute(
            text(
                "SELECT kind, name, birth, countries, identifiers, expired_identifiers FROM verify.screening_request_subject WHERE request_id = :r"
            ),
            {"r": uuid.UUID(origin)},
        )
    ).first()
    if case is None or subject is None:
        raise ScreeningError("CASE_NOT_FOUND", origin)
    party = Party(
        "subject",
        PartyKind(subject.kind),
        subject.name,
        subject.birth,
        tuple(subject.countries),
        tuple(subject.identifiers),
        tuple(subject.expired_identifiers),
    )
    run_no = int(row.runs) + 1
    request = elig.Request(
        str(row.tenant_id),
        case.market,
        case.purpose,
        case.region,
        elig.SubjectType(case.subject_type),
        identity,
        frozenset(cov.Klass(k) for k in row.classes),
        case.subject_ref,
    )
    attempt_marks = {s: now for s in row.sources}
    view: dict[str, Any] | None = None
    refusal: ScreeningError | None = None
    try:
        view = await requests.open_request(
            conn, str(row.tenant_id), actor=str(row.created_by), idempotency_key=f"watch-{watch_id}-{run_no}", request=request, subject=party, pack=pack, snapshots=snapshots,
            max_age=max_age, match_policy=match_policy, now=now, registry=registry, adapters=adapters,
        )  # fmt: skip
    except ScreeningError as e:
        refusal = e  # an unsigned policy, an expired identity claim: the run is an attempt that did not succeed
    succeeded: dict[str, str] = {}
    if view is not None:
        for s in await requests._sources_of(conn, view["request_id"]):
            fresh = s.last_validated_at is not None and now - s.last_validated_at <= max_age
            if (
                s.available and s.snapshot and fresh
            ):  # answered, from a corpus released inside the policy's age: a stale list is an attempt, not a success
                succeeded[s.source_id] = s.snapshot
    for source_id in row.sources:
        await conn.execute(
            text(
                "INSERT INTO verify.screening_watch_mark (watch_id, tenant_id, source_id, last_attempt_at, last_success_at, snapshot) VALUES (:w, :t, :s, :at, :ok, :sn) "
                "ON CONFLICT (watch_id, source_id) DO UPDATE SET last_attempt_at = EXCLUDED.last_attempt_at, "
                "last_success_at = coalesce(EXCLUDED.last_success_at, verify.screening_watch_mark.last_success_at), snapshot = coalesce(EXCLUDED.snapshot, verify.screening_watch_mark.snapshot)"
            ),
            {
                "w": uuid.UUID(watch_id),
                "t": row.tenant_id,
                "s": source_id,
                "at": attempt_marks[source_id],
                "ok": now if source_id in succeeded else None,
                "sn": succeeded.get(source_id),
            },
        )
    await conn.execute(
        text(
            "UPDATE verify.screening_watch SET runs = :n, rescreen_requested_at = NULL WHERE watch_id = :w"
        ),
        {"n": run_no, "w": uuid.UUID(watch_id)},
    )
    return {
        "watch_id": watch_id,
        "run": run_no,
        "request_id": view["request_id"] if view else None,
        "refused": refusal.detail if refusal else None,
        "sources_succeeded": sorted(succeeded),
        "sources_not_succeeded": sorted(set(row.sources) - set(succeeded)),
    }

"""The Command Center's read side (ZID-CPS-WF-02 sections 05, 07, 08, 09).

Every figure is computed from the tenant's own facts inside its own transaction (row-level security; the tenant never comes from the request,
Q02), minus the facts an approved correction excluded, and carries its definition version, quality and the watermark it was computed at.
Access is decided afresh per call from the person's live roles and analytics grants; every refusal, drill, export, download, correction and
grant is written to the immutable audit record (07.12 item 05)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.tenant.ports import live_roles
from zid_platform.modules.verify_analytics.domain import access as ac
from zid_platform.modules.verify_analytics.domain import metrics as mx
from zid_platform.modules.verify_analytics.domain.definitions import CATALOGUE

MAX_WINDOW = timedelta(days=92)
EXPORT_TTL = timedelta(hours=24)


class AnalyticsError(ValueError):
    def __init__(self, code: str, detail: str = "", status: int = 422) -> None:
        super().__init__(code)
        self.code, self.detail, self.status = code, detail or code, status


# ------------------------------------------------------------------------------------------------------------------------------ audit


async def audit(
    conn: AsyncConnection,
    tenant: str,
    actor: str,
    *,
    action: str,
    outcome: str,
    target: str,
    details: dict[str, str],
    reason: str | None = None,
) -> None:
    await record_audit(
        conn, tenant,
        AuditFacts(
            event_type=f"verify_analytics.{action}", actor_type="HUMAN", action=action, target_type="verify_analytics", target_ref=target[:128],
            outcome=outcome, evidence_class=EvidenceClass.E3, occurred_at=datetime.now(UTC), actor_principal_id=actor, reason_code=reason,
            source_service="verify_analytics", source_event_id=str(uuid7()), details=details,
        ),
    )  # fmt: skip


# ------------------------------------------------------------------------------------------------------------------------------ access


async def _roles(conn: AsyncConnection, principal: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {a.role.value for a in await live_roles(conn, principal) if a.status.value == "ACTIVE"}
        )
    )


async def _granted(conn: AsyncConnection, principal: str, now: datetime) -> frozenset[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT permissions FROM metering.va_grant WHERE grantee = :p AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > :now)"
            ),
            {"p": uuid.UUID(principal), "now": now},
        )
    ).all()
    return frozenset(p for r in rows for p in r.permissions)


async def permissions(conn: AsyncConnection, principal: str) -> list[str]:
    roles, granted = (
        await _roles(conn, principal),
        await _granted(conn, principal, datetime.now(UTC)),
    )
    return sorted(p for p in ac.PERMISSIONS if ac.allowed(roles, granted, p))


async def check(conn: AsyncConnection, tenant: str, principal: str, permission: str) -> bool:
    """Does the person hold the key now. A refusal is recorded; the caller commits that record before refusing the request."""
    if ac.allowed(
        await _roles(conn, principal),
        await _granted(conn, principal, datetime.now(UTC)),
        permission,
    ):
        return True
    await audit(
        conn,
        tenant,
        principal,
        action="access.decision",
        outcome="DENIED",
        target=permission,
        details={"permission": permission},
        reason="NO_ANALYTICS_GRANT",
    )
    return False


async def give(
    conn: AsyncConnection,
    tenant: str,
    actor: str,
    *,
    grantee: str,
    bundle: str,
    expires_at: datetime | None,
) -> str | AnalyticsError:
    """The grant id, or the refusal (already recorded; returned rather than raised so the record commits)."""
    now = datetime.now(UTC)
    member = any(a.status.value == "ACTIVE" for a in await live_roles(conn, grantee))
    try:
        keys = ac.check_grant(
            actor_roles=await _roles(conn, actor),
            actor=actor,
            grantee=grantee,
            grantee_is_member=member,
            bundle=bundle,
            now=now,
            expires_at=expires_at,
        )
    except ac.AccessError as e:
        await audit(
            conn,
            tenant,
            actor,
            action="access.granted",
            outcome="DENIED",
            target=grantee,
            details={"bundle": bundle},
            reason=e.code,
        )
        return AnalyticsError(e.code, e.detail, 403 if "OWNER" in e.code else 422)
    gid = uuid.uuid4()
    await conn.execute(
        text("INSERT INTO metering.va_grant (grant_id, tenant_id, grantee, bundle, permissions, granted_by, granted_at, expires_at) "
             "VALUES (:g, :t, :p, :b, :k, :by, :now, :exp)"),
        {"g": gid, "t": uuid.UUID(tenant), "p": uuid.UUID(grantee), "b": bundle, "k": sorted(keys), "by": uuid.UUID(actor), "now": now, "exp": expires_at},
    )  # fmt: skip
    await audit(
        conn,
        tenant,
        actor,
        action="access.granted",
        outcome="SUCCESS",
        target=str(gid),
        details={"bundle": bundle, "grantee": grantee},
    )
    return str(gid)


async def take_away(conn: AsyncConnection, tenant: str, actor: str, grant_id: str) -> None:
    if "OWNER" not in await _roles(conn, actor):
        raise AnalyticsError(
            "ANALYTICS_DELEGATION_OWNER_ONLY", "Only an Owner takes analytics access away.", 403
        )
    gone = await conn.execute(
        text(
            "UPDATE metering.va_grant SET revoked_at = now(), revoked_by = :by WHERE grant_id = :g AND revoked_at IS NULL"
        ),
        {"g": uuid.UUID(grant_id), "by": uuid.UUID(actor)},
    )
    if not gone.rowcount:
        raise AnalyticsError(
            "ANALYTICS_GRANT_NOT_FOUND", "No live analytics grant by that id.", 404
        )
    await audit(
        conn, tenant, actor, action="access.revoked", outcome="SUCCESS", target=grant_id, details={}
    )


async def grants(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM metering.va_grant WHERE revoked_at IS NULL ORDER BY granted_at DESC"
            )
        )
    ).all()
    return [{"grant_id": str(r.grant_id), "grantee": str(r.grantee), "bundle": r.bundle, "permissions": list(r.permissions),
             "granted_at": r.granted_at.isoformat(), "expires_at": r.expires_at.isoformat() if r.expires_at else None} for r in rows]  # fmt: skip


# ------------------------------------------------------------------------------------------------------------------------------ facts


def window(
    start: date | None, end: date | None, tz: str, now: datetime
) -> tuple[datetime, datetime]:
    """Whole days in the person's timezone (Q13: day edges follow DST), at most 92 of them; the default is the last 7 days."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise AnalyticsError("TIMEZONE_UNKNOWN", "Unknown timezone.") from e
    today = now.astimezone(zone).date()
    end = end or today
    start = start or end - timedelta(days=6)
    if start > end:
        raise AnalyticsError("WINDOW_INVALID", "The window ends before it starts.")
    lo = datetime.combine(start, datetime.min.time(), zone)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), zone)
    if hi - lo > MAX_WINDOW + timedelta(days=1):
        raise AnalyticsError("WINDOW_TOO_LONG", "Choose at most 92 days.")
    return lo, hi


_EXCLUDED = (
    "event_id NOT IN (SELECT event_id FROM metering.va_correction WHERE approved_by IS NOT NULL)"
)


async def journeys(
    conn: AsyncConnection, lo: datetime, hi: datetime, *, environment: str, market: str | None
) -> list[mx.Journey]:
    """Every fact of the sessions created in the window (their later events too, so a decision after the window still counts for its cohort)."""
    rows = (
        await conn.execute(
            text(f"""
                SELECT event_type, session_ref, occurred_at, environment, market, attrs FROM metering.va_fact
                WHERE {_EXCLUDED} AND session_ref IN (
                    SELECT session_ref FROM metering.va_fact WHERE event_type = 'verification.session.created' AND occurred_at >= :lo AND occurred_at < :hi
                      AND environment = :env AND (CAST(:mk AS text) IS NULL OR market = :mk) AND {_EXCLUDED})
            """),
            {"lo": lo, "hi": hi, "env": environment, "mk": market},
        )
    ).all()
    return mx.journeys([dict(r._mapping) for r in rows])


async def facts_of(
    conn: AsyncConnection, prefix: str, lo: datetime, hi: datetime
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                f"SELECT event_type, case_id, occurred_at, attrs FROM metering.va_fact WHERE {_EXCLUDED} AND event_type LIKE :p AND occurred_at >= :lo AND occurred_at < :hi"
            ),
            {"p": prefix + "%", "lo": lo, "hi": hi},
        )
    ).all()
    return [
        {"event_type": r.event_type, "occurred_at": r.occurred_at, **(r.attrs or {})} for r in rows
    ]


async def watermarks(conn: AsyncConnection) -> list[dict[str, Any]]:
    return [
        dict(r._mapping)
        for r in (
            await conn.execute(text("SELECT * FROM metering.va_watermark ORDER BY source"))
        ).all()
    ]


# ------------------------------------------------------------------------------------------------------------------------------ views


async def quality(
    conn: AsyncConnection, *, now: datetime, stale_after: timedelta
) -> tuple[str, list[dict[str, Any]], str | None]:
    q, rows = mx.freshness(await watermarks(conn), now=now, stale_after=stale_after)
    mark = max((r["last_ingested_at"] for r in rows), default=None)
    return q, rows, mark


def _scope(
    environment: str,
    market: str | None,
    lo: datetime,
    hi: datetime,
    tz: str,
    mark: str | None,
    k: int,
) -> dict[str, Any]:
    return {"environment": environment, "market": market, "from": lo.isoformat(), "to": hi.isoformat(), "timezone": tz, "watermark_utc": mark, "min_group": k,
            "data_label": "SANDBOX" if environment in ("local", "test", "dev", "sandbox") else "OBSERVED"}  # fmt: skip


def fingerprint(request: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()


async def overview(
    conn: AsyncConnection,
    *,
    lo: datetime,
    hi: datetime,
    tz: str,
    environment: str,
    market: str | None,
    now: datetime,
    k: int,
    stale_after: timedelta,
) -> dict[str, Any]:
    q, fresh, mark = await quality(conn, now=now, stale_after=stale_after)
    js = await journeys(conn, lo, hi, environment=environment, market=market)
    m = mx.session_metrics(js, now=now, k=k, quality=q)
    hooks = mx.webhook_delivery(await facts_of(conn, "webhook.delivery.", lo, hi), k=k, quality=q)
    scope = _scope(environment, market, lo, hi, tz, mark, k)
    return {
        "scope": scope, "quality": q, "freshness": fresh,
        "tiles": [m["M01"], m["M03"], m["M05"], hooks],
        "decisions": mx.decision_split(js, now=now, k=k), "review": m["M09"],
        "query_fingerprint": fingerprint({"view": "overview", **scope}),
    }  # fmt: skip


SESSION_METRICS = ("M01", "M02", "M03", "M04", "M05", "M06", "M07", "M08", "M09", "M10")
DIMENSIONS = ("market", "method", "environment", "day")


async def query(conn: AsyncConnection, *, metric_ids: list[str], dimension: str | None, cohort: str, lo: datetime, hi: datetime, tz: str, environment: str,
                market: str | None, now: datetime, k: int, stale_after: timedelta) -> dict[str, Any]:  # fmt: skip
    """Allow-listed metrics and dimensions only (no arbitrary SQL, 08); every row protected by the small-group rules."""
    unknown = [m for m in metric_ids if m not in CATALOGUE]
    if unknown or not metric_ids:
        raise AnalyticsError(
            "METRIC_UNKNOWN", f"Unknown metric: {', '.join(unknown) or 'none given'}."
        )
    if dimension is not None and dimension not in DIMENSIONS:
        raise AnalyticsError("DIMENSION_NOT_ALLOWED", "That breakdown is not available.")
    if cohort not in ("created", "started"):
        raise AnalyticsError("COHORT_UNKNOWN", "Cohort is created or started.")
    if any(CATALOGUE[m].finance for m in metric_ids):
        raise AnalyticsError(
            "FINANCE_METRIC_ELSEWHERE",
            "Cost and yield are read from the economics view, under billing access.",
            403,
        )
    q, fresh, mark = await quality(conn, now=now, stale_after=stale_after)
    js = await journeys(conn, lo, hi, environment=environment, market=market)
    sm = mx.session_metrics(js, now=now, k=k, quality=q)
    out: dict[str, Any] = {"results": [], "breakdowns": {}}
    for mid in metric_ids:
        if mid in SESSION_METRICS:
            out["results"].append(sm[mid])
            if dimension:
                out["breakdowns"][mid] = mx.breakdown(
                    js, dimension=dimension, metric=mid, now=now, k=k, tz=tz
                )
        elif mid == "M11":
            rows = mx.provider_quality(
                await facts_of(conn, "verification.idv.provider_check_finished", lo, hi), k=k
            )
            num = sum(r["numerator"] or 0 for r in rows if not r.get("suppressed"))
            den = sum(r["denominator"] or 0 for r in rows if not r.get("suppressed"))
            out["results"].append(mx.result("M11", num, den, k=k, quality=q, extra={"providers": rows, "comparable": False,
                                                                                      "note": "Providers serve different methods and markets; rows are not a league table."}))  # fmt: skip
        elif mid == "M12":
            out["results"].append(
                mx.webhook_delivery(
                    await facts_of(conn, "webhook.delivery.", lo, hi), k=k, quality=q
                )
            )
        elif mid == "M16":
            out["results"].append({"metric_id": "M16", "name": CATALOGUE["M16"].name, "definition_version": "1.0", "unit": "seconds", "quality": q, "sources": fresh,
                                   "value": max((r["lag_seconds"] for r in fresh), default=None), "suppressed": False})  # fmt: skip
        elif mid == "M15":
            out["results"].append(mx.result("M15", None, None, k=k, quality="SEE_OPERATIONS"))
    out["funnel"] = mx.funnel(js, now=now, k=k, cohort=cohort)
    out["first_attempt_causes"] = mx.first_attempt_causes(js, k=k)
    scope = _scope(environment, market, lo, hi, tz, mark, k)
    request = {
        "metric_ids": sorted(metric_ids),
        "dimension": dimension,
        "cohort": cohort,
        **scope,
        "versions": {m: CATALOGUE[m].version for m in metric_ids},
    }
    return {
        "scope": scope,
        "quality": q,
        **out,
        "query_fingerprint": fingerprint(request),
        "request": request,
    }


async def economics(conn: AsyncConnection, tenant: str, *, finance: bool, lo: datetime, hi: datetime, environment: str, market: str | None, now: datetime, k: int,
                    vendor_cost: Any, ledger: Any) -> dict[str, Any]:  # fmt: skip
    """M13 and M14 (07.07). Quantities for everyone with analytics access; cost and price only with billing access (Q29). Costs stay per
    currency: no FX normalization until the owners name the rate source (D-03), and incomplete costs are PROVISIONAL, never complete."""
    js = await journeys(conn, lo, hi, environment=environment, market=market)
    approved = sum(1 for j in js if (j.final or {}).get("state") == "FINAL_APPROVED")
    month = lo.date().replace(day=1)
    meters = {m["meter"]: m for m in (await ledger(conn, month))["meters"]}
    usage = meters.get("IDV_SUBMISSION", {})
    out: dict[str, Any] = {
        "approved_subjects": None if mx.suppressed_count(approved, k) else approved,
        "billable_units": {
            "meter": "IDV_SUBMISSION",
            "month": month.isoformat(),
            "net": usage.get("net"),
            "frozen": usage.get("frozen"),
        },
        "finance_visible": finance,
    }
    if not finance:
        return out
    costs: dict[str, Decimal] = await vendor_cost(conn, tenant, lo)
    out["M13"] = [
        {**mx.result("M13", float(total / approved) if approved else None, approved, k=k, quality="PROVISIONAL"), "currency": cur, "cost_total": str(total),
         "note": "Supplier costs recorded for this organization only; self-run checks carry no supplier cost; allocation of internal cost awaits D-03."}
        for cur, total in sorted(costs.items())
    ] or [{**mx.result("M13", None, None, k=k, quality="NO_DATA"), "note": "No attributable supplier cost recorded in this window."}]  # fmt: skip
    out["M14"] = {**mx.result("M14", None, None, k=k, quality="NO_PRICE"),
                  "note": "IDV_SUBMISSION is a usage meter with no approved price: there is no yield to show, and it is never shown as zero."}  # fmt: skip
    return out


async def drill(conn: AsyncConnection, tenant: str, actor: str, session_ref: str) -> dict[str, Any]:
    """One session's event timeline (07.02 item 04): event types, times and codes. No evidence, media or decision reasons; the case itself
    stays in the case workspace under its own access. Every drill is on record (Q47)."""
    rows = (
        await conn.execute(
            text(
                f"SELECT event_type, occurred_at, attrs FROM metering.va_fact WHERE session_ref = :r AND {_EXCLUDED} ORDER BY occurred_at"
            ),
            {"r": session_ref},
        )
    ).all()
    if not rows:
        raise AnalyticsError("NOT_FOUND", "No such session.", 404)
    await audit(
        conn,
        tenant,
        actor,
        action="drill",
        outcome="SUCCESS",
        target=session_ref,
        details={"session_ref": session_ref},
    )
    keep = ("attempt_no", "step", "result", "method", "state", "decided_by", "decision_version")
    return {
        "session_ref": session_ref,
        "timeline": [
            {
                "event": r.event_type,
                "at": r.occurred_at.isoformat(),
                **{k: v for k, v in (r.attrs or {}).items() if k in keep},
            }
            for r in rows
        ],
    }


# ------------------------------------------------------------------------------------------------------------------------- corrections


async def request_correction(
    conn: AsyncConnection, tenant: str, actor: str, *, event_id: str, reason: str
) -> str:
    """Ask for one fact to be left out of the metrics (07.11 item 04). Nothing changes until a different person approves it."""
    if not (
        await conn.execute(
            text("SELECT 1 FROM metering.va_fact WHERE event_id = :e"), {"e": uuid.UUID(event_id)}
        )
    ).first():
        raise AnalyticsError("NOT_FOUND", "No such fact.", 404)
    cid = uuid.uuid4()
    try:
        await conn.execute(
            text(
                "INSERT INTO metering.va_correction (correction_id, tenant_id, event_id, reason, requested_by, requested_at) VALUES (:c, :t, :e, :r, :by, now())"
            ),
            {
                "c": cid,
                "t": uuid.UUID(tenant),
                "e": uuid.UUID(event_id),
                "r": reason,
                "by": uuid.UUID(actor),
            },
        )
    except Exception as e:  # the unique index: one correction per fact
        raise AnalyticsError("CORRECTION_EXISTS", "That fact already has a correction.", 409) from e
    await audit(
        conn,
        tenant,
        actor,
        action="correction.requested",
        outcome="SUCCESS",
        target=str(cid),
        details={"event_id": event_id},
    )
    return str(cid)


async def approve_correction(
    conn: AsyncConnection, tenant: str, actor: str, correction_id: str
) -> None:
    row = (
        await conn.execute(
            text(
                "SELECT requested_by, approved_by FROM metering.va_correction WHERE correction_id = :c FOR UPDATE"
            ),
            {"c": uuid.UUID(correction_id)},
        )
    ).first()
    if row is None:
        raise AnalyticsError("NOT_FOUND", "No such correction.", 404)
    if row.approved_by is not None:
        raise AnalyticsError("CORRECTION_APPROVED", "Already approved.", 409)
    if str(row.requested_by) == actor:
        raise AnalyticsError(
            "CORRECTION_SAME_PERSON",
            "A correction is approved by someone other than the person who asked for it.",
            403,
        )
    await conn.execute(
        text(
            "UPDATE metering.va_correction SET approved_by = :by, approved_at = now() WHERE correction_id = :c"
        ),
        {"c": uuid.UUID(correction_id), "by": uuid.UUID(actor)},
    )
    await audit(
        conn,
        tenant,
        actor,
        action="correction.approved",
        outcome="SUCCESS",
        target=correction_id,
        details={},
    )


async def corrections(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT * FROM metering.va_correction ORDER BY requested_at DESC LIMIT 200")
        )
    ).all()
    return [{"correction_id": str(r.correction_id), "event_id": str(r.event_id), "reason": r.reason, "requested_by": str(r.requested_by),
             "requested_at": r.requested_at.isoformat(), "approved_by": str(r.approved_by) if r.approved_by else None,
             "approved_at": r.approved_at.isoformat() if r.approved_at else None} for r in rows]  # fmt: skip


# ----------------------------------------------------------------------------------------------------------------------- snapshots, exports


async def snapshot(conn: AsyncConnection, tenant: str, actor: str, result: dict[str, Any]) -> str:
    """Freeze a result with its request, definition versions and watermark (07.10 item 04): a rerun or an export of this id gives these totals."""
    sid = uuid.uuid4()
    await conn.execute(
        text("INSERT INTO metering.va_snapshot (snapshot_id, tenant_id, fingerprint, request, result, created_by, created_at) "
             "VALUES (:s, :t, :f, CAST(:q AS jsonb), CAST(:r AS jsonb), :by, now())"),
        {"s": sid, "t": uuid.UUID(tenant), "f": result["query_fingerprint"], "q": json.dumps(result.get("request") or result["scope"], default=str),
         "r": json.dumps(result, default=str), "by": uuid.UUID(actor)},
    )  # fmt: skip
    return str(sid)


async def load_snapshot(conn: AsyncConnection, snapshot_id: str) -> dict[str, Any]:
    row = (
        await conn.execute(
            text("SELECT result FROM metering.va_snapshot WHERE snapshot_id = :s"),
            {"s": uuid.UUID(snapshot_id)},
        )
    ).first()
    if row is None:
        raise AnalyticsError("NOT_FOUND", "No such snapshot.", 404)
    return dict(row.result)


def cell(value: Any) -> str:
    """A CSV cell that a spreadsheet will never run as a formula (07.10 item 02)."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def render(result: dict[str, Any], fmt: str) -> bytes:
    """Aggregates only, already protected: the export carries exactly what the screen showed, plus its definition versions and watermark."""
    if fmt == "json":
        return json.dumps(result, default=str, indent=2).encode()
    buf = io.StringIO()
    w = csv.writer(buf)
    scope = result.get("scope", {})
    w.writerow(
        [
            cell(x)
            for x in (
                "# fingerprint",
                result.get("query_fingerprint"),
                "watermark_utc",
                scope.get("watermark_utc"),
                "quality",
                result.get("quality"),
            )
        ]
    )
    w.writerow(
        [
            "metric_id",
            "name",
            "definition_version",
            "numerator",
            "denominator",
            "value",
            "quality",
            "suppressed",
        ]
    )
    for r in result.get("results", []) + result.get("tiles", []):
        w.writerow(
            [
                cell(r.get(k))
                for k in (
                    "metric_id",
                    "name",
                    "definition_version",
                    "numerator",
                    "denominator",
                    "value",
                    "quality",
                    "suppressed",
                )
            ]
        )
    return buf.getvalue().encode()


async def create_export(
    conn: AsyncConnection, tenant: str, actor: str, *, snapshot_id: str, fmt: str, purpose: str
) -> dict[str, Any]:
    """A job over a frozen snapshot, stored in this cell's database (the tenant's region), valid 24 hours, downloaded once with a token shown only now."""
    if fmt not in ("csv", "json"):
        raise AnalyticsError("FORMAT_UNKNOWN", "Format is csv or json.")
    content = render(await load_snapshot(conn, snapshot_id), fmt)
    token = secrets.token_urlsafe(32)
    eid, now = uuid.uuid4(), datetime.now(UTC)
    await conn.execute(
        text("INSERT INTO metering.va_export (export_id, tenant_id, snapshot_id, format, purpose, requested_by, created_at, expires_at, token_hash, content) "
             "VALUES (:e, :t, :s, :f, :p, :by, :now, :exp, :h, :c)"),
        {"e": eid, "t": uuid.UUID(tenant), "s": uuid.UUID(snapshot_id), "f": fmt, "p": purpose, "by": uuid.UUID(actor), "now": now,
         "exp": now + EXPORT_TTL, "h": hashlib.sha256(token.encode()).hexdigest(), "c": content},
    )  # fmt: skip
    await audit(
        conn,
        tenant,
        actor,
        action="export.created",
        outcome="SUCCESS",
        target=str(eid),
        details={"snapshot_id": snapshot_id, "format": fmt, "purpose": purpose[:100]},
    )
    return {
        "export_id": str(eid),
        "status": "READY",
        "expires_at": (now + EXPORT_TTL).isoformat(),
        "download_token": token,
        "format": fmt,
    }


def _export_view(r: Any, now: datetime) -> dict[str, Any]:
    state = (
        "REVOKED"
        if r.revoked_at
        else "DOWNLOADED"
        if r.downloaded_at
        else "EXPIRED"
        if r.expires_at <= now
        else "READY"
    )
    return {"export_id": str(r.export_id), "snapshot_id": str(r.snapshot_id), "format": r.format, "purpose": r.purpose, "status": state,
            "requested_by": str(r.requested_by), "created_at": r.created_at.isoformat(), "expires_at": r.expires_at.isoformat()}  # fmt: skip


async def exports(conn: AsyncConnection, actor: str) -> list[dict[str, Any]]:
    """The person's own export jobs (an export is owner-scoped, 08)."""
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM metering.va_export WHERE requested_by = :a ORDER BY created_at DESC LIMIT 100"
            ),
            {"a": uuid.UUID(actor)},
        )
    ).all()
    now = datetime.now(UTC)
    return [_export_view(r, now) for r in rows]


async def download(
    conn: AsyncConnection, tenant: str, actor: str, export_id: str, token: str
) -> tuple[bytes, str] | AnalyticsError:
    """Once, by the person who asked, with the token, before it expires or is revoked (Q39). Every try is on record; a refusal is returned, not
    raised, so its record commits."""
    r = (
        await conn.execute(
            text("SELECT * FROM metering.va_export WHERE export_id = :e FOR UPDATE"),
            {"e": uuid.UUID(export_id)},
        )
    ).first()
    now = datetime.now(UTC)
    why = None
    if r is None or str(r.requested_by) != actor:
        why = "NOT_FOUND"
    elif r.revoked_at:
        why = "EXPORT_REVOKED"
    elif r.downloaded_at:
        why = "EXPORT_ALREADY_DOWNLOADED"
    elif r.expires_at <= now:
        why = "EXPORT_EXPIRED"
    elif not secrets.compare_digest(r.token_hash, hashlib.sha256(token.encode()).hexdigest()):
        why = "EXPORT_TOKEN_INVALID"
    if why:
        await audit(
            conn,
            tenant,
            actor,
            action="export.downloaded",
            outcome="DENIED",
            target=export_id,
            details={},
            reason=why,
        )
        return AnalyticsError(
            why, "This download link cannot be used.", 404 if why == "NOT_FOUND" else 410
        )
    assert r is not None  # no refusal means the row was found
    await conn.execute(
        text(
            "UPDATE metering.va_export SET downloaded_at = :now, content = ''::bytea WHERE export_id = :e"
        ),
        {"e": uuid.UUID(export_id), "now": now},
    )
    await audit(
        conn,
        tenant,
        actor,
        action="export.downloaded",
        outcome="SUCCESS",
        target=export_id,
        details={},
    )
    return bytes(r.content), "text/csv" if r.format == "csv" else "application/json"


async def revoke_export(conn: AsyncConnection, tenant: str, actor: str, export_id: str) -> None:
    gone = await conn.execute(
        text(
            "UPDATE metering.va_export SET revoked_at = now(), revoked_by = :a, content = ''::bytea WHERE export_id = :e AND requested_by = :a AND revoked_at IS NULL"
        ),
        {"e": uuid.UUID(export_id), "a": uuid.UUID(actor)},
    )
    if not gone.rowcount:
        raise AnalyticsError("NOT_FOUND", "No such export.", 404)
    await audit(
        conn,
        tenant,
        actor,
        action="export.revoked",
        outcome="SUCCESS",
        target=export_id,
        details={},
    )

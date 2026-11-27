"""The Command Center API (ZID-CPS-WF-02 section 08): query-only aggregates, no raw applicant data, no arbitrary SQL.

    GET  /v1/verify-analytics/access/me              the caller's analytics keys and whether they see cost and price
    GET  /v1/verify-analytics/overview               WF-02.01 tiles, decision split, review SLA, freshness
    POST /v1/verify-analytics/query                  allow-listed metrics, one allow-listed dimension, funnel and causes; optional snapshot
    GET  /v1/verify-analytics/snapshots/{id}         a frozen result (reruns reproduce it)
    GET  /v1/verify-analytics/definitions            the versioned metric catalogue
    GET  /v1/verify-analytics/lineage/{metric_id}    sources, watermark and correction count behind one metric
    GET  /v1/verify-analytics/quality                per-source freshness, late and rejected counts
    GET  /v1/verify-analytics/operations             API availability, SLO catalogue, published incidents
    GET  /v1/verify-analytics/economics              quantities; cost per approved subject and yield only with billing access
    GET  /v1/verify-analytics/coverage               markets, their routes and whether a face comparison is authorized
    GET  /v1/verify-analytics/sessions/{ref}/timeline one session's event timeline (drill)
    GET|POST /v1/verify-analytics/corrections, POST .../corrections/{id}:approve
    GET|POST /v1/verify-analytics/exports, GET .../exports/{id}/download, POST .../exports/{id}:revoke
    GET|POST /v1/verify-analytics/grants, DELETE .../grants/{id}

The tenant comes from the signed-in caller and the environment from this deployment, never from the request (Q01, Q02)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import ledger_summary, tenant_vendor_cost
from zid_platform.modules.metering.ports import usage_summary
from zid_platform.modules.operations.ports import public_incidents, service_catalogue
from zid_platform.modules.tenant.ports import billing_permission_checker
from zid_platform.modules.verification.ports import idv_coverage
from zid_platform.modules.verify_analytics.application import service as svc
from zid_platform.modules.verify_analytics.domain import access as ac
from zid_platform.modules.verify_analytics.domain import metrics as mx
from zid_platform.modules.verify_analytics.domain.definitions import CATALOGUE

router = APIRouter(tags=["verify-analytics"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
P = "/v1/verify-analytics"


def _me(caller: Caller) -> tuple[str, str]:
    if not caller.ctx.principal_id or not caller.ctx.tenant_id:
        raise ZidError("UNAUTHENTICATED", "A signed-in member is required.", http_status=401)
    return str(caller.ctx.tenant_id), str(caller.ctx.principal_id)


async def _run[T](
    request: Request,
    caller: Caller,
    permission: str | None,
    fn: Callable[[AsyncConnection, str, str], Awaitable[T]],
) -> T:
    """Decide access in its own transaction (so a refusal's record commits), then do the work in another. A value the work returns as an
    AnalyticsError is a refusal it recorded: raised only after its transaction committed."""
    tenant, me = _me(caller)
    engine = request_engine(request)
    if permission:
        async with tenant_transaction(engine, caller.ctx) as conn:
            allowed = await svc.check(conn, tenant, me, permission)
        if not allowed:
            raise ZidError(
                "ANALYTICS_PERMISSION_REQUIRED",
                "Your account does not have analytics access for this.",
                http_status=403,
            )
    try:
        async with tenant_transaction(engine, caller.ctx) as conn:
            out = await fn(conn, tenant, me)
    except svc.AnalyticsError as e:
        raise ZidError(e.code, e.detail, http_status=e.status) from e
    if isinstance(out, svc.AnalyticsError):
        raise ZidError(out.code, out.detail, http_status=out.status)
    return out


def _policy(request: Request) -> tuple[str, int, timedelta]:
    s = request.app.state
    return (str(getattr(s, "environment", "production")), int(getattr(s, "verify_analytics_min_group", 20)),
            timedelta(minutes=int(getattr(s, "verify_analytics_stale_minutes", 15))))  # fmt: skip


MARKET = r"^[A-Z]{2}$"


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date | None = Field(default=None, alias="from")
    end: date | None = Field(default=None, alias="to")
    tz: str = Field(default="UTC", max_length=64)
    market: str | None = Field(default=None, pattern=MARKET)


def _window(w: Window) -> tuple[datetime, datetime]:
    try:
        return svc.window(w.start, w.end, w.tz, datetime.now(UTC))
    except svc.AnalyticsError as e:
        raise ZidError(e.code, e.detail, http_status=e.status) from e


def _qwindow(start: Annotated[date | None, Query(alias="from")] = None, end: Annotated[date | None, Query(alias="to")] = None,
             tz: Annotated[str, Query(max_length=64)] = "UTC", market: Annotated[str | None, Query(pattern=MARKET)] = None) -> Window:  # fmt: skip
    return Window.model_validate({"from": start, "to": end, "tz": tz, "market": market})


WindowDep = Annotated[Window, Depends(_qwindow)]


async def _finance(request: Request, caller: Caller) -> bool:
    """Cost and price follow the billing permission layer, so Billing Admin stays its own authority (Q29)."""
    try:
        await billing_permission_checker()(request, caller, "billing.overview.read")
        return True
    except ZidError:
        return False


@router.get(f"{P}/access/me")
async def access_me(request: Request, caller: CallerDep) -> dict[str, Any]:
    keys = await _run(request, caller, None, lambda conn, t, me: svc.permissions(conn, me))
    return {"permissions": keys, "finance": await _finance(request, caller),
            "bundles": {b: sorted(k) for b, k in ac.BUNDLES.items()}, "catalogue": ac.PERMISSIONS}  # fmt: skip


@router.get(f"{P}/overview")
async def overview(request: Request, caller: CallerDep, w: WindowDep) -> dict[str, Any]:
    env, k, stale = _policy(request)
    lo, hi = _window(w)
    return await _run(request, caller, "analytics.read", lambda conn, t, me: svc.overview(
        conn, lo=lo, hi=hi, tz=w.tz, environment=env, market=w.market, now=datetime.now(UTC), k=k, stale_after=stale))  # fmt: skip


class QueryIn(Window):
    metric_ids: list[str] = Field(min_length=1, max_length=16)
    dimension: str | None = Field(default=None, max_length=20)
    cohort: str = "created"
    snapshot: bool = False


@router.post(f"{P}/query")
async def query(body: QueryIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    env, k, stale = _policy(request)
    lo, hi = _window(body)

    async def go(conn: AsyncConnection, tenant: str, me: str) -> dict[str, Any]:
        out = await svc.query(conn, metric_ids=body.metric_ids, dimension=body.dimension, cohort=body.cohort, lo=lo, hi=hi, tz=body.tz,
                              environment=env, market=body.market, now=datetime.now(UTC), k=k, stale_after=stale)  # fmt: skip
        if body.snapshot:
            out["snapshot_id"] = await svc.snapshot(conn, tenant, me, out)
        return out

    return await _run(request, caller, "analytics.read", go)


@router.get(f"{P}/snapshots/{{snapshot_id}}")
async def snapshot(snapshot_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(
        request, caller, "analytics.read", lambda conn, t, me: svc.load_snapshot(conn, snapshot_id)
    )


@router.get(f"{P}/definitions")
async def definitions(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection, t: str, me: str) -> dict[str, Any]:
        return {"metrics": [m.view() for m in CATALOGUE.values()]}

    return await _run(request, caller, "analytics.read", go)


@router.get(f"{P}/lineage/{{metric_id}}")
async def lineage(metric_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    if metric_id not in CATALOGUE:
        raise ZidError("METRIC_UNKNOWN", "Unknown metric.", http_status=404)
    _, _, stale = _policy(request)

    async def go(conn: AsyncConnection, t: str, me: str) -> dict[str, Any]:
        q, fresh, mark = await svc.quality(conn, now=datetime.now(UTC), stale_after=stale)
        m = CATALOGUE[metric_id]
        return {"metric": m.view(), "path": ["source service outbox", "ingest consumer (inbox-deduplicated)", "metering.va_fact (append-only)",
                                             "correction journal (approved exclusions)", "query API", "chart"],
                "quality": q, "watermark_utc": mark, "sources": fresh, "approved_corrections": sum(1 for c in await svc.corrections(conn) if c["approved_by"])}  # fmt: skip

    return await _run(request, caller, "analytics.read", go)


@router.get(f"{P}/quality")
async def quality(request: Request, caller: CallerDep) -> dict[str, Any]:
    _, _, stale = _policy(request)

    async def go(conn: AsyncConnection, t: str, me: str) -> dict[str, Any]:
        q, fresh, mark = await svc.quality(conn, now=datetime.now(UTC), stale_after=stale)
        return {
            "quality": q,
            "watermark_utc": mark,
            "sources": fresh,
            "stale_after_minutes": int(stale.total_seconds() // 60),
        }

    return await _run(request, caller, "analytics.read", go)


@router.get(f"{P}/operations")
async def operations(
    request: Request, caller: CallerDep, days: Annotated[int, Query(ge=1, le=92)] = 7
) -> dict[str, Any]:
    """07.08 and 07.09. Availability counts server errors only against valid requests (4xx excluded); 100% is never inferred from no data (M15)."""
    _, _, stale = _policy(request)

    async def go(conn: AsyncConnection, t: str, me: str) -> dict[str, Any]:
        usage = await usage_summary(conn, days=days)
        ok = sum(u["calls"] for u in usage if u["status_class"] == "2xx")
        bad = sum(u["calls"] for u in usage if u["status_class"] == "5xx")
        q, fresh, _ = await svc.quality(conn, now=datetime.now(UTC), stale_after=stale)
        cat = service_catalogue()
        return {
            "availability": {**mx.result("M15", ok, ok + bad, k=1, quality="OBSERVED"), "window_days": days, "client_errors_excluded": True},
            "slos": [{"service_id": s.service_id, "name": s.name, "owner": s.owner, "slos": [{"slo_id": o.slo_id, "sli": o.sli, "target": o.target} for o in s.slos]}
                     for s in cat.services.values()],
            "incidents": await public_incidents(conn, cell_id=getattr(request.app.state, "cell_id", None)),
            "freshness": {"quality": q, "sources": fresh},
        }  # fmt: skip

    return await _run(request, caller, "analytics.operations.read", go)


@router.get(f"{P}/economics")
async def economics(request: Request, caller: CallerDep, w: WindowDep) -> dict[str, Any]:
    env, k, _ = _policy(request)
    lo, hi = _window(w)
    finance = await _finance(request, caller)
    return await _run(request, caller, "analytics.read", lambda conn, tenant, me: svc.economics(
        conn, tenant, finance=finance, lo=lo, hi=hi, environment=env, market=w.market, now=datetime.now(UTC), k=k,
        vendor_cost=tenant_vendor_cost, ledger=ledger_summary))  # fmt: skip


@router.get(f"{P}/coverage")
async def coverage(request: Request, caller: CallerDep) -> dict[str, Any]:
    """07.06 item 01: what is configured to run, per market. A market with no published policy is simply absent, never shown as available."""

    async def go(conn: AsyncConnection, t: str, me: str) -> dict[str, Any]:
        return {"markets": idv_coverage(getattr(request.app.state, "idv_policies", None))}

    return await _run(request, caller, "analytics.read", go)


@router.get(f"{P}/sessions/{{session_ref}}/timeline")
async def timeline(session_ref: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        "analytics.drill",
        lambda conn, tenant, me: svc.drill(conn, tenant, me, session_ref),
    )


class CorrectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    reason: str = Field(min_length=10, max_length=500)


@router.get(f"{P}/corrections")
async def corrections(request: Request, caller: CallerDep) -> dict[str, Any]:
    return {
        "items": await _run(
            request, caller, "analytics.read", lambda conn, t, me: svc.corrections(conn)
        )
    }


@router.post(f"{P}/corrections", status_code=201)
async def request_correction(
    body: CorrectionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    cid = await _run(
        request,
        caller,
        "analytics.corrections.request",
        lambda conn, tenant, me: svc.request_correction(
            conn, tenant, me, event_id=body.event_id, reason=body.reason
        ),
    )
    return {"correction_id": cid, "approved": False}


@router.post(f"{P}/corrections/{{correction_id}}:approve")
async def approve_correction(
    correction_id: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    await _run(
        request,
        caller,
        "analytics.corrections.approve",
        lambda conn, tenant, me: svc.approve_correction(conn, tenant, me, correction_id),
    )
    return {"correction_id": correction_id, "approved": True}


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    format: str = Field(default="csv", pattern=r"^(csv|json)$")
    purpose: str = Field(min_length=3, max_length=200)


@router.get(f"{P}/exports")
async def exports(request: Request, caller: CallerDep) -> dict[str, Any]:
    return {
        "items": await _run(
            request, caller, "analytics.export", lambda conn, t, me: svc.exports(conn, me)
        )
    }


@router.post(f"{P}/exports", status_code=202)
async def create_export(body: ExportIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        "analytics.export",
        lambda conn, tenant, me: svc.create_export(
            conn, tenant, me, snapshot_id=body.snapshot_id, fmt=body.format, purpose=body.purpose
        ),
    )


@router.get(f"{P}/exports/{{export_id}}/download")
async def download(
    export_id: str,
    request: Request,
    caller: CallerDep,
    token: Annotated[str, Header(alias="X-Download-Token", max_length=100)],
) -> Response:
    got = await _run(
        request,
        caller,
        "analytics.export",
        lambda conn, tenant, me: svc.download(conn, tenant, me, export_id, token),
    )
    assert not isinstance(got, svc.AnalyticsError)  # _run raised it
    body, ctype = got
    return Response(
        body,
        media_type=ctype,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="analytics-{export_id}"',
        },
    )


@router.post(f"{P}/exports/{{export_id}}:revoke")
async def revoke_export(export_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    await _run(
        request,
        caller,
        "analytics.export",
        lambda conn, tenant, me: svc.revoke_export(conn, tenant, me, export_id),
    )
    return {"export_id": export_id, "status": "REVOKED"}


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grantee_principal_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    bundle: str = Field(max_length=40)
    expires_at: datetime | None = None


@router.get(f"{P}/grants")
async def grants(request: Request, caller: CallerDep) -> dict[str, Any]:
    return {
        "items": await _run(
            request, caller, "analytics.access.delegate", lambda conn, t, me: svc.grants(conn)
        )
    }


@router.post(f"{P}/grants", status_code=201)
async def give(body: GrantIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    gid = await _run(request, caller, None, lambda conn, tenant, me: svc.give(conn, tenant, me, grantee=body.grantee_principal_id, bundle=body.bundle,
                                                                             expires_at=body.expires_at))  # fmt: skip
    return {"grant_id": gid}


@router.delete(f"{P}/grants/{{grant_id}}", status_code=204)
async def take_away(grant_id: str, request: Request, caller: CallerDep) -> Response:
    await _run(
        request, caller, None, lambda conn, tenant, me: svc.take_away(conn, tenant, me, grant_id)
    )
    return Response(status_code=204)

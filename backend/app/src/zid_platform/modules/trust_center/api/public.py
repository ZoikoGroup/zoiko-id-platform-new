"""The public Trust Center, read-only (ZID-CP-WF12 v2.0, WF-12.01 to 12.08; CR-174). No sign-in. Nothing here can write certification state, claim approval, availability or advisories: every read is
a projection of what the staff register approved. The one write is a vulnerability report, which goes only to staff.

    GET  /public/trust/v1/claims                 approved, current statements with their evidence summary and when they took effect
    GET  /public/trust/v1/certifications         what was assessed, by whom, for which entity, product and period; history kept separate
    GET  /public/trust/v1/advisories             incident advisories, every version, corrections marked, none erased
    GET  /public/status/v1/availability          measured availability, validated by a second person; "not yet reported" when there is none, never 100%
    GET  /.well-known/security.txt               RFC 9116, served only when a contact is configured and the file has not lapsed
    POST /public/trust/v1/vulnerability-reports  a report to the security team: a reference and a response target come back, never the text
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_outbox import emit

from zid_platform.api_auth import request_engine
from zid_platform.modules.operations.ports import service_catalogue
from zid_platform.modules.trust_center.application import service as svc
from zid_platform.modules.trust_center.domain import security_txt as stx

router = APIRouter(tags=["trust-center-public"])
CACHE = {"Cache-Control": "public, max-age=60"}


def _json(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(body, headers=CACHE)


@router.get("/public/trust/v1/claims")
async def claims(request: Request) -> JSONResponse:
    async with request_engine(request).connect() as conn:
        return _json(await svc.public_claims(conn, now=datetime.now(UTC)))


@router.get("/public/trust/v1/certifications")
async def certifications(request: Request) -> JSONResponse:
    async with request_engine(request).connect() as conn:
        return _json(await svc.public_certifications(conn, now=datetime.now(UTC)))


@router.get("/public/trust/v1/advisories")
async def advisories(request: Request) -> JSONResponse:
    async with request_engine(request).connect() as conn:
        return _json(await svc.public_advisories(conn))


@router.get("/public/status/v1/availability")
async def availability(request: Request) -> JSONResponse:
    names = sorted(service_catalogue().services)
    async with request_engine(request).connect() as conn:
        return _json(await svc.public_availability(conn, names, now=datetime.now(UTC)))


def _security_txt(request: Request) -> tuple[str, str]:
    s = request.app.state
    contact = getattr(s, "trust_security_contact", None)
    expires = getattr(s, "trust_security_expires", None)
    base = (getattr(s, "public_base_url", None) or str(request.base_url)).rstrip("/")
    if not contact or expires is None:
        raise ZidError("SECURITY_TXT_NOT_CONFIGURED", "No security contact is configured for this deployment.", http_status=404)
    t = stx.SecurityTxt(
        contacts=(contact,), expires=expires, canonical=(f"{base}/.well-known/security.txt",), policy=getattr(s, "trust_security_policy_url", None),
        acknowledgments=getattr(s, "trust_security_acknowledgments_url", None), encryption=getattr(s, "trust_security_encryption_url", None),
    )  # fmt: skip
    try:
        return stx.render(t, now=datetime.now(UTC)), base
    except ValueError as e:
        raise ZidError("SECURITY_TXT_INVALID", f"The security.txt configuration is not valid: {e}", http_status=503) from e


@router.get("/.well-known/security.txt")
async def security_txt(request: Request) -> PlainTextResponse:
    body, _ = _security_txt(request)
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "public, max-age=300"})


class TrustReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=20, max_length=8000)
    affected: str = Field(default="", max_length=2000)
    severity: Literal["critical", "high", "medium", "low", "info"] = "medium"
    contact: str | None = Field(default=None, max_length=200, description="Optional. Only the security team sees it.")


@router.post("/public/trust/v1/vulnerability-reports", status_code=201)
async def vulnerability_report(body: TrustReportIn, request: Request) -> JSONResponse:
    now = datetime.now(UTC)
    ip = request.client.host if request.client else "unknown"
    salt = str(getattr(request.app.state, "trust_report_salt", ""))
    source = hashlib.sha256(f"{salt}|{ip}|{now:%Y-%m-%d}".encode()).hexdigest()
    tenant = str(getattr(request.app.state, "trust_report_tenant", "") or "")
    base_ctx = current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")
    try:
        # the event goes out under the operators' own tenant, so staff are told; the report tables themselves hold no tenant data
        async with tenant_transaction(request_engine(request), base_ctx.with_identity(tenant, None) if tenant else base_ctx) as conn:
            out = await svc.receive_report(conn, summary=body.summary, affected=body.affected, severity=body.severity, contact=body.contact, source_hash=source, now=now)
            if tenant:
                await emit(
                    conn, name="trust.vulnerability.reported", tenant_id=tenant, aggregate_type="trust_report", aggregate_id=out["reference"], aggregate_version=1,
                    data={"reference": out["reference"], "severity": body.severity, "first_response_target": out["first_response_target"]},
                )  # fmt: skip
    except svc.TrustError as e:
        raise ZidError(e.code, e.detail or e.code, http_status=429 if e.code == "RATE_LIMITED" else 422, retryable=e.code == "RATE_LIMITED") from e
    return JSONResponse(out, status_code=201, headers={"Cache-Control": "no-store"})


@router.get("/public/trust/v1/privacy")
async def privacy(request: Request, market: str | None = Query(default=None, pattern=r"^[A-Z]{2}$")) -> JSONResponse:
    """Subprocessors, retention, biometric statements and transfer conditions for one market, from approved records only. Nothing is filled in: a kind with no record says so."""
    async with request_engine(request).connect() as conn:
        return _json(await svc.public_privacy(conn, market=market, now=datetime.now(UTC)))

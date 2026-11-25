"""Opening production for a partner: the company's side and the operator's side (CR-153; ZID-EXT-IDV-001 epic E10, gates G1 to G4).

The company asks once its sandbox integration is certified. An operator takes the request into review, confirms each item only a person can confirm, and a different operator approves. Approval writes the
company's production grant with a digest of exactly what was allowed; revoking it stops every production application and its tokens at once. Production applications can be created only while a grant is
active. Whether a production session can be served is a separate, later gate (the live engine): this module says only what the company is ALLOWED to do.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.verify_api.application import observe, service
from zid_platform.modules.verify_api.domain import go_live as g
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.infrastructure import go_live_store as db
from zid_platform.modules.webhook.ports import active_endpoint_count


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def request_view(r: dict[str, Any], *, operator: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "request_id": str(r["request_id"]),
        "status": r["status"],
        "markets": list(r["markets"]),
        "methods": list(r["methods"]),
        "purpose_code": r["purpose_code"],
        "volume_band": r["volume_band"],
        "use_case": r["use_case"],
        "terms_version": r["terms_version"],
        "dpa_version": r["dpa_version"],
        "certification": r["certification"],
        "note": r["note"],
        "created_at": _iso(r["created_at"]),
        "decided_at": _iso(r["decided_at"]),
    }
    if operator:
        out["tenant_id"] = str(r["tenant_id"])
        out["reviewer_id"] = str(r["reviewer_id"]) if r["reviewer_id"] else None
        out["accepted_by"] = str(r["accepted_by"])
        out["attestations"] = list(g.ATTESTATIONS)
    return out


def grant_view(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "grant_id": str(r["grant_id"]),
        "status": r["status"],
        "markets": list(r["markets"]),
        "methods": list(r["methods"]),
        "manifest_digest": bytes(r["manifest_digest"]).hex(),
        "signed": r.get("signature") is not None,
        "signature": r.get("signature"),
        "approved_at": _iso(r["approved_at"]),
        "revoked_at": _iso(r["revoked_at"]),
        "revoke_reason": r["revoke_reason"],
    }


CompanySource = Callable[[AsyncConnection, str], Awaitable[tuple[str, datetime] | None]]
_company_source: CompanySource | None = None


def configure_company_source(source: CompanySource | None) -> None:
    """The composition root says how to read a company's own verification claim (this module imports no organization code). Unset, no company counts as verified: it fails closed."""
    global _company_source
    _company_source = source


async def company_check(conn: AsyncConnection, tenant_id: str) -> dict[str, Any]:
    """Run inside the company's own scope."""
    if _company_source is None:
        return g.company_fact(None)
    return g.company_fact(await _company_source(conn, tenant_id))


async def _checks(conn: AsyncConnection) -> list[dict[str, Any]]:
    approved, declined = await db.sandbox_facts(conn)
    return g.certification(g.SandboxFacts(approved, declined, await active_endpoint_count(conn)))


async def production_open(conn: AsyncConnection) -> bool:
    """May this company create production applications right now?"""
    return await db.active_grant(conn) is not None


async def status_view(conn: AsyncConnection, tenant_id: str) -> dict[str, Any]:
    """Everything the Go live screen needs in one answer: the automatic checks now, the company's own verification, the latest request, the grant, and the choices."""
    latest = await db.latest_request(conn)
    grant = await db.active_grant(conn)
    return {
        "certification": await _checks(conn),
        "company": await company_check(conn, tenant_id),
        "request": request_view(latest) if latest else None,
        "grant": grant_view(grant) if grant else None,
        "terms_version": g.TERMS_VERSION,
        "dpa_version": g.DPA_VERSION,
        "options": {
            "methods": list(g.METHODS),
            "purposes": list(g.PURPOSE_CHOICES),
            "volume_bands": [{"id": k, "label": v} for k, v in g.VOLUME_BANDS.items()],
        },
    }


# ------------------------------------------------------------------------------- the company


async def submit(
    conn: AsyncConnection,
    tenant_id: str,
    principal: str,
    body: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    clean = g.check_request(body)
    if await db.active_grant(conn) is not None:
        raise m.PartnerError("GO_LIVE_ALREADY_APPROVED", "Production is already open for you.")
    checks = await _checks(conn)
    if not g.certified(checks):
        unmet = "; ".join(c["label"].lower() for c in checks if not c["met"])
        raise m.PartnerError(
            "GO_LIVE_NOT_READY", f"Finish your sandbox testing first. Still needed: {unmet}."
        )
    request_id = str(uuid.uuid4())
    try:
        await db.insert_request(
            conn,
            request_id=request_id,
            tenant_id=tenant_id,
            clean=clean,
            accepted_by=principal,
            certification=checks,
        )
    except Exception as e:  # the unique index: one open request per company
        if "api_go_live_one_open" in str(e):
            raise m.PartnerError(
                "GO_LIVE_REQUEST_OPEN",
                "You already have a request waiting. Withdraw it first to send a new one.",
            ) from e
        raise
    made = await db.get_request(conn, request_id)
    assert made is not None
    return request_view(made)


async def withdraw(
    conn: AsyncConnection, request_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    r = await db.get_request(conn, request_id, lock=True)
    if r is None:
        raise m.PartnerError("NOT_FOUND", "No such request.")
    g.check_transition(g.GoLiveStatus(r["status"]), g.GoLiveStatus.WITHDRAWN)
    await db.move_request(conn, request_id, g.GoLiveStatus.WITHDRAWN.value, now)
    fresh = await db.get_request(conn, request_id)
    assert fresh is not None
    return request_view(fresh)


# ------------------------------------------------------------------------------- the operator (inside `db.operator_scope`)


async def operator_open(conn: AsyncConnection) -> list[dict[str, Any]]:
    return [request_view(r, operator=True) for r in await db.open_requests(conn)]


async def _operator_load(conn: AsyncConnection, request_id: str) -> dict[str, Any]:
    r = await db.get_request(conn, request_id, lock=True)
    if r is None:
        raise m.PartnerError("NOT_FOUND", "No such request.")
    return r


async def start_review(
    conn: AsyncConnection, request_id: str, operator: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    r = await _operator_load(conn, request_id)
    g.check_transition(g.GoLiveStatus(r["status"]), g.GoLiveStatus.IN_REVIEW)
    await db.move_request(conn, request_id, g.GoLiveStatus.IN_REVIEW.value, now, reviewer=operator)
    return request_view((await db.get_request(conn, request_id)) or r, operator=True)


async def decline(
    conn: AsyncConnection,
    request_id: str,
    operator: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    r = await _operator_load(conn, request_id)
    g.check_transition(g.GoLiveStatus(r["status"]), g.GoLiveStatus.DECLINED)
    clean = g.check_note(note, required=True)
    await db.move_request(
        conn, request_id, g.GoLiveStatus.DECLINED.value, now, decider=operator, note=clean
    )
    observe.GO_LIVE.inc(event="declined")
    return request_view((await db.get_request(conn, request_id)) or r, operator=True)


async def approve(
    conn: AsyncConnection,
    request_id: str,
    operator: str,
    attestations: dict[str, Any],
    note: str | None,
    now: datetime | None = None,
    *,
    sign: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """Approve: a different operator from the reviewer, every confirmation given, the sandbox checks still met now. Leaves the company's production grant, the digest of what it allows and a signature over both.
    With no signing key there is no approval: a grant is never made unsigned."""
    now = now or datetime.now(UTC)
    if sign is None:
        raise m.PartnerError(
            "GO_LIVE_SIGNING_UNAVAILABLE",
            "Approval needs the go-live signing key and none is configured. Nothing was approved.",
        )
    r = await _operator_load(conn, request_id)
    g.check_transition(g.GoLiveStatus(r["status"]), g.GoLiveStatus.APPROVED)
    if r["reviewer_id"] is not None and str(r["reviewer_id"]) == operator:
        raise m.PartnerError(
            "GO_LIVE_SELF_APPROVAL",
            "The person who took the request into review cannot also approve it.",
        )
    confirmed = g.check_attestations(attestations)
    clean_note = g.check_note(note, required=False)
    tenant_id = str(r["tenant_id"])
    async with in_tenant(
        conn, tenant_id
    ):  # the company's own sandbox facts, read under its own scope
        checks = await _checks(conn)
        if not g.certified(checks):
            raise m.PartnerError(
                "GO_LIVE_NOT_READY", "The company's sandbox checks are no longer all met."
            )
        company = await company_check(conn, tenant_id)
        if not company["verified"]:
            raise m.PartnerError(
                "GO_LIVE_COMPANY_NOT_VERIFIED",
                "The company has no registry-verified company check on record. Documents alone are not enough; finish its company verification first.",
            )
        record = g.manifest(
            company_check={"claim": company["claim"], "assessed_at": company["assessed_at"]},
            tenant_id=tenant_id,
            request_id=request_id,
            markets=list(r["markets"]),
            methods=list(r["methods"]),
            purpose_code=r["purpose_code"],
            terms_version=r["terms_version"],
            dpa_version=r["dpa_version"],
            certification_checks=checks,
            attestations=confirmed,
            reviewer_id=str(r["reviewer_id"] or operator),
            approver_id=operator,
            approved_at=now,
        )
    grant_id = str(uuid.uuid4())
    digest = g.digest(record)
    signature = sign(
        {
            "kind": "verify_api.production_grant.signed",
            "grant_id": grant_id,
            "manifest": record,
            "manifest_digest": digest.hex(),
        }
    )
    await db.insert_grant(
        conn,
        grant_id=grant_id,
        tenant_id=tenant_id,
        request_id=request_id,
        markets=list(r["markets"]),
        methods=list(r["methods"]),
        manifest=record,
        digest=digest,
        signature=signature,
        approved_by=operator,
        approved_at=now,
    )
    await db.move_request(
        conn,
        request_id,
        g.GoLiveStatus.APPROVED.value,
        now,
        decider=operator,
        note=clean_note or None,
    )
    observe.GO_LIVE.inc(event="approved")
    return request_view((await db.get_request(conn, request_id)) or r, operator=True)


async def revoke(
    conn: AsyncConnection, tenant_id: str, operator: str, reason: str, now: datetime | None = None
) -> dict[str, Any]:
    """Emergency stop: the grant ends, and every production application of the company is suspended and its tokens stop at once."""
    now = now or datetime.now(UTC)
    why = g.check_note(reason, required=True)
    revoked = await db.revoke_grant(conn, tenant_id, operator, why, now)
    if revoked is None:
        raise m.PartnerError("NOT_FOUND", "That company has no active production grant.")
    observe.GO_LIVE.inc(event="revoked")
    stopped = 0
    async with in_tenant(conn, tenant_id):
        for application_id in await db.production_application_ids(conn):
            try:
                await service.set_status(conn, application_id, "SUSPENDED", now)
                stopped += 1
            except m.PartnerError:
                continue  # already suspended: its tokens were already ended
    return {**grant_view(revoked), "applications_stopped": stopped}

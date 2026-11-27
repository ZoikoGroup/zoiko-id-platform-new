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

from zid_platform.modules.verify_api.application import (
    approval_policy,
    certification_service,
    observe,
    service,
)
from zid_platform.modules.verify_api.domain import go_live as g
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.infrastructure import go_live_store as db
from zid_platform.modules.verify_api.infrastructure import single_approver
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


async def status_view(
    conn: AsyncConnection, tenant_id: str, principal: str | None = None
) -> dict[str, Any]:
    """Everything the Go live screen needs in one answer: the automatic checks now, the company's own verification, the latest request, the grant, and the choices."""
    latest = await db.latest_request(conn)
    grant = await db.active_grant(conn)
    return {
        "self_serve": await self_serve_status(
            conn, tenant_id, principal
        ),  # production opens by itself when all three are met (see open_production)
        "certification": await _checks(conn),
        "adversarial_suite": await certification_service.suite_for_go_live(conn, tenant_id),
        "suite_gate": suite_gate_mode(),  # off, shadow or enforce (CR-173)
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


def suite_gate_mode() -> str:
    """Must the sandbox persona suite pass before a go-live request? off, shadow (the default: it is recorded when it WOULD have refused) or enforce. Read on each call so a deployment can change it
    without a restart. The owners decide when to move it from shadow to enforce."""
    import os

    mode = os.environ.get("ZID_VERIFY_GO_LIVE_SUITE_GATE", "shadow").strip().lower()
    return mode if mode in ("off", "shadow", "enforce") else "shadow"


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
    mode = suite_gate_mode()
    if mode != "off":
        suite = await certification_service.suite_for_go_live(conn, tenant_id, now)
        if not suite["met"]:
            if mode == "enforce":
                raise m.PartnerError(
                    "GO_LIVE_SUITE_NOT_PASSED",
                    "Run the sandbox test scenarios first. Your server has not passed every one recently: "
                    + (
                        suite.get("reason")
                        or "; ".join(suite.get("reasons", [])).lower().replace("_", " ")
                    )
                    + ".",
                )
            observe.GO_LIVE.inc(
                event="suite_would_block"
            )  # shadow: the request goes ahead and the platform counts what an enforced gate would have refused
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


# ------------------------------------------------------------------------------- self-serve: production opens by itself
# The owner's decision (26 Nov 2026): once the person's own identity check and the company check are complete and the company holds a PAID plan, production opens with no operator and no sandbox test, like
# other verification products that unlock live access when the account is verified and paid. A deployment chooses it with ZID_VERIFY_SELF_SERVE_GO_LIVE; off (the default) keeps the request that an operator approves.
# The grant is signed exactly as before and records that the automatic rule opened it: its approver is the fixed identity below, never a person.

SELF_SERVE_ACTOR = "00000000-0000-4000-8000-0000000000a1"
PersonSource = Callable[[AsyncConnection, str], Awaitable[bool]]
PlanSource = Callable[[AsyncConnection], Awaitable[bool]]
_person_source: PersonSource | None = None
_plan_source: PlanSource | None = None
_NEEDED = {
    "identity": "your own identity check",
    "company": "your company check",
    "paid_plan": "a paid plan, with its payment completed",
}


def configure_self_serve_sources(person: PersonSource | None, plan: PlanSource | None) -> None:
    """The composition root says how to read a person's identity check and a company's plan (this module imports neither the verification nor the commercial code). Unset, they read as not met."""
    global _person_source, _plan_source
    _person_source, _plan_source = person, plan


def self_serve_enabled() -> bool:
    import os

    return os.environ.get("ZID_VERIFY_SELF_SERVE_GO_LIVE", "").strip().lower() in {
        "on",
        "true",
        "1",
        "yes",
    }


async def self_serve_status(
    conn: AsyncConnection, tenant_id: str, principal: str | None
) -> dict[str, Any]:
    """What is met now, for the Go live screen: the person's identity check, the company check and the paid plan. Read only."""
    identity = bool(_person_source and principal and await _person_source(conn, principal))
    company = bool((await company_check(conn, tenant_id))["verified"])
    plan = bool(_plan_source and await _plan_source(conn))
    return {
        "enabled": self_serve_enabled(),
        "identity": identity,
        "company": company,
        "paid_plan": plan,
        "ready": identity and company and plan,
    }


async def open_production(
    conn: AsyncConnection,
    tenant_id: str,
    principal: str,
    body: dict[str, Any],
    *,
    sign: Callable[[dict[str, Any]], str] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Open production for this company if the three conditions hold; otherwise say which do not. Never opens on a doubt."""
    if not self_serve_enabled():
        raise m.PartnerError(
            "GO_LIVE_SELF_SERVE_OFF",
            "Production opens through a request that the platform approves.",
        )
    clean = g.check_request(body)
    if await db.active_grant(conn) is not None:
        raise m.PartnerError("GO_LIVE_ALREADY_APPROVED", "Production is already open for you.")
    state = await self_serve_status(conn, tenant_id, principal)
    missing = [label for key, label in _NEEDED.items() if not state[key]]
    if missing:
        raise m.PartnerError(
            "PRODUCTION_REQUIREMENTS_NOT_MET",
            "Production opens by itself when these are done: " + "; ".join(missing) + ".",
        )
    now = now or datetime.now(UTC)
    request_id = str(uuid.uuid4())
    try:
        await db.insert_request(
            conn,
            request_id=request_id,
            tenant_id=tenant_id,
            clean=clean,
            accepted_by=principal,
            certification=await _checks(conn),
        )
    except Exception as e:  # the unique index: one open request per company
        if "api_go_live_one_open" in str(e):
            raise m.PartnerError(
                "GO_LIVE_REQUEST_OPEN",
                "You already have a request waiting. Withdraw it first to open production this way.",
            ) from e
        raise
    note = "Opened by the automatic rule: the person's identity check, the company check and a paid plan were all complete."
    async with db.operator_scope(conn):
        await start_review(conn, request_id, SELF_SERVE_ACTOR, now)
        await approve(
            conn,
            request_id,
            SELF_SERVE_ACTOR,
            {k: True for k in g.ATTESTATIONS},
            note,
            now,
            sign=sign,
            automatic=True,
        )
    observe.GO_LIVE.inc(event="self_serve_opened")
    made = await db.get_request(conn, request_id)
    assert made is not None
    return request_view(made)


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
    automatic: bool = False,
) -> dict[str, Any]:
    """`automatic` is only for the self-serve rule (`open_production`): the same fixed identity reviews and approves, and the sandbox checks are not part of it. Everything else here still holds.
    Approve: a different operator from the reviewer, every confirmation given, the sandbox checks still met now. Leaves the company's production grant, the digest of what it allows and a signature over both.
    With no signing key there is no approval: a grant is never made unsigned."""
    now = now or datetime.now(UTC)
    if sign is None:
        raise m.PartnerError(
            "GO_LIVE_SIGNING_UNAVAILABLE",
            "Approval needs the go-live signing key and none is configured. Nothing was approved.",
        )
    r = await _operator_load(conn, request_id)
    await single_approver.apply(
        conn, approval_policy.single_approver() or automatic
    )  # the table's own different-person rule yields only in single-approver mode or for the automatic rule (verify_api_0019)
    g.check_transition(g.GoLiveStatus(r["status"]), g.GoLiveStatus.APPROVED)
    if (
        r["reviewer_id"] is not None
        and str(r["reviewer_id"]) == operator
        and not approval_policy.single_approver()
        and not automatic
    ):
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
        if not automatic and not g.certified(checks):
            raise m.PartnerError(
                "GO_LIVE_NOT_READY", "The company's sandbox checks are no longer all met."
            )
        company = await company_check(conn, tenant_id)
        if not company["verified"]:
            raise m.PartnerError(
                "GO_LIVE_COMPANY_NOT_VERIFIED",
                "The company has no completed company check on record. Finish its company verification first: a registry match, or a certificate of incorporation whose reading agrees with the claim.",
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

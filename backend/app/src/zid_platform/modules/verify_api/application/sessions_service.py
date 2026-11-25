"""Verify API sessions: create, read, list, decide, cancel and resume, and the applicant's hosted calls (CR-150; ZID-EXT-IDV-001 sections 9 and 10, WF-09 to WF-14).

A partner's SERVER creates a session and gets a launch link for the person. The link is a token tied to this one session; only its hash is kept, so it is shown once. The person opens it, and in the sandbox picks a
simulated outcome (there is no capture and no real check). The partner's server reads the minimized decision. An application can see only the sessions it created, and only in its own environment.

In this slice every session is a SANDBOX session. A production session needs the live engine and the production gate; until they exist the API refuses it rather than pretend.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant
from zid_outbox import emit

from zid_platform.modules.verify_api.application import live_service, observe
from zid_platform.modules.verify_api.application import usage_service as usage
from zid_platform.modules.verify_api.application.service import VerifyApiCaller
from zid_platform.modules.verify_api.domain import app_settings
from zid_platform.modules.verify_api.domain import live as lv
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain import sessions as s
from zid_platform.modules.verify_api.infrastructure import session_store as db
from zid_platform.modules.verify_api.infrastructure import store

SANDBOX_POLICY = "pol_sandbox_simulated"
LAUNCH_INVALID = "This link is not valid. Ask the company that sent it for a new one."


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def launch_url(origin: str, token: str) -> str:
    return f"{origin.rstrip('/')}/verify/session/{token}"


def session_view(
    row: dict[str, Any], *, url: str | None = None, url_expires: datetime | None = None
) -> dict[str, Any]:
    """A session as its owner reads it. The launch link appears only in the answer that issues it; it is never stored and never read back."""
    out: dict[str, Any] = {
        "session_id": row["session_id"],
        "status": row["status"],
        "customer_reference": row["customer_reference"],
        "workflow_id": row["workflow_id"],
        "workflow_version": row["workflow_version"],
        "country": row["country"],
        "region": row["region"],
        "purpose_code": row["purpose_code"],
        "locale": row["locale"],
        "metadata": dict(row["metadata"] or {}),
        "mode": row["environment"].lower(),
        "created_at": _iso(row["created_at"]),
        "opened_at": _iso(row["opened_at"]),
        "completed_at": _iso(row["completed_at"]),
        "expires_at": _iso(row["expires_at"]),
        "recheck_of": row.get("recheck_of"),
    }
    if url is not None:
        out["launch_url"] = url
        out["launch_expires_at"] = _iso(url_expires)
    return out


def decision_view(d: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """The minimized decision (section 10.2): the outcome, which checks passed, reason codes and provenance. Never a photograph, a document image, a score or a reviewer's note."""
    return {
        "session_id": d["session_id"],
        "decision_id": d["decision_id"],
        "status": row["status"],
        "outcome": d["outcome"],
        "assurance": {"scheme": "zoikoid-proofing", "level": "SANDBOX_SIMULATED"}
        if d["sandbox"]
        else (dict(lv.LIVE_ASSURANCE) if d["outcome"] == "verified" else None),
        "checks": list(d["checks"]),
        "reason_codes": list(d["reason_codes"]),
        "evidence_receipt_id": None,  # no signed evidence receipt exists yet; a sandbox decision has no evidence behind it at all
        "policy_version": d["policy_version"],
        "region": d["region"],
        "completed_at": _iso(d["completed_at"]),
        "decision_version": d["decision_version"],
        "supersedes_decision_id": d["supersedes_decision_id"],
        "sandbox": d["sandbox"],
    }


def _own_environment(caller: VerifyApiCaller, row: dict[str, Any]) -> None:
    if row["environment"] != caller.environment:
        raise m.PartnerError(
            "ENVIRONMENT_MISMATCH", "That session belongs to the other environment."
        )


DECIDED_EVENT = "verification.api.session_decided"
CLOSED_EVENT = "verification.api.session_closed"


async def _announce(
    conn: AsyncConnection, row: dict[str, Any], name: str, version: int, data: dict[str, Any]
) -> None:
    """Tell the company's registered webhook endpoints (through the platform's outbox and the webhook module) that a session changed. Only opaque ids, the status and the outcome travel: no
    photograph, no document data, no score. The company's own customer reference is theirs and is included so they can match it without a lookup."""
    tenant = str(row["tenant_id"])  # from the session row, never from a request
    env = row["environment"].lower()
    if name == CLOSED_EVENT:
        observe.SESSIONS.inc(environment=env, event="closed")
    else:
        observe.SESSIONS.inc(environment=env, event="decided")
        observe.DECISIONS.inc(environment=env, outcome=str(data.get("outcome") or "unknown"))
    async with in_tenant(
        conn, tenant
    ):  # the hosted page has no signed-in company: this is the session's own
        await emit(
            conn,
            name=name,
            tenant_id=tenant,
            aggregate_type="verify_api_session",
            aggregate_id=row["session_id"],
            aggregate_version=version,
            data={
                "session_id": row["session_id"],
                "application_id": str(row["application_id"]),
                "customer_reference": row["customer_reference"],
                "sandbox": row["environment"] == "SANDBOX",
                **data,
            },
        )


async def _announce_closed(conn: AsyncConnection, row: dict[str, Any], status: s.Status) -> None:
    """A session ended without a decision: canceled, or ran out of time."""
    prior = await db.latest_decision(conn, row["session_id"])
    await _announce(
        conn,
        row,
        CLOSED_EVENT,
        (prior["decision_version"] if prior else 0) + 1,
        {"status": status.value},
    )


async def _expire_if_due(
    conn: AsyncConnection, row: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """A session nobody finished in time becomes expired the next time anyone looks at it, and its link stops working."""
    if row["expires_at"] <= now and s.Status(row["status"]) not in s.FINAL:
        await db.set_status(conn, row["session_id"], s.Status.EXPIRED.value, now, completed=True)
        await db.end_launch(conn, row["session_id"])
        await _announce_closed(conn, row, s.Status.EXPIRED)
        row = {**row, "status": s.Status.EXPIRED.value, "completed_at": now}
    return row


async def _load(
    conn: AsyncConnection, caller: VerifyApiCaller, session_id: str, now: datetime
) -> dict[str, Any]:
    row = await db.get_session(conn, session_id, caller.application_id, lock=True)
    if row is None:
        raise m.PartnerError("NOT_FOUND", "No such session.")
    _own_environment(caller, row)
    return await _expire_if_due(conn, row, now)


# ------------------------------------------------------------------------------- the partner's server


async def create_session(
    conn: AsyncConnection,
    caller: VerifyApiCaller,
    *,
    key: str | None,
    body: dict[str, Any],
    origin: str,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Create one session, safely repeatable. Returns the session and whether it is new. A repeat of the same request with the same key returns the same session (with a fresh launch link, because the
    first one was shown once); the same key with a different request is refused."""
    caller.require("verifications:create")
    return await _create(conn, caller, key=key, body=body, origin=origin, now=now, recheck_of=None)


async def _create(
    conn: AsyncConnection,
    caller: VerifyApiCaller,
    *,
    key: str | None,
    body: dict[str, Any],
    origin: str,
    now: datetime | None,
    recheck_of: str | None,
) -> tuple[dict[str, Any], bool]:
    """The one way a session is made, for a first request and for a recheck alike. The caller has already been required to hold the right scope."""
    now = now or datetime.now(UTC)
    idem = s.check_idempotency_key(key)
    app = await store.get_application(conn, caller.application_id)
    if app is None or app["status"] != "ACTIVE":
        raise m.PartnerError("AUTH_INVALID", "The application is not active.")
    allowed = list(app["allowed_workflows"])
    if "workflow_id" not in body and (allowed or app["default_workflow_id"]):
        body = {
            **body,
            "workflow_id": app_settings.default_workflow(allowed, app["default_workflow_id"]),
        }
    clean = s.check_session_request(body)
    if caller.environment == "SANDBOX":
        s.check_synthetic(clean)  # the sandbox never takes what looks like a real person's details (CR-173)
    if not app_settings.entitled(allowed, clean["workflow_id"]):
        raise m.PartnerError(
            "WORKFLOW_NOT_ENTITLED",
            f"This application may not start {clean['workflow_id']}. Allowed: {', '.join(allowed)}.",
        )
    if caller.environment == "PRODUCTION":
        # refused BEFORE anything is created, so a customer is never asked for a photograph that cannot be checked
        await live_service.require_enabled(conn, clean)
    if clean["return_origin"] is not None and clean["return_origin"] not in list(
        app["redirect_origins"]
    ):
        raise m.PartnerError(
            "RETURN_ORIGIN_NOT_REGISTERED",
            "return_origin must be one of the addresses registered on the application.",
        )
    digest = s.request_hash({**clean, "recheck_of": recheck_of} if recheck_of else clean)
    prior = await db.get_idempotency(conn, caller.application_id, idem)
    stale = prior is not None and now - prior["created_at"] >= s.IDEMPOTENCY_TTL
    if prior is not None and not stale:
        if bytes(prior["request_hash"]) != digest:
            raise m.PartnerError(
                "INVALID_IDEMPOTENCY_REUSE",
                "That Idempotency-Key was already used for a different request. Use a new key for a new request.",
            )
        row = await _load(conn, caller, prior["session_id"], now)
        if s.Status(row["status"]) in (s.Status.READY, s.Status.OPENED):
            token = s.new_launch_token()
            expires = min(now + s.LAUNCH_TTL, row["expires_at"])
            await db.set_launch(conn, row["session_id"], m.hash_secret(token), expires)
            return session_view(row, url=launch_url(origin, token), url_expires=expires), False
        return session_view(row), False
    session_id = s.new_session_id()
    token = s.new_launch_token()
    launch_expires = now + s.LAUNCH_TTL
    await db.insert_session(
        conn,
        session_id=session_id,
        tenant_id=caller.tenant_id,
        application_id=caller.application_id,
        environment=caller.environment,
        clean=clean,
        status=s.Status.READY.value,
        token_hash=m.hash_secret(token),
        launch_expires_at=launch_expires,
        expires_at=now + s.SESSION_TTL,
        recheck_of=recheck_of,
    )
    if stale:
        await db.replace_idempotency(
            conn,
            application_id=caller.application_id,
            key=idem,
            request_hash=digest,
            session_id=session_id,
        )
    else:
        await db.insert_idempotency(
            conn,
            application_id=caller.application_id,
            key=idem,
            tenant_id=caller.tenant_id,
            request_hash=digest,
            session_id=session_id,
        )
    created_row = await db.get_session(conn, session_id, caller.application_id)
    assert created_row is not None
    await usage.meter_session_started(
        conn, created_row
    )  # production only; a sandbox session is never usage
    observe.SESSIONS.inc(environment=created_row["environment"].lower(), event="started")
    return session_view(
        created_row, url=launch_url(origin, token), url_expires=launch_expires
    ), True


RECHECKABLE = (s.Status.COMPLETED, s.Status.INCONCLUSIVE, s.Status.EXPIRED)


def recheck_body(row: dict[str, Any]) -> dict[str, Any]:
    """The request for a recheck: the same customer, workflow, market and purpose as the session it follows. Nothing new is asked for, so a recheck cannot widen what was agreed."""
    body: dict[str, Any] = {
        "customer_reference": row["customer_reference"],
        "workflow_id": row["workflow_id"],
        "workflow_version": row["workflow_version"],
        "country": row["country"],
        "region": row["region"],
        "purpose_code": row["purpose_code"],
        "locale": row["locale"],
        "metadata": dict(row["metadata"] or {}),
    }
    if row["return_origin"]:
        body["return_origin"] = row["return_origin"]
    return body


async def recheck_session(
    conn: AsyncConnection,
    caller: VerifyApiCaller,
    session_id: str,
    *,
    key: str | None,
    origin: str,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """A new check of a customer after a result: a NEW session that follows the original, with its own link and its own decision. Only after the original has ended with a result or without one
    (finished, no answer, or ran out of time); a session still in progress is not rechecked, it is finished or canceled. The original is never changed."""
    now = now or datetime.now(UTC)
    caller.require("verifications:recheck")
    row = await _load(conn, caller, session_id, now)
    if s.Status(row["status"]) not in RECHECKABLE:
        raise m.PartnerError(
            "SESSION_STATE_INVALID",
            "Only a finished session, one with no answer, or one that ran out of time can be rechecked.",
        )
    return await _create(
        conn,
        caller,
        key=key,
        body=recheck_body(row),
        origin=origin,
        now=now,
        recheck_of=row["session_id"],
    )


async def get_session(
    conn: AsyncConnection, caller: VerifyApiCaller, session_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    caller.require("verifications:read")
    return session_view(await _load(conn, caller, session_id, now))


def _cursor(row: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(
        f"{row['created_at'].isoformat()}|{row['session_id']}".encode()
    ).decode()


def _decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(value.encode()).decode()
        when, _, sid = raw.partition("|")
        return datetime.fromisoformat(when), sid
    except (ValueError, UnicodeDecodeError) as e:
        raise m.PartnerError("VALIDATION_FAILED", "The cursor is not valid.") from e


async def list_sessions(
    conn: AsyncConnection,
    caller: VerifyApiCaller,
    *,
    status: str | None = None,
    customer_reference: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    cursor: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    caller.require("verifications:list")
    if status is not None and status not in {x.value for x in s.Status}:
        raise m.PartnerError(
            "VALIDATION_FAILED", f"status is one of: {', '.join(x.value for x in s.Status)}."
        )
    if not 1 <= limit <= s.MAX_LIST:
        raise m.PartnerError("VALIDATION_FAILED", f"limit is between 1 and {s.MAX_LIST}.")
    rows = await db.list_sessions(
        conn,
        caller.application_id,
        status=status,
        reference=customer_reference,
        created_from=created_from,
        created_to=created_to,
        after=_decode_cursor(cursor) if cursor else None,
        limit=limit + 1,
    )
    rows = [r for r in rows if r["environment"] == caller.environment]
    page = rows[:limit]
    return {
        "items": [session_view(r) for r in page],
        "next_cursor": _cursor(page[-1]) if len(rows) > limit and page else None,
    }


async def get_decision(
    conn: AsyncConnection, caller: VerifyApiCaller, session_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    caller.require("decisions:read")
    row = await _load(conn, caller, session_id, now)
    d = await db.latest_decision(conn, session_id)
    if d is None:
        raise m.PartnerError(
            "DECISION_NOT_AVAILABLE",
            f"There is no decision yet: the session is {row['status'].replace('_', ' ')}.",
        )
    return decision_view(d, row)


async def cancel_session(
    conn: AsyncConnection, caller: VerifyApiCaller, session_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    caller.require("verifications:create")
    row = await _load(conn, caller, session_id, now)
    s.check_transition(s.Status(row["status"]), s.Status.CANCELED)
    await db.set_status(conn, session_id, s.Status.CANCELED.value, now, completed=True)
    await db.end_launch(conn, session_id)
    await _announce_closed(conn, row, s.Status.CANCELED)
    fresh = await db.get_session(conn, session_id, caller.application_id)
    assert fresh is not None
    return session_view(fresh)


async def resume_token(
    conn: AsyncConnection,
    caller: VerifyApiCaller,
    session_id: str,
    *,
    origin: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A fresh launch link for a person who left: the old one stops working. Only while the session can still be finished."""
    now = now or datetime.now(UTC)
    caller.require("verifications:resume")
    row = await _load(conn, caller, session_id, now)
    if s.Status(row["status"]) not in (
        s.Status.READY,
        s.Status.OPENED,
        s.Status.CAPTURE_STARTED,
        s.Status.ACTION_REQUIRED,
    ):
        raise m.PartnerError(
            "SESSION_STATE_INVALID",
            f"A session that is {row['status'].replace('_', ' ')} cannot be resumed.",
        )
    token = s.new_launch_token()
    expires = min(now + s.LAUNCH_TTL, row["expires_at"])
    await db.set_launch(conn, session_id, m.hash_secret(token), expires)
    return session_view(row, url=launch_url(origin, token), url_expires=expires)


# ------------------------------------------------------------------------------- the sandbox simulator


async def apply_outcome(
    conn: AsyncConnection, row: dict[str, Any], fixture: s.Fixture, now: datetime
) -> dict[str, Any] | None:
    """Drive a SANDBOX session to a simulated result and record the decision, superseding an earlier one if there is one. Always marked as a simulation. Returns the decision, or None for an expired session."""
    if row["environment"] != "SANDBOX":
        raise m.PartnerError(
            "ENVIRONMENT_MISMATCH", "A simulated outcome can be applied only to a sandbox session."
        )
    methods = list(s.WORKFLOWS[row["workflow_id"]]["methods"])
    target, outcome, checks, reasons = s.simulate(fixture, methods)
    s.check_simulated_transition(s.Status(row["status"]), target)
    final = target in s.FINAL
    await db.set_status(
        conn, row["session_id"], target.value, now, opened=row["opened_at"] is None, completed=final
    )
    await db.end_launch(conn, row["session_id"])  # the person is done with the link
    if outcome is None:
        await _announce_closed(conn, row, target)
        return None
    made = await _write_decision(
        conn,
        row,
        target=target,
        outcome=outcome,
        checks=checks,
        reasons=list(reasons),
        sandbox=True,
        policy_version=SANDBOX_POLICY,
        now=now,
    )
    if target is s.Status.MANUAL_REVIEW:
        # a session waiting for review is queued for a reviewer; imported here because review finalizes through this module
        from zid_platform.modules.verify_api.application import review_service

        async with in_tenant(conn, str(row["tenant_id"])):
            await review_service.open_from_session(conn, {**row, "status": target.value}, now)
    return made


async def _write_decision(
    conn: AsyncConnection,
    row: dict[str, Any],
    *,
    target: s.Status,
    outcome: s.Outcome,
    checks: list[dict[str, str]],
    reasons: list[str],
    sandbox: bool,
    policy_version: str,
    now: datetime,
) -> dict[str, Any]:
    """Store a decision (a new version that supersedes an earlier one), count the usage fact, and tell the company's webhook endpoints. One path for a simulated result and a live one, so they behave alike."""
    prior = await db.latest_decision(conn, row["session_id"])
    decision_id = s.new_decision_id()
    await db.insert_decision(
        conn,
        decision_id=decision_id,
        session_id=row["session_id"],
        tenant_id=str(row["tenant_id"]),
        version=(prior["decision_version"] + 1) if prior else 1,
        supersedes=prior["decision_id"] if prior else None,
        outcome=outcome.value,
        checks=checks,
        reason_codes=reasons,
        sandbox=sandbox,
        policy_version=policy_version,
        region=row["region"],
        completed_at=now,
    )
    made = await db.latest_decision(conn, row["session_id"])
    assert made is not None
    async with in_tenant(
        conn, str(row["tenant_id"])
    ):  # the person's page has no signed-in company: the usage fact is written in the session's own scope, or the ledger would silently refuse it
        await usage.meter_check_completed(
            conn, row, outcome.value, now
        )  # production only; one fact per session, however often its decision is revised
    await _announce(
        conn,
        row,
        DECIDED_EVENT,
        int(made["decision_version"]),
        {
            "status": target.value,
            "outcome": outcome.value,
            "decision_id": made["decision_id"],
            "decision_version": int(made["decision_version"]),
            "supersedes_decision_id": made["supersedes_decision_id"],
            "reason_codes": list(made["reason_codes"]),
            "policy_version": made["policy_version"],
        },
    )
    return made


async def record_decision(
    conn: AsyncConnection,
    row: dict[str, Any],
    *,
    target: s.Status,
    outcome: s.Outcome,
    checks: list[dict[str, str]],
    reasons: list[str],
    sandbox: bool,
    policy_version: str,
    now: datetime,
) -> dict[str, Any]:
    """The live check ends a session here: the status, the link, then the decision."""
    await db.set_status(
        conn,
        row["session_id"],
        target.value,
        now,
        opened=row["opened_at"] is None,
        completed=target in s.FINAL,
    )
    await db.end_launch(conn, row["session_id"])
    return await _write_decision(
        conn,
        row,
        target=target,
        outcome=outcome,
        checks=checks,
        reasons=reasons,
        sandbox=sandbox,
        policy_version=policy_version,
        now=now,
    )


async def simulate_for_partner(
    conn: AsyncConnection,
    caller: VerifyApiCaller,
    session_id: str,
    fixture_name: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A partner's server drives its own sandbox session to an outcome, with no browser: the way a test suite exercises every state."""
    now = now or datetime.now(UTC)
    caller.require("verifications:create")
    fixture = s.check_fixture(fixture_name)
    row = await _load(conn, caller, session_id, now)
    d = await apply_outcome(conn, row, fixture, now)
    fresh = await db.get_session(conn, session_id, caller.application_id)
    assert fresh is not None
    return {"session": session_view(fresh), "decision": decision_view(d, fresh) if d else None}


async def fixture_session(
    conn: AsyncConnection,
    caller: VerifyApiCaller,
    fixture_name: str,
    *,
    key: str | None,
    body: dict[str, Any],
    origin: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a session and drive it straight to a simulated outcome in one call, for a test suite. Defaults make the body optional; the key still makes it safe to repeat."""
    now = now or datetime.now(UTC)
    fixture = s.check_fixture(fixture_name)
    full = {
        "customer_reference": "sandbox-test",
        "country": "US",
        "purpose_code": "customer_onboarding",
        **body,
    }
    view, created = await create_session(conn, caller, key=key, body=full, origin=origin, now=now)
    row = await _load(conn, caller, view["session_id"], now)
    if not created and s.Status(row["status"]) not in (s.Status.READY, s.Status.OPENED):
        decision = await db.latest_decision(conn, view["session_id"])
        return {
            "session": session_view(row),
            "decision": decision_view(decision, row) if decision else None,
            "created": False,
        }
    d = await apply_outcome(conn, row, fixture, now)
    fresh = await db.get_session(conn, view["session_id"], caller.application_id)
    assert fresh is not None
    return {
        "session": session_view(fresh),
        "decision": decision_view(d, fresh) if d else None,
        "created": created,
    }


# ------------------------------------------------------------------------------- the person opening the link (no signed-in company)


async def _by_link(conn: AsyncConnection, token: str, now: datetime) -> dict[str, Any]:
    """The session a launch link belongs to, or one answer for every way a link can be wrong: unknown, ended, expired or for a finished session. Call inside `store.auth_scope`."""
    if not token.startswith(s.LAUNCH_PREFIX):
        raise m.PartnerError("LAUNCH_INVALID", LAUNCH_INVALID)
    row = await db.session_by_launch(conn, m.hash_secret(token))
    if (
        row is None
        or row["launch_expires_at"] is None
        or row["launch_expires_at"] <= now
        or row["expires_at"] <= now
        or s.Status(row["status"]) in s.FINAL
        or row["application_status"] != "ACTIVE"
    ):
        raise m.PartnerError("LAUNCH_INVALID", LAUNCH_INVALID)
    return row


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """What the person is told: who is asking, why, in which language, whether this is a test, and where things stand. Not the partner's reference for them, not the session id, nothing about anyone else."""
    branding = dict(row.get("branding") or {})
    return {
        "requested_by": branding.get("display_name") or row["application_name"],
        "theme": branding.get("theme", app_settings.DEFAULT_THEME),
        "support_url": branding.get("support_url"),
        "purpose_code": row["purpose_code"],
        "locale": branding.get("locale") or row["locale"],
        "workflow": s.WORKFLOWS[row["workflow_id"]]["name"],
        "status": row["status"],
        "sandbox": row["environment"] == "SANDBOX",
        "live": row["environment"] == "PRODUCTION",
        "logo": bool(row.get("has_logo")),
        "link_expires_at": _iso(row["launch_expires_at"]),
        "outcomes": [f.value for f in s.Fixture] if row["environment"] == "SANDBOX" else [],
    }


async def hosted_context(
    conn: AsyncConnection, token: str, now: datetime | None = None
) -> dict[str, Any]:
    return _public(await _by_link(conn, token, now or datetime.now(UTC)))


async def hosted_open(
    conn: AsyncConnection, token: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    row = await _by_link(conn, token, now)
    if s.Status(row["status"]) is s.Status.READY:
        s.check_transition(s.Status.READY, s.Status.OPENED)
        await db.set_status(conn, row["session_id"], s.Status.OPENED.value, now, opened=True)
        row = {**row, "status": s.Status.OPENED.value}
    return _public(row)


async def hosted_simulate(
    conn: AsyncConnection, token: str, fixture_name: str, now: datetime | None = None
) -> dict[str, Any]:
    """The person picks a simulated outcome on the test page. Only for a sandbox session; a live one has no such button and this refuses it."""
    now = now or datetime.now(UTC)
    fixture = s.check_fixture(fixture_name)
    row = await _by_link(conn, token, now)
    if row["environment"] != "SANDBOX":
        raise m.PartnerError(
            "ENVIRONMENT_MISMATCH", "Outcomes can be chosen only on a sandbox test session."
        )
    await apply_outcome(conn, row, fixture, now)
    # a return address is offered only while it is still one the application has registered
    back = (
        row["return_origin"] if row["return_origin"] in list(row["application_origins"]) else None
    )
    return {"done": True, "sandbox": True, "return_origin": back}


def catalogue(app: dict[str, Any] | None = None) -> dict[str, Any]:
    """The approved workflows. For an application, each says whether it may start it and which one is its default."""
    allowed = list(app["allowed_workflows"]) if app else []
    default = app_settings.default_workflow(allowed, app["default_workflow_id"]) if app else None
    return {
        "workflows": [
            {
                "entitled": app_settings.entitled(allowed, k),
                "default": k == default,
                "workflow_id": k,
                "name": v["name"],
                "description": v["description"],
                "methods": v["methods"],
                "versions": v["versions"],
                "current_version": s.CURRENT_VERSION,
            }
            for k, v in s.WORKFLOWS.items()
        ],
        "purposes": list(s.PURPOSES),
    }

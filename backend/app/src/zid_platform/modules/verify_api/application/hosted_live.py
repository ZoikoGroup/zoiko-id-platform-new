"""The person's side of a LIVE (production) verification: the notice and consent, a one-time capture value, and the two photographs (CR-164; docs/specs/verify-api-live-check-product-definition.md).

The link is the only thing the person holds, as for the test page. Each step is checked against the session's state, so the order cannot be skipped: open, consent, nonce, capture. A decline ends the session with nothing photographed.
A failure that is the service's (a file refused, the scanner or the face service down) changes nothing and spends no try: the whole request is rolled back. A face that did not match is a retake while tries remain and then
`inconclusive`, never `not_verified`. The result is recorded exactly like any other decision, so the webhook, the usage fact and the supersession rules behave the same.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.verification.ports import (
    CaptureError,
    CaseError,
    PartnerServices,
    partner_capture,
)
from zid_platform.modules.verify_api.application import media_service as media
from zid_platform.modules.verify_api.application import sessions_service as ss
from zid_platform.modules.verify_api.domain import live as lv
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain import sessions as s
from zid_platform.modules.verify_api.infrastructure import session_store as db

MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_SELFIE_BYTES = 6 * 1024 * 1024


def _b64(value: str, what: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as e:
        raise m.PartnerError("VALIDATION_FAILED", f"The {what} is not valid.") from e
    if not 1 <= len(raw) <= MAX_IMAGE_BYTES:
        raise m.PartnerError("VALIDATION_FAILED", f"The {what} is empty or too large.")
    return raw


def _refused(e: CaptureError) -> m.PartnerError:
    """A service problem or a refused file: nothing was spent, and the person may try the same step again."""
    return m.PartnerError("CAPTURE_REFUSED" if e.status < 500 else "CAPTURE_UNAVAILABLE", e.detail)


async def _live_row(conn: AsyncConnection, token: str, now: datetime) -> dict[str, Any]:
    row = await ss._by_link(conn, token, now)
    if row["environment"] != "PRODUCTION":
        raise m.PartnerError(
            "ENVIRONMENT_MISMATCH", "There is no photograph step on a sandbox test session."
        )
    return row


def _rules(sv: PartnerServices, row: dict[str, Any]) -> Any:
    try:
        rules = partner_capture.enabled_rules(sv, row["country"])
    except CaptureError as e:
        raise _refused(e) from e
    if rules is None:
        raise m.PartnerError(
            "MARKET_NOT_ENABLED",
            "This verification cannot be carried out for this country right now.",
        )
    return rules


async def extras(conn: AsyncConnection, row: dict[str, Any], sv: PartnerServices) -> dict[str, Any]:
    """What the page needs to run a live check, on top of the usual view: that it is live, the notice version the person must be shown, and the tries left. Nothing else about the engine."""
    if row["environment"] != "PRODUCTION":
        return {"live": False}
    try:
        rules = partner_capture.enabled_rules(sv, row["country"])
    except CaptureError:
        rules = None
    if rules is None:
        return {"live": True, "available": False, "notice_version": None, "attempts_left": 0}
    async with in_tenant(conn, str(row["tenant_id"])):
        case = await partner_capture.find(conn, row["session_id"])
    used = partner_capture.attempts_used(case) if case is not None else 0
    return {
        "live": True,
        "available": True,
        "notice_version": partner_capture.notice_version(rules),
        "attempts_left": lv.attempts_left(used, partner_capture.max_attempts(rules)),
        "photos_kept_days": media.keep_days(),  # 0: neither photograph is kept; otherwise they are kept this many days and the company can open them under a recorded reason
    }


def _back(row: dict[str, Any]) -> str | None:
    return (
        row["return_origin"] if row["return_origin"] in list(row["application_origins"]) else None
    )


async def _finish(
    conn: AsyncConnection,
    row: dict[str, Any],
    case: Any,
    result: lv.Result,
    reasons: tuple[str, ...],
    now: datetime,
) -> None:
    target = s.Status.COMPLETED if result is lv.Result.VERIFIED else s.Status.INCONCLUSIVE
    s.check_transition(s.Status.PROCESSING, target)
    await ss.record_decision(
        conn,
        row,
        target=target,
        outcome=s.Outcome(result.value),
        checks=lv.checks_for(result),
        reasons=list(reasons),
        sandbox=False,
        policy_version=case.policy_version,
        now=now,
    )
    async with in_tenant(conn, str(row["tenant_id"])):
        if result is lv.Result.VERIFIED:
            await partner_capture.revoke_claim(conn, case.case_id, now)


async def consent(
    conn: AsyncConnection,
    token: str,
    sv: PartnerServices,
    *,
    notice_version: str,
    consented: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    row = await _live_row(conn, token, now)
    if s.Status(row["status"]) not in (s.Status.READY, s.Status.OPENED):
        raise m.PartnerError(
            "SESSION_STATE_INVALID", "This verification has already started or ended."
        )
    rules = _rules(sv, row)
    if notice_version != partner_capture.notice_version(rules):
        raise m.PartnerError(
            "VALIDATION_FAILED", "The notice has changed. Reload the page and read it again."
        )
    if s.Status(row["status"]) is s.Status.READY:
        await db.set_status(conn, row["session_id"], s.Status.OPENED.value, now, opened=True)
    tenant = str(row["tenant_id"])
    try:
        async with in_tenant(conn, tenant):
            case = await partner_capture.find(conn, row["session_id"])
            if case is None:
                case = await partner_capture.start(
                    conn, tenant_id=tenant, session_id=row["session_id"], rules=rules, now=now
                )
            case = await partner_capture.consent(
                conn,
                case.case_id,
                rules,
                notice_version=notice_version,
                consented=consented,
                now=now,
            )
            if not consented:
                await partner_capture.abandon(conn, case.case_id, rules, now)
    except CaseError as e:
        raise m.PartnerError("CAPTURE_REFUSED", e.detail or e.code) from e
    if consented:
        return {"consented": True, "done": False}
    await db.set_status(conn, row["session_id"], s.Status.CANCELED.value, now, completed=True)
    await db.end_launch(
        conn, row["session_id"]
    )  # nothing was photographed, and the person has said no
    await ss._announce_closed(conn, row, s.Status.CANCELED)
    return {"consented": False, "done": True, "return_origin": _back(row)}


async def nonce(
    conn: AsyncConnection, token: str, sv: PartnerServices, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    row = await _live_row(conn, token, now)
    status = s.Status(row["status"])
    if status not in (s.Status.OPENED, s.Status.ACTION_REQUIRED, s.Status.CAPTURE_STARTED):
        raise m.PartnerError(
            "SESSION_STATE_INVALID", "This verification is not waiting for photographs."
        )
    rules = _rules(sv, row)
    tenant = str(row["tenant_id"])
    try:
        async with in_tenant(conn, tenant):
            case = await partner_capture.find(conn, row["session_id"])
            if case is None or case.consent_at is None or case.withdrawn_at is not None:
                raise m.PartnerError("SESSION_STATE_INVALID", "Agree to the notice first.")
            value = await partner_capture.issue_nonce(conn, case.case_id, now)
    except CaseError as e:
        raise m.PartnerError("CAPTURE_REFUSED", e.detail or e.code) from e
    if status is not s.Status.CAPTURE_STARTED:
        s.check_transition(status, s.Status.CAPTURE_STARTED)
        await db.set_status(conn, row["session_id"], s.Status.CAPTURE_STARTED.value, now)
    return {
        "nonce": value,
        "attempts_left": lv.attempts_left(
            partner_capture.attempts_used(case), partner_capture.max_attempts(rules)
        ),
    }


async def capture(
    conn: AsyncConnection,
    token: str,
    sv: PartnerServices,
    *,
    nonce: str,
    captured_at: str,
    image_base64: str,
    selfie_base64: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    row = await _live_row(conn, token, now)
    if s.Status(row["status"]) is not s.Status.CAPTURE_STARTED:
        raise m.PartnerError("SESSION_STATE_INVALID", "Ask for a new capture step first.")
    rules = _rules(sv, row)
    image, selfie = (
        _b64(image_base64, "document photograph"),
        _b64(selfie_base64, "live photograph"),
    )
    try:
        taken = datetime.fromisoformat(captured_at)
    except ValueError as e:
        raise m.PartnerError("VALIDATION_FAILED", "The capture time is not valid.") from e
    if taken.tzinfo is None:
        raise m.PartnerError("VALIDATION_FAILED", "The capture time needs a time zone.")
    tenant = str(row["tenant_id"])
    exhausted = False
    async with in_tenant(conn, tenant):
        case = await partner_capture.find(conn, row["session_id"])
        if case is None or case.policy_hash != rules.policy_hash:
            raise m.PartnerError(
                "CAPTURE_REFUSED", "This verification cannot continue. Ask for a new link."
            )
        await db.set_status(conn, row["session_id"], s.Status.SUBMITTED.value, now)
        try:
            case, _problem = await partner_capture.submit(
                conn,
                case.case_id,
                rules,
                sv,
                nonce=nonce,
                captured_at=taken,
                image=image,
                selfie=selfie,
                now=now,
            )
            # Only when the deployment chose to keep photographs (ZID_VERIFY_KEEP_PHOTOS_DAYS): kept encrypted with an end date, for the people at the company to look at under a recorded reason.
            await media.keep_capture(
                conn,
                tenant_id=tenant,
                session_id=row["session_id"],
                document=image,
                selfie=selfie,
                captured_at=taken,
                now=now,
            )
        except CaptureError as e:
            raise _refused(
                e
            ) from e  # nothing is kept: the request is rolled back, so no try is spent
        except CaseError as e:
            if e.code not in partner_capture.EXHAUSTED_CODES:
                raise m.PartnerError("CAPTURE_REFUSED", e.detail or e.code) from e
            exhausted = True
            case = await partner_capture.get(conn, case.case_id) or case
    await db.set_status(conn, row["session_id"], s.Status.PROCESSING.value, now)
    left = lv.attempts_left(
        partner_capture.attempts_used(case), partner_capture.max_attempts(rules)
    )
    outcome = lv.outcome_of(
        case.decision.value, case.reasons, attempts_left=left, exhausted=exhausted
    )
    if outcome.result is lv.Result.RETRY:
        s.check_transition(s.Status.PROCESSING, s.Status.ACTION_REQUIRED)
        await db.set_status(conn, row["session_id"], s.Status.ACTION_REQUIRED.value, now)
        return {"done": False, "retry": True, "reason": outcome.reasons[0], "attempts_left": left}
    await _finish(conn, row, case, outcome.result, outcome.reasons, now)
    await db.end_launch(conn, row["session_id"])
    return {
        "done": True,
        "closed": outcome.result is not lv.Result.VERIFIED,
        "return_origin": _back(row),
    }


async def extras_for(
    conn: AsyncConnection, token: str, sv: PartnerServices, now: datetime | None = None
) -> dict[str, Any]:
    """The live extras for a person's link (the page asks for them when it opens)."""
    row = await ss._by_link(conn, token, now or datetime.now(UTC))
    return await extras(conn, row, sv)

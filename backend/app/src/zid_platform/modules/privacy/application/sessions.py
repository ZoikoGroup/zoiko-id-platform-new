"""The privacy-safe verification session (ZID-PRI-004 section 07). Default deny: capture is enabled only while EVERY prerequisite holds.

The server decides, every time it is asked, whether capture may happen: the purpose decision taken at the start must have been allowed, the exact
notice must have been acknowledged for this session, the live consent or other lawful-basis check must still pass (so a withdrawal mid-session stops
new capture, AT-003), the session must not be cancelled, and the person must not have chosen a non-biometric alternative (AT-012: a poor camera or a
chosen alternative is never reported as fraud and never ends in a loop). It never returns an image, a template or a score.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.application import control as ctl
from zid_platform.modules.privacy.application import registry
from zid_platform.modules.privacy.application import service as v1
from zid_platform.modules.privacy.domain.model import PrivacyError

STATEMENT = "Choosing an alternative never counts against you, and a failed camera capture is never reported as fraud."


def _subject(ref: str | None) -> str | None:
    if not ref:
        return None
    try:
        return str(uuid.UUID(ref.removeprefix("sub_")))
    except ValueError:
        return None


def new_ref() -> str:
    return f"vs_{uuid.uuid4()}"


async def open_session(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    activity_code: str,
    purpose_code: str,
    data_category: str,
    subject_ref: str | None,
    country: str | None,
    subregion: str | None,
    assurance_goal: str | None,
    alternative_mode: str | None,
    notice_version: str | None,
    provider_service_ref: str | None,
    pack_id: str | None,
    market: str | None = None,
    environment: str | None = None,
    return_url: str | None = None,
    return_state: str | None = None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    allowed_modes = {m["id"] for m in ctl.alternatives_config()}
    if alternative_mode and alternative_mode not in allowed_modes and alternative_mode != "NONE":
        raise PrivacyError("ALTERNATIVE_UNKNOWN", alternative_mode)
    decision = await ctl.decide_purpose(
        conn, tenant_id, actor, activity_code=activity_code, purpose_code=purpose_code,
        data_category=data_category, subject_ref=_subject(subject_ref) or subject_ref, country=country, subregion=subregion,
        notice_version=notice_version, provider_service_ref=provider_service_ref, pack_id=pack_id,
    )  # fmt: skip
    live = await v1.check_processing(
        conn, tenant_id, activity_code=activity_code, purpose_code=purpose_code, data_category=data_category,
        subject_principal_id=_subject(subject_ref),
    )  # fmt: skip
    # What stays true whatever the person does next (an unknown notice, an unapproved provider, an unknown pack). The consent or restriction
    # reason is NOT kept: it is read live on every status call, so it clears when consent is given and returns when it is withdrawn.
    static = [
        c
        for c in decision["reason_codes"]
        if c != "PROCESSING_PERMITTED_BY_RECORDED_FACTS" and (live.allowed or c != live.reason)
    ]
    ref = new_ref()
    subject = None
    if subject_ref:
        try:
            subject = uuid.UUID(subject_ref.removeprefix("sub_"))
        except ValueError:
            subject = None
    if market and subject is None:
        subject = uuid.uuid4()  # a hosted person the business did not name: a fresh pseudonymous subject, so their own consent and rights attach to it
    await conn.execute(
        text("""
            INSERT INTO privacy.verification_session (session_ref, tenant_id, subject_principal_id, activity_code, purpose_code,
                data_category, country, subregion, assurance_goal, alternative_mode, notice_version, provider_service_ref, decision_id,
                allowed_at_open, reason_codes, state, created_by, market, applicant_id, environment, return_url, return_state, expires_at)
            VALUES (:r, :t, :s, :a, :p, :d, :c, :sr, :g, :alt, :n, :ps, :dec, :al, :rc, 'OPEN', :by, :m, :app, :env, :ru, :rs, :exp)
        """),
        {
            "r": ref,
            "t": uuid.UUID(tenant_id),
            "s": subject,
            "a": activity_code,
            "p": purpose_code,
            "d": data_category,
            "c": country,
            "sr": subregion,
            "g": assurance_goal,
            "alt": alternative_mode,
            "n": notice_version,
            "ps": provider_service_ref,
            "dec": uuid.UUID(str(decision["decision_id"])),
            "al": bool(decision["allowed"]),
            "rc": static,
            "by": uuid.UUID(actor),
            "m": market,
            "app": subject
            if market
            else None,  # the pseudonymous person a hosted proofing case is opened for
            "env": environment,
            "ru": return_url,
            "rs": return_state,
            "exp": expires_at,
        },
    )
    return await status(conn, tenant_id, ref)


async def _load(conn: AsyncConnection, ref: str, *, lock: bool = False) -> Any:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM privacy.verification_session WHERE session_ref = :r"
                + (" FOR UPDATE" if lock else "")
            ),
            {"r": ref},
        )
    ).first()
    if row is None:
        raise PrivacyError("NOT_FOUND", "unknown verification session")
    return row


async def status(conn: AsyncConnection, tenant_id: str, ref: str) -> dict[str, Any]:
    """The subject-facing status. Capture is recomputed here from live records; nothing about enabling it is stored."""
    s = await _load(conn, ref)
    blockers: list[str] = []
    route = "CAMERA"
    if s.state == "CANCELED":
        blockers.append("SESSION_CANCELED")
    blockers += list(s.reason_codes)
    if s.alternative_mode and s.alternative_mode != "NONE":
        route = "ALTERNATIVE"
        blockers.append("NON_BIOMETRIC_ALTERNATIVE_CHOSEN")
    notice: dict[str, Any] | None = None
    if s.notice_version:
        code, _, version = s.notice_version.partition("@")
        acks = await ctl.acknowledged(conn, ref)
        acknowledged = any(
            a["notice_code"] == code and str(a["notice_version"]) == version for a in acks
        )
        if not acknowledged:
            blockers.append("NOTICE_NOT_ACKNOWLEDGED")
        n = (
            await conn.execute(
                text(
                    "SELECT notice_code, version, content_uri, content_hash FROM privacy.notice WHERE notice_code = :c AND version::text = :v"
                ),
                {"c": code, "v": version},
            )
        ).first()
        notice = {  # WF-04 B01: the exact notice the person is asked to read, and whether they have
            "notice_version": s.notice_version,
            "content_uri": n.content_uri if n is not None else None,
            "content_hash": n.content_hash if n is not None else None,
            "acknowledged": acknowledged,
        }
    if s.state == "OPEN":
        live = await v1.check_processing(
            conn, tenant_id, activity_code=s.activity_code, purpose_code=s.purpose_code,
            data_category=s.data_category,
            subject_principal_id=str(s.subject_principal_id) if s.subject_principal_id else None,
        )  # fmt: skip
        if not live.allowed:  # consent withdrawn, a restriction applied, or the activity retired since the session opened
            blockers.append(live.reason)
    enabled = not blockers
    return {
        "session_ref": ref,
        "state": s.state,
        "capture_enabled": enabled,
        "route": route if enabled or route == "ALTERNATIVE" else "NONE",
        "blockers": sorted(set(blockers)),
        "purpose_decision_id": str(s.decision_id),
        "purpose_code": s.purpose_code,
        "region": s.country,
        "alternative_mode": s.alternative_mode,
        "assisted_state": s.assisted_state,
        "notice": notice,
        "alternatives_available": True,
        "alternatives": [
            {"id": m["id"], "label": m["label"], "biometric": False}
            for m in ctl.alternatives_config()
        ],
        "statement": STATEMENT,
        "checked_at": datetime.now(UTC).isoformat(),
    }


async def choose_alternative(
    conn: AsyncConnection, tenant_id: str, actor: str, ref: str, *, mode: str
) -> dict[str, Any]:
    """WF-04 B02 (CR-108): the person chooses a non-biometric route, or goes back to the camera (NONE). No reason is asked for: a person never has
    to disclose a disability or a medical condition to be offered another way, and the choice is never scored against them."""
    s = await _load(conn, ref, lock=True)
    if s.state != "OPEN":
        raise PrivacyError("SESSION_CANCELED")
    ids = {m["id"] for m in ctl.alternatives_config()}
    if mode != "NONE" and mode not in ids:
        raise PrivacyError("ALTERNATIVE_NOT_OFFERED", mode)
    if s.subject_principal_id is not None and actor not in (
        str(s.subject_principal_id),
        str(s.created_by),
    ):
        raise PrivacyError("SESSION_NOT_YOURS")
    await conn.execute(
        text(
            "UPDATE privacy.verification_session SET alternative_mode = CAST(:m AS text), "
            # CR-110: choosing another way puts the person in the assisted queue; going back to the camera takes them out while nobody has started
            "assisted_state = CASE WHEN CAST(:m AS text) IS NULL THEN (CASE WHEN assisted_state = 'WAITING' THEN NULL ELSE assisted_state END) "
            "ELSE COALESCE(assisted_state, 'WAITING') END WHERE session_ref = :r"
        ),
        {"m": None if mode == "NONE" else mode, "r": ref},
    )
    await v1._audit(
        conn, tenant_id, None, "privacy.verification_session.route_chosen", actor=actor, target_type="verification_session",
        target_ref=ref, details={"mode": mode},
    )  # fmt: skip
    return await status(conn, tenant_id, ref)


async def cancel(conn: AsyncConnection, tenant_id: str, ref: str) -> dict[str, Any]:
    s = await _load(conn, ref, lock=True)
    if s.state != "CANCELED":
        await conn.execute(
            text(
                "UPDATE privacy.verification_session SET state = 'CANCELED', canceled_at = now() WHERE session_ref = :r"
            ),
            {"r": ref},
        )
    return await status(conn, tenant_id, ref)


async def record_consent(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    ref: str,
    *,
    notice_version: str,
    capture_method: str,
    affirmative_action: str,
) -> dict[str, Any]:
    """Consent for THIS session's activity, only by the person it is about, and only where the activity's lawful basis is consent (`give_consent` refuses any other
    basis with PRIVACY_FALSE_CONSENT): another basis is never turned into consent by a button."""
    s = await _load(conn, ref)
    if s.state == "CANCELED":
        raise PrivacyError("PRIVACY_INVALID_STATE", "the session is cancelled")
    if s.subject_principal_id is None or str(s.subject_principal_id) != actor:
        raise PrivacyError(
            "CONSENT_IS_THE_SUBJECTS_OWN", "only the person the session is about can consent"
        )
    await registry.require_notice(conn, notice_version)
    cid = await v1.give_consent(
        conn, tenant_id, subject_principal_id=actor, activity_code=s.activity_code,
        purpose_code=s.purpose_code, notice_version=notice_version, capture_method=capture_method,
        affirmative_action=affirmative_action,
    )  # fmt: skip
    return {"consent_id": cid, "session": await status(conn, tenant_id, ref)}


# ------------------------------------------------------------------------------------------------------------- assisted route back office (CR-110)

ASSISTED_REASONS = frozenset(
    {
        "PERSON_UNREACHABLE",
        "PERSON_DECLINED",
        "DOCUMENT_NOT_AVAILABLE",
        "NOT_ELIGIBLE_FOR_ROUTE",
        "OTHER",
    }
)


async def list_assisted(
    conn: AsyncConnection, *, states: tuple[str, ...] = ("WAITING", "IN_PROGRESS"), limit: int = 100
) -> list[dict[str, Any]]:
    """People waiting for, or being helped by, a person (WF-04 B02). Oldest first. No reason for choosing the alternative exists to show."""
    rows = (
        await conn.execute(
            text(
                "SELECT session_ref, subject_principal_id, purpose_code, country, alternative_mode, assisted_state, assisted_by, assisted_at, "
                "assisted_outcome_ref, assisted_reason_code, created_at FROM privacy.verification_session "
                "WHERE assisted_state = ANY(:s) ORDER BY created_at LIMIT :n"
            ),
            {"s": list(states), "n": limit},
        )
    ).all()
    out = []
    for r in rows:
        d = dict(r._mapping)
        out.append(
            {
                k: (
                    str(v)
                    if isinstance(v, uuid.UUID)
                    else v.isoformat()
                    if isinstance(v, datetime)
                    else v
                )
                for k, v in d.items()
            }
        )
    return out


async def claim_assisted(
    conn: AsyncConnection, tenant_id: str, actor: str, ref: str
) -> dict[str, Any]:
    s = await _load(conn, ref, lock=True)
    if s.assisted_state != "WAITING":
        raise PrivacyError("ASSISTED_NOT_WAITING")
    if s.subject_principal_id is not None and str(s.subject_principal_id) == actor:
        raise PrivacyError(
            "ASSISTED_OWN_SESSION", "a person never handles their own assisted verification"
        )
    await conn.execute(
        text(
            "UPDATE privacy.verification_session SET assisted_state = 'IN_PROGRESS', assisted_by = :by, assisted_at = now() WHERE session_ref = :r"
        ),
        {"by": uuid.UUID(actor), "r": ref},
    )
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.assisted_route.claimed",
        actor=actor,
        target_type="verification_session",
        target_ref=ref,
    )
    return {"session_ref": ref, "assisted_state": "IN_PROGRESS"}


async def complete_assisted(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    ref: str,
    *,
    done: bool,
    outcome_ref: str | None,
    reason_code: str | None,
) -> dict[str, Any]:
    """DONE names the verification or review that did the work; NOT_COMPLETED names a reason code (never a medical or personal reason)."""
    s = await _load(conn, ref, lock=True)
    if s.assisted_state != "IN_PROGRESS":
        raise PrivacyError("ASSISTED_NOT_IN_PROGRESS")
    if str(s.assisted_by) != actor:
        raise PrivacyError("ASSISTED_NOT_YOURS", "the person who took it completes it")
    if done and not (outcome_ref or "").strip():
        raise PrivacyError("ASSISTED_OUTCOME_REF_REQUIRED")
    if not done and reason_code not in ASSISTED_REASONS:
        raise PrivacyError("ASSISTED_REASON_REQUIRED", ", ".join(sorted(ASSISTED_REASONS)))
    state = "DONE" if done else "NOT_COMPLETED"
    await conn.execute(
        text(
            "UPDATE privacy.verification_session SET assisted_state = :st, assisted_at = now(), assisted_outcome_ref = :o, "
            "assisted_reason_code = :rc WHERE session_ref = :r"
        ),
        {
            "st": state,
            "o": (outcome_ref or "").strip() or None,
            "rc": None if done else reason_code,
            "r": ref,
        },
    )
    await v1._audit(
        conn,
        tenant_id,
        None,
        "privacy.assisted_route.completed",
        actor=actor,
        target_type="verification_session",
        target_ref=ref,
        details={"state": state, "reason_code": None if done else reason_code},
    )
    return {"session_ref": ref, "assisted_state": state}

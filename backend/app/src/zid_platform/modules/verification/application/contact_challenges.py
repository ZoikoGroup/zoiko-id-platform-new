"""WF-05 contact challenges on a verification case (ZID-DTV-WF-05 DTV-20, DTV-21, §19, §22, A02-A04; CR-114).

    add_contact      an email or phone for a case: normalized, encrypted in the vault, keyed-hashed per tenant; a change is a new revision and
                     revokes every open challenge of the earlier one
    start            a challenge on the current revision, after the send limits; returns the delivery (destination and code) for the caller to
                     send after the transaction commits, and nothing of it is stored
    resend           a new code, same expiry
    confirm          check a code; on success the case records CONTACT_CONTROL_CONFIRMED (PASS) from the challenge method, and nothing else

A method is used only if the case was cleared for it when it opened (EMAIL_CHALLENGE, PHONE_SMS_CHALLENGE, PHONE_VOICE_CHALLENGE).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.communication.ports import keep_destination, read_destination
from zid_platform.modules.evidence.ports import current_runtime
from zid_platform.modules.verification.application import check_cases
from zid_platform.modules.verification.domain import check_cases as cc
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain import contact_challenges as ch
from zid_platform.modules.verification.domain.assertions import Assertion
from zid_platform.modules.verification.infrastructure import phone_numbers

METHOD = {
    ch.Channel.EMAIL: cm.CheckMethod.EMAIL_CHALLENGE,
    ch.Channel.SMS: cm.CheckMethod.PHONE_SMS_CHALLENGE,
    ch.Channel.VOICE: cm.CheckMethod.PHONE_VOICE_CHALLENGE,
}
KIND = {
    ch.Channel.EMAIL: ch.ContactKind.EMAIL,
    ch.Channel.SMS: ch.ContactKind.PHONE,
    ch.Channel.VOICE: ch.ContactKind.PHONE,
}
CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)


@lru_cache(maxsize=1)
def limits() -> ch.Limits:
    doc: dict[str, Any] = (
        yaml.safe_load(
            (CONFIG / "verification/contact-challenges.yaml").read_text(encoding="utf-8")
        )
        or {}
    )
    return ch.parse_limits(doc)


async def keys() -> tuple[bytes, bytes]:
    """(contact hash key, challenge secret key), derived from the cell's internal HMAC key; never stored."""
    mac = current_runtime().mac_key
    return await asyncio.gather(
        asyncio.to_thread(mac.mac, b"zid.wf05.contact-hash.v1"),
        asyncio.to_thread(mac.mac, b"zid.wf05.challenge-secret.v1"),
    )


@dataclass(frozen=True, slots=True)
class Contact:
    contact_id: str
    case_id: str
    kind: ch.ContactKind
    revision: int
    masked: str
    vault_object_id: str
    calling_code: str | None
    unusual: bool
    superseded: bool


@dataclass(frozen=True, slots=True)
class Delivery:
    """What to send, after the transaction commits. The only place the destination and the code meet."""

    channel: ch.Channel
    destination: str
    code: str
    expires_at: datetime
    challenge_id: str
    tenant_id: str


_CONTACT = "contact_id, case_id, kind, revision, masked, vault_object_id, calling_code, unusual, superseded_at"
_CHALLENGE = (
    "challenge_id, contact_id, contact_revision, channel, session_ref, secret_hash, state, expires_at, attempt_count, send_count, last_sent_at, "
    "created_at, consumed_at, end_reason"
)


def _contact(r: Any) -> Contact:
    return Contact(
        str(r.contact_id), str(r.case_id), ch.ContactKind(r.kind), r.revision, r.masked, r.vault_object_id, r.calling_code, r.unusual,
        r.superseded_at is not None,
    )  # fmt: skip


def _challenge(r: Any) -> ch.Challenge:
    return ch.Challenge(
        str(r.challenge_id), str(r.contact_id), r.contact_revision, ch.Channel(r.channel), r.session_ref, bytes(r.secret_hash),
        ch.State(r.state), r.expires_at, r.attempt_count, r.send_count, r.last_sent_at, r.created_at, r.consumed_at, r.end_reason,
    )  # fmt: skip


async def _load_contact(conn: AsyncConnection, contact_id: str) -> Contact:
    try:
        cid = uuid.UUID(contact_id)
    except ValueError as e:
        raise ch.ChallengeError("CONTACT_NOT_FOUND") from e
    row = (
        await conn.execute(
            text(f"SELECT {_CONTACT} FROM verify.check_contact WHERE contact_id = :c"), {"c": cid}
        )
    ).first()
    if row is None:
        raise ch.ChallengeError("CONTACT_NOT_FOUND")
    return _contact(row)


async def _current_revision(conn: AsyncConnection, case_id: str, kind: ch.ContactKind) -> int:
    row = (
        await conn.execute(
            text(
                "SELECT max(revision) AS r FROM verify.check_contact WHERE case_id = :c AND kind = :k"
            ),
            {"c": uuid.UUID(case_id), "k": kind.value},
        )
    ).first()
    return int(row.r or 0) if row is not None else 0


async def _save(conn: AsyncConnection, c: ch.Challenge, tenant_id: str, *, insert: bool) -> None:
    p = {
        "id": uuid.UUID(c.challenge_id), "t": uuid.UUID(tenant_id), "c": uuid.UUID(c.contact_id), "rev": c.contact_revision,
        "ch": c.channel.value, "s": c.session_ref, "h": c.secret_hash, "st": c.state.value, "ex": c.expires_at, "a": c.attempt_count,
        "n": c.send_count, "ls": c.last_sent_at, "at": c.created_at, "cons": c.consumed_at, "er": c.end_reason,
    }  # fmt: skip
    if insert:
        sql = (
            f"INSERT INTO verify.check_challenge (tenant_id, {_CHALLENGE}) VALUES (:t, :id, :c, :rev, :ch, :s, :h, :st, :ex, :a, :n, :ls, :at, "
            ":cons, :er)"
        )
    else:
        sql = (
            "UPDATE verify.check_challenge SET secret_hash = :h, state = :st, attempt_count = :a, send_count = :n, last_sent_at = :ls, "
            "consumed_at = :cons, end_reason = :er WHERE challenge_id = :id"
        )
    await conn.execute(text(sql), p)


async def _revoke_open(conn: AsyncConnection, tenant_id: str, contact_id: str, reason: str) -> None:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_CHALLENGE} FROM verify.check_challenge WHERE contact_id = :c "
                "AND state IN ('PENDING', 'SENT', 'DELIVERED_UNKNOWN') FOR UPDATE"
            ),
            {"c": uuid.UUID(contact_id)},
        )
    ).all()
    for r in rows:
        await _save(conn, ch.revoke(_challenge(r), reason), tenant_id, insert=False)


def _cleared(case: cc.Case, kind: ch.ContactKind) -> bool:
    wanted = {m for c, m in METHOD.items() if KIND[c] is kind}
    return bool(wanted & set(case.available))


async def add_contact(
    conn: AsyncConnection,
    case_id: str,
    *,
    kind: ch.ContactKind,
    raw: str,
    region: str | None,
    now: datetime,
) -> Contact:
    case = await check_cases.get_case(conn, case_id)
    if case.status is cc.CaseStatus.CLOSED:
        raise ch.ChallengeError("CASE_CLOSED")
    if not _cleared(case, kind):
        raise ch.ChallengeError(
            "METHOD_NOT_AVAILABLE_FOR_CASE", f"no {kind.value} challenge was cleared for this case"
        )
    n = phone_numbers.normalize(kind, raw, region or case.market)
    hash_key, _ = await keys()
    prev = (
        await conn.execute(
            text(
                f"SELECT {_CONTACT} FROM verify.check_contact WHERE case_id = :c AND kind = :k AND superseded_at IS NULL FOR UPDATE"
            ),
            {"c": uuid.UUID(case_id), "k": kind.value},
        )
    ).first()
    if prev is not None:
        await _revoke_open(conn, case.tenant_id, str(prev.contact_id), "CONTACT_CHANGED")
        await conn.execute(
            text("UPDATE verify.check_contact SET superseded_at = :n WHERE contact_id = :c"),
            {"n": now, "c": prev.contact_id},
        )
    object_id = await keep_destination(conn, case.tenant_id, n.value)
    contact_id = str(uuid.uuid4())
    revision = await _current_revision(conn, case_id, kind) + 1
    await conn.execute(
        text(
            "INSERT INTO verify.check_contact (contact_id, tenant_id, case_id, kind, revision, value_hash, vault_object_id, masked, region, "
            "calling_code, unusual, created_at) VALUES (:id, :t, :c, :k, :r, :h, :o, :m, :rg, :cc, :u, :at)"
        ),
        {
            "id": uuid.UUID(contact_id),
            "t": uuid.UUID(case.tenant_id),
            "c": uuid.UUID(case_id),
            "k": kind.value,
            "r": revision,
            "h": ch.contact_hash(hash_key, case.tenant_id, kind, n.value),
            "o": object_id,
            "m": n.masked,
            "rg": n.region,
            "cc": n.calling_code,
            "u": n.unusual,
            "at": now,
        },
    )
    return Contact(
        contact_id, case_id, kind, revision, n.masked, object_id, n.calling_code, n.unusual, False
    )


async def _recent(
    conn: AsyncConnection, contact: Contact, channel: ch.Channel, now: datetime
) -> ch.Recent:
    hour, day = now - timedelta(hours=1), now - timedelta(days=1)
    row = (
        await conn.execute(
            text(
                "SELECT "
                "coalesce(sum(g.send_count) FILTER (WHERE g.contact_id = :contact AND g.last_sent_at > :hour), 0) AS contact_hour, "
                "coalesce(sum(g.send_count) FILTER (WHERE k.case_id = :case AND g.last_sent_at > :hour), 0) AS case_hour, "
                "coalesce(sum(g.send_count) FILTER (WHERE k.calling_code = :cc AND g.channel = :ch AND g.last_sent_at > :hour), 0) AS prefix_hour, "
                "coalesce(sum(g.send_count) FILTER (WHERE g.channel = :ch AND g.last_sent_at > :day), 0) AS tenant_day "
                "FROM verify.check_challenge g JOIN verify.check_contact k ON k.contact_id = g.contact_id WHERE g.last_sent_at > :day"
            ),
            {
                "contact": uuid.UUID(contact.contact_id),
                "case": uuid.UUID(contact.case_id),
                "cc": contact.calling_code,
                "ch": channel.value,
                "hour": hour,
                "day": day,
            },
        )
    ).one()
    return ch.Recent(
        int(row.contact_hour), int(row.case_hour), int(row.prefix_hour), int(row.tenant_day)
    )


async def start(
    conn: AsyncConnection,
    contact_id: str,
    *,
    channel: ch.Channel,
    session_ref: str,
    now: datetime,
    lim: ch.Limits | None = None,
) -> tuple[ch.Challenge, Delivery]:
    contact = await _load_contact(conn, contact_id)
    if contact.superseded:
        raise ch.ChallengeError("CONTACT_SUPERSEDED", "use the current contact")
    if KIND[channel] is not contact.kind:
        raise ch.ChallengeError("CHANNEL_NOT_FOR_CONTACT")
    case = await check_cases.get_case(conn, contact.case_id)
    if case.status is cc.CaseStatus.CLOSED:
        raise ch.ChallengeError("CASE_CLOSED")
    if METHOD[channel] not in case.available:
        raise ch.ChallengeError("METHOD_NOT_AVAILABLE_FOR_CASE")
    ch.check_send(
        lim or limits(),
        channel,
        await _recent(conn, contact, channel, now),
        calling_code=contact.calling_code,
    )
    await _revoke_open(conn, case.tenant_id, contact.contact_id, "SUPERSEDED_BY_NEW_CHALLENGE")
    _, secret_key = await keys()
    challenge, code = ch.issue(
        challenge_id=str(uuid.uuid4()), contact_id=contact.contact_id, contact_revision=contact.revision, channel=channel,
        session_ref=session_ref, key=secret_key, now=now,
    )  # fmt: skip
    await _save(conn, challenge, case.tenant_id, insert=True)
    destination = await read_destination(conn, case.tenant_id, contact.vault_object_id)
    return challenge, Delivery(
        channel, destination, code, challenge.expires_at, challenge.challenge_id, case.tenant_id
    )


async def _load_challenge(
    conn: AsyncConnection, challenge_id: str, contact_id: str
) -> ch.Challenge:
    try:
        cid = uuid.UUID(challenge_id)
    except ValueError as e:
        raise ch.ChallengeError("CHALLENGE_NOT_FOUND") from e
    row = (
        await conn.execute(
            text(
                f"SELECT {_CHALLENGE} FROM verify.check_challenge WHERE challenge_id = :c FOR UPDATE"
            ),
            {"c": cid},
        )
    ).first()
    if row is None or str(row.contact_id) != contact_id:
        raise ch.ChallengeError("CHALLENGE_NOT_FOUND")
    return _challenge(row)


async def resend(
    conn: AsyncConnection,
    contact_id: str,
    challenge_id: str,
    *,
    session_ref: str,
    now: datetime,
    lim: ch.Limits | None = None,
) -> tuple[ch.Challenge, Delivery]:
    contact = await _load_contact(conn, contact_id)
    old = await _load_challenge(conn, challenge_id, contact_id)
    if old.session_ref != session_ref:
        raise ch.ChallengeError("CHALLENGE_NOT_FOUND")
    case = await check_cases.get_case(conn, contact.case_id)
    ch.check_send(
        lim or limits(),
        old.channel,
        await _recent(conn, contact, old.channel, now),
        calling_code=contact.calling_code,
    )
    _, secret_key = await keys()
    new, code = ch.resend(old, key=secret_key, now=now)
    await _save(conn, new, case.tenant_id, insert=False)
    destination = await read_destination(conn, case.tenant_id, contact.vault_object_id)
    return new, Delivery(
        new.channel, destination, code, new.expires_at, new.challenge_id, case.tenant_id
    )


async def confirm(
    conn: AsyncConnection,
    contact_id: str,
    challenge_id: str,
    code: str,
    *,
    session_ref: str,
    source: str,
    now: datetime,
) -> ch.Outcome:
    contact = await _load_contact(conn, contact_id)
    challenge = await _load_challenge(conn, challenge_id, contact_id)
    case = await check_cases.get_case(conn, contact.case_id)
    _, secret_key = await keys()
    outcome = ch.confirm(
        challenge, code, key=secret_key, contact_revision=await _current_revision(conn, contact.case_id, contact.kind),
        session_ref=session_ref, now=now,
    )  # fmt: skip
    if outcome.challenge != challenge:
        await _save(conn, outcome.challenge, case.tenant_id, insert=False)
    if outcome.verified:
        await check_cases.record_assertion(
            conn, case.case_id, assertion=Assertion.CONTACT_CONTROL_CONFIRMED, verdict=cc.Verdict.PASS, method=METHOD[challenge.channel],
            source=source, reason_codes=(), confidence=None, observed_at=now, expires_at=None, recorded_by="contact-challenge", now=now,
        )  # fmt: skip
    return outcome


def challenge_view(c: ch.Challenge, contact: Contact | None = None) -> dict[str, Any]:
    return {
        "challenge_id": c.challenge_id,
        "status": c.state.value,
        "channel": c.channel.value,
        "masked_target": contact.masked if contact else None,
        "expires_at": c.expires_at.isoformat(),
        "attempts_left": max(0, ch.MAX_ATTEMPTS - c.attempt_count),
        "sends_left": max(0, ch.MAX_SENDS - c.send_count),
    }


def contact_view(c: Contact) -> dict[str, Any]:
    return {
        "contact_id": c.contact_id,
        "case_id": c.case_id,
        "type": c.kind.value,
        "revision": c.revision,
        "masked": c.masked,
        "unusual_number": c.unusual if c.kind is ch.ContactKind.PHONE else None,
        "note": "A format that is accepted does not mean the number or inbox belongs to the person.",
    }


__all__ = [
    "METHOD",
    "Contact",
    "Delivery",
    "add_contact",
    "challenge_view",
    "confirm",
    "contact_view",
    "keys",
    "limits",
    "resend",
    "start",
]

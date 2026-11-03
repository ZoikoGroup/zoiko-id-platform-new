"""Registration intents, the account index and the invitation index in PostgreSQL (slice L-11, CR-048).

Every function runs on a connection already scoped to the cell's registration tenant (the caller wraps it in `in_tenant`), so row-level
security applies. The address is held in clear only in `email_pending`, written once and cleared when it is proved or expires.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.onboarding.domain.registration import Intent, Journey, State


@dataclass(frozen=True, slots=True)
class Record:
    intent_id: str
    tenant_id: str
    request_ref: str
    country: str
    journey: Journey
    locale: str
    notice_version: str
    email_hash: str
    email_pending: str | None
    state: State
    token_hash: bytes | None
    token_expires_at: datetime | None
    resend_count: int
    last_sent_at: datetime | None
    confirm_attempts: int
    home_cell_id: str
    existing_account: bool
    account_tenant_id: str | None
    account_principal_id: str | None
    invitation_tenant_id: str | None
    created_at: datetime
    expires_at: datetime

    @property
    def intent(self) -> Intent:
        return Intent(
            self.state,
            self.token_hash,
            self.token_expires_at,
            self.resend_count,
            self.last_sent_at,
            self.confirm_attempts,
            self.expires_at,
            self.email_hash,
        )


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def _record(r: Row[Any]) -> Record:
    m = r._mapping
    return Record(
        str(m["intent_id"]),
        str(m["tenant_id"]),
        m["request_ref"],
        m["country"],
        Journey(m["journey"]),
        m["locale"],
        m["notice_version"],
        m["email_hash"],
        m["email_pending"],
        State(m["state"]),
        None if m["token_hash"] is None else bytes(m["token_hash"]),
        m["token_expires_at"],
        m["resend_count"],
        m["last_sent_at"],
        m["confirm_attempts"],
        m["home_cell_id"],
        m["existing_account"],
        _s(m["account_tenant_id"]),
        _s(m["account_principal_id"]),
        _s(m["invitation_tenant_id"]),
        m["created_at"],
        m["expires_at"],
    )


async def insert_intent(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    request_ref: str,
    country: str,
    journey: Journey,
    locale: str,
    notice_version: str,
    email_hash: str,
    email_pending: str | None,
    state: State,
    token_hash: bytes | None,
    token_expires_at: datetime | None,
    sent_at: datetime | None,
    idempotency_key: str | None,
    home_cell_id: str,
    existing_account: bool,
    now: datetime,
    expires_at: datetime,
) -> str:
    intent_id = str(uuid.uuid4())
    await conn.execute(
        text("""
            INSERT INTO platform.registration_intent (intent_id, tenant_id, request_ref, country, journey, locale, notice_version,
                email_hash, email_pending, state, token_hash, token_expires_at, last_sent_at, idempotency_key, home_cell_id,
                existing_account, created_at, updated_at, expires_at)
            VALUES (:id, :t, :ref, :country, :journey, :locale, :notice, :h, :email, :state, :th, :te, :sent, :idem, :cell,
                :existing, :now, :now, :exp)
        """),
        {
            "id": uuid.UUID(intent_id),
            "t": uuid.UUID(tenant_id),
            "ref": request_ref,
            "country": country,
            "journey": journey.value,
            "locale": locale,
            "notice": notice_version,
            "h": email_hash,
            "email": email_pending,
            "state": state.value,
            "th": token_hash,
            "te": token_expires_at,
            "sent": sent_at,
            "idem": idempotency_key,
            "cell": home_cell_id,
            "existing": existing_account,
            "now": now,
            "exp": expires_at,
        },
    )
    return intent_id


async def by_ref(conn: AsyncConnection, request_ref: str, *, lock: bool = False) -> Record | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.registration_intent WHERE request_ref = :r"
                + (" FOR UPDATE" if lock else "")
            ),
            {"r": request_ref},
        )
    ).first()
    return None if row is None else _record(row)


async def by_token_hash(
    conn: AsyncConnection, digest: bytes, *, lock: bool = False
) -> Record | None:
    """The open request a link belongs to. The link carries only its token, so a person can open it on another device."""
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.registration_intent WHERE token_hash = :h AND state = 'VERIFICATION_SENT'"
                + (" FOR UPDATE" if lock else "")
            ),
            {"h": digest},
        )
    ).first()
    return None if row is None else _record(row)


async def by_idempotency_key(conn: AsyncConnection, tenant_id: str, key: str) -> Record | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.registration_intent WHERE tenant_id = :t AND idempotency_key = :k"
            ),
            {"t": uuid.UUID(tenant_id), "k": key},
        )
    ).first()
    return None if row is None else _record(row)


async def sent_last_hour(
    conn: AsyncConnection, tenant_id: str, email_hash: str, since: datetime
) -> int:
    """How many requests this address already started in the window (sent or held), so one address cannot be used to flood a person."""
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM platform.registration_intent WHERE tenant_id = :t AND email_hash = :h "
                    "AND created_at >= :since"
                ),
                {"t": uuid.UUID(tenant_id), "h": email_hash, "since": since},
            )
        ).scalar_one()
    )


async def supersede_open(
    conn: AsyncConnection, tenant_id: str, email_hash: str, now: datetime
) -> int:
    """A new request for an address ends the earlier open ones: their links stop working and their copy of the address is cleared."""
    result = await conn.execute(
        text("""
            UPDATE platform.registration_intent SET state = 'EXPIRED', token_hash = NULL, email_pending = NULL, updated_at = :now
            WHERE tenant_id = :t AND email_hash = :h AND state IN ('INITIATED', 'VERIFICATION_SENT')
        """),
        {"t": uuid.UUID(tenant_id), "h": email_hash, "now": now},
    )
    return int(result.rowcount)


async def mark_sent(
    conn: AsyncConnection,
    intent_id: str,
    *,
    token_hash: bytes,
    token_expires_at: datetime,
    now: datetime,
    resend: bool,
) -> None:
    await conn.execute(
        text("""
            UPDATE platform.registration_intent SET token_hash = :th, token_expires_at = :te, last_sent_at = :now, updated_at = :now,
                state = 'VERIFICATION_SENT', resend_count = resend_count + CASE WHEN :resend THEN 1 ELSE 0 END, confirm_attempts = 0
            WHERE intent_id = :id
        """),
        {
            "id": uuid.UUID(intent_id),
            "th": token_hash,
            "te": token_expires_at,
            "now": now,
            "resend": resend,
        },
    )


async def bump_attempts(conn: AsyncConnection, intent_id: str, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE platform.registration_intent SET confirm_attempts = confirm_attempts + 1, updated_at = :now WHERE intent_id = :id"
        ),
        {"id": uuid.UUID(intent_id), "now": now},
    )


async def set_state(conn: AsyncConnection, intent_id: str, state: State, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE platform.registration_intent SET state = :s, updated_at = :now WHERE intent_id = :id"
        ),
        {"id": uuid.UUID(intent_id), "s": state.value, "now": now},
    )


async def verified(
    conn: AsyncConnection,
    intent_id: str,
    *,
    account_tenant_id: str,
    account_principal_id: str,
    invitation_tenant_id: str | None,
    now: datetime,
) -> None:
    """The inbox is proved: bind the account, spend the token, and clear the address (only its keyed hash stays)."""
    await conn.execute(
        text("""
            UPDATE platform.registration_intent SET state = 'VERIFIED', token_hash = NULL, token_expires_at = NULL, email_pending = NULL,
                account_tenant_id = :at, account_principal_id = :ap, invitation_tenant_id = :it, updated_at = :now
            WHERE intent_id = :id
        """),
        {
            "id": uuid.UUID(intent_id),
            "at": uuid.UUID(account_tenant_id),
            "ap": uuid.UUID(account_principal_id),
            "it": None if invitation_tenant_id is None else uuid.UUID(invitation_tenant_id),
            "now": now,
        },
    )


async def sweep_expired(conn: AsyncConnection, tenant_id: str, now: datetime) -> list[str]:
    """Abandoned requests end, their token is dropped and their copy of the address is cleared. Returns the request ids."""
    rows = (
        await conn.execute(
            text("""
                UPDATE platform.registration_intent SET state = 'EXPIRED', token_hash = NULL, email_pending = NULL, updated_at = :now
                WHERE tenant_id = :t AND state IN ('INITIATED', 'VERIFICATION_SENT') AND expires_at <= :now
                RETURNING intent_id
            """),
            {"t": uuid.UUID(tenant_id), "now": now},
        )
    ).all()
    return [str(r.intent_id) for r in rows]


# ------------------------------------------------------------------------------------------------ account index


async def account_for(
    conn: AsyncConnection, tenant_id: str, email_hash: str
) -> tuple[str, str] | None:
    row = (
        await conn.execute(
            text(
                "SELECT account_tenant_id, account_principal_id FROM platform.registration_account "
                "WHERE tenant_id = :t AND email_hash = :h"
            ),
            {"t": uuid.UUID(tenant_id), "h": email_hash},
        )
    ).first()
    return None if row is None else (str(row.account_tenant_id), str(row.account_principal_id))


async def insert_account(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    email_hash: str,
    account_tenant_id: str,
    account_principal_id: str,
    country: str,
) -> bool:
    """False if the address already has an account (two confirms racing: the first wins, the second creates nothing)."""
    row = await conn.execute(
        text("""
            INSERT INTO platform.registration_account (tenant_id, email_hash, account_tenant_id, account_principal_id, country)
            VALUES (:t, :h, :at, :ap, :c) ON CONFLICT (tenant_id, email_hash) DO NOTHING RETURNING email_hash
        """),
        {
            "t": uuid.UUID(tenant_id),
            "h": email_hash,
            "at": uuid.UUID(account_tenant_id),
            "ap": uuid.UUID(account_principal_id),
            "c": country,
        },
    )
    return row.first() is not None


# ------------------------------------------------------------------------------------------ invitation index


async def index_invitation(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    email_hash: str,
    invite_tenant_id: str,
    principal_id: str,
    role: str,
) -> None:
    await conn.execute(
        text("""
            INSERT INTO platform.registration_invitation_index (tenant_id, email_hash, invite_tenant_id, principal_id, role)
            VALUES (:t, :h, :it, :p, :role) ON CONFLICT (invite_tenant_id, principal_id) DO NOTHING
        """),
        {
            "t": uuid.UUID(tenant_id),
            "h": email_hash,
            "it": uuid.UUID(invite_tenant_id),
            "p": uuid.UUID(principal_id),
            "role": role,
        },
    )


async def pending_invitation(
    conn: AsyncConnection, tenant_id: str, email_hash: str
) -> tuple[str, str, str] | None:
    """The newest pending invitation for this address: (inviting tenant, invited principal, role)."""
    row = (
        await conn.execute(
            text("""
                SELECT invite_tenant_id, principal_id, role FROM platform.registration_invitation_index
                WHERE tenant_id = :t AND email_hash = :h AND status = 'PENDING' ORDER BY created_at DESC LIMIT 1
            """),
            {"t": uuid.UUID(tenant_id), "h": email_hash},
        )
    ).first()
    return None if row is None else (str(row.invite_tenant_id), str(row.principal_id), row.role)


async def mark_routed(
    conn: AsyncConnection, tenant_id: str, invite_tenant_id: str, principal_id: str
) -> None:
    await conn.execute(
        text("""
            UPDATE platform.registration_invitation_index SET status = 'ROUTED'
            WHERE tenant_id = :t AND invite_tenant_id = :it AND principal_id = :p
        """),
        {
            "t": uuid.UUID(tenant_id),
            "it": uuid.UUID(invite_tenant_id),
            "p": uuid.UUID(principal_id),
        },
    )

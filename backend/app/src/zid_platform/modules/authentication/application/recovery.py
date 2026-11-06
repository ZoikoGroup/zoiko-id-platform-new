"""Account recovery (ZID-AUTH-001 §4, §14, §16; AUTH-W7; AUTH-13).

issue_codes()       new batch of one-time codes (the previous batch stops working)
start_with_code()   RC-1 (ready at once) or, for an administrator, RC-3 and for a lockbox
                    account RC-4 (a code is not enough for either)
open_assisted()     support opens an RC-2/RC-3 case; it can never complete one
advance()           re-proofing -> second-administrator approval (RC-4: k designated
                    approvers) -> cooling-off -> READY,
                    then a 15-minute, purpose-bound enrollment ticket
complete()          the new passkey registered with that ticket finishes the recovery:
                    recovery.activated signal (revision++, every session revoked), and for
                    re-proofed recoveries the old strong factors are marked LOST
deny()              by an administrator, or by the owner from a live session (anti-hijack)
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.authentication.domain.authenticator import (
    ENROLLMENT_TICKET_TTL,
    STRONG_TYPES,
    Authenticator,
    AuthenticatorStatus,
)
from zid_platform.modules.authentication.domain.recovery import (
    CASE_TTL,
    CODES_PER_BATCH,
    LOCKBOX_CASE_TTL,
    PRIVILEGED_ACTIONS,
    RECOVERY_TICKET_TTL,
    REPROOF_LEVEL,
    REPROOF_LEVELS,
    RecoveryError,
    classify,
    evaluate,
    new_code,
    transaction_context,
)
from zid_platform.modules.authentication.infrastructure import authenticator_store as authn_store
from zid_platform.modules.authentication.infrastructure import recovery_store as store
from zid_platform.modules.authority.ports import evaluate_approval, holds_any_action
from zid_platform.modules.principal.ports import PrincipalStatus, load_principal
from zid_platform.modules.signals.ports import CanonicalSignal, ingest
from zid_platform.modules.verification.ports import fresh_reproof

assert RECOVERY_TICKET_TTL <= ENROLLMENT_TICKET_TTL


async def _event(
    conn: AsyncConnection, name: str, case: store.Case, version: int, **data: object
) -> None:
    await emit(
        conn,
        name=name,
        tenant_id=case.tenant_id,
        aggregate_type="recovery_case",
        aggregate_id=case.recovery_id,
        aggregate_version=version,
        data={
            "recovery_id": case.recovery_id,
            "principal_id": case.principal_id,
            "recovery_class": case.recovery_class,
            **data,
        },
    )


async def issue_codes(
    conn: AsyncConnection, *, tenant_id: str, principal_id: str, now: datetime
) -> list[str]:
    codes = [new_code() for _ in range(CODES_PER_BATCH)]
    await store.replace_codes(
        conn, tenant_id=tenant_id, principal_id=principal_id, codes=codes, now=now
    )
    return codes


@dataclass(frozen=True, slots=True)
class Progress:
    recovery_id: str
    recovery_class: str
    status: str
    handle: str | None = None
    enrollment_ticket: str | None = None
    missing: tuple[str, ...] = ()
    cooling_off_until: datetime | None = None


async def _eligible(conn: AsyncConnection, principal_id: str) -> None:
    try:
        principal, _ = await load_principal(conn, principal_id)
    except ValueError as e:
        raise RecoveryError("RECOVERY_NOT_ALLOWED") from e
    if principal.status not in (PrincipalStatus.ACTIVE, PrincipalStatus.SUSPENDED):
        raise RecoveryError("RECOVERY_NOT_ALLOWED")


async def _open(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    with_code: bool,
    opened_by: str,
    now: datetime,
) -> tuple[store.Case, str]:
    await _eligible(conn, principal_id)
    privileged = await holds_any_action(conn, principal_id, PRIVILEGED_ACTIONS, now)
    lockbox = await store.load_policy(conn, principal_id) is not None
    cls, method = classify(privileged=privileged, with_code=with_code, lockbox=lockbox)
    for existing in await store.open_cases_for(conn, principal_id):  # one live case at a time
        await store.update(conn, existing, status="DENIED", now=now, denied_reason="SUPERSEDED")
    handle = secrets.token_urlsafe(32)
    gates = evaluate(cls, reproof_at=None, approval_state=None, cooling_off_until=None, now=now)
    case = await store.open_case(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        recovery_class=cls,
        method=method,
        status=gates.status,
        opened_by=opened_by,
        handle=handle,
        now=now,
        expires_at=now + (LOCKBOX_CASE_TTL if cls == "RC-4" else CASE_TTL),
    )
    await _event(
        conn, "recovery.started", case, 1, method=method, opened_by=opened_by.split(":")[0]
    )
    return case, handle


async def start_with_code(
    conn: AsyncConnection, *, tenant_id: str, code: str, now: datetime
) -> Progress:
    principal_id = await store.spend_code(conn, code, now)
    if principal_id is None:
        raise RecoveryError("RECOVERY_CODE_INVALID")
    case, handle = await _open(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        with_code=True,
        opened_by="self:code",
        now=now,
    )
    if case.recovery_class == "RC-1":
        return await _ready(conn, case, handle, now)
    return Progress(
        case.recovery_id, case.recovery_class, case.status, handle, None, ("REPROOF_REQUIRED",)
    )


EMAIL_RECOVERY_COOLDOWN = timedelta(minutes=10)


async def start_with_email(
    conn: AsyncConnection, *, tenant_id: str, principal_id: str, now: datetime
) -> Progress | None:
    """Recovery from the account's own email address (the caller proved the inbox by sending the link there).

    Treated like a recovery code: an ordinary member is READY at once (RC-1: an enrollment ticket for one passkey), while an
    administrator (RC-3) or a lockbox account (RC-4) only opens a case that still needs re-proofing and approval, as the
    platform's rule requires. None when one was already started in the last ten minutes, so a stranger cannot flood an inbox."""
    if await store.recent_by(conn, principal_id, "self:email", now - EMAIL_RECOVERY_COOLDOWN):
        return None
    case, handle = await _open(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        with_code=True,
        opened_by="self:email",
        now=now,
    )
    if case.recovery_class == "RC-1":
        return await _ready(conn, case, handle, now)
    return Progress(
        case.recovery_id, case.recovery_class, case.status, handle, None, ("REPROOF_REQUIRED",)
    )


async def open_assisted(
    conn: AsyncConnection, *, tenant_id: str, principal_id: str, operator_id: str, now: datetime
) -> Progress:
    case, handle = await _open(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        with_code=False,
        opened_by=f"operator:{operator_id}",
        now=now,
    )
    return Progress(
        case.recovery_id, case.recovery_class, case.status, handle, None, ("REPROOF_REQUIRED",)
    )


async def _ready(
    conn: AsyncConnection, case: store.Case, handle: str | None, now: datetime
) -> Progress:
    """A purpose-bound, 15-minute enrollment ticket: it can register one passkey, nothing else."""
    ticket = await authn_store.issue_ticket(
        conn,
        tenant_id=case.tenant_id,
        principal_id=case.principal_id,
        purpose="RECOVERY",
        issued_by=f"recovery:{case.recovery_id}",
        expires_at=now + RECOVERY_TICKET_TTL,
        recovery_id=case.recovery_id,
    )
    return Progress(case.recovery_id, case.recovery_class, "READY", handle, ticket)


async def advance(
    conn: AsyncConnection,
    *,
    recovery_id: str,
    handle: str,
    approval_request_id: str | None,
    now: datetime,
) -> Progress:
    case = await store.by_handle(conn, recovery_id, handle)
    if case.status in ("COMPLETED", "DENIED", "EXPIRED"):
        raise RecoveryError("RECOVERY_CLOSED")
    if now >= case.expires_at:
        await store.update(conn, case, status="EXPIRED", now=now)
        raise RecoveryError("RECOVERY_EXPIRED")
    level = REPROOF_LEVELS.get(case.recovery_class, REPROOF_LEVEL)
    reproof = case.reproof_assertion_id or await fresh_reproof(
        conn, case.principal_id, min_level=level, since=case.created_at, now=now
    )
    policy = await store.load_policy(conn, case.principal_id)
    if case.recovery_class == "RC-4" and policy is None:
        raise RecoveryError("LOCKBOX_POLICY_MISSING")  # lifting is refused while a case is open
    approval_id = approval_request_id or case.approval_request_id
    approval_state = None
    if case.recovery_class in ("RC-3", "RC-4") and approval_id is not None:
        try:
            outcome = await evaluate_approval(
                conn,
                request_id=approval_id,
                current_transaction_context=transaction_context(case.recovery_id),
                now=now,
            )
            approval_state = outcome.state.value
            if (
                case.recovery_class == "RC-4"
                and policy is not None
                and approval_state == "APPROVED"
                and not policy.quorum(outcome.counted)
            ):
                approval_state = "PENDING"  # approved, but not by k designated approvers
        except ValueError:
            approval_state = "DENIED"
    gates = evaluate(
        case.recovery_class,
        reproof_at=now if reproof else None,
        approval_state=approval_state,
        cooling_off_until=case.cooling_off_until,
        now=now,
        cooling_off=policy.cooling_off if case.recovery_class == "RC-4" and policy else None,
    )
    await store.update(
        conn,
        case,
        status=gates.status,
        now=now,
        reproof_assertion_id=reproof,
        approval_request_id=approval_id,
        cooling_off_until=gates.cooling_off_until,
    )
    if gates.status == "READY":
        return await _ready(conn, case, None, now)
    return Progress(
        case.recovery_id,
        case.recovery_class,
        gates.status,
        None,
        None,
        gates.missing,
        gates.cooling_off_until,
    )


async def complete(
    conn: AsyncConnection, *, recovery_id: str, authenticator: Authenticator, now: datetime
) -> None:
    """Called when the RECOVERY ticket's passkey is registered (same transaction)."""
    case = await store.load(conn, recovery_id)
    if case.status != "READY" or case.principal_id != authenticator.principal_id:
        raise RecoveryError("RECOVERY_NOT_READY")
    if case.method == "REPROOF":  # no trusted factor was used: the old ones are presumed lost
        for a in await authn_store.for_principal(conn, case.principal_id, active_only=True):
            if (
                a.authenticator_id != authenticator.authenticator_id
                and a.authenticator_type in STRONG_TYPES
            ):
                await authn_store.set_status(
                    conn, a, AuthenticatorStatus.LOST, reason="recovery", now=now
                )
    await store.update(conn, case, status="COMPLETED", now=now)
    await ingest(  # §14.2: revision++ and every existing session revoked, recorded as a signal
        conn,
        CanonicalSignal(
            tenant_id=case.tenant_id,
            subject_type="PRINCIPAL",
            subject_ref=case.principal_id,
            source_id="recovery",
            source_event_id=case.recovery_id,
            source_event_type="recovery.completed",
            event_type="recovery.activated",
            effective_at=now,
            observed_at=now,
            confidence="AUTHORITATIVE",
        ),
        now=now,
    )
    await _event(
        conn, "recovery.completed", case, 2, authenticator_id=authenticator.authenticator_id
    )


async def deny(conn: AsyncConnection, *, recovery_id: str, reason: str, now: datetime) -> None:
    case = await store.load(conn, recovery_id)
    if case.status in ("COMPLETED", "DENIED", "EXPIRED"):
        raise RecoveryError("RECOVERY_CLOSED")
    await store.update(conn, case, status="DENIED", now=now, denied_reason=reason)
    await _event(conn, "recovery.denied", case, 2, reason=reason)

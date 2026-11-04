"""Finance authority in motion (ZID-COM-CL-003, ZID-COM-CL-004, ZID-UX-COM-009; slice L-14b, CR-054).

Named people hold roles for a time (appointments). A policy counts only while its hash is signed by a Founder and a CFO. A credit note or a pilot
credit is asked for as a request whose facts are frozen with a hash; each role its tier names signs that exact hash, each a different person and never
the one who asked; a separate person, who neither asked nor signed, runs it once. A change of the facts is a new request. Everything is checked again
at the moment of running: the policy still signed and unchanged, every signer still appointed, the approval not lapsed. Every step is in the
commercial change log. Nothing here can approve what the policy does not allow, and nothing is applied without all of it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.commercial.application import invoicing_service as invoicing
from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.domain import finance as rules
from zid_platform.modules.commercial.domain.finance import FinanceError, Kind, State
from zid_platform.modules.commercial.domain.invoicing import InvoicingError
from zid_platform.modules.commercial.infrastructure.finance_config import finance_policy

ROLES = (
    "AE", "CCO", "CFO", "FOUNDER", "GROUP_CFO", "REVOPS", "FINANCE_OPS_LEAD", "CONTROLLER",
    "DISPUTES_SPECIALIST", "FINANCE_EXECUTOR",
)  # fmt: skip
SANDBOX = frozenset({"local", "test"})  # a deployed environment never acts on an unsigned policy


# ---------------------------------------------------------------------------------------------------------------- appointments


async def appoint(
    conn: AsyncConnection,
    *,
    principal: str,
    role: str,
    appointed_by: str,
    effective_from: datetime,
    effective_to: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Appoint a person to a Zoiko internal role for a time. Never to oneself (nobody raises their own limits). Customer-side authority (a
    delegated spend limit, a Procurement Approver) is the customer Owner's to give and lives in the commitment tables, never here."""
    now = now or datetime.now(UTC)
    if role not in ROLES:
        raise FinanceError("ROLE_UNKNOWN", role)
    if principal == appointed_by:
        raise FinanceError(
            "SELF_APPOINTMENT", "nobody appoints themselves or raises their own limit"
        )
    if effective_to is not None and effective_to <= effective_from:
        raise FinanceError("WINDOW_INVALID", "an appointment ends after it starts")
    aid = uuid.uuid4()
    try:
        await conn.execute(
            text(
                "INSERT INTO commercial.commercial_role_holder (appointment_id, principal_id, role, granted_by, "
                "effective_from, effective_to) VALUES (:a, :p, :r, :g, :f, :e)"
            ),
            {"a": aid, "p": uuid.UUID(principal), "r": role, "g": uuid.UUID(appointed_by), "f": effective_from, "e": effective_to},
        )  # fmt: skip
    except Exception as e:
        if "commercial_role_holder_live" in str(e):
            raise FinanceError(
                "ALREADY_APPOINTED", "that person already holds this role; end it first"
            ) from e
        raise
    await log_change(conn, kind="FINANCE_APPOINTMENT_GRANTED", ref=str(aid), tenant_id=None, actor=appointed_by, reason=role,
                     detail={"principal": principal, "role": role, "from": effective_from.isoformat(), "to": effective_to.isoformat() if effective_to else None})  # fmt: skip
    return {"appointment_id": str(aid), "principal_id": principal, "role": role,
            "effective_from": effective_from.isoformat(), "effective_to": effective_to.isoformat() if effective_to else None}  # fmt: skip


async def end_appointment(
    conn: AsyncConnection, appointment_id: str, *, by: str, now: datetime | None = None
) -> None:
    """Ends an appointment at once (the row stays). It takes effect for the next decision, including a request already approved."""
    now = now or datetime.now(UTC)
    row = (
        await conn.execute(
            text(
                "UPDATE commercial.commercial_role_holder SET revoked_at = :now, revoked_by = :by "
                "WHERE appointment_id = :a AND revoked_at IS NULL RETURNING principal_id, role"
            ),
            {"a": uuid.UUID(appointment_id), "by": uuid.UUID(by), "now": now},
        )
    ).first()
    if row is None:
        raise FinanceError("APPOINTMENT_NOT_FOUND", "no live appointment with that id")
    await log_change(conn, kind="FINANCE_APPOINTMENT_ENDED", ref=appointment_id, tenant_id=None,
                     actor=by, reason=row.role, detail={"principal": str(row.principal_id)})  # fmt: skip


async def holds(conn: AsyncConnection, principal: str, role: str, now: datetime) -> bool:
    """Does this person hold this role right now: appointed, started, not ended, not revoked,"""
    row = (
        await conn.execute(
            text(
                "SELECT 1 FROM commercial.commercial_role_holder WHERE principal_id = :p AND role = :r AND revoked_at IS NULL "
                "AND effective_from <= :now AND (effective_to IS NULL OR effective_to > :now)"
            ),
            {"p": uuid.UUID(principal), "r": role, "now": now},
        )
    ).first()
    return row is not None


async def appointments(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT appointment_id, principal_id, role, effective_from, effective_to, revoked_at "
                "FROM commercial.commercial_role_holder ORDER BY role, effective_from DESC"
            )
        )
    ).mappings().all()  # fmt: skip
    return [
        {
            "appointment_id": str(r["appointment_id"]), "principal_id": str(r["principal_id"]), "role": r["role"],
            "effective_from": r["effective_from"].isoformat(),
            "effective_to": r["effective_to"].isoformat() if r["effective_to"] else None,
            "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
        }
        for r in rows
    ]  # fmt: skip


# ---------------------------------------------------------------------------------------------------------------------- policy


async def policy_state(conn: AsyncConnection) -> dict[str, Any]:
    p = finance_policy()
    signed = (
        await conn.execute(
            text(
                "SELECT signer_role, signer_principal, signed_at FROM commercial.finance_policy_signature WHERE content_hash = :h"
            ),
            {"h": p.content_hash},
        )
    ).all()
    roles = {r.signer_role for r in signed}
    return {
        "version": p.version, "content_hash": p.content_hash, "effective": {"FOUNDER", "CFO"} <= roles,
        "signatures": [{"role": r.signer_role, "signed_at": r.signed_at.isoformat()} for r in signed],
        "awaiting": sorted({"FOUNDER", "CFO"} - roles),
    }  # fmt: skip


async def require_effective(conn: AsyncConnection, environment: str) -> None:
    """The lock: in a deployed environment nothing is opened, signed or run on a policy the Founder and the CFO have not signed."""
    if environment in SANDBOX:
        return
    if not (await policy_state(conn))["effective"]:
        raise FinanceError(
            "POLICY_NOT_SIGNED",
            "the finance policy is not signed by the Founder and the CFO, so its limits have no authority",
        )


async def sign_policy(
    conn: AsyncConnection, *, signer: str, content_hash: str, now: datetime | None = None
) -> dict[str, Any]:
    """A Founder or a CFO signs the policy file as it is now. They sign the hash they were shown, so a file that changed under them is not signed."""
    now = now or datetime.now(UTC)
    p = finance_policy()
    if content_hash != p.content_hash:
        raise FinanceError(
            "POLICY_CHANGED", "the policy is not the one you were shown; read it again"
        )
    role = None
    for candidate in ("FOUNDER", "CFO"):
        if await holds(conn, signer, candidate, now):
            role = candidate
            break
    if role is None:
        raise FinanceError(
            "NOT_A_SIGNATORY", "only an appointed Founder or CFO signs the finance policy"
        )
    try:
        await conn.execute(
            text(
                "INSERT INTO commercial.finance_policy_signature (content_hash, policy_version, signer_principal, signer_role, signed_at) VALUES (:h, :v, :p, :r, :n)"
            ),
            {"h": p.content_hash, "v": p.version, "p": uuid.UUID(signer), "r": role, "n": now},
        )
    except Exception as e:
        raise FinanceError("ALREADY_SIGNED", f"{role} has signed this version already") from e
    await log_change(
        conn,
        kind="FINANCE_POLICY_SIGNED",
        ref=p.content_hash,
        tenant_id=None,
        actor=signer,
        reason=role,
        detail={"version": p.version},
    )
    return await policy_state(conn)


# ------------------------------------------------------------------------------------------------------------------- requests


def _view(r: Any, approvals: list[Any]) -> dict[str, Any]:
    return {
        "request_id": str(r["request_id"]), "kind": r["kind"], "tenant_id": str(r["tenant_id"]),
        "invoice_id": str(r["invoice_id"]) if r["invoice_id"] else None, "currency": r["currency"],
        "amount": f"{Decimal(r['amount_minor']) / 100:.2f}", "usd_minor": r["usd_minor"], "exposure_usd_minor": r["exposure_usd_minor"],
        "category": r["category"], "reason": r["reason"], "snapshot_hash": r["snapshot_hash"], "policy_version": r["policy_version"],
        "required_roles": list(r["required_roles"]), "state": r["state"], "requested_by": str(r["requested_by"]),
        "created_at": r["created_at"].isoformat(), "expires_at": r["expires_at"].isoformat(),
        "approved_at": r["approved_at"].isoformat() if r["approved_at"] else None,
        "approval_expires_at": r["approval_expires_at"].isoformat() if r["approval_expires_at"] else None,
        "approvals": [{"role": a["role"], "principal_id": str(a["principal_id"]), "decision": a["decision"], "decided_at": a["decided_at"].isoformat()} for a in approvals],
        "result": r["result"],
    }  # fmt: skip


async def _load(conn: AsyncConnection, request_id: str, *, lock: bool = False) -> Any:
    row = (
        await conn.execute(
            text("SELECT * FROM commercial.finance_request WHERE request_id = :r" + (" FOR UPDATE" if lock else "")),
            {"r": uuid.UUID(request_id)},
        )
    ).mappings().first()  # fmt: skip
    if row is None:
        raise FinanceError("REQUEST_NOT_FOUND", "no such request")
    return row


async def _approvals(conn: AsyncConnection, request_id: str) -> list[Any]:
    return list(
        (
            await conn.execute(
                text("SELECT role, principal_id, decision, decided_at FROM commercial.finance_approval WHERE request_id = :r ORDER BY decided_at"),
                {"r": uuid.UUID(request_id)},
            )
        ).mappings().all()
    )  # fmt: skip


async def _lapse(conn: AsyncConnection, r: Any, now: datetime) -> Any:
    """An undecided request, or an approval nobody ran, that has lapsed is EXPIRED. Written the moment anyone looks."""
    state = State(r["state"])
    late = (state is State.PENDING_APPROVAL and r["expires_at"] <= now) or (
        state is State.APPROVED
        and r["approval_expires_at"] is not None
        and r["approval_expires_at"] <= now
    )
    if not late:
        return r
    await conn.execute(
        text("UPDATE commercial.finance_request SET state = 'EXPIRED' WHERE request_id = :r"),
        {"r": r["request_id"]},
    )
    await log_change(
        conn,
        kind="FINANCE_REQUEST_EXPIRED",
        ref=str(r["request_id"]),
        tenant_id=str(r["tenant_id"]),
        actor=None,
        reason=state.value,
    )
    return await _load(conn, str(r["request_id"]))


async def open_request(
    conn: AsyncConnection,
    *,
    kind: Kind,
    tenant_id: str,
    invoice_id: str | None,
    amount: Decimal,
    currency: str,
    category: str,
    reason: str,
    requested_by: str,
    ends_at: datetime | None = None,
    evidence_ref: str | None = None,
    disposition: str | None = None,
    program_ref: str | None = None,
    sponsor: str | None = None,
    environment: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Ask for a credit note against an issued invoice, or a pilot credit for an organization. The tier, and so who must sign, is the policy's
    and is worked out here from the exposure; the person asking says what they want and why, never who approves."""
    now = now or datetime.now(UTC)
    policy = finance_policy()
    await require_effective(conn, environment)
    if not any([await holds(conn, requested_by, r, now) for r in policy.request_roles]):
        raise FinanceError(
            "NOT_A_REQUESTER",
            "only an appointed Finance Operations Lead or RevOps opens a finance request",
        )
    if not reason.strip():
        raise FinanceError("REASON_REQUIRED", "say why")
    account = await invoicing.account_of_tenant(conn, tenant_id)
    if account is None:
        raise FinanceError(
            "ACCOUNT_REQUIRED",
            "money instruments are for an organization that has a commercial account",
        )
    evidence_ref = (evidence_ref or "").strip() or None
    program_ref = (program_ref or "").strip() or None
    sponsor = (sponsor or "").strip() or None
    if kind is Kind.CREDIT_NOTE:
        if category not in ("CORRECTION", "GOODWILL"):
            raise FinanceError("CATEGORY_INVALID", "a credit note is a correction or goodwill")
        if invoice_id is None:
            raise FinanceError("INVOICE_REQUIRED", "a credit note corrects one issued invoice")
        if category == "CORRECTION" and evidence_ref is None:
            raise FinanceError(
                "EVIDENCE_REQUIRED",
                "a correction names its evidence (the source records that show the error)",
            )
    else:
        if category != "PILOT":
            raise FinanceError("CATEGORY_INVALID", "a pilot credit is a pilot benefit")
        if invoice_id is not None:
            raise FinanceError("INVOICE_NOT_ALLOWED", "a pilot credit is not against an invoice")
        if program_ref is None or sponsor is None:
            raise FinanceError(
                "PILOT_PROGRAM_REQUIRED",
                "a pilot benefit names its approved program and its sponsor",
            )
        limit = policy.pilot_credit.max_days or 0
        if ends_at is None or ends_at <= now or ends_at > now + timedelta(days=limit):
            raise FinanceError(
                "PILOT_PERIOD_INVALID",
                f"a pilot benefit ends in the future and lasts at most {limit} days; renewing is a new request",
            )
    amount_minor = rules.to_minor(amount)
    if (
        invoice_id is not None
    ):  # the invoice must be this organization's, issued, in this currency, with this much left to credit
        async with in_tenant(conn, tenant_id):
            try:
                inv, left = await invoicing.credit_headroom(conn, invoice_id)
            except InvoicingError as e:
                raise FinanceError("INVOICE_NOT_CREDITABLE", e.detail or e.code) from e
        if (
            str(inv["tenant_id"]) != tenant_id
            or inv["status"] != "ISSUED"
            or inv["currency"] != currency
        ):
            raise FinanceError(
                "INVOICE_NOT_CREDITABLE",
                "that is not an issued invoice of this organization in this currency",
            )
        if amount > left:
            raise FinanceError(
                "CREDIT_NOTE_EXCEEDS_INVOICE", "credits cannot exceed what remains of the invoice"
            )
        async with in_tenant(conn, tenant_id):
            paid = await invoicing.amount_paid(conn, invoice_id)
        if paid > 0:  # a credit against money already taken must say what happens to that money
            if disposition not in rules.PAID_DISPOSITIONS:
                raise FinanceError(
                    "DISPOSITION_REQUIRED",
                    "the invoice is paid: say whether the credit is refunded, held as a customer balance or settled outside the platform",
                )
        elif disposition not in (None, "INVOICE_BALANCE"):
            raise FinanceError(
                "DISPOSITION_INVALID", "an unpaid invoice is credited against its own balance"
            )
        else:
            disposition = "INVOICE_BALANCE"
    usd = rules.usd_minor(amount_minor, currency, policy.fx_usd)
    instrument = policy.instrument(kind)
    prior = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(sum(usd_minor), 0) FROM commercial.finance_request WHERE tenant_id = :t AND kind = :k "
                    "AND state = ANY(:s) AND created_at > :since"
                ),
                {"t": uuid.UUID(tenant_id), "k": kind.value, "s": [s.value for s in rules.COUNTS_TOWARDS_EXPOSURE], "since": now - timedelta(days=instrument.window_days)},
            )
        ).scalar_one()
    )  # fmt: skip
    exposure = (
        usd + prior
    )  # credits for one organization within the window add up: no splitting to stay under a limit
    tier = rules.tier_for(instrument, exposure)
    snapshot = {
        "kind": kind.value, "tenant_id": tenant_id, "invoice_id": invoice_id, "currency": currency, "amount_minor": amount_minor,
        "usd_minor": usd, "exposure_usd_minor": exposure, "category": category, "reason": reason.strip(),
        "policy_version": policy.version, "policy_hash": policy.content_hash, "required_roles": list(tier.roles),
        "ends_at": ends_at.isoformat() if ends_at else None, "account_id": account["account_id"], "evidence_ref": evidence_ref,
        "disposition": disposition, "program_ref": program_ref, "sponsor": sponsor,
    }  # fmt: skip
    digest = rules.snapshot_hash(snapshot)
    request_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.finance_request (request_id, kind, tenant_id, invoice_id, currency, amount_minor, usd_minor, exposure_usd_minor, "
            "category, reason, snapshot, snapshot_hash, policy_version, policy_hash, required_roles, state, requested_by, created_at, expires_at) "
            "VALUES (:id, :k, :t, :i, :c, :a, :u, :x, :cat, :r, CAST(:s AS jsonb), :h, :pv, :ph, :roles, 'PENDING_APPROVAL', :by, :now, :exp)"
        ),
        {
            "id": request_id, "k": kind.value, "t": uuid.UUID(tenant_id), "i": uuid.UUID(invoice_id) if invoice_id else None, "c": currency,
            "a": amount_minor, "u": usd, "x": exposure, "cat": category, "r": reason.strip(), "s": _json(snapshot), "h": digest,
            "pv": policy.version, "ph": policy.content_hash, "roles": list(tier.roles), "by": uuid.UUID(requested_by), "now": now,
            "exp": now + timedelta(days=policy.request_valid_days),
        },
    )  # fmt: skip
    await log_change(conn, kind="FINANCE_REQUEST_OPENED", ref=str(request_id), tenant_id=tenant_id, actor=requested_by, reason=category,
                     detail={"kind": kind.value, "usd_minor": usd, "exposure_usd_minor": exposure, "required_roles": list(tier.roles), "snapshot_hash": digest})  # fmt: skip
    return _view(await _load(conn, str(request_id)), [])


def _json(obj: Any) -> str:
    import json

    return json.dumps(obj, sort_keys=True)


async def decide(
    conn: AsyncConnection,
    request_id: str,
    *,
    role: str,
    approver: str,
    decision: str,
    presented_hash: str,
    reason: str | None,
    environment: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One role signs (or rejects) the exact request it read. The person must hold that role now; they sign once; they did not ask for it."""
    now = now or datetime.now(UTC)
    if decision not in ("APPROVE", "REJECT"):
        raise FinanceError("DECISION_INVALID", decision)
    r = await _lapse(conn, await _load(conn, request_id, lock=True), now)
    if State(r["state"]) is not State.PENDING_APPROVAL:
        raise FinanceError("NOT_PENDING", f"a request that is {r['state']} is not decided")
    await require_effective(conn, environment)
    if r["policy_hash"] != finance_policy().content_hash and environment not in SANDBOX:
        raise FinanceError(
            "POLICY_CHANGED", "the policy changed after this request was opened; ask again"
        )
    decided = {a["role"]: str(a["principal_id"]) for a in await _approvals(conn, request_id)}
    rules.check_decision(required=tuple(r["required_roles"]), role=role, approver=approver, requester=str(r["requested_by"]), decided=decided,
                         presented_hash=presented_hash, request_hash=r["snapshot_hash"])  # fmt: skip
    if not await holds(conn, approver, role, now):
        raise FinanceError("NOT_APPOINTED", f"you are not appointed as {role} now")
    if decision == "REJECT" and not (reason or "").strip():
        raise FinanceError("REASON_REQUIRED", "say why it is rejected")
    await conn.execute(
        text("INSERT INTO commercial.finance_approval (request_id, role, principal_id, decision, snapshot_hash, reason, decided_at) VALUES (:r, :ro, :p, :d, :h, :why, :n)"),
        {"r": uuid.UUID(request_id), "ro": role, "p": uuid.UUID(approver), "d": decision, "h": presented_hash, "why": reason, "n": now},
    )  # fmt: skip
    decided[role] = approver
    if decision == "REJECT":
        await conn.execute(
            text("UPDATE commercial.finance_request SET state = 'REJECTED' WHERE request_id = :r"),
            {"r": uuid.UUID(request_id)},
        )
    elif rules.is_complete(tuple(r["required_roles"]), decided):
        await conn.execute(
            text(
                "UPDATE commercial.finance_request SET state = 'APPROVED', approved_at = :n, approval_expires_at = :x WHERE request_id = :r"
            ),
            {
                "r": uuid.UUID(request_id),
                "n": now,
                "x": now + timedelta(days=finance_policy().approval_valid_days),
            },
        )
    await log_change(conn, kind="FINANCE_REQUEST_DECIDED", ref=request_id, tenant_id=str(r["tenant_id"]), actor=approver, reason=decision,
                     detail={"role": role, "snapshot_hash": presented_hash})  # fmt: skip
    return _view(await _load(conn, request_id), await _approvals(conn, request_id))


async def cancel(
    conn: AsyncConnection, request_id: str, *, by: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    r = await _lapse(conn, await _load(conn, request_id, lock=True), now)
    if str(r["requested_by"]) != by:
        raise FinanceError("NOT_THE_REQUESTER", "only the person who asked cancels a request")
    if State(r["state"]) not in (State.PENDING_APPROVAL, State.APPROVED, State.APPLICATION_FAILED):
        raise FinanceError("NOT_CANCELABLE", f"a request that is {r['state']} cannot be canceled")
    await conn.execute(
        text("UPDATE commercial.finance_request SET state = 'CANCELED' WHERE request_id = :r"),
        {"r": uuid.UUID(request_id)},
    )
    await log_change(
        conn,
        kind="FINANCE_REQUEST_CANCELED",
        ref=request_id,
        tenant_id=str(r["tenant_id"]),
        actor=by,
    )
    return _view(await _load(conn, request_id), await _approvals(conn, request_id))


async def execute(
    conn: AsyncConnection,
    request_id: str,
    *,
    executor: str,
    idempotency_key: str,
    environment: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run an approved request, once, by someone who neither asked nor signed. Everything is checked again now. A retry with the same key, or a
    second call after it was applied, is the same answer and does nothing. A failure to apply is recorded and may be tried again."""
    now = now or datetime.now(UTC)
    policy = finance_policy()
    r = await _lapse(conn, await _load(conn, request_id, lock=True), now)
    if State(r["state"]) is State.APPLIED:
        return _view(
            r, await _approvals(conn, request_id)
        )  # the one right was used: a replay is the same answer
    await require_effective(conn, environment)
    approvals = await _approvals(conn, request_id)
    if r["policy_hash"] != policy.content_hash and environment not in SANDBOX:
        raise FinanceError("POLICY_CHANGED", "the policy changed since the approval; ask again")
    if not any([await holds(conn, executor, role, now) for role in policy.execute_roles]):
        raise FinanceError(
            "NOT_AN_EXECUTOR", "only an appointed Finance Executor runs an approved request"
        )
    state = State(r["state"])
    if state is State.APPLICATION_FAILED:
        state = State.APPROVED  # a failed application is tried again on the same approval
    rules.check_execution(state=state, approval_expires_at=r["approval_expires_at"], now=now, executor=executor, requester=str(r["requested_by"]),
                          approvers=tuple(str(a["principal_id"]) for a in approvals if a["decision"] == "APPROVE"))  # fmt: skip
    for a in approvals:  # every signer still holds the role they signed with: a revoked appointment voids the approval
        if a["decision"] == "APPROVE" and not await holds(
            conn, str(a["principal_id"]), a["role"], now
        ):
            await conn.execute(text("UPDATE commercial.finance_request SET state = 'EXPIRED', result = CAST(:x AS jsonb) WHERE request_id = :r"),
                               {"r": uuid.UUID(request_id), "x": _json({"reason": "SIGNER_AUTHORITY_LOST", "role": a["role"]})})  # fmt: skip
            await log_change(
                conn,
                kind="FINANCE_REQUEST_VOIDED",
                ref=request_id,
                tenant_id=str(r["tenant_id"]),
                actor=executor,
                reason="SIGNER_AUTHORITY_LOST",
            )
            raise FinanceError(
                "SIGNER_AUTHORITY_LOST",
                f"the {a['role']} who signed no longer holds that appointment; ask again",
            )
    now_account = await invoicing.account_of_tenant(conn, str(r["tenant_id"]))
    if now_account is None or now_account["account_id"] != r["snapshot"].get("account_id"):
        await conn.execute(
            text(
                "UPDATE commercial.finance_request SET state = 'EXPIRED', result = CAST(:x AS jsonb) WHERE request_id = :r"
            ),
            {"r": uuid.UUID(request_id), "x": _json({"reason": "PAYER_CHANGED"})},
        )
        await log_change(
            conn,
            kind="FINANCE_REQUEST_VOIDED",
            ref=request_id,
            tenant_id=str(r["tenant_id"]),
            actor=executor,
            reason="PAYER_CHANGED",
        )
        raise FinanceError("PAYER_CHANGED", "the legal payer changed since the approval; ask again")
    await conn.execute(
        text("UPDATE commercial.finance_request SET state = 'APPLYING' WHERE request_id = :r"),
        {"r": uuid.UUID(request_id)},
    )
    result: dict[str, Any]
    try:
        async with conn.begin_nested():
            result = await _apply(conn, r, executor, now)
    except (
        InvoicingError
    ) as e:  # nothing was applied: the failure is recorded and the approval stands for another try
        failed = {"error": e.code, "detail": e.detail}
        await conn.execute(text("UPDATE commercial.finance_request SET state = 'APPLICATION_FAILED', result = CAST(:x AS jsonb) WHERE request_id = :r"),
                           {"r": uuid.UUID(request_id), "x": _json(failed)})  # fmt: skip
        await log_change(
            conn,
            kind="FINANCE_REQUEST_FAILED",
            ref=request_id,
            tenant_id=str(r["tenant_id"]),
            actor=executor,
            reason=e.code,
        )
        return _view(await _load(conn, request_id), approvals)
    await conn.execute(
        text("INSERT INTO commercial.finance_execution (request_id, executed_by, idempotency_key, outcome, result, executed_at) VALUES (:r, :e, :k, 'APPLIED', CAST(:x AS jsonb), :n)"),
        {"r": uuid.UUID(request_id), "e": uuid.UUID(executor), "k": idempotency_key, "x": _json(result), "n": now},
    )  # fmt: skip
    await conn.execute(text("UPDATE commercial.finance_request SET state = 'APPLIED', result = CAST(:x AS jsonb) WHERE request_id = :r"),
                       {"r": uuid.UUID(request_id), "x": _json(result)})  # fmt: skip
    await log_change(
        conn,
        kind="FINANCE_REQUEST_APPLIED",
        ref=request_id,
        tenant_id=str(r["tenant_id"]),
        actor=executor,
        detail=result,
    )
    return _view(await _load(conn, request_id), approvals)


async def _apply(conn: AsyncConnection, r: Any, executor: str, now: datetime) -> dict[str, Any]:
    if r["kind"] == Kind.CREDIT_NOTE.value:
        async with in_tenant(conn, str(r["tenant_id"])):
            note = await invoicing.credit_note(
                conn, str(r["invoice_id"]), amount=Decimal(r["amount_minor"]) / 100, reason_code=f"FINANCE_{r['category']}",
                reverses_line=None, issuer=executor, now=now,
            )  # fmt: skip
        disposition = r["snapshot"].get("disposition")
        return {
            "credit_note": note.get("number"),
            "credit_note_id": note.get("credit_note_id"),
            "disposition": disposition,
            # a refund is its own decision (ZID-COM-CL-005): this note records the credit and never moves money by itself
            "refund": "SEPARATE_DECISION_REQUIRED" if disposition == "REFUND" else None,
        }
    ends = datetime.fromisoformat(r["snapshot"]["ends_at"])
    pid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.finance_pilot_credit (pilot_credit_id, request_id, tenant_id, currency, amount_minor, starts_at, ends_at, created_at) "
            "VALUES (:p, :r, :t, :c, :a, :s, :e, :n)"
        ),
        {"p": pid, "r": r["request_id"], "t": r["tenant_id"], "c": r["currency"], "a": r["amount_minor"], "s": now, "e": ends, "n": now},
    )  # fmt: skip
    return {
        "pilot_credit_id": str(pid),
        "classification": "PILOT_NON_BILLABLE",
        "ends_at": ends.isoformat(),
    }


async def get_request(
    conn: AsyncConnection, request_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    r = await _lapse(conn, await _load(conn, request_id, lock=True), now or datetime.now(UTC))
    return _view(r, await _approvals(conn, request_id))


async def list_requests(
    conn: AsyncConnection, *, state: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT * FROM commercial.finance_request WHERE (CAST(:s AS text) IS NULL OR state = CAST(:s AS text)) ORDER BY created_at DESC LIMIT :n"),
            {"s": state, "n": limit},
        )
    ).mappings().all()  # fmt: skip
    return [_view(r, []) for r in rows]


async def lapse_due(conn: AsyncConnection, now: datetime) -> int:
    """The worker's pass: every undecided request and unrun approval that has lapsed becomes EXPIRED."""
    rows = (
        await conn.execute(
            text(
                "SELECT request_id FROM commercial.finance_request WHERE (state = 'PENDING_APPROVAL' AND expires_at <= :n) "
                "OR (state = 'APPROVED' AND approval_expires_at <= :n)"
            ),
            {"n": now},
        )
    ).all()
    for row in rows:
        await _lapse(conn, await _load(conn, str(row.request_id), lock=True), now)
    return len(rows)


async def overview(
    conn: AsyncConnection, principal: str, now: datetime | None = None
) -> dict[str, Any]:
    """F01: what needs action, why and who owns it. Counts are queue measurements as of `as_of` (not revenue or cash); a request is "mine" only when
    I hold a role it still needs, did not ask for it and have not signed it."""
    now = now or datetime.now(UTC)
    await lapse_due(conn, now)
    rows = (
        await conn.execute(text("SELECT request_id, kind, state, required_roles, requested_by, expires_at, approval_expires_at FROM commercial.finance_request WHERE state IN ('PENDING_APPROVAL', 'APPROVED', 'APPLICATION_FAILED', 'APPLYING')"))
    ).mappings().all()  # fmt: skip
    mine: list[str] = []
    soon = timedelta(days=3)
    counts = {
        "pending_approval": 0,
        "approved_unexecuted": 0,
        "failed_application": 0,
        "expiring_soon": 0,
    }
    for r in rows:
        state = r["state"]
        if state == "PENDING_APPROVAL":
            counts["pending_approval"] += 1
            signed = {
                str(a["principal_id"]): a["role"]
                for a in await _approvals(conn, str(r["request_id"]))
            }
            done = set(signed.values())
            need = [role for role in r["required_roles"] if role not in done]
            if str(r["requested_by"]) != principal and principal not in signed:
                for role in need:
                    if await holds(conn, principal, role, now):
                        mine.append(str(r["request_id"]))
                        break
            if r["expires_at"] - now <= soon:
                counts["expiring_soon"] += 1
        elif state == "APPROVED":
            counts["approved_unexecuted"] += 1
            if r["approval_expires_at"] and r["approval_expires_at"] - now <= soon:
                counts["expiring_soon"] += 1
        elif state == "APPLICATION_FAILED":
            counts["failed_application"] += 1
    events = (
        await conn.execute(
            text(
                "SELECT count(*) FROM commercial.stripe_event WHERE outcome IN ('NEEDS_REVIEW', 'UNMATCHED') AND resolved_at IS NULL"
            )
        )
    ).scalar_one()
    policy = await policy_state(conn)
    exceptions = []
    if not policy["effective"]:
        exceptions.append(
            {
                "key": "POLICY_INACTIVE",
                "severity": "warn",
                "text": "The finance policy is not signed by the Founder and the CFO: in a deployed environment every approval stays disabled.",
            }
        )
    if counts["failed_application"]:
        exceptions.append(
            {
                "key": "APPLICATION_FAILED",
                "severity": "danger",
                "text": f"{counts['failed_application']} approved request(s) failed to apply and can be retried by a Finance Executor.",
            }
        )
    return {
        "as_of": now.isoformat(), "counts": {**counts, "payment_events_open": int(events)}, "mine": mine,
        "policy": {"version": policy["version"], "effective": policy["effective"], "awaiting": policy["awaiting"]}, "exceptions": exceptions,
        "definitions": "Counts are queue measurements as of the time shown. They are not revenue, gross merchandise value or bank cash.",
    }  # fmt: skip

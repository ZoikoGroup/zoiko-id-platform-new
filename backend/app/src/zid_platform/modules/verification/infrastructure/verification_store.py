"""Verification profiles, adapters, sessions, assertions, review and reliance in PostgreSQL.

ZID-DATA-001 §6; ZID-VERIFY-001 §3-5, §7, §9, §16-17, §22. Inside the caller's tenant transaction
(RLS). Claim values are stored only as commitments; provider payloads are not stored at all
beyond the normalized facts a pending manual review needs.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain.verification import (
    CIRCUIT_OPEN,
    CIRCUIT_THRESHOLD,
    ClaimResult,
    HeldAssertion,
    Profile,
    SessionStatus,
    VerificationError,
)


def commitment(*parts: str) -> bytes:
    return hashlib.sha256("\x00".join(parts).encode()).digest()


# ------------------------------------------------------------------------------------- profiles


async def create_profile(
    conn: AsyncConnection, *, tenant_id: str, name: str, level: str, requirements: dict[str, Any]
) -> tuple[str, int]:
    version = (
        await conn.execute(
            text(
                "SELECT coalesce(max(profile_version), 0) + 1 FROM verify.assurance_profile "
                "WHERE profile_name = :n"
            ),
            {"n": name},
        )
    ).scalar_one()
    pid = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO verify.assurance_profile (assurance_profile_id, tenant_id, profile_name, "
            "profile_version, level, requirements_json) VALUES (:id, :t, :n, :v, :l, CAST(:r AS jsonb))"
        ),
        {
            "id": uuid.UUID(pid),
            "t": uuid.UUID(tenant_id),
            "n": name,
            "v": version,
            "l": level,
            "r": json.dumps(requirements),
        },
    )
    return pid, int(version)


async def load_profile(conn: AsyncConnection, profile_id: str) -> Profile:
    try:
        pid = uuid.UUID(profile_id)
    except ValueError as e:
        raise VerificationError("PROFILE_NOT_FOUND") from e
    row = (
        await conn.execute(
            text(
                "SELECT assurance_profile_id, tenant_id, profile_name, profile_version, level, "
                "requirements_json, status FROM verify.assurance_profile WHERE assurance_profile_id = :p"
            ),
            {"p": pid},
        )
    ).first()
    if row is None:
        raise VerificationError("PROFILE_NOT_FOUND")
    return Profile.from_json(
        str(row.assurance_profile_id),
        str(row.tenant_id),
        row.profile_name,
        row.profile_version,
        row.level,
        row.requirements_json,
        row.status,
    )


async def list_profiles(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT assurance_profile_id, profile_name, profile_version, level, status, "
                "requirements_json -> 'jurisdictions' AS jurisdictions FROM verify.assurance_profile "
                "ORDER BY profile_name, profile_version"
            )
        )
    ).all()
    return [
        {
            "profile_id": str(r.assurance_profile_id),
            "name": r.profile_name,
            "version": r.profile_version,
            "level": r.level,
            "status": r.status,
            "jurisdictions": r.jurisdictions,
        }
        for r in rows
    ]


async def activate_profile(conn: AsyncConnection, profile: Profile, now: datetime) -> None:
    """One ACTIVE version per profile name: the previous one retires."""
    await conn.execute(
        text(
            "UPDATE verify.assurance_profile SET status = 'RETIRED' WHERE profile_name = :n "
            "AND status = 'ACTIVE'"
        ),
        {"n": profile.name},
    )
    result = await conn.execute(
        text(
            "UPDATE verify.assurance_profile SET status = 'ACTIVE', effective_at = :now "
            "WHERE assurance_profile_id = :p AND status = 'DRAFT'"
        ),
        {"p": uuid.UUID(profile.profile_id), "now": now},
    )
    if result.rowcount != 1:
        raise VerificationError("PROFILE_NOT_DRAFT")


# ------------------------------------------------------------------------------------- adapters

_ACOLS = (
    "adapter_id, tenant_id, provider, mapping_version, submit_endpoint, callback_jwks, "
    "callback_algorithms, equivalence_group, supported_evidence, jurisdictions, status, "
    "consecutive_failures, open_until"
)


async def create_adapter(conn: AsyncConnection, a: dict[str, Any]) -> None:
    await conn.execute(
        text(
            f"INSERT INTO verify.provider_adapter ({_ACOLS}) VALUES (:adapter_id, :tenant_id, "
            ":provider, :mapping_version, :submit_endpoint, CAST(:callback_jwks AS jsonb), "
            ":callback_algorithms, :equivalence_group, :supported_evidence, :jurisdictions, "
            "'ACTIVE', 0, NULL)"
        ),
        {
            **a,
            "tenant_id": uuid.UUID(a["tenant_id"]),
            "callback_jwks": json.dumps(a["callback_jwks"]),
        },
    )


def _adapter(r: object) -> dict[str, Any]:
    m = dict(r._mapping)  # type: ignore[attr-defined]
    m["tenant_id"] = str(m["tenant_id"])
    return m


async def load_adapter(conn: AsyncConnection, adapter_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(f"SELECT {_ACOLS} FROM verify.provider_adapter WHERE adapter_id = :a"),
            {"a": adapter_id},
        )
    ).first()
    return _adapter(row) if row else None


async def list_adapters(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (await conn.execute(text(f"SELECT {_ACOLS} FROM verify.provider_adapter"))).all()
    return [_adapter(r) for r in rows]


def adapter_view(a: dict[str, Any]) -> dict[str, Any]:
    """Administrative view: no callback keys (they are pinned trust, not display data)."""
    return {
        "adapter_id": a["adapter_id"],
        "provider": a["provider"],
        "mapping_version": a["mapping_version"],
        "submit_endpoint": a["submit_endpoint"],
        "callback_algorithms": list(a["callback_algorithms"]),
        "equivalence_group": a["equivalence_group"],
        "supported_evidence": list(a["supported_evidence"]),
        "jurisdictions": list(a["jurisdictions"]),
        "status": a["status"],
        "consecutive_failures": a["consecutive_failures"],
        "open_until": a["open_until"].isoformat() if a["open_until"] else None,
    }


async def adapter_failed(conn: AsyncConnection, adapter_id: str, now: datetime) -> None:
    """Circuit breaker (FR-VER-006): N consecutive failures open the circuit for a while."""
    await conn.execute(
        text(
            "UPDATE verify.provider_adapter SET consecutive_failures = consecutive_failures + 1, "
            "open_until = CASE WHEN consecutive_failures + 1 >= :n THEN :until ELSE open_until END, "
            "status = CASE WHEN consecutive_failures + 1 >= :n AND status = 'ACTIVE' THEN 'DEGRADED' "
            "ELSE status END WHERE adapter_id = :a"
        ),
        {"a": adapter_id, "n": CIRCUIT_THRESHOLD, "until": now + CIRCUIT_OPEN},
    )


async def adapter_succeeded(conn: AsyncConnection, adapter_id: str) -> None:
    await conn.execute(
        text(
            "UPDATE verify.provider_adapter SET consecutive_failures = 0, open_until = NULL, "
            "status = CASE WHEN status = 'DEGRADED' THEN 'ACTIVE' ELSE status END WHERE adapter_id = :a"
        ),
        {"a": adapter_id},
    )


async def set_adapter_status(conn: AsyncConnection, adapter_id: str, status: str) -> bool:
    result = await conn.execute(
        text(
            "UPDATE verify.provider_adapter SET status = :s WHERE adapter_id = :a AND status <> 'RETIRED'"
        ),
        {"a": adapter_id, "s": status},
    )
    return result.rowcount == 1


# ------------------------------------------------------------------------------------- sessions


@dataclass(frozen=True, slots=True)
class VSession:
    session_id: str
    tenant_id: str
    profile_id: str
    subject_principal_id: str
    purpose: str
    jurisdiction: str
    status: SessionStatus
    adapter_id: str | None
    provider_transaction_ref: str | None
    attempted_adapters: tuple[str, ...]
    outcome_code: str | None
    pending_result: dict[str, Any] | None
    row_version: int
    created_at: datetime
    expires_at: datetime


_SCOLS = (
    "session_id, tenant_id, assurance_profile_id, subject_principal_id, purpose, jurisdiction, "
    "status, adapter_id, provider_transaction_ref, attempted_adapters, outcome_code, pending_result, "
    "row_version, created_at, expires_at"
)


def _session(r: object) -> VSession:
    m = r._mapping  # type: ignore[attr-defined]
    return VSession(
        session_id=str(m["session_id"]),
        tenant_id=str(m["tenant_id"]),
        profile_id=str(m["assurance_profile_id"]),
        subject_principal_id=str(m["subject_principal_id"]),
        purpose=m["purpose"],
        jurisdiction=m["jurisdiction"],
        status=SessionStatus(m["status"]),
        adapter_id=m["adapter_id"],
        provider_transaction_ref=m["provider_transaction_ref"],
        attempted_adapters=tuple(m["attempted_adapters"]),
        outcome_code=m["outcome_code"],
        pending_result=m["pending_result"],
        row_version=m["row_version"],
        created_at=m["created_at"],
        expires_at=m["expires_at"],
    )


async def create_session(conn: AsyncConnection, s: VSession, supersedes: str | None = None) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.verification_session (session_id, tenant_id, assurance_profile_id, "
            "subject_principal_id, purpose, jurisdiction, status, created_at, updated_at, expires_at, "
            "supersedes_session_id) VALUES (:id, :t, :p, :s, :pu, :j, :st, :c, :c, :exp, :sup)"
        ),
        {
            "id": uuid.UUID(s.session_id),
            "t": uuid.UUID(s.tenant_id),
            "p": uuid.UUID(s.profile_id),
            "s": uuid.UUID(s.subject_principal_id),
            "pu": s.purpose,
            "j": s.jurisdiction,
            "st": s.status.value,
            "c": s.created_at,
            "exp": s.expires_at,
            "sup": uuid.UUID(supersedes) if supersedes else None,
        },
    )


async def load_session(conn: AsyncConnection, session_id: str, *, lock: bool = False) -> VSession:
    try:
        sid = uuid.UUID(session_id)
    except ValueError as e:
        raise VerificationError("VERIFICATION_NOT_FOUND") from e
    row = (
        await conn.execute(
            text(
                f"SELECT {_SCOLS} FROM verify.verification_session WHERE session_id = :s"
                + (" FOR UPDATE" if lock else "")
            ),
            {"s": sid},
        )
    ).first()
    if row is None:
        raise VerificationError("VERIFICATION_NOT_FOUND")
    return _session(row)


async def session_by_ref(conn: AsyncConnection, adapter_id: str, ref: str) -> VSession | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_SCOLS} FROM verify.verification_session WHERE adapter_id = :a "
                "AND provider_transaction_ref = :r FOR UPDATE"
            ),
            {"a": adapter_id, "r": ref},
        )
    ).first()
    return _session(row) if row else None


async def update_session(
    conn: AsyncConnection,
    s: VSession,
    *,
    status: SessionStatus,
    now: datetime,
    adapter_id: str | None = None,
    ref: str | None = None,
    attempted: tuple[str, ...] | None = None,
    outcome: str | None = None,
    pending: dict[str, Any] | None = None,
    basis: dict[str, Any] | None = None,
) -> VSession:
    result = await conn.execute(
        text(
            "UPDATE verify.verification_session SET status = :st, updated_at = :now, "
            "adapter_id = coalesce(:a, adapter_id), provider_transaction_ref = coalesce(:r, "
            "provider_transaction_ref), attempted_adapters = coalesce(:att, attempted_adapters), "
            "outcome_code = coalesce(:o, outcome_code), pending_result = CAST(:pend AS jsonb), "
            "decision_basis = coalesce(CAST(:basis AS jsonb), decision_basis), "
            "row_version = row_version + 1 WHERE session_id = :id AND row_version = :v"
        ),
        {
            "id": uuid.UUID(s.session_id),
            "v": s.row_version,
            "st": status.value,
            "now": now,
            "a": adapter_id,
            "r": ref,
            "att": list(attempted) if attempted is not None else None,
            "o": outcome,
            "pend": json.dumps(pending) if pending is not None else None,
            "basis": json.dumps(basis) if basis is not None else None,
        },
    )
    if result.rowcount != 1:
        raise VerificationError("CONCURRENT_MODIFICATION")
    return await load_session(conn, s.session_id)


async def decision_basis(conn: AsyncConnection, session_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text("SELECT decision_basis FROM verify.verification_session WHERE session_id = :s"),
            {"s": uuid.UUID(session_id)},
        )
    ).first()
    return dict(row.decision_basis) if row and row.decision_basis else None


async def record_callback(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    adapter_id: str,
    event_id: str,
    body: bytes,
    outcome: str,
) -> str:
    """NEW, DUPLICATE (same content) or CONFLICT (same event id, different content)."""
    content = hashlib.sha256(body).digest()
    inserted = await conn.execute(
        text(
            "INSERT INTO verify.provider_callback (tenant_id, adapter_id, provider_event_id, "
            "content_hash, outcome) VALUES (:t, :a, :e, :h, :o) ON CONFLICT DO NOTHING"
        ),
        {"t": uuid.UUID(tenant_id), "a": adapter_id, "e": event_id, "h": content, "o": outcome},
    )
    if inserted.rowcount == 1:
        return "NEW"
    stored = (
        await conn.execute(
            text(
                "SELECT content_hash FROM verify.provider_callback WHERE adapter_id = :a "
                "AND provider_event_id = :e"
            ),
            {"a": adapter_id, "e": event_id},
        )
    ).scalar_one()
    return "DUPLICATE" if bytes(stored) == content else "CONFLICT"


# ----------------------------------------------------------------------------------- assertions


async def insert_assertions(
    conn: AsyncConnection,
    s: VSession,
    profile: Profile,
    claims: tuple[ClaimResult, ...],
    *,
    adapter_id: str,
    reasons: tuple[str, ...],
    evidence_commitment: str | None,
    now: datetime,
) -> list[str]:
    ids = []
    for c in claims:
        aid = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO verify.verification_assertion (assertion_id, tenant_id, "
                "subject_principal_id, session_id, claim_type, claim_value_commitment, "
                "verification_method, evidence_types, provider_id, provider_reference, "
                "assurance_profile_id, assurance_level, jurisdiction, evidence_commitment, status, "
                "reason_codes, performed_at, valid_until) VALUES (:id, :t, :s, :sess, :ct, :cv, :m, "
                ":ev, :prov, :ref, :p, :lvl, :j, :ec, 'VALID', :rc, :now, :until)"
            ),
            {
                "id": uuid.UUID(aid),
                "t": uuid.UUID(s.tenant_id),
                "s": uuid.UUID(s.subject_principal_id),
                "sess": uuid.UUID(s.session_id),
                "ct": c.claim,
                "cv": commitment(aid, c.claim, c.value),
                "m": c.method,
                "ev": list(c.evidence),
                "prov": adapter_id,
                "ref": s.provider_transaction_ref,
                "p": uuid.UUID(profile.profile_id),
                "lvl": profile.level,
                "j": s.jurisdiction,
                "ec": hashlib.sha256((evidence_commitment or "").encode()).digest()
                if evidence_commitment
                else None,
                "rc": list(reasons),
                "now": now,
                "until": now + profile.freshness,
            },
        )
        ids.append(aid)
    return ids


async def assertions_for(conn: AsyncConnection, principal_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT a.assertion_id, a.claim_type, a.verification_method, a.evidence_types, "
                "a.assurance_level, a.status, a.performed_at, a.valid_until, a.jurisdiction, "
                "a.reason_codes, a.claim_value_commitment, p.requirements_json -> 'reliance_purposes' AS purposes "
                "FROM verify.verification_assertion a JOIN verify.assurance_profile p "
                "USING (assurance_profile_id) WHERE a.subject_principal_id = :s "
                "ORDER BY a.performed_at DESC"
            ),
            {"s": uuid.UUID(principal_id)},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


def held(rows: list[dict[str, Any]]) -> list[HeldAssertion]:
    return [
        HeldAssertion(
            assertion_id=str(r["assertion_id"]),
            claim_type=r["claim_type"],
            assurance_level=r["assurance_level"],
            status=r["status"],
            valid_until=r["valid_until"],
            jurisdiction=r["jurisdiction"],
            profile_purposes=frozenset(r["purposes"] or ()),
        )
        for r in rows
    ]


async def revoke_assertions(
    conn: AsyncConnection, *, principal_id: str | None, assertion_id: str | None, now: datetime
) -> list[str]:
    where = "status = 'VALID' AND " + (
        "assertion_id = :a" if assertion_id else "subject_principal_id = :p"
    )
    rows = (
        await conn.execute(
            text(
                f"UPDATE verify.verification_assertion SET status = 'REVOKED', revoked_at = :now "
                f"WHERE {where} RETURNING assertion_id"
            ),
            {
                "now": now,
                "a": uuid.UUID(assertion_id) if assertion_id else None,
                "p": uuid.UUID(principal_id) if principal_id else None,
            },
        )
    ).all()
    return [str(r.assertion_id) for r in rows]


async def expire_due(conn: AsyncConnection, now: datetime) -> list[tuple[str, str]]:
    rows = (
        await conn.execute(
            text(
                "UPDATE verify.verification_assertion SET status = 'EXPIRED' WHERE status = 'VALID' "
                "AND valid_until <= :now RETURNING assertion_id, subject_principal_id"
            ),
            {"now": now},
        )
    ).all()
    return [(str(r.assertion_id), str(r.subject_principal_id)) for r in rows]


# --------------------------------------------------------------------------------------- review


async def open_case(
    conn: AsyncConnection, s: VSession, *, approvals: int, reasons: tuple[str, ...]
) -> str:
    cid = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO verify.review_case (case_id, tenant_id, session_id, required_approvals, "
            "reason_codes) VALUES (:id, :t, :s, :n, :r)"
        ),
        {
            "id": uuid.UUID(cid),
            "t": uuid.UUID(s.tenant_id),
            "s": uuid.UUID(s.session_id),
            "n": approvals,
            "r": list(reasons),
        },
    )
    return cid


async def load_case(conn: AsyncConnection, case_id: str) -> dict[str, Any]:
    try:
        cid = uuid.UUID(case_id)
    except ValueError as e:
        raise VerificationError("REVIEW_CASE_NOT_FOUND") from e
    row = (
        await conn.execute(
            text("SELECT * FROM verify.review_case WHERE case_id = :c FOR UPDATE"), {"c": cid}
        )
    ).first()
    if row is None:
        raise VerificationError("REVIEW_CASE_NOT_FOUND")
    return dict(row._mapping)


async def add_decision(
    conn: AsyncConnection,
    *,
    case: dict[str, Any],
    reviewer: str,
    decision: str,
    reasons: list[str],
    now: datetime,
) -> list[str]:
    """Records one decision per reviewer; returns all decisions so far."""
    inserted = await conn.execute(
        text(
            "INSERT INTO verify.review_decision (case_id, tenant_id, reviewer_principal_id, decision, "
            "reason_codes, decided_at) VALUES (:c, :t, :r, :d, :rc, :now) ON CONFLICT DO NOTHING"
        ),
        {
            "c": case["case_id"],
            "t": case["tenant_id"],
            "r": uuid.UUID(reviewer),
            "d": decision,
            "rc": reasons,
            "now": now,
        },
    )
    if inserted.rowcount != 1:
        raise VerificationError("REVIEW_DUPLICATE_REVIEWER")
    rows = (
        await conn.execute(
            text("SELECT decision FROM verify.review_decision WHERE case_id = :c"),
            {"c": case["case_id"]},
        )
    ).all()
    return [r.decision for r in rows]


async def close_case(conn: AsyncConnection, case_id: Any, status: str, now: datetime) -> None:
    await conn.execute(
        text("UPDATE verify.review_case SET status = :s, closed_at = :now WHERE case_id = :c"),
        {"c": case_id, "s": status, "now": now},
    )


# ------------------------------------------------------------------------------------- reliance


async def record_reliance(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    client_id: str,
    purpose: str,
    required_level: str,
    jurisdiction: str | None,
    assertion_ids: tuple[str, ...],
    outcome: str,
    reasons: tuple[str, ...],
    now: datetime,
) -> str:
    did = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO verify.reliance_decision (reliance_decision_id, tenant_id, "
            "subject_principal_id, relying_client_id, purpose, required_level, jurisdiction, "
            "assertion_ids, outcome, reason_codes, decided_at) VALUES (:id, :t, :p, :c, :pu, :l, :j, "
            ":a, :o, :r, :now)"
        ),
        {
            "id": uuid.UUID(did),
            "t": uuid.UUID(tenant_id),
            "p": uuid.UUID(principal_id),
            "c": client_id,
            "pu": purpose,
            "l": required_level,
            "j": jurisdiction,
            "a": [uuid.UUID(a) for a in assertion_ids],
            "o": outcome,
            "r": list(reasons),
            "now": now,
        },
    )
    return did

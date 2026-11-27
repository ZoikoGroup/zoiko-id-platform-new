"""Proof documents for a company claim (WF-13B section 06, AT-12).

`add_proof` keeps a fixed order so nothing sensitive happens before it is allowed:
    1. the claim exists for this tenant;  2. a repeat of the same key (or the same content) is the same proof;
    3. the file is judged (type from its own bytes, size, kind, no active content, limit);
    4. the tenant's residency says this cell may hold it, BEFORE the file is scanned or stored;
    5. the file is scanned (nothing unscanned is stored in a deployed environment);  6. it is encrypted into the evidence vault;
    7. the record, an event and an audit record are written.
A refused file leaves an audit record (hash and reason, never the content) and nothing else. A proof never changes a company's status.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_evidence import hash_bytes
from zid_outbox import emit

from zid_platform.modules.organization.domain.kyb import KybError
from zid_platform.modules.organization.domain.proof import (
    ProofError,
    ProofPolicy,
    check_proof,
    sanitize_name,
)
from zid_platform.modules.organization.domain.proof_events import (
    PROOF_ACCESS_DENIED,
    PROOF_READ,
    PROOF_REJECTED,
)
from zid_platform.modules.organization.infrastructure import kyb_store, proof_store
from zid_platform.modules.organization.infrastructure.proof_scan import Scanner, ScanStatus

SANDBOX_ENVIRONMENTS = frozenset({"local", "test"})
StorageGate = Callable[
    [], Awaitable[None]
]  # raises ProofError unless this cell may hold the tenant's restricted data


@dataclass(frozen=True, slots=True)
class ProofView:
    proof_id: str
    case_id: str
    kind: str
    media_type: str
    size_bytes: int
    content_hash: str
    label: str
    scan_status: str
    created_at: datetime


def view(p: proof_store.ProofRow) -> ProofView:
    return ProofView(
        p.proof_id,
        p.case_id,
        p.kind,
        p.media_type,
        p.size_bytes,
        p.content_hash,
        p.label,
        p.scan_status,
        p.created_at,
    )


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    event: str,
    outcome: str,
    case_id: str,
    actor: str,
    reason: str | None,
    details: dict[str, Any],
    now: datetime,
) -> None:
    from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit

    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=event,
            actor_type="HUMAN",
            action=event.rsplit(".", 1)[-1],
            target_type="kyb_case",
            target_ref=case_id,
            outcome=outcome,
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor,
            reason_code=reason,
            source_service="organization",
            source_event_id=str(uuid.uuid4()),
            details=details,
        ),
    )


async def record_case_unreachable(
    conn: AsyncConnection, tenant_id: str, *, case_id: str, actor: str, attempt: str, now: datetime
) -> None:
    """T12: the caller asked for a claim that is not theirs to see (another organization's, or none at all: Row-Level Security makes
    the two indistinguishable, and the answer stays the same 404 for both). The attempt is audited in the CALLER's own tenant. The id
    the caller supplied and the kind of attempt only; nothing about the other organization is known or recorded."""
    await _audit(
        conn, tenant_id, event=PROOF_ACCESS_DENIED, outcome="DENIED", case_id=case_id, actor=actor, reason="CASE_NOT_VISIBLE",
        details={"attempt": attempt, "reason": "CASE_NOT_VISIBLE"}, now=now,
    )  # fmt: skip


async def add_proof(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    *,
    content: bytes,
    declared_type: str | None,
    kind: str,
    filename: str | None,
    idempotency_key: str,
    uploaded_by: str,
    policy: ProofPolicy,
    scanner: Scanner | None,
    environment: str,
    storage_gate: StorageGate,
    now: datetime,
) -> tuple[ProofView, bool]:
    """Returns (proof, created). A repeat is (the same proof, False)."""
    from zid_platform.modules.evidence.ports import store_object

    claim = await kyb_store.load_case(
        conn, tenant_id, case_id
    )  # CASE_NOT_FOUND for another tenant's claim, same as one that never was
    if claim.closed_at is not None:
        raise KybError("CLAIM_CLOSED", claim.closed_reason or "")
    content_hash = str(hash_bytes(content, alg="sha-256"))
    again = await proof_store.by_key(conn, tenant_id, idempotency_key)
    if again is not None:
        if again.case_id != case_id or again.content_hash != content_hash:
            raise ProofError(
                "PROOF_IDEMPOTENCY_CONFLICT", "That key was used for a different document."
            )
        return view(again), False

    async def refuse(error: ProofError) -> ProofError:
        await _audit(
            conn, tenant_id, event=PROOF_REJECTED, outcome="DENIED", case_id=case_id, actor=uploaded_by, reason=error.code,
            details={"reason": error.code, "size_bytes": len(content), "content_hash": content_hash, "kind": kind}, now=now,
        )  # fmt: skip
        return error

    try:
        media = check_proof(content, declared_type=declared_type, kind=kind, policy=policy)
    except ProofError as e:
        raise await refuse(e) from e
    same = await proof_store.by_content(conn, tenant_id, case_id, content_hash)
    if same is not None:
        return view(same), False
    if await proof_store.count(conn, tenant_id, case_id) >= policy.per_case_limit:
        raise await refuse(
            ProofError(
                "PROOF_LIMIT_REACHED", f"A claim can hold {policy.per_case_limit} documents."
            )
        )

    try:
        await (
            storage_gate()
        )  # the residency check comes before the file goes anywhere, scanner included
    except ProofError as e:
        raise await refuse(e) from e

    if scanner is None:
        if environment not in SANDBOX_ENVIRONMENTS:
            raise ProofError(
                "PROOF_SCAN_UNAVAILABLE",
                "Uploads are paused until the file scanner is available. Nothing was stored.",
            )
        scan = ScanStatus.NOT_SCANNED
    else:
        scan = await scanner.scan(content)
        if scan is ScanStatus.INFECTED:
            raise await refuse(
                ProofError("PROOF_INFECTED", "That file was refused by the security scan.")
            )
        if scan is not ScanStatus.CLEAN:
            raise ProofError(
                "PROOF_SCAN_UNAVAILABLE",
                "The file could not be scanned right now. Nothing was stored; try again.",
            )

    # How long it is kept: the published market pack's schedule when there is one, otherwise the proof policy's own period (unset: kept until the owners decide). The pack is the signed one.
    from zid_platform.modules.organization.application.kyb_market_policy import retention_days_for

    days = (
        await retention_days_for(conn, claim.jurisdiction, claim.entity_form, now=now)
        or policy.retention_days
    )
    object_id, _ = await store_object(
        conn,
        tenant_id,
        content=content,
        object_type="kyb_proof",
        classification="D3",
        retention_class="kyb_proof",
        expires_at=now + timedelta(days=days) if days else None,
    )
    proof_id = str(uuid.uuid4())
    label = sanitize_name(filename)
    created = await proof_store.insert(
        conn, tenant_id, proof_id=proof_id, case_id=case_id, kind=kind, media_type=media, size_bytes=len(content),
        content_hash=content_hash, object_id=object_id, label=label, scan_status=scan.value, policy_version=policy.version,
        idempotency_key=idempotency_key, uploaded_by=uploaded_by, now=now,
    )  # fmt: skip
    if not created:  # a concurrent upload of the same key or content won: this is the same proof
        winner = await proof_store.by_key(
            conn, tenant_id, idempotency_key
        ) or await proof_store.by_content(conn, tenant_id, case_id, content_hash)
        if winner is None:
            raise ProofError("PROOF_CONFLICT", "Try again.")
        return view(winner), False
    data = {
        "case_id": case_id,
        "proof_id": proof_id,
        "kind": kind,
        "media_type": media,
        "size_bytes": len(content),
        "content_hash": content_hash,
        "scan_status": scan.value,
    }
    event_id = await emit(
        conn, name="organization.kyb.proof_added", tenant_id=tenant_id, aggregate_type="kyb_proof", aggregate_id=proof_id,
        aggregate_version=1, data=data,
    )  # fmt: skip
    await _audit(
        conn, tenant_id, event="organization.kyb.proof_added", outcome="SUCCESS", case_id=case_id, actor=uploaded_by, reason=None,
        details={**data, "source_event_id": event_id}, now=now,
    )  # fmt: skip
    stored = await proof_store.load(conn, tenant_id, case_id, proof_id)
    assert stored is not None
    return view(stored), True


async def claimed_company(conn: AsyncConnection, tenant_id: str, case_id: str) -> tuple[str, str]:
    """(legal name, registration number) as the claim states them: what an uploaded certificate is compared with."""
    case = await kyb_store.load_case(conn, tenant_id, case_id)
    return case.applicant_name, case.registration_number


async def list_proofs(conn: AsyncConnection, tenant_id: str, case_id: str) -> list[ProofView]:
    await kyb_store.load_case(conn, tenant_id, case_id)
    return [view(p) for p in await proof_store.for_case(conn, tenant_id, case_id)]


async def read_proof(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    proof_id: str,
    *,
    reader: str,
    now: datetime,
) -> tuple[ProofView, bytes]:
    """A reviewer opens a proof. Every read is an audit record naming who, what and when."""
    from zid_platform.modules.evidence.ports import read_object

    await kyb_store.load_case(conn, tenant_id, case_id)
    row = await proof_store.load(conn, tenant_id, case_id, proof_id)
    if row is None:
        raise ProofError("PROOF_NOT_FOUND", "No such document.")
    content = await read_object(conn, tenant_id, row.object_id)
    await _audit(
        conn, tenant_id, event=PROOF_READ, outcome="SUCCESS", case_id=case_id, actor=reader, reason=None,
        details={"proof_id": proof_id, "content_hash": row.content_hash}, now=now,
    )  # fmt: skip
    return view(row), content


async def keep_reading(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    proof_id: str,
    reading: dict[str, Any],
    now: datetime,
) -> None:
    """Keep the OCR reading of a stored certificate: it is what completes the claim's company check (CR-132)."""
    if reading.get("verdict") in ("CONSISTENT", "CONTRADICTS", "NOT_ENOUGH"):
        await proof_store.record_reading(
            conn, tenant_id, case_id, proof_id, reading=reading, now=now
        )


async def has_consistent_reading(conn: AsyncConnection, tenant_id: str, case_id: str) -> bool:
    return await proof_store.has_consistent_reading(conn, tenant_id, case_id)

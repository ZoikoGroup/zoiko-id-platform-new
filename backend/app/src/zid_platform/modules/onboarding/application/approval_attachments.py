"""Documents attached to an approval request (ZID-UX-WF-013C AT-12): scanned, expiring, scoped and outside logs.

`add` keeps the order of the company-claim proofs so nothing sensitive happens before it is allowed:
    1. a repeat of the same key (or the same content) is the same attachment;  2. the file is judged (type from its own bytes, size,
    kind, no active content, limit);  3. the organization's residency says this cell may hold it, BEFORE the file is scanned or stored;
    4. the file is scanned (nothing unscanned is stored in a deployed environment);  5. it is encrypted into the evidence vault;
    6. the record and an audit record are written.
Who may attach and who may open is decided by the caller from the approval service (`case_access`). Access ends when the request does:
no document is opened after the request's own expiry, whatever the vault still holds. A refused file leaves an audit record (hash and
reason, never the content) and nothing else. An attachment never changes what a request asks for, and no document or its content is
ever put in a log, an event or a URL.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_evidence import hash_bytes

from zid_platform.modules.evidence.ports import (
    AuditFacts,
    EvidenceClass,
    read_object,
    record_audit,
    store_object,
)
from zid_platform.modules.onboarding.infrastructure import attachment_store as store
from zid_platform.modules.organization.ports import (
    ProofError,
    ProofPolicy,
    Scanner,
    ScanStatus,
    check_proof,
    sanitize_name,
)

SANDBOX_ENVIRONMENTS = frozenset({"local", "test"})
StorageGate = Callable[
    [], Awaitable[None]
]  # raises ProofError unless this cell may hold the tenant's restricted data

ADDED = "approval.attachment.added"
REJECTED = "approval.attachment.rejected"
READ = "approval.attachment.read"


@dataclass(frozen=True, slots=True)
class AttachmentView:
    attachment_id: str
    kind: str
    media_type: str
    size_bytes: int
    content_hash: str
    label: str
    scan_status: str
    expires_at: datetime
    created_at: datetime


def view(a: store.AttachmentRow) -> AttachmentView:
    return AttachmentView(
        a.attachment_id,
        a.kind,
        a.media_type,
        a.size_bytes,
        a.content_hash,
        a.label,
        a.scan_status,
        a.expires_at,
        a.created_at,
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
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=event,
            actor_type="HUMAN",
            action=event.rsplit(".", 1)[-1],
            target_type="approval_case",
            target_ref=case_id,
            outcome=outcome,
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor,
            reason_code=reason,
            source_service="onboarding",
            source_event_id=str(uuid.uuid4()),
            details=details,
        ),
    )


async def add(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    *,
    case_expires_at: datetime,
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
) -> tuple[AttachmentView, bool]:
    """Returns (attachment, created). A repeat is (the same attachment, False)."""
    content_hash = str(hash_bytes(content, alg="sha-256"))
    again = await store.by_key(conn, tenant_id, idempotency_key)
    if again is not None:
        if again.case_id != case_id or again.content_hash != content_hash:
            raise ProofError(
                "ATTACHMENT_IDEMPOTENCY_CONFLICT", "That key was used for a different document."
            )
        return view(again), False

    async def refuse(error: ProofError) -> ProofError:
        await _audit(
            conn, tenant_id, event=REJECTED, outcome="DENIED", case_id=case_id, actor=uploaded_by, reason=error.code,
            details={"reason": error.code, "size_bytes": len(content), "content_hash": content_hash, "kind": kind}, now=now,
        )  # fmt: skip
        return error

    try:
        media = check_proof(content, declared_type=declared_type, kind=kind, policy=policy)
    except ProofError as e:
        raise await refuse(ProofError(e.code.replace("PROOF_", "ATTACHMENT_", 1), e.message)) from e
    same = await store.by_content(conn, tenant_id, case_id, content_hash)
    if same is not None:
        return view(same), False
    if await store.count(conn, tenant_id, case_id) >= policy.per_case_limit:
        raise await refuse(
            ProofError(
                "ATTACHMENT_LIMIT_REACHED", f"A request can hold {policy.per_case_limit} documents."
            )
        )
    try:
        await (
            storage_gate()
        )  # the residency check comes before the file goes anywhere, scanner included
    except ProofError as e:
        raise await refuse(ProofError("ATTACHMENT_RESIDENCY_DENIED", e.message)) from e

    if scanner is None:
        if environment not in SANDBOX_ENVIRONMENTS:
            raise ProofError(
                "ATTACHMENT_SCAN_UNAVAILABLE",
                "Uploads are paused until the file scanner is available. Nothing was stored.",
            )
        scan = ScanStatus.NOT_SCANNED
    else:
        scan = await scanner.scan(content)
        if scan is ScanStatus.INFECTED:
            raise await refuse(
                ProofError("ATTACHMENT_INFECTED", "That file was refused by the security scan.")
            )
        if scan is not ScanStatus.CLEAN:
            raise ProofError(
                "ATTACHMENT_SCAN_UNAVAILABLE",
                "The file could not be scanned right now. Nothing was stored; try again.",
            )

    object_id, _ = await store_object(
        conn,
        tenant_id,
        content=content,
        object_type="approval_attachment",
        classification="D3",
        retention_class="approval_attachment",
        expires_at=now + timedelta(days=policy.retention_days) if policy.retention_days else None,
    )
    attachment_id = str(uuid.uuid4())
    created = await store.insert(
        conn, tenant_id, attachment_id=attachment_id, case_id=case_id, kind=kind, media_type=media, size_bytes=len(content),
        content_hash=content_hash, object_id=object_id, label=sanitize_name(filename), scan_status=scan.value,
        policy_version=policy.version, idempotency_key=idempotency_key, uploaded_by=uploaded_by, expires_at=case_expires_at, now=now,
    )  # fmt: skip
    if (
        not created
    ):  # a concurrent upload of the same key or content won: this is the same attachment
        winner = await store.by_key(conn, tenant_id, idempotency_key) or await store.by_content(
            conn, tenant_id, case_id, content_hash
        )
        if winner is None:
            raise ProofError("ATTACHMENT_CONFLICT", "Try again.")
        return view(winner), False
    await _audit(
        conn, tenant_id, event=ADDED, outcome="SUCCESS", case_id=case_id, actor=uploaded_by, reason=None,
        details={"attachment_id": attachment_id, "kind": kind, "media_type": media, "size_bytes": len(content),
                 "content_hash": content_hash, "scan_status": scan.value}, now=now,
    )  # fmt: skip
    stored = await store.load(conn, tenant_id, case_id, attachment_id)
    assert stored is not None
    return view(stored), True


async def listing(conn: AsyncConnection, tenant_id: str, case_id: str) -> list[AttachmentView]:
    return [view(a) for a in await store.for_case(conn, tenant_id, case_id)]


async def read(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    attachment_id: str,
    *,
    reader: str,
    now: datetime,
) -> tuple[AttachmentView, bytes]:
    """An approver opens a document. Access ends when the request expires, and every read is an audit record naming who and when."""
    row = await store.load(conn, tenant_id, case_id, attachment_id)
    if row is None:
        raise ProofError("ATTACHMENT_NOT_FOUND", "No such document.")
    if row.expires_at <= now:
        raise ProofError(
            "ATTACHMENT_EXPIRED", "Access to this document has ended with the request."
        )
    content = await read_object(conn, tenant_id, row.object_id)
    await _audit(
        conn, tenant_id, event=READ, outcome="SUCCESS", case_id=case_id, actor=reader, reason=None,
        details={"attachment_id": attachment_id, "content_hash": row.content_hash}, now=now,
    )  # fmt: skip
    return view(row), content

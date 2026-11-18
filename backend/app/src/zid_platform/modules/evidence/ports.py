"""Public Protocol interface of the evidence module. Other modules import ONLY this.

Producers (in their own transaction, so evidence commits iff the domain change does):
- `record_decision_receipt(conn, tenant, ReceiptFacts, floor=..., material=...)`  DecisionReceipt
- `audit_timeline(conn, target_type, target_ref)` one object's audit trail, codes and hashes only (CR-110)
- `record_audit(conn, tenant | None, AuditFacts)`                                  AuditRecord
Vault (DEC-F1):
- `actor_handle`, `store_object`, `read_object`, `erase_subject`, `is_held`
Replay: `register_replayer(decision_type, evaluator)` - the producing module supplies the pinned
evaluator for its decision type; evidence never re-implements domain logic.
Worker: `worker_pass` (sequence, checkpoint, anchor, retention sweep).
"""

from __future__ import annotations

from zid_platform.modules.evidence.application import trust as trust_model
from zid_platform.modules.evidence.application.active_auth import (
    verify as verify_active_authentication,
)
from zid_platform.modules.evidence.application.assessment import Assessment, assess
from zid_platform.modules.evidence.application.destinations import (
    keep_destination,
    read_destination,
)
from zid_platform.modules.evidence.application.dg2 import chip_photo
from zid_platform.modules.evidence.application.epassport import verify as verify_chip
from zid_platform.modules.evidence.application.issuer_hook import register_issuer_binding
from zid_platform.modules.evidence.application.provenance import (
    Node,
    add_edges,
    artifact,
    register_artifact,
)
from zid_platform.modules.evidence.application.receipts import (
    commitment,
    ingest_event,
    material_commitment,
    principal_handle,
    receipt_summary,
    record_audit,
    record_decision_receipt,
    sequence_pending,
)
from zid_platform.modules.evidence.application.replay import register_replayer
from zid_platform.modules.evidence.application.runtime import (
    EvidenceRuntime,
    configure_runtime,
    current_runtime,
    runtime_from_settings,
)
from zid_platform.modules.evidence.application.seals import SealCheck, check_signed_xml
from zid_platform.modules.evidence.application.vault import (
    ErasureResult,
    TenantErasure,
    actor_handle,
    erase_subject,
    erase_subject_ref,
    erase_tenant,
    hold_status,
    is_held,
    read_object,
    store_object,
    subject_ref,
)
from zid_platform.modules.evidence.application.worker import WorkerReport, worker_pass
from zid_platform.modules.evidence.domain.ledger import EvidenceClass, LedgerError
from zid_platform.modules.evidence.domain.receipts import AuditFacts, ReceiptFacts, ReplayResult
from zid_platform.modules.evidence.infrastructure.trust_config import trust_config

__all__ = [
    "Assessment",
    "AuditFacts",
    "ErasureResult",
    "EvidenceClass",
    "EvidenceRuntime",
    "LedgerError",
    "Node",
    "ReceiptFacts",
    "ReplayResult",
    "SealCheck",
    "TenantErasure",
    "WorkerReport",
    "actor_handle",
    "add_edges",
    "artifact",
    "assess",
    "check_signed_xml",
    "chip_photo",
    "commitment",
    "configure_runtime",
    "current_runtime",
    "erase_subject",
    "erase_subject_ref",
    "erase_tenant",
    "erase_uploads",
    "hold_status",
    "ingest_event",
    "is_held",
    "keep_destination",
    "material_commitment",
    "principal_handle",
    "purge_expired_uploads",
    "read_destination",
    "read_object",
    "receipt_summary",
    "record_audit",
    "record_decision_receipt",
    "register_artifact",
    "register_issuer_binding",
    "register_replayer",
    "runtime_from_settings",
    "sequence_pending",
    "store_object",
    "subject_ref",
    "trust_config",
    "trust_model",
    "unusable_evidence",
    "upload_summary",
    "uploads_by_subject",
    "verify_active_authentication",
    "verify_chip",
    "worker_pass",
]


async def unusable_evidence(conn, refs, now):  # type: ignore[no-untyped-def]
    """The evidence references that cannot back a check (unknown, not scanned clean, deleted or past retention). Empty means all can."""
    from zid_platform.modules.evidence.application.uploads import unusable

    return await unusable(conn, list(refs), now)


async def purge_expired_uploads(conn, now):  # type: ignore[no-untyped-def]
    """Erase the bytes of uploaded evidence whose retention has ended. Returns how many."""
    from zid_platform.modules.evidence.application.uploads import purge_expired

    return await purge_expired(conn, now)


async def uploads_by_subject(conn, subject):  # type: ignore[no-untyped-def]
    """The uploads held about one person (metadata rows, never bytes), for the privacy data map."""
    from zid_platform.modules.evidence.infrastructure.upload_store import by_subject

    return await by_subject(conn, subject)


async def upload_summary(conn, upload_id):  # type: ignore[no-untyped-def]
    """A readable summary of one upload for an access request: what, when, scan result and retention; never the bytes or a hash."""
    from sqlalchemy import text

    row = (
        (
            await conn.execute(
                text(
                    "SELECT declared_type, size_bytes, state, scan_state, created_at, expires_at, erased_at "
                    "FROM evidence.upload WHERE upload_id = CAST(:u AS uuid)"
                ),
                {"u": upload_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def erase_uploads(conn, tenant_id, subject):  # type: ignore[no-untyped-def]
    """Governed erasure of one person's uploads, bytes included. Returns how many rows went."""
    from zid_platform.modules.evidence.infrastructure.upload_store import delete_subject

    return await delete_subject(conn, tenant_id, subject)


async def audit_timeline(
    conn: object, target_type: str, target_ref: str
) -> list[dict[str, object]]:
    """One object's audit trail, oldest first: event, actor type, action, outcome, reason code, class, record hash and time (CR-110)."""
    from zid_platform.modules.evidence.infrastructure.receipt_store import audit_for_target

    rows = await audit_for_target(conn, target_type, target_ref)  # type: ignore[arg-type]
    return [{**dict(r._mapping), "occurred_at": r.occurred_at.isoformat()} for r in rows]

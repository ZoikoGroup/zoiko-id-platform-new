"""Public Protocol interface of the evidence module. Other modules import ONLY this.

Producers (in their own transaction, so evidence commits iff the domain change does):
- `record_decision_receipt(conn, tenant, ReceiptFacts, floor=..., material=...)`  DecisionReceipt
- `record_audit(conn, tenant | None, AuditFacts)`                                  AuditRecord
Vault (DEC-F1):
- `actor_handle`, `store_object`, `read_object`, `erase_subject`, `is_held`
Replay: `register_replayer(decision_type, evaluator)` - the producing module supplies the pinned
evaluator for its decision type; evidence never re-implements domain logic.
Worker: `worker_pass` (sequence, checkpoint, anchor, retention sweep).
"""

from __future__ import annotations

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

__all__ = [
    "AuditFacts",
    "ErasureResult",
    "EvidenceClass",
    "EvidenceRuntime",
    "LedgerError",
    "Node",
    "ReceiptFacts",
    "ReplayResult",
    "TenantErasure",
    "WorkerReport",
    "actor_handle",
    "add_edges",
    "artifact",
    "commitment",
    "configure_runtime",
    "current_runtime",
    "erase_subject",
    "erase_subject_ref",
    "erase_tenant",
    "hold_status",
    "ingest_event",
    "is_held",
    "material_commitment",
    "principal_handle",
    "read_object",
    "receipt_summary",
    "record_audit",
    "record_decision_receipt",
    "register_artifact",
    "register_replayer",
    "runtime_from_settings",
    "sequence_pending",
    "store_object",
    "subject_ref",
    "worker_pass",
]

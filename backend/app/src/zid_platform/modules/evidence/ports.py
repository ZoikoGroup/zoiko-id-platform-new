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

from zid_platform.modules.evidence.application.receipts import (
    commitment,
    ingest_event,
    material_commitment,
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
    actor_handle,
    erase_subject,
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
    "ReceiptFacts",
    "ReplayResult",
    "WorkerReport",
    "actor_handle",
    "commitment",
    "configure_runtime",
    "current_runtime",
    "erase_subject",
    "ingest_event",
    "is_held",
    "material_commitment",
    "read_object",
    "record_audit",
    "record_decision_receipt",
    "register_replayer",
    "runtime_from_settings",
    "sequence_pending",
    "store_object",
    "subject_ref",
    "worker_pass",
]

"""DecisionReceipt and AuditRecord producers, ledger sequencing (EVIDENCE-001 §2.1, §3, §7; DEC-F1).

Write path (§2.1):
    domain transaction --> record_decision_receipt / record_audit   (same transaction: the receipt
                                                                     commits iff the decision does)
    worker             --> sequence_pending  -> ledger append (hash chain, E3 entry signature)
                       --> checkpointer      -> signed Merkle checkpoint
A crash between the two steps leaves an un-sequenced receipt that the next pass commits; a retry
never creates a second entry (EVID-24: ledger appends are idempotent per record).

- E0 decisions produce no subject-level receipt, only aggregate counts (§1).
- The recorded class is never below the action's floor (EVID-02); E2/E3 must carry their material
  commitments (EVID-01); every body passes the minimization check (EVID-12).
- E3 material inputs (the exact fact projection the decision was computed from) are kept as a
  supporting object under the actor's erasable subject key, so replay works until lawful erasure,
  and afterwards reports INCOMPLETE_BY_LAWFUL_ERASURE instead of silently passing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_evidence import Digest, canonicalize, hash_json
from zid_outbox import CloudEvent, emit

from zid_platform.modules.evidence.application import ledger
from zid_platform.modules.evidence.application.runtime import EvidenceRuntime, current_runtime
from zid_platform.modules.evidence.application.checkpointer import tenant_scope
from zid_platform.modules.evidence.application.vault import actor_handle, make_handle, store_object
from zid_platform.modules.evidence.domain.ledger import (
    PLATFORM_TENANT_ID,
    EvidenceClass,
    LedgerError,
    Partition,
)
from zid_platform.modules.evidence.domain.receipts import (
    ANNOTATION_KINDS,
    RETENTION_CLASS,
    AuditFacts,
    ReceiptFacts,
    audit_body,
    check_audit,
    check_material,
    check_minimized,
    receipt_body,
    require_class,
)
from zid_platform.modules.evidence.infrastructure import ledger_store
from zid_platform.modules.evidence.infrastructure import receipt_store as store

HASH_ALG = "sha-256"
MATERIAL_OBJECT_TYPE = "decision.material_inputs"


def commitment(value: str, *, tenant_id: str, kind: str) -> str:
    """Resource/target commitment: a digest scoped by tenant and kind, never the raw value."""
    return str(hash_json({"tenant": tenant_id, "kind": kind, "value": value}, alg=HASH_ALG))


def material_commitment(material: Mapping[str, Any]) -> str:
    return str(hash_json(material, alg=HASH_ALG))


def _jsonable(material: Mapping[str, Any]) -> dict[str, Any]:
    """Material inputs as plain JSON (datetimes as ISO strings) so they hash deterministically."""
    out: dict[str, Any] = json.loads(json.dumps(material, default=_default, sort_keys=True))
    return out


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    raise TypeError(f"not evidence material: {type(value).__name__}")


async def record_decision_receipt(
    conn: AsyncConnection,
    tenant_id: str,
    facts: ReceiptFacts,
    *,
    floor: EvidenceClass | str,
    material: Mapping[str, Any] | None = None,
    rt: EvidenceRuntime | None = None,
) -> str | None:
    """Write the receipt in the producer's transaction; returns its id (None for E0)."""
    rt = rt or current_runtime()
    cls = require_class(facts.evidence_class, floor)
    if cls is EvidenceClass.E0:
        await store.bump_aggregate(
            conn, tenant_id, facts.effective_at.date(), facts.action, facts.outcome
        )
        return None
    material_json = _jsonable(material) if material is not None else None
    if material_json is not None:
        facts = replace(facts, input_commitment=material_commitment(material_json))
    check_material(facts)
    receipt_id = str(uuid7())
    actor = await actor_handle(conn, tenant_id, facts.actor_principal_id, rt=rt)
    represented = (
        await actor_handle(conn, tenant_id, facts.represented_principal_id, rt=rt)
        if facts.represented_principal_id
        and facts.represented_principal_id != facts.actor_principal_id
        else None
    )
    material_object_id = None
    if material_json is not None and cls is EvidenceClass.E3:
        material_object_id, _ = await store_object(
            conn,
            tenant_id,
            content=canonicalize(material_json),
            object_type=MATERIAL_OBJECT_TYPE,
            classification="D4",
            retention_class=RETENTION_CLASS[cls],
            subject_principal_id=facts.actor_principal_id,
            rt=rt,
        )
    body = receipt_body(
        facts,
        receipt_id=receipt_id,
        tenant_id=tenant_id,
        actor_handle=actor,
        represented_handle=represented,
        resource_commitment=(
            commitment(facts.resource_ref, tenant_id=tenant_id, kind="resource")
            if facts.resource_ref
            else None
        ),
        material_object_id=material_object_id,
    )
    receipt_hash = hash_json(body, alg=HASH_ALG)
    await store.insert_receipt(
        conn,
        {
            "receipt_id": receipt_id,
            "tenant_id": tenant_id,
            "decision_id": facts.decision_id,
            "evidence_class": cls.value,
            "decision_type": facts.decision_type,
            "outcome": facts.outcome,
            "reason_codes": facts.reason_codes,
            "actor_handle": actor,
            "represented_handle": represented,
            "action": facts.action,
            "resource_class": facts.resource_class,
            "purpose_id": facts.purpose_id,
            "policy_release_id": facts.policy_release_id,
            "relation_revision": facts.relation_revision,
            "security_revision": facts.security_revision,
            "risk_class": facts.risk_class,
            "input_commitment": facts.input_commitment,
            "material_object_id": material_object_id,
            "previous_receipt_id": facts.previous_receipt_id,
            "retention_class": RETENTION_CLASS[cls],
            "body": body,
            "receipt_hash": str(receipt_hash),
            "effective_at": facts.effective_at,
        },
    )
    await emit(
        conn,
        name="evidence.receipt.committed",
        tenant_id=tenant_id,
        aggregate_type="decision_receipt",
        aggregate_id=receipt_id,
        aggregate_version=1,
        data={
            "receipt_id": receipt_id,
            "decision_id": facts.decision_id,
            "evidence_class": cls.value,
            "action": facts.action,
            "outcome": facts.outcome,
            "receipt_hash": str(receipt_hash),
        },
    )
    return receipt_id


async def record_audit(
    conn: AsyncConnection,
    tenant_id: str | None,
    facts: AuditFacts,
    *,
    rt: EvidenceRuntime | None = None,
) -> str | None:
    """A protected AuditRecord (§7). `tenant_id` None: the platform-security partition (operator
    and security evidence). Returns None when `source_event_id` was already recorded."""
    rt = rt or current_runtime()
    check_audit(facts)
    audit_id = str(uuid7())
    actor = (
        await actor_handle(conn, tenant_id, facts.actor_principal_id, rt=rt)
        if facts.actor_principal_id and tenant_id
        else None
    )
    if facts.actor_principal_id and tenant_id is None:
        # platform partition: operators are pseudonymized under the platform scope
        actor = await make_handle(rt, PLATFORM_TENANT_ID, "evidence:operator", facts.actor_principal_id)
    body = audit_body(facts, audit_id=audit_id, tenant_id=tenant_id, actor_handle=actor)
    values = {
        "audit_id": audit_id,
        "tenant_id": tenant_id,
        "event_type": facts.event_type,
        "actor_type": facts.actor_type,
        "actor_handle": actor,
        "target_type": facts.target_type,
        "target_ref": facts.target_ref,
        "action": facts.action,
        "outcome": facts.outcome,
        "reason_code": facts.reason_code,
        "evidence_class": facts.evidence_class.value,
        "source_event_id": facts.source_event_id,
        "body": body,
        "record_hash": str(hash_json(body, alg=HASH_ALG)),
        "occurred_at": facts.occurred_at,
    }
    if tenant_id is None:
        async with ledger_store.platform_scope(conn):
            inserted = await store.insert_audit(conn, values)
    else:
        inserted = await store.insert_audit(conn, values)
    if not inserted:
        return None
    if tenant_id is not None:
        await emit(
            conn,
            name="evidence.audit.committed",
            tenant_id=tenant_id,
            aggregate_type="audit_record",
            aggregate_id=audit_id,
            aggregate_version=1,
            data={
                "audit_id": audit_id,
                "event_type": facts.event_type,
                "evidence_class": facts.evidence_class.value,
                "record_hash": values["record_hash"],
            },
        )
    return audit_id


async def annotate_receipt(
    conn: AsyncConnection,
    tenant_id: str,
    receipt_id: str,
    *,
    kind: str,
    author_principal_id: str,
    reason_code: str,
    note_commitment: str | None = None,
    corrected: Mapping[str, Any] | None = None,
    superseded_by: str | None = None,
    rt: EvidenceRuntime | None = None,
) -> str:
    """Correction, supersession, annotation or invalidation (§20): a new linked record; the
    original receipt is never touched and still verifies."""
    rt = rt or current_runtime()
    if kind not in ANNOTATION_KINDS:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "unknown annotation kind")
    if await store.load_receipt(conn, receipt_id) is None:
        raise LedgerError("NOT_FOUND", "unknown receipt")
    annotation_id = str(uuid7())
    author = await actor_handle(conn, tenant_id, author_principal_id, rt=rt)
    body = {
        "schema_version": "receipt-annotation.v1",
        "annotation_id": annotation_id,
        "receipt_id": receipt_id,
        "tenant_id": tenant_id,
        "kind": kind,
        "reason_code": reason_code,
        "note_commitment": note_commitment,
        "corrected": dict(corrected or {}),
        "superseded_by": superseded_by,
        "author_handle": author,
    }
    check_minimized(body)
    await store.insert_annotation(
        conn,
        {
            "annotation_id": annotation_id,
            "tenant_id": tenant_id,
            "receipt_id": receipt_id,
            "kind": kind,
            "body": body,
            "record_hash": str(hash_json(body, alg=HASH_ALG)),
            "author_handle": author,
        },
    )
    if kind == "INVALIDATION":
        await emit(
            conn,
            name="evidence.receipt.invalidated",
            tenant_id=tenant_id,
            aggregate_type="receipt_annotation",
            aggregate_id=annotation_id,
            aggregate_version=1,
            data={"receipt_id": receipt_id, "annotation_id": annotation_id, "reason_code": reason_code},
        )
    return annotation_id


# ------------------------------------------------------------------------- outbox -> audit ingest


def _event_name(event_type: str) -> str:
    """`com.zoiko.id.operator_access.approved.v1` -> `operator_access.approved`."""
    parts = event_type.split(".")
    return ".".join(parts[3:-1])


def _str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return str(value) if value is not None else None


async def ingest_event(
    conn: AsyncConnection, event: CloudEvent, *, rt: EvidenceRuntime | None = None
) -> str | None:
    """Evidence-bearing domain events become protected AuditRecords (§7.1), at most once per event.

    An event qualifies when its data names an `evidence_class` of E1-E3. Ids and commitments are
    copied; nothing else of the payload is (minimization)."""
    data = event.data
    cls = data.get("evidence_class")
    if cls not in ("E1", "E2", "E3"):
        return None
    name = _event_name(event.type)
    actor = (
        _str(data, "requester_principal_id")
        or _str(data, "approver_principal_id")
        or _str(data, "reviewer_principal_id")
        or _str(data, "decided_by")
        or _str(data, "changed_by")
        or _str(data, "actor_principal_id")
    )
    details = {
        k: v
        for k, v in data.items()
        if (k.endswith(("_id", "_commitment", "_class")) or k in ("capability", "state", "outcome"))
        and not k.endswith("principal_id")
        and isinstance(v, str | int | bool | type(None))
    }
    facts = AuditFacts(
        event_type=name,
        actor_type="OPERATOR" if name.startswith("operator_access.") and actor else "HUMAN",
        action=name,
        target_type=event.aggregatetype,
        target_ref=event.aggregateid,
        outcome="DENIED" if name.endswith((".denied", ".revoked")) else "SUCCESS",
        evidence_class=EvidenceClass(cls),
        occurred_at=event.time,
        actor_principal_id=actor,
        source_service=event.source,
        source_event_id=event.id,
        approval_refs=tuple(str(a) for a in data.get("approver_principal_ids", ()) or ()),
        details=details,
    )
    tenant = None if event.tenantid == PLATFORM_TENANT_ID else event.tenantid
    return await record_audit(conn, tenant, facts, rt=rt)


# ---------------------------------------------------------------------------- ledger sequencing


async def sequence_pending(
    conn: AsyncConnection, *, limit: int = 1000, rt: EvidenceRuntime | None = None
) -> int:
    """Worker pass: commit every un-ledgered evidence record to its partition's hash chain.

    Run as the evidence worker role (sees every tenant); the stored body is re-hashed first, so a
    row altered before sequencing is reported, never sealed (EVIDENCE_LEDGER_CONFLICT)."""
    rt = rt or current_runtime()
    committed = 0
    async with ledger_store.platform_scope(conn):
        batches = [(rt_, await store.unledgered(conn, rt_, limit)) for rt_ in store.LEDGERED]
    for record_type, rows in batches:
        for row in rows:
            body = dict(row.body)
            if not hash_json(body, alg=HASH_ALG).matches(Digest.parse(row.record_hash)):
                raise LedgerError(
                    "EVIDENCE_LEDGER_CONFLICT", f"{record_type} {row.record_id} body changed"
                )
            cls = row.evidence_class or "E2"
            tenant = str(row.tenant_id) if row.tenant_id is not None else None
            await _append_in_scope(
                conn,
                tenant,
                record_type=record_type,
                record_id=row.record_id,
                record=body,
                evidence_class=cls,
                observed_at=row.created_at,
                rt=rt,
            )
            committed += 1
    return committed


async def _append_in_scope(
    conn: AsyncConnection,
    tenant_id: str | None,
    *,
    record_type: str,
    record_id: str,
    record: Mapping[str, Any],
    evidence_class: str,
    observed_at: datetime,
    rt: EvidenceRuntime,
) -> None:
    partition = Partition(tenant_id, rt.cell_id)
    async with tenant_scope(conn, partition):
        await ledger.append(
            conn,
            tenant_id=tenant_id,
            record_type=record_type,
            record_id=record_id,
            record=record,
            evidence_class=evidence_class,
            observed_at=observed_at,
            cell_id=rt.cell_id,
            signer=rt.signer,
        )


def register_loaders() -> None:
    """Export bundles include the committed record body (pseudonymous by construction)."""
    for record_type in store.LEDGERED:

        async def loader(
            conn: AsyncConnection, record_id: str, _rt: str = record_type
        ) -> Mapping[str, Any] | None:
            return await store.load_body(conn, _rt, record_id)

        ledger.register_record_loader(record_type, loader)


register_loaders()


__all__ = [
    "MATERIAL_OBJECT_TYPE",
    "annotate_receipt",
    "commitment",
    "ingest_event",
    "material_commitment",
    "record_audit",
    "record_decision_receipt",
    "sequence_pending",
]


async def receipt_summary(conn: AsyncConnection, receipt_id: str) -> dict[str, Any] | None:
    """The pseudonymous facts of a receipt other modules may route on (privacy human review)."""
    row = await store.load_receipt(conn, receipt_id)
    if row is None:
        return None
    return {
        "receipt_id": str(row.receipt_id),
        "decision_id": str(row.decision_id),
        "action": row.action,
        "outcome": row.outcome,
        "reason_codes": list(row.reason_codes),
        "evidence_class": row.evidence_class,
        "actor_handle": row.actor_handle,
        "effective_at": row.effective_at.isoformat(),
    }


async def principal_handle(tenant_id: str, principal_id: str, rt: EvidenceRuntime | None = None) -> str:
    """The receipt actor handle of a principal (deterministic; no resolver row is written)."""
    from zid_platform.modules.evidence.application.vault import make_handle
    from zid_platform.modules.evidence.domain.receipts import ACTOR_DOMAIN

    return await make_handle(rt or current_runtime(), tenant_id, ACTOR_DOMAIN, principal_id)

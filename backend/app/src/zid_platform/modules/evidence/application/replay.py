"""Receipt verification, deterministic replay and signed export packages (EVIDENCE-001 §11.1, §17,
§18, §19; DEC-F2 §6).

Replay sequence (§19.1): verify the receipt hash and its ledger inclusion, load the retained
material inputs and check them against the receipt's commitment, then hand them to the evaluator
the producing module registered for the decision type (pinned to the historic immutable policy
release; never today's provider or policy, EVID-11). Results:

    VERIFIED_MATCH                  replayed outcome, reasons and obligations equal the receipt
    VERIFIED_STRUCTURAL_ONLY        integrity verified; no material inputs retained (E1/E2)
    MISMATCH                        replay differs (E3: a security incident, EVID-10)
    INCOMPLETE_BY_LAWFUL_ERASURE    material inputs were lawfully crypto-erased
    UNVERIFIABLE_INTEGRITY_FAILURE  hash, signature or ledger inconsistency
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_crypto import sign_jws
from zid_evidence import (
    CheckpointManifest,
    Digest,
    canonicalize,
    hash_bytes,
    hash_json,
    loads_strict,
    verify_bundle,
)
from zid_outbox import emit

from zid_platform.modules.evidence.application import ledger
from zid_platform.modules.evidence.application.receipts import material_commitment, record_audit
from zid_platform.modules.evidence.application.runtime import EvidenceRuntime, current_runtime
from zid_platform.modules.evidence.application.vault import actor_handle, read_object
from zid_platform.modules.evidence.domain import verification_axes
from zid_platform.modules.evidence.domain.export_events import EXPORT_REFUSED
from zid_platform.modules.evidence.domain.ledger import EvidenceClass, LedgerError
from zid_platform.modules.evidence.domain.receipts import (
    CUSTODY_ACTIONS,
    EXPORT_MODES,
    AuditFacts,
    ReplayResult,
    compare_outcome,
)
from zid_platform.modules.evidence.infrastructure import receipt_store as store
from zid_platform.modules.evidence.infrastructure.tsa import TimestampError

PACKAGE_TYP = "zid-evidence-package+jws"
REPLAY_FIELDS = ("outcome", "reason_codes", "obligations")

# decision_type -> evaluator(conn, receipt body, material inputs) -> replayed outcome fields
Replayer = Callable[
    [AsyncConnection, Mapping[str, Any], Mapping[str, Any]], Awaitable[Mapping[str, Any]]
]
_REPLAYERS: dict[str, Replayer] = {}


def register_replayer(decision_type: str, replayer: Replayer) -> None:
    _REPLAYERS[decision_type] = replayer


async def _timestamp_check(
    bundle: Mapping[str, Any], verifier: ledger.TimestampChecker | None
) -> dict[str, Any] | None:
    """The checkpoint's RFC 3161 timestamp, proved against the TSA trust list. None when the checkpoint is not anchored.

    Without a trust list the timestamp is reported but not claimed: a stored token nobody checked proves nothing."""
    manifest_fields = dict(bundle["checkpoint"])
    ref = manifest_fields.pop("external_timestamp_ref", None)
    manifest_fields.pop("signature", None)
    if ref is None:
        return None
    if verifier is None:
        return {
            "name": "external_timestamp",
            "ok": True,
            "detail": "present, not verified (no TSA trust list)",
        }
    manifest = CheckpointManifest.from_dict(manifest_fields)
    try:
        await verifier.verify_ref(ref, manifest.hash(alg=manifest.merkle_root.alg))
    except TimestampError as e:
        return {"name": "external_timestamp", "ok": False, "detail": str(e)}
    return {
        "name": "external_timestamp",
        "ok": True,
        "detail": "verified against the TSA trust list",
    }


@dataclass(slots=True)
class ReceiptVerification:
    receipt_id: str
    hash_ok: bool = False
    ledgered: bool = False
    sealed: bool = False
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.hash_ok and self.ledgered and all(c["ok"] for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "ok": self.ok,
            "receipt_hash_valid": self.hash_ok,
            "ledgered": self.ledgered,
            "sealed": self.sealed,
            "checks": self.checks,
            # CR-170 (ZID-CP-WF08 08.1): the same evidence as separate axes, with no overall success. `ok` above is only the integrity checks.
            "axes": verification_axes.axes(
                checks=self.checks, receipt_hash_valid=self.hash_ok, ledgered=self.ledgered, sealed=self.sealed, keys_source="trusted", now=datetime.now(UTC)
            ),
        }


async def verify_receipt(
    conn: AsyncConnection, receipt_id: str, *, rt: EvidenceRuntime | None = None
) -> ReceiptVerification:
    """Recompute the receipt digest and verify its ledger entry, checkpoint and inclusion proof
    against trusted evidence keys - the same checks an offline verifier runs (EVID-05, EVID-07)."""
    rt = rt or current_runtime()
    out = ReceiptVerification(receipt_id)
    row = await store.load_receipt(conn, receipt_id)
    if row is None:
        raise LedgerError("NOT_FOUND", "unknown receipt")
    body = dict(row.body)
    out.hash_ok = hash_json(body, alg="sha-256").matches(Digest.parse(row.receipt_hash))
    try:
        bundle = await ledger.export_bundle(
            conn, receipt_id, keys=rt.trusted_keys(), record_type="decision_receipt", record=body
        )
    except LedgerError as e:
        if e.code == "NOT_FOUND":
            return out  # not yet sequenced: committed, not ledgered
        if e.code == "EVIDENCE_CHECKPOINT_MISSING":
            out.ledgered = True
            return out  # ledgered, not yet sealed: never reported as sealed (EVID-25)
        raise
    out.ledgered = out.sealed = True
    report = verify_bundle(bundle, trusted_keys=rt.trusted_keys())
    out.checks = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in report.checks]
    stamp = await _timestamp_check(bundle, rt.timestamp_verifier)
    if stamp is not None:
        out.checks.append(stamp)
    entry_hash = Digest.parse(bundle["ledger_entry"]["record_hash"])
    if not entry_hash.matches(Digest.parse(row.receipt_hash)):
        out.checks.append({"name": "ledger_record_hash", "ok": False, "detail": "differs"})
    return out


async def replay_receipt(
    conn: AsyncConnection, tenant_id: str, receipt_id: str, *, rt: EvidenceRuntime | None = None
) -> dict[str, Any]:
    rt = rt or current_runtime()
    verification = await verify_receipt(conn, receipt_id, rt=rt)
    row = await store.load_receipt(conn, receipt_id)
    assert row is not None
    body = dict(row.body)
    result: dict[str, Any] = {"receipt_id": receipt_id, "verification": verification.to_dict()}
    if not verification.hash_ok or (verification.sealed and not verification.ok):
        result["result"] = ReplayResult.UNVERIFIABLE_INTEGRITY_FAILURE
    elif row.material_object_id is None:
        result["result"] = ReplayResult.VERIFIED_STRUCTURAL_ONLY
    else:
        try:
            raw = await read_object(conn, tenant_id, str(row.material_object_id), rt=rt)
        except LedgerError as e:
            if e.code not in ("EVIDENCE_RAW_OBJECT_ERASED", "EVIDENCE_RETENTION_EXPIRED"):
                raise
            result["result"] = ReplayResult.INCOMPLETE_BY_LAWFUL_ERASURE
            result["detail"] = (
                "identity-resolution and material inputs unavailable by lawful erasure"
            )
        else:
            material = loads_strict(raw)
            if material_commitment(material) != row.input_commitment:
                result["result"] = ReplayResult.UNVERIFIABLE_INTEGRITY_FAILURE
                result["detail"] = "material inputs do not match the receipt commitment"
            elif (replayer := _REPLAYERS.get(row.decision_type)) is None:
                result["result"] = ReplayResult.VERIFIED_STRUCTURAL_ONLY
                result["detail"] = f"no replay evaluator for {row.decision_type}"
            else:
                try:
                    replayed = dict(await replayer(conn, body, material))
                except LedgerError as e:
                    if e.code == "EVIDENCE_REPLAY_MISMATCH":  # the historic artifact was altered
                        result["result"] = ReplayResult.MISMATCH
                        result["differences"] = ["policy_artifact"]
                        result["detail"] = e.detail
                    elif e.code == "EVIDENCE_POLICY_ARTIFACT_MISSING":
                        result["result"] = ReplayResult.VERIFIED_STRUCTURAL_ONLY
                        result["detail"] = e.detail
                    else:
                        raise
                else:
                    recorded = {f: body.get(f) for f in REPLAY_FIELDS}
                    diff = compare_outcome(recorded, _plain(replayed), REPLAY_FIELDS)
                    result["replayed"] = _plain(replayed)
                    result["differences"] = diff
                    result["result"] = (
                        ReplayResult.MISMATCH if diff else ReplayResult.VERIFIED_MATCH
                    )
    replay_id = str(uuid7())
    result["replay_id"] = replay_id
    await emit(
        conn,
        name="evidence.receipt.replay_completed",
        tenant_id=tenant_id,
        aggregate_type="receipt_replay",
        aggregate_id=replay_id,
        aggregate_version=1,
        data={"receipt_id": receipt_id, "result": result["result"]},
    )
    if result["result"] in (ReplayResult.MISMATCH, ReplayResult.UNVERIFIABLE_INTEGRITY_FAILURE):
        await emit(
            conn,
            name="evidence.integrity.failed",
            tenant_id=tenant_id,
            aggregate_type="evidence_incident",
            aggregate_id=str(uuid7()),
            aggregate_version=1,
            data={
                "receipt_id": receipt_id,
                "replay_id": replay_id,
                "kinds": [
                    "replay_mismatch" if result["result"] == ReplayResult.MISMATCH else "integrity"
                ],
                "evidence_class": body.get("evidence_class"),
            },
        )
    return result


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


async def export_package(
    conn: AsyncConnection,
    tenant_id: str,
    receipt_id: str,
    *,
    mode: str,
    purpose: str,
    case_ref: str,
    custodian_principal_id: str,
    redact: list[str] | None = None,
    case_id: str | None = None,
    rt: EvidenceRuntime | None = None,
) -> dict[str, Any]:
    """A self-contained, signed EvidencePackage (§17): receipt, ledger entry, checkpoint, inclusion
    proof, verification keys and the canonicalization profile; the manifest is canonicalized,
    hashed and signed. Every export leaves a chain-of-custody record (§18, EVID-20).

    `redact`: fields withheld from a disclosure package (§13 "Redaction", EVID-19). The full record
    is then not shipped; the package carries the redacted view, a digest of every withheld field
    and the original record hash, and the signed manifest attests the view derives from it.
    `case_id`: an export made for an open case is bound to it in the custody record."""
    from datetime import UTC, datetime

    from zid_platform.modules.evidence.application.provenance import case_ref as case_reference
    from zid_platform.modules.evidence.application.provenance import provenance, require_open_case

    rt = rt or current_runtime()
    if mode not in EXPORT_MODES:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "unknown export mode")
    if case_id is not None:
        await require_open_case(conn, case_id, datetime.now(UTC))
        case_ref = case_reference(case_id)
    row = await store.load_receipt(conn, receipt_id)
    if row is None:
        raise LedgerError("NOT_FOUND", "unknown receipt")
    body = dict(row.body)
    keys = rt.trusted_keys()
    disclosure = None
    if redact:
        unknown = sorted(set(redact) - set(body))
        if unknown:
            raise LedgerError(
                "EVIDENCE_SCHEMA_INVALID", f"not receipt fields: {', '.join(unknown)}"
            )
        view = {k: v for k, v in body.items() if k not in set(redact)}
        disclosure = {
            "view": view,
            "withheld": {
                k: str(hash_json({k: body[k]}, alg="sha-256")) for k in sorted(set(redact))
            },
            "source_record_hash": row.receipt_hash,
        }
    bundle = await ledger.export_bundle(
        conn,
        receipt_id,
        keys=keys,
        record_type="decision_receipt",
        record=None if redact else body,
        withhold_record=bool(redact),
    )
    if body.get("evidence_class") == "E3":  # §8.1: every E3 export carries its lineage
        bundle["provenance"] = await provenance(conn, "decision_receipt", receipt_id, depth=3)
    if disclosure is not None:
        bundle["disclosure"] = disclosure
    package_hash = hash_bytes(canonicalize(bundle), alg="sha-256")
    manifest = {
        "format": "zid-evidence-package/1",
        "package_id": str(uuid7()),
        "mode": mode,
        "receipt_id": receipt_id,
        "bundle_hash": str(package_hash),
        "canonicalization": "RFC8785-JCS",
        "hash_algorithm": "sha-256",
        "verifier": "python -m zid_evidence verify <bundle.json> --keys <keys.json>",
        "redacted_fields": sorted(set(redact or ())),
        "source_record_hash": row.receipt_hash,
    }
    signature = await asyncio.to_thread(sign_jws, rt.signer, manifest, typ=PACKAGE_TYP)
    await record_custody(
        conn,
        tenant_id,
        case_ref=case_ref,
        item_id=receipt_id,
        custodian_principal_id=custodian_principal_id,
        action="EXPORTED",
        purpose=purpose,
        export_mode=mode,
        package_hash=str(package_hash),
        rt=rt,
    )
    await emit(
        conn,
        name="evidence.export.created",
        tenant_id=tenant_id,
        aggregate_type="evidence_package",
        aggregate_id=manifest["package_id"],
        aggregate_version=1,
        data={
            "package_id": manifest["package_id"],
            "receipt_id": receipt_id,
            "mode": mode,
            "bundle_hash": str(package_hash),
            "evidence_class": "E3",
        },
    )
    return {"manifest": manifest, "manifest_signature": signature, "bundle": bundle, "keys": keys}


async def record_export_refusal(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    principal_id: str,
    resource: str,
    mode: str,
    code: str,
) -> None:
    """T38: an export that was refused (no stated purpose) is itself recorded: who asked, for what, in which mode, and why it was
    refused. Ids and codes only; the purpose text is absent by definition."""
    from datetime import UTC, datetime

    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=EXPORT_REFUSED,
            actor_type="HUMAN",
            action="refused",
            target_type=resource.split(":", 1)[0],
            target_ref=resource,
            outcome="DENIED",
            evidence_class=EvidenceClass.E3,
            occurred_at=datetime.now(UTC),
            actor_principal_id=principal_id or None,
            reason_code=code,
            source_service="evidence",
            details={"export_mode": mode, "reason": code},
        ),
    )


async def record_custody(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    case_ref: str,
    item_id: str,
    custodian_principal_id: str,
    action: str,
    purpose: str,
    export_mode: str | None = None,
    package_hash: str | None = None,
    rt: EvidenceRuntime | None = None,
) -> str:
    rt = rt or current_runtime()
    if action not in CUSTODY_ACTIONS:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "unknown custody action")
    custody_id = str(uuid7())
    custodian = await actor_handle(conn, tenant_id, custodian_principal_id, rt=rt)
    case_commitment = str(hash_json({"tenant": tenant_id, "case": case_ref}, alg="sha-256"))
    body = {
        "schema_version": "custody-record.v1",
        "custody_id": custody_id,
        "tenant_id": tenant_id,
        "case_commitment": case_commitment,
        "evidence_item_id": item_id,
        "custodian_handle": custodian,
        "custody_action": action,
        "purpose": purpose,
        "export_mode": export_mode,
        "package_hash": package_hash,
    }
    await store.insert_custody(
        conn,
        {
            "custody_id": custody_id,
            "tenant_id": tenant_id,
            "case_ref": case_commitment,
            "evidence_item_id": item_id,
            "custodian_handle": custodian,
            "custody_action": action,
            "purpose": purpose,
            "export_mode": export_mode,
            "package_hash": package_hash,
            "body": body,
            "record_hash": str(hash_json(body, alg="sha-256")),
        },
    )
    await emit(
        conn,
        name="evidence.custody.changed",
        tenant_id=tenant_id,
        aggregate_type="custody_record",
        aggregate_id=custody_id,
        aggregate_version=1,
        data={"custody_id": custody_id, "evidence_item_id": item_id, "custody_action": action},
    )
    return custody_id


__all__ = [
    "PACKAGE_TYP",
    "ReceiptVerification",
    "export_package",
    "record_custody",
    "register_replayer",
    "replay_receipt",
    "verify_receipt",
]

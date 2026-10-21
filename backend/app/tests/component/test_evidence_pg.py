"""Evidence ledger, receipts and crypto-erasure against PostgreSQL with RLS (F-W1, F-W2; DEC-F1,
DEC-F2; EVID-01..27).

Real PDP decisions write pseudonymous receipts in their own transaction; the worker sequences them
into the hash-chained ledger and seals signed Merkle checkpoints; an offline verifier checks the
export; replay re-runs the historic decision; crypto-erasure makes the person unrecoverable while
the receipt and its checkpoint still verify.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_crypto import LocalSigner
from zid_crypto.purposes import KeyPurpose
from zid_db import tenant_transaction
from zid_evidence import Digest, verify_bundle
from zid_platform.modules.authority.ports import Request as AuthorityRequest
from zid_platform.modules.decision.ports import (
    AuthnFact,
    DecisionRequest,
    Outcome,
    action_spec,
    evaluate_request,
    register_evidence_replayer,
)
from zid_platform.modules.evidence.application import ledger, replay, vault
from zid_platform.modules.evidence.application.checkpointer import anchor_pending, seal_partition
from zid_platform.modules.evidence.application.runtime import (
    EvidenceRuntime,
    configure_runtime,
    current_runtime,
    development_runtime,
)
from zid_platform.modules.evidence.application.ledger import LedgerConfig
from zid_platform.modules.evidence.domain.ledger import LedgerError, Partition
from zid_platform.modules.evidence.infrastructure.tsa import TimestampError
from zid_platform.modules.evidence.ports import (
    AuditFacts,
    EvidenceClass,
    ReceiptFacts,
    record_audit,
    record_decision_receipt,
    worker_pass,
)
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.protected import catalog

NOW = datetime.now(UTC)


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def fresh_runtime() -> Any:
    rt = development_runtime("uk-1")
    configure_runtime(rt)
    register_evidence_replayer(catalog())
    yield rt
    configure_runtime(None)


async def seed_admin(c: Any, tenant: str, admin: str, actions: list[str]) -> None:
    await create_principal(
        c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
    )
    await make_admin(c, tenant, admin, actions, ["receipt:*", "principal:*", "pref:*"])


async def decide(c: Any, tenant: str, admin: str, action: str, resource: str = "receipt:1") -> Any:
    return await evaluate_request(
        c,
        req=DecisionRequest(
            decision_id=uid(),
            tenant_id=tenant,
            action=action_spec(action, catalog()),
            resource_id=resource,
            represented_principal_id=None,
            purpose=None,
            tool_id=None,
            at=datetime.now(UTC),
        ),
        executor_id=admin,
        authority_request=AuthorityRequest(action, resource, datetime.now(UTC), actor_assurance=3),
        authn=AuthnFact(3, datetime.now(UTC) - timedelta(minutes=1)),
        security_state="NORMAL",
        catalog=catalog(),
    )


async def receipt_of(c: Any, decision_id: str) -> Any:
    return (
        await c.execute(
            text("SELECT *, evidence_class::text AS ev FROM evidence.decision_receipt WHERE decision_id = :d"),
            {"d": uuid.UUID(decision_id)},
        )
    ).first()


def test_pdp_decisions_leave_minimized_receipts_by_class(rt_url: str) -> None:
    """EVID-01/02/12, PRIV-29: E3 receipt with material object, E1 receipt, E0 aggregate only."""
    tenant, admin = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await seed_admin(c, tenant, admin, ["evidence.export", "identity.read", "preferences.update"])
            d3 = await decide(c, tenant, admin, "evidence.export")
            d1 = await decide(c, tenant, admin, "identity.read", "principal:*")
            d0 = await decide(c, tenant, admin, "preferences.update", "pref:1")
            assert d3.outcome is Outcome.ALLOW, d3.reason_codes
            r3, r1 = await receipt_of(c, d3.decision_id), await receipt_of(c, d1.decision_id)
            assert r3.ev == "E3" and r3.material_object_id is not None
            assert r1.ev == "E1" and r1.material_object_id is None
            assert await receipt_of(c, d0.decision_id) is None  # E0: no subject-level receipt
            count = (
                await c.execute(
                    text("SELECT decisions FROM evidence.decision_aggregate WHERE action = 'preferences.update'")
                )
            ).scalar_one()
            assert count == 1
            body = json.dumps(dict(r3.body))
            assert admin not in body and tenant in body  # pseudonymous: no principal id
            assert r3.actor_handle.startswith("ph1.") and r3.body["input_commitment"].startswith("sha-256:")
            # the resolver maps the handle back only through the vault (EVID-13 precondition)
            assert await vault.resolve_handle(c, tenant, r3.actor_handle) == admin

    asyncio.run(run())


def test_floor_and_material_and_minimization_are_enforced(rt_url: str) -> None:
    """EVID-02 (no lowering), EVID-01 (material), EVID-12 (prohibited content)."""
    tenant = uid()

    def facts(**kw: Any) -> ReceiptFacts:
        base: dict[str, Any] = {
            "decision_id": uid(),
            "decision_type": "AUTHORIZATION",
            "evidence_class": EvidenceClass.E2,
            "action": "invoice.approve",
            "resource_class": "invoice",
            "outcome": "ALLOW",
            "reason_codes": ("AUTHORITY_VALID",),
            "risk_class": "R2",
            "effective_at": NOW,
            "actor_principal_id": uid(),
            "relation_revision": 1,
            "input_commitment": "sha-256:" + "0" * 64,
        }
        return ReceiptFacts(**{**base, **kw})

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(LedgerError, match="EVIDENCE_CLASS_INSUFFICIENT"):
                await record_decision_receipt(c, tenant, facts(evidence_class=EvidenceClass.E1), floor="E2")
            with pytest.raises(LedgerError, match="EVIDENCE_MATERIAL_INPUT_MISSING"):
                await record_decision_receipt(c, tenant, facts(input_commitment=None), floor="E2")
            with pytest.raises(LedgerError, match="EVIDENCE_SCHEMA_INVALID"):
                await record_decision_receipt(
                    c, tenant, facts(assurance_summary={"email": "a@b.example"}), floor="E2"
                )
            with pytest.raises(LedgerError, match="EVIDENCE_SCHEMA_INVALID"):
                await record_decision_receipt(
                    c, tenant, facts(purpose_id="contact jane@corp.example"), floor="E2"
                )
            assert await record_decision_receipt(c, tenant, facts(), floor="E2")

    asyncio.run(run())


def test_worker_seals_and_offline_verifier_accepts_the_export(rt_url: str, owner_url: str) -> None:
    """EVID-07/18/24: sequence once, seal, inclusion proof and signed package verify offline."""
    tenant, admin = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await seed_admin(c, tenant, admin, ["evidence.export"])
                d = await decide(c, tenant, admin, "evidence.export")
                receipt_id = str((await receipt_of(c, d.decision_id)).receipt_id)
                v = await replay.verify_receipt(c, receipt_id)
                assert v.hash_ok and not v.ledgered and not v.sealed  # committed, not yet ledgered
        async with engine(owner_url) as eng, eng.begin() as c:  # the worker role
            first = await worker_pass(c, force_seal=True)
            assert first.sequenced >= 1 and first.checkpoints
        async with engine(owner_url) as eng, eng.begin() as c:
            again = await worker_pass(c, force_seal=True)
            assert again.sequenced == 0  # EVID-24: a re-run never adds a second entry
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            v = await replay.verify_receipt(c, receipt_id)
            assert v.ok and v.sealed, v.to_dict()
            pkg = await replay.export_package(
                c,
                tenant,
                receipt_id,
                mode="CUSTOMER_VERIFY",
                purpose="self-verification",
                case_ref="case-1",
                custodian_principal_id=admin,
            )
            report = verify_bundle(pkg["bundle"], trusted_keys=current_runtime().trusted_keys())
            assert report.ok, report.to_dict()
            custody = (
                await c.execute(text("SELECT custody_action FROM evidence.custody_record"))
            ).scalars().all()
            assert custody == ["EXPORTED"]  # EVID-20
            # the E3 entry carries its own signature
            sig = (
                await c.execute(
                    text("SELECT entry_signature FROM evidence.ledger_entry WHERE record_id = :r"),
                    {"r": receipt_id},
                )
            ).scalar_one()
            assert sig and sig.count(".") == 2

    asyncio.run(run())


def test_replay_match_mismatch_and_lawful_erasure(rt_url: str, owner_url: str) -> None:
    """EVID-09/10/13, PRIV-09: replay matches; a corrupted evaluator is an incident; after
    crypto-erasure the person is unrecoverable, the receipt still verifies, replay says why."""
    tenant, admin = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await seed_admin(c, tenant, admin, ["evidence.export"])
            d = await decide(c, tenant, admin, "evidence.export")
            receipt = await receipt_of(c, d.decision_id)
            receipt_id, handle = str(receipt.receipt_id), receipt.actor_handle
        async with engine(owner_url) as eng, eng.begin() as c:
            await worker_pass(c, force_seal=True)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert (await replay.replay_receipt(c, tenant, receipt_id))["result"] == "VERIFIED_MATCH"

            async def corrupt(conn: Any, body: Mapping[str, Any], m: Mapping[str, Any]) -> Mapping[str, Any]:
                return {"outcome": "DENY", "reason_codes": ["X"], "obligations": []}

            replay.register_replayer("AUTHORIZATION", corrupt)
            out = await replay.replay_receipt(c, tenant, receipt_id)
            assert out["result"] == "MISMATCH" and "outcome" in out["differences"]
            incidents = (
                await c.execute(
                    text("SELECT count(*) FROM events.outbox_event WHERE event_type = "
                         "'com.zoiko.id.evidence.integrity.failed.v1'")
                )
            ).scalar_one()
            assert incidents == 1
            register_evidence_replayer(catalog())

            result = await vault.erase_subject(c, tenant, admin, request_ref="dsr-1")
            assert result.outcome == "ERASED" and result.key_destroyed and result.erased_objects
            with pytest.raises(LedgerError, match="EVIDENCE_RAW_OBJECT_ERASED"):
                await vault.resolve_handle(c, tenant, handle)
            v = await replay.verify_receipt(c, receipt_id)
            assert v.ok  # structure survives erasure
            out = await replay.replay_receipt(c, tenant, receipt_id)
            assert out["result"] == "INCOMPLETE_BY_LAWFUL_ERASURE"
            # idempotent, and a destroyed key can never come back
            assert (await vault.erase_subject(c, tenant, admin, request_ref="dsr-1")).outcome == "NOTHING_TO_ERASE"
        async with engine(owner_url) as eng, eng.begin() as c:
            with pytest.raises(DBAPIError, match="cannot be restored"):
                await c.execute(
                    text("UPDATE evidence.subject_key SET state = 'ACTIVE', wrapped_key = '\\x00', "
                         "destroyed_at = NULL WHERE tenant_id = :t"),
                    {"t": uuid.UUID(tenant)},
                )

    asyncio.run(run())


def test_legal_hold_scopes_erasure_and_retention(rt_url: str, owner_url: str) -> None:
    """EVID-14/15, PRIV-07/08/17: a scoped hold keeps exactly what it names; release resumes."""
    tenant, subject, other, legal, approver = uid(), uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            held, _ = await vault.store_object(
                c, tenant, content=b"registry extract", object_type="authority.registry_extract",
                classification="D3", retention_class="raw", subject_principal_id=subject,
            )
            loose, _ = await vault.store_object(
                c, tenant, content=b"provider response", object_type="verification.provider_response",
                classification="D3", retention_class="raw", subject_principal_id=subject,
            )
            expired, _ = await vault.store_object(
                c, tenant, content=b"old", object_type="verification.provider_response",
                classification="D2", retention_class="raw", subject_principal_id=other,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            with pytest.raises(LedgerError, match="independent approver"):
                await vault.create_hold(c, tenant, basis="litigation", reference="m-1",
                                        requested_by=legal, approved_by=legal,
                                        object_types=["authority.registry_extract"],
                                        review_at=datetime.now(UTC) + timedelta(days=30))
            hold = await vault.create_hold(
                c, tenant, basis="litigation", reference="m-1", requested_by=legal,
                approved_by=approver, subject_principal_ids=[subject],
                object_types=["authority.registry_extract"],
                review_at=datetime.now(UTC) + timedelta(days=30),
            )
            r = await vault.erase_subject(c, tenant, subject, request_ref="dsr-2")
            assert r.outcome == "PARTIAL" and r.retained_objects == (held,) and loose in r.erased_objects
            assert not r.key_destroyed and r.hold_ids == (hold.legal_hold_id,)
            assert await vault.read_object(c, tenant, held) == b"registry extract"
            with pytest.raises(LedgerError, match="EVIDENCE_RAW_OBJECT_ERASED"):
                await vault.read_object(c, tenant, loose)
        async with engine(owner_url) as eng, eng.begin() as c:  # retention sweep (worker)
            report = await worker_pass(c)
            assert expired in report.disposed and held not in report.disposed
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(LedgerError, match="EVIDENCE_RETENTION_EXPIRED"):
                await vault.read_object(c, tenant, expired)
            with pytest.raises(LedgerError, match="cannot release"):
                await vault.release_hold(c, tenant, hold.legal_hold_id, by=legal, reason="done")
            await vault.release_hold(c, tenant, hold.legal_hold_id, by=approver, reason="matter closed")
            r = await vault.erase_subject(c, tenant, subject, request_ref="dsr-2b")
            assert r.outcome == "ERASED" and r.key_destroyed and r.erased_objects == (held,)

    asyncio.run(run())


def test_restore_reapplies_tombstones(rt_url: str, owner_url: str) -> None:
    """EVID-27, PRIV-11: a backup restored from before the erasure is erased again before use."""
    tenant, subject = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            obj, _ = await vault.store_object(
                c, tenant, content=b"passport scan", object_type="verification.document",
                classification="D3", retention_class="raw", subject_principal_id=subject,
            )
            ref = await vault.subject_ref(tenant, subject)
        # the tombstone the archive holds (the erasure happened after the backup was taken)
        tombstone = {"tenant_id": tenant, "subject_ref": ref, "outcome": "ERASED", "erased_objects": [obj]}
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert await vault.reapply_tombstones(c, tenant, [tombstone]) == 1
            with pytest.raises(LedgerError, match="EVIDENCE_RAW_OBJECT_ERASED"):
                await vault.read_object(c, tenant, obj)
            assert await vault.reapply_tombstones(c, tenant, [tombstone]) == 0

    asyncio.run(run())


def test_ledger_tamper_is_detected(rt_url: str, owner_url: str) -> None:
    """EVID-06: modifying a committed entry (by a DBA who disables the trigger) is detected."""
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for i in range(3):
                await ledger.append(c, tenant_id=tenant, record_type="test.record", record_id=f"r{i}",
                                    record={"n": i}, evidence_class="E2", cell_id="uk-1")
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(DBAPIError, match="append-only"):
                await c.execute(text("UPDATE evidence.ledger_entry SET record_id = 'x' WHERE record_id = 'r0'"))
        pid = Partition(tenant, "uk-1").id
        async with engine(owner_url) as eng, eng.begin() as c:
            await seal_partition(c, pid, signer=current_runtime().signer)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            report = await ledger.verify_partition(c, pid, keys=current_runtime().trusted_keys())
            assert report.ok and report.sealed_through == 3
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text("ALTER TABLE evidence.ledger_entry DISABLE TRIGGER ledger_entry_append_only"))
            await c.execute(
                text("UPDATE evidence.ledger_entry SET record_hash = :h WHERE partition_id = :p AND sequence_no = 2"),
                {"h": "sha-256:" + "1" * 64, "p": pid},
            )
            await c.execute(text("ALTER TABLE evidence.ledger_entry ENABLE TRIGGER ledger_entry_append_only"))
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            report = await ledger.verify_partition(c, pid, keys=current_runtime().trusted_keys())
            assert not report.ok and report.first_broken_sequence == 2
            emitted = (
                await c.execute(text("SELECT count(*) FROM events.outbox_event WHERE event_type = "
                                     "'com.zoiko.id.evidence.integrity.failed.v1'"))
            ).scalar_one()
            assert emitted == 1

    asyncio.run(run())


def test_key_rotation_clock_skew_tsa_outage_and_isolation(
    rt_url: str, owner_url: str, fresh_runtime: EvidenceRuntime
) -> None:
    """EVID-16/21/23/25 and the platform-security partition."""
    tenant, other = uid(), uid()

    class DownThenUp:
        up = False

        async def timestamp(self, digest: Digest) -> str:
            if not self.up:
                raise TimestampError("TSA unreachable")
            return "rfc3161:dGVzdA=="

    async def run() -> None:
        old_signer = fresh_runtime.signer
        pid = Partition(tenant, "uk-1").id
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await ledger.append(c, tenant_id=tenant, record_type="test.record", record_id="a",
                                record={"a": 1}, evidence_class="E1", cell_id="uk-1")
            skewed = LedgerConfig(cell_id="uk-1", clock=lambda: datetime.now(UTC) + timedelta(minutes=5))
            with pytest.raises(LedgerError, match="EVIDENCE_TIME_UNTRUSTED"):
                await ledger.append(c, tenant_id=tenant, record_type="test.record", record_id="e3",
                                    record={"a": 2}, evidence_class="E3", config=skewed)
        tsa = DownThenUp()
        async with engine(owner_url) as eng, eng.begin() as c:
            cp = await seal_partition(c, pid, signer=old_signer, tsa=tsa)
            assert cp is not None and cp.external_timestamp_ref is None  # sealed, not anchored
            tsa.up = True
            assert cp.checkpoint_id in await anchor_pending(c, tsa)
        rotated = LocalSigner("dev-evidence-uk-1-v2", purpose=KeyPurpose.EVIDENCE_SIGNING)
        new_rt = EvidenceRuntime(
            cell_id="uk-1", mac_key=fresh_runtime.mac_key, provider=fresh_runtime.provider,
            subject_kek=fresh_runtime.subject_kek, data_kek=fresh_runtime.data_kek, signer=rotated,
            historic_keys=(old_signer.public_jwk(),),
        )
        configure_runtime(new_rt)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await ledger.append(c, tenant_id=tenant, record_type="test.record", record_id="b",
                                record={"b": 1}, evidence_class="E1", cell_id="uk-1")
        async with engine(owner_url) as eng, eng.begin() as c:
            await seal_partition(c, pid, signer=rotated)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            report = await ledger.verify_partition(c, pid, keys=new_rt.trusted_keys())
            assert report.ok and report.checkpoints == 2  # EVID-21: old checkpoint still verifies
            only_new = await ledger.verify_partition(c, pid, keys=[rotated.public_jwk()], emit_incident=False)
            assert not only_new.ok  # the historic key really is what verifies the old checkpoint
            await record_audit(c, None, AuditFacts(
                event_type="operator_access.activated", actor_type="OPERATOR", action="activate",
                target_type="jit_grant", target_ref=uid(), outcome="SUCCESS",
                evidence_class=EvidenceClass.E3, occurred_at=NOW, actor_principal_id=uid(),
            ))
            seen = (await c.execute(text("SELECT count(*) FROM evidence.audit_record"))).scalar_one()
            assert seen == 0  # platform-security rows are invisible in a tenant transaction
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(other)) as c:
            rows = (await c.execute(text("SELECT count(*) FROM evidence.ledger_entry"))).scalar_one()
            assert rows == 0  # EVID-16: another tenant sees nothing
            with pytest.raises(LedgerError, match="NOT_FOUND"):
                await ledger.inclusion_proof(c, "a", record_type="test.record")

    asyncio.run(run())

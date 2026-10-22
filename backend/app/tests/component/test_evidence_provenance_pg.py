"""Artifacts, provenance, cases, redacted disclosure, receipt retention, KEK rewrap and the
privacy registry against PostgreSQL with RLS (EVID-08/14/19/20/28, DEC-F5, PRIV-12/20/22/23/26).

A real PDP decision registers the immutable action definition it used and the PROV lineage of its
receipt; a case binds exports to a purpose; a redacted package still verifies offline; retention
purges record bodies but never the ledger, and never what a hold names; subject keys follow a KEK
rotation (and a tenant's own KEK) without a flag day; a challenge of a significant automated
decision reaches a qualified human with the decision's evidence.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_evidence_pg import decide, receipt_of, seed_admin
from zid_crypto.providers import KeyRef
from zid_crypto.purposes import KeyPurpose
from zid_db import tenant_transaction
from zid_evidence import hash_json, verify_bundle
from zid_platform.modules.decision.ports import register_evidence_replayer
from zid_platform.modules.evidence.application import provenance, replay, retention, vault
from zid_platform.modules.evidence.application.runtime import (
    configure_runtime,
    current_runtime,
    development_runtime,
)
from zid_platform.modules.evidence.domain.ledger import LedgerError
from zid_platform.modules.evidence.ports import worker_pass
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.privacy.application import registry
from zid_platform.modules.privacy.application import service as privacy
from zid_platform.modules.privacy.domain.model import PrivacyError
from zid_platform.protected import catalog


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def fresh_runtime() -> Any:
    rt = development_runtime("uk-1")
    configure_runtime(rt)
    register_evidence_replayer(catalog())
    yield rt
    configure_runtime(None)


def test_decision_registers_artifacts_and_provenance(rt_url: str, owner_url: str) -> None:
    """EVID-08/28: the decision's artifacts are immutable and ledgered; its receipt has lineage
    that a different tenant cannot see; an E3 export carries the lineage."""
    tenant, other, admin = uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await seed_admin(c, tenant, admin, ["evidence.export"])
            d = await decide(c, tenant, admin, "evidence.export")
            receipt_id = str((await receipt_of(c, d.decision_id)).receipt_id)
            graph = await provenance.provenance(c, "decision_receipt", receipt_id)
            rels = {(e["relation"], e["object_type"]) for e in graph["edges"]}
            assert ("wasGeneratedBy", "authorization_decision") in rels
            assert ("used", "action_definition") in rels and (
                "wasAssociatedWith",
                "service",
            ) in rels
            action_id = next(
                e["object_ref"] for e in graph["edges"] if e["object_type"] == "action_definition"
            )
            [defn] = await provenance.artifact(c, tenant, "ACTION_DEFINITION", "evidence.export")
            assert str(defn["artifact_id"]) == action_id
            assert provenance.digest_matches(defn["content_hash"], defn["body"])
            assert defn["tenant_id"] is None  # a platform artifact, shared by every tenant
            # same identity, same body: idempotent; changed body: refused
            again, _ = await provenance.register_artifact(
                c, None, "ACTION_DEFINITION", "evidence.export", defn["version"], defn["body"]
            )
            assert again == action_id
            with pytest.raises(LedgerError, match="EVIDENCE_LEDGER_CONFLICT"):
                await provenance.register_artifact(
                    c,
                    None,
                    "ACTION_DEFINITION",
                    "evidence.export",
                    defn["version"],
                    {**defn["body"], "x": 1},
                )
        async with engine(owner_url) as eng, eng.begin() as c:
            with pytest.raises(DBAPIError, match="append-only"):
                await c.execute(
                    text("UPDATE evidence.artifact SET body = '{}' WHERE artifact_id = :a"),
                    {"a": uuid.UUID(action_id)},
                )
        async with engine(owner_url) as eng, eng.begin() as c:
            await worker_pass(c, force_seal=True)
            ledgered = (
                (
                    await c.execute(
                        text("SELECT record_type FROM evidence.ledger_entry WHERE record_id = :r"),
                        {"r": action_id},
                    )
                )
                .scalars()
                .all()
            )
            assert ledgered == ["artifact"]
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(other)) as c:
            assert (await provenance.provenance(c, "decision_receipt", receipt_id))["edges"] == []
            shared = await provenance.artifact(c, other, "ACTION_DEFINITION", "evidence.export")
            assert [str(a["artifact_id"]) for a in shared] == [action_id]  # visible, but no lineage
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            pkg = await replay.export_package(
                c,
                tenant,
                receipt_id,
                mode="CUSTOMER_VERIFY",
                purpose="lineage",
                case_ref="c-1",
                custodian_principal_id=admin,
            )
            assert pkg["bundle"]["provenance"]["edges"]
            assert (await replay.replay_receipt(c, tenant, receipt_id))[
                "result"
            ] == "VERIFIED_MATCH"

    asyncio.run(run())


def test_case_bound_redacted_export(rt_url: str, owner_url: str) -> None:
    """EVID-19/20: a redacted disclosure verifies offline and proves what it withheld; exports are
    bound to an open case and leave custody; a closed case admits nothing more."""
    tenant, admin = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await seed_admin(c, tenant, admin, ["evidence.export"])
            d = await decide(c, tenant, admin, "evidence.export")
            receipt = await receipt_of(c, d.decision_id)
            receipt_id = str(receipt.receipt_id)
        async with engine(owner_url) as eng, eng.begin() as c:
            await worker_pass(c, force_seal=True)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            case_id = await provenance.open_case(
                c,
                tenant,
                kind="AUDIT",
                purpose="regulator audit",
                reference="REG-2026-14",
                opened_by=admin,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            pkg = await replay.export_package(
                c,
                tenant,
                receipt_id,
                mode="REGULATOR_LEGAL",
                purpose="audit",
                case_ref="ignored",
                custodian_principal_id=admin,
                redact=["input_commitment"],
                case_id=case_id,
            )
            bundle, manifest = pkg["bundle"], pkg["manifest"]
            assert (
                bundle["record"] is None and "input_commitment" not in bundle["disclosure"]["view"]
            )
            body = dict(receipt.body)
            assert bundle["disclosure"]["withheld"]["input_commitment"] == str(
                hash_json({"input_commitment": body["input_commitment"]}, alg="sha-256")
            )
            assert manifest["redacted_fields"] == ["input_commitment"]
            assert manifest["source_record_hash"] == receipt.receipt_hash
            report = verify_bundle(bundle, trusted_keys=current_runtime().trusted_keys())
            assert report.ok and report.structural_only, report.to_dict()
            # the redacted view plus the withheld field reproduces the ledgered record hash
            full = {**bundle["disclosure"]["view"], "input_commitment": body["input_commitment"]}
            assert provenance.digest_matches(receipt.receipt_hash, full)
            with pytest.raises(LedgerError, match="EVIDENCE_SCHEMA_INVALID"):
                await replay.export_package(
                    c,
                    tenant,
                    receipt_id,
                    mode="REGULATOR_LEGAL",
                    purpose="audit",
                    case_ref="x",
                    custodian_principal_id=admin,
                    redact=["not_a_field"],
                )
            await replay.record_custody(
                c,
                tenant,
                case_ref=provenance.case_ref(case_id),
                item_id=receipt_id,
                custodian_principal_id=admin,
                action="COLLECTED",
                purpose="audit",
            )
            items = await provenance.case_items(c, tenant, case_id)
            assert [i["custody_action"] for i in items] == ["EXPORTED", "COLLECTED"]
            await provenance.close_case(c, case_id)
            with pytest.raises(LedgerError, match="EVIDENCE_ACCESS_DENIED"):
                await replay.export_package(
                    c,
                    tenant,
                    receipt_id,
                    mode="REGULATOR_LEGAL",
                    purpose="audit",
                    case_ref="x",
                    custodian_principal_id=admin,
                    case_id=case_id,
                )
            [listed] = await provenance.cases(c)
            assert listed["status"] == "CLOSED"

    asyncio.run(run())


def test_receipt_retention_purges_bodies_not_the_ledger(
    rt_url: str, owner_url: str, fresh_runtime: Any
) -> None:
    """EVID-14: an expired receipt body is purged (evidenced), its ledger entry stays; a receipt a
    hold names is kept; nothing but the retention pass may delete a receipt."""
    tenant, kept, gone, legal, approver = uid(), uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (kept, gone):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await make_admin(c, tenant, [kept, gone], ["evidence.export"], ["receipt:*"])
            r_kept = str(
                (
                    await receipt_of(
                        c, (await decide(c, tenant, kept, "evidence.export")).decision_id
                    )
                ).receipt_id
            )
            r_gone = str(
                (
                    await receipt_of(
                        c, (await decide(c, tenant, gone, "evidence.export")).decision_id
                    )
                ).receipt_id
            )
            await vault.create_hold(
                c,
                tenant,
                basis="litigation",
                reference="m-3",
                requested_by=legal,
                approved_by=approver,
                subject_principal_ids=[kept],
                object_types=["evidence.decision_receipt"],
                review_at=datetime.now(UTC) + timedelta(days=30),
            )
        async with engine(owner_url) as eng, eng.begin() as c:
            await worker_pass(c, force_seal=True)  # ledgered before anything expires
        async with engine(owner_url) as eng, eng.begin() as c:
            with pytest.raises(DBAPIError, match="append-only"):
                await c.execute(
                    text("DELETE FROM evidence.decision_receipt WHERE receipt_id = :r"),
                    {"r": uuid.UUID(r_gone)},
                )
        async with engine(owner_url) as eng, eng.begin() as c:
            counts = await retention.purge_expired(
                c, {"E3": 30}, now=datetime.now(UTC) + timedelta(days=31)
            )
            assert counts["purged"] >= 1 and counts["held"] >= 1
            left = set(
                (
                    await c.execute(
                        text(
                            "SELECT receipt_id::text FROM evidence.decision_receipt WHERE tenant_id = :t"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).scalars()
            )
            assert left == {r_kept}
            entries = (
                await c.execute(
                    text("SELECT count(*) FROM evidence.ledger_entry WHERE record_id IN (:a, :b)"),
                    {"a": r_kept, "b": r_gone},
                )
            ).scalar_one()
            assert entries == 2
            deleted = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE event_type = "
                        "'com.zoiko.id.evidence.retention.deleted.v1' AND aggregate_id = :r"
                    ),
                    {"r": r_gone},
                )
            ).scalar_one()
            assert deleted == 1

    asyncio.run(run())


def test_kek_rotation_and_tenant_kek_rewrap(
    rt_url: str, owner_url: str, fresh_runtime: Any
) -> None:
    """DEC-F5 §4/§7: subject keys follow a KEK rotation and a move to the tenant's own KEK in the
    background, the data stays readable throughout, and an erased subject is never rewrapped."""
    tenant, subject, erased = uid(), uid(), uid()
    rt = fresh_runtime

    async def kek_of(c: Any, ref_of: str) -> tuple[str, int]:
        row = (
            await c.execute(
                text(
                    "SELECT kek_id, kek_version FROM evidence.subject_key WHERE tenant_id = :t AND subject_ref = :s"
                ),
                {"t": uuid.UUID(tenant), "s": ref_of},
            )
        ).one()
        return row.kek_id, int(row.kek_version)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            obj, _ = await vault.store_object(
                c,
                tenant,
                content=b"passport scan",
                object_type="verification.document",
                classification="D3",
                retention_class="raw",
                subject_principal_id=subject,
            )
            await vault.store_object(
                c,
                tenant,
                content=b"x",
                object_type="verification.document",
                classification="D3",
                retention_class="raw",
                subject_principal_id=erased,
            )
            await vault.erase_subject(c, tenant, erased, request_ref="dsr-k")
        ref = await vault.subject_ref(tenant, subject, rt)
        rt.provider.rotate_key(rt.subject_kek)
        async with engine(owner_url) as eng, eng.begin() as c:
            assert (
                await vault.rewrap_subject_keys(c, rt=rt, tenant_id=tenant) == 1
            )  # not the erased one
            assert await kek_of(c, ref) == (rt.subject_kek.key_id, 2)
            assert await vault.rewrap_subject_keys(c, rt=rt, tenant_id=tenant) == 0
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert await vault.read_object(c, tenant, obj) == b"passport scan"

        tenant_kek = KeyRef(KeyPurpose.SUBJECT_ERASURE_KEY, f"tenant-kek-{tenant[:8]}")
        rt.provider.ensure_key(tenant_kek.purpose, tenant_kek.key_id)
        moved = dataclasses.replace(rt, tenant_keks={tenant: tenant_kek})
        configure_runtime(moved)
        async with engine(owner_url) as eng, eng.begin() as c:
            assert await vault.rewrap_subject_keys(c, rt=moved, tenant_id=tenant) == 1
            assert await kek_of(c, ref) == (tenant_kek.key_id, 1)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert await vault.read_object(c, tenant, obj) == b"passport scan"
            new, _ = await vault.store_object(
                c,
                tenant,
                content=b"selfie",
                object_type="verification.document",
                classification="D3",
                retention_class="raw",
                subject_principal_id=uid(),
            )
            assert await vault.read_object(c, tenant, new) == b"selfie"
        # HYOK: the customer revokes its key; the platform KEK never stands in for it
        await rt.provider.destroy_key(tenant_kek)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(LedgerError):
                await vault.read_object(c, tenant, obj)
        async with engine(owner_url) as eng, eng.begin() as c:
            assert (
                await vault.rewrap_subject_keys(c, rt=moved, tenant_id=tenant) == 0
            )  # skipped, not failed

    asyncio.run(run())


def test_notices_processors_and_automated_decision_review(rt_url: str, owner_url: str) -> None:
    """PRIV-26 notice versions; PRIV-12/20 processor approval; PRIV-22/23 a challenge of a
    significant automated decision routes to a qualified human with the original evidence."""
    tenant, admin, dpo, stranger = uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(PrivacyError, match="PRIVACY_APPROVAL_REQUIRED"):
                await registry.publish_notice(
                    c,
                    tenant,
                    code="PRIVACY_NOTICE",
                    audience="end_user",
                    content="v1 text of the notice",
                    created_by=admin,
                    approved_by=admin,
                )
            n1 = await registry.publish_notice(
                c,
                tenant,
                code="PRIVACY_NOTICE",
                audience="end_user",
                content="v1 text of the notice",
                created_by=admin,
                approved_by=dpo,
            )
            n2 = await registry.publish_notice(
                c,
                tenant,
                code="PRIVACY_NOTICE",
                audience="end_user",
                content="v2 text of the notice",
                created_by=admin,
                approved_by=dpo,
            )
            assert (n1.version, n2.version) == (1, 2)
            assert (await registry.require_notice(c, "PRIVACY_NOTICE@1")).status == "RETIRED"
            with pytest.raises(PrivacyError, match="PRIVACY_NOTICE_UNKNOWN"):
                await registry.require_notice(c, "PRIVACY_NOTICE@9")
            arts = await provenance.artifact(c, tenant, "CONSENT_NOTICE_VERSION", "PRIVACY_NOTICE")
            assert [a["version"] for a in arts] == ["1", "2"]
            with pytest.raises(DBAPIError, match="immutable"):
                async with c.begin_nested():
                    await c.execute(
                        text(
                            "UPDATE privacy.notice SET content_hash = 'sha-256:00' WHERE version = 1"
                        )
                    )

            await registry.register_processor(
                c,
                tenant,
                processor_id="mailer",
                name="Mailer Ltd",
                role="PROCESSOR",
                data_categories=["D1"],
                locations=["GB", "IE"],
                created_by=admin,
            )
            with pytest.raises(PrivacyError, match="contract"):
                await registry.set_processor_status(c, tenant, "mailer", "APPROVED", actor=dpo)
            with pytest.raises(PrivacyError, match="PRIVACY_PROCESSOR_NOT_APPROVED"):
                await registry.require_processor(c, "mailer", "D1")
            await registry.register_processor(
                c,
                tenant,
                processor_id="kyc",
                name="KYC Co",
                role="SUBPROCESSOR",
                data_categories=["D3"],
                locations=["GB"],
                created_by=admin,
                contract_ref="DPA-KYC-2",
            )
            with pytest.raises(PrivacyError, match="independent approver"):
                await registry.set_processor_status(c, tenant, "kyc", "APPROVED", actor=admin)
            p = await registry.set_processor_status(c, tenant, "kyc", "APPROVED", actor=dpo)
            assert p.status == "APPROVED" and str(p.approved_by) == dpo
            with pytest.raises(PrivacyError, match="not approved for D1"):
                await registry.require_processor(c, "kyc", "D1")
            await registry.set_processor_status(c, tenant, "kyc", "SUSPENDED", actor=dpo)
            with pytest.raises(PrivacyError, match="PRIVACY_PROCESSOR_NOT_APPROVED"):
                await registry.require_processor(c, "kyc", "D3")

            await seed_admin(c, tenant, admin, ["evidence.export"])
            d = await decide(c, tenant, admin, "evidence.export")
            receipt_id = str((await receipt_of(c, d.decision_id)).receipt_id)
            with pytest.raises(PrivacyError, match="PRIVACY_DPIA_REQUIRED"):
                await registry.create_adm_profile(
                    c,
                    tenant,
                    decision_class="evidence.export",
                    purpose_code="SECURITY",
                    input_data_categories=["D1"],
                    model_or_rules_ref="policy:evidence",
                    solely_automated=True,
                    significance_class="MATERIAL",
                    explanation_profile="Exports need E3 assurance.",
                    human_review_route="evidence-ops",
                    created_by=dpo,
                )

            def challenge(rtype: str, subject: str, rid: str) -> Any:
                return privacy.open_request(
                    c,
                    tenant,
                    request_type=rtype,
                    subject_principal_id=subject,
                    requester_principal_id=subject,
                    jurisdiction_pack_id="uk-gdpr",
                    authn_level=2,
                    auth_age=timedelta(minutes=1),
                    scope={"receipt_id": rid},
                )

            req = await challenge("HUMAN_REVIEW", admin, receipt_id)
            with pytest.raises(
                PrivacyError, match="PRIVACY_ADM_PROFILE_MISSING"
            ):  # not a registered ADM
                await privacy.fulfil_request(c, tenant, str(req.request_id), actor=dpo)
            await registry.create_adm_profile(
                c,
                tenant,
                decision_class="evidence.export",
                purpose_code="SECURITY",
                input_data_categories=["D1"],
                model_or_rules_ref="policy:evidence",
                solely_automated=True,
                significance_class="MATERIAL",
                explanation_profile="Exports need E3 assurance and a recent strong login.",
                human_review_route="evidence-ops-reviewers",
                created_by=dpo,
                risk_assessment_ref="DPIA-17",
            )
            r = await privacy.fulfil_request(c, tenant, str(req.request_id), actor=dpo)
            [item] = r.fulfilment_items
            assert r.status == "REVIEW" and item["route"] == "evidence-ops-reviewers"
            assert item["receipt_id"] == receipt_id and item["significance_class"] == "MATERIAL"
            acc = await challenge("ADMT_ACCESS", admin, receipt_id)
            [item] = (
                await privacy.fulfil_request(c, tenant, str(acc.request_id), actor=dpo)
            ).fulfilment_items
            assert "strong login" in item["explanation_profile"]
            other = await challenge("HUMAN_REVIEW", stranger, receipt_id)
            with pytest.raises(
                PrivacyError, match="NOT_FOUND"
            ):  # not a decision about this subject
                await privacy.fulfil_request(c, tenant, str(other.request_id), actor=dpo)

    asyncio.run(run())

"""Privacy control plane and residency against PostgreSQL with RLS (F-W3, F-W5; DEC-F3;
PRIV-01..24; TEN-21).

Purpose and role-map governance, the runtime processing check, consent, rights requests with
proportional verification, crypto-erasure with holds and processor stages, the retention sweep,
and cross-border transfer denial - all with protected audit evidence.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application import vault
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.privacy.application import registry
from zid_platform.modules.privacy.application import service as privacy
from zid_platform.modules.privacy.domain.model import PrivacyError
from zid_platform.modules.tenant.application import residency
from zid_platform.modules.tenant.domain.residency import ResidencyError

DISPOSED: list[tuple[str, str]] = []


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def runtime() -> Any:
    configure_runtime(development_runtime("uk-1"))

    async def dispose(conn: Any, entry: Any, action: str) -> None:
        DISPOSED.append((entry.object_ref, action))

    async def export(conn: Any, entry: Any) -> Any:
        return {"ref": entry.object_ref, "kind": entry.object_type}

    privacy.register_store("test.records", disposer=dispose, exporter=export)
    yield
    configure_runtime(None)


async def governed_activity(
    c: Any, tenant: str, author: str, reviewer: str, *, basis: str = "CONSENT", code: str = "MARKETING_EMAIL"
) -> str:
    pid = await privacy.create_purpose(
        c, tenant, code="NEWSLETTER", description="Product news to opted-in users",
        data_categories=["D1"], recipient_classes=["tenant"], actor=author,
    )
    with pytest.raises(PrivacyError, match="PRIVACY_APPROVAL_REQUIRED"):
        await privacy.activate_purpose(c, tenant, pid, approver=author)
    await privacy.activate_purpose(c, tenant, pid, approver=reviewer)
    return await privacy.create_activity(
        c, tenant, code=code, name="Newsletter", purpose_codes=["NEWSLETTER"], data_categories=["D1"],
        lawful_basis=basis, jurisdiction_pack_id="uk-gdpr", recipient_classes=["tenant"],
        launch_flow=True, actor=author,
    )


def test_registry_role_map_consent_and_processing_check(rt_url: str) -> None:
    """DEC-F3 release check; PRIV-01/02/03/04/05/27."""
    tenant, author, reviewer, subject = uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            aid = await governed_activity(c, tenant, author, reviewer)
            with pytest.raises(PrivacyError, match="PRIVACY_ROLE_ASSIGNMENT_MISSING"):
                await privacy.activate_activity(c, tenant, aid, actor=author)
            blockers = await privacy.release_check(c)
            assert [b["activity_code"] for b in blockers] == ["MARKETING_EMAIL"]
            with pytest.raises(PrivacyError, match="independent approver"):
                await privacy.assign_role(
                    c, tenant, aid, data_controller_principal_id=tenant, zoiko_role="PROCESSOR",
                    jurisdiction_pack_id="uk-gdpr", approval_ref="COUNSEL-1", created_by=author,
                    approved_by=author, contract_or_dpa_ref="DPA-7",
                )
            await privacy.assign_role(
                c, tenant, aid, data_controller_principal_id=tenant, zoiko_role="PROCESSOR",
                jurisdiction_pack_id="uk-gdpr", approval_ref="COUNSEL-1", created_by=author,
                approved_by=reviewer, contract_or_dpa_ref="DPA-7",
            )
            await privacy.activate_activity(c, tenant, aid, actor=author)
            assert await privacy.release_check(c) == []

            def check(**kw: Any) -> Any:
                args = {"activity_code": "MARKETING_EMAIL", "purpose_code": "NEWSLETTER",
                        "data_category": "D1", "subject_principal_id": subject, **kw}
                return privacy.check_processing(c, tenant, **args)

            assert (await check()).reason == "PRIVACY_CONSENT_REQUIRED"
            assert (await check(purpose_code="ANALYTICS")).reason == "PRIVACY_PURPOSE_NOT_DECLARED"
            assert (await check(data_category="D3")).reason == "PRIVACY_DATA_CATEGORY_NOT_ALLOWED"
            assert (await check(activity_code="UNKNOWN")).reason == "PRIVACY_ACTIVITY_NOT_ACTIVE"
            with pytest.raises(PrivacyError, match="PRIVACY_NOTICE_UNKNOWN"):  # PRIV-26
                await privacy.give_consent(
                    c, tenant, subject_principal_id=subject, activity_code="MARKETING_EMAIL",
                    purpose_code="NEWSLETTER", notice_version="NEWS_NOTICE@1", capture_method="WEB",
                    affirmative_action="checkbox:newsletter",
                )
            await registry.publish_notice(
                c, tenant, code="NEWS_NOTICE", audience="end_user", content="We send product news monthly.",
                created_by=author, approved_by=reviewer, processing_activity_codes=["MARKETING_EMAIL"],
            )
            consent = await privacy.give_consent(
                c, tenant, subject_principal_id=subject, activity_code="MARKETING_EMAIL",
                purpose_code="NEWSLETTER", notice_version="NEWS_NOTICE@1", capture_method="WEB",
                affirmative_action="checkbox:newsletter",
            )
            ok = await check()
            assert ok.allowed and ok.consent_id == consent and ok.lawful_basis == "CONSENT"
            await privacy.withdraw_consent(c, tenant, consent, subject_principal_id=subject, reason="no")
            assert (await check()).reason == "PRIVACY_CONSENT_REQUIRED"  # PRIV-02
            with pytest.raises(PrivacyError, match="PRIVACY_CONSENT_TERMINAL"):
                await privacy.withdraw_consent(c, tenant, consent, subject_principal_id=subject, reason="x")
            audits = set(
                (await c.execute(text("SELECT event_type FROM evidence.audit_record"))).scalars()
            )
            assert {"privacy.consent.given", "privacy.consent.withdrawn",
                    "privacy.processing_activity.changed"} <= audits
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(DBAPIError, match="terminal"):  # PRIV-03 in the database too
                await c.execute(text("UPDATE privacy.consent_receipt SET status = 'ACTIVE', withdrawn_at = NULL"))
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            contract = await governed_contract(c, tenant, author, reviewer)
            with pytest.raises(PrivacyError, match="PRIVACY_FALSE_CONSENT"):  # PRIV-04
                await privacy.give_consent(
                    c, tenant, subject_principal_id=subject, activity_code=contract,
                    purpose_code="ACCOUNT", notice_version="NEWS_NOTICE@1", capture_method="WEB",
                    affirmative_action="checkbox",
                )

    asyncio.run(run())


async def governed_contract(c: Any, tenant: str, author: str, reviewer: str) -> str:
    pid = await privacy.create_purpose(
        c, tenant, code="ACCOUNT", description="Operate the user's account", data_categories=["D1"],
        actor=author,
    )
    await privacy.activate_purpose(c, tenant, pid, approver=reviewer)
    aid = await privacy.create_activity(
        c, tenant, code="ACCOUNT_SERVICE", name="Account", purpose_codes=["ACCOUNT"],
        data_categories=["D1"], lawful_basis="CONTRACT", jurisdiction_pack_id="uk-gdpr", actor=author,
    )
    await privacy.assign_role(
        c, tenant, aid, data_controller_principal_id=tenant, zoiko_role="CONTROLLER",
        jurisdiction_pack_id="uk-gdpr", approval_ref="COUNSEL-2", created_by=author, approved_by=reviewer,
    )
    await privacy.activate_activity(c, tenant, aid, actor=author)
    return "ACCOUNT_SERVICE"


def test_rights_requests_verification_access_and_opt_out(rt_url: str) -> None:
    """PRIV-13/14/15/18, deadlines never guessed (COUNSEL_PENDING)."""
    tenant, author, reviewer, subject, agent = uid(), uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await governed_contract(c, tenant, author, reviewer)
            await privacy.register_personal_data(
                c, tenant, subject_principal_id=subject, store_name="test.records",
                object_type="profile", object_ref="rec-1", purpose_code="ACCOUNT", data_category="D1",
                retention_policy_code=None,
            )
            opt = await privacy.open_request(
                c, tenant, request_type="OPT_OUT_SALE_SHARING", subject_principal_id=subject,
                requester_principal_id=None, jurisdiction_pack_id="us-ca-ccpa",
            )
            opt = await privacy.store.load_request(c, str(opt.request_id))
            assert opt.identity_verification_state == "NOT_REQUIRED" and opt.status == "COMPLETED"
            prefs = {p.preference_type: p.value for p in await privacy.store.preferences_of(c, subject)}
            assert prefs == {"OPT_OUT_SALE_SHARING": True}

            # a takeover with weak account evidence: stays unverified, no disclosure (PRIV-14)
            weak = await privacy.open_request(
                c, tenant, request_type="ACCESS", subject_principal_id=subject,
                requester_principal_id=subject, jurisdiction_pack_id="uk-gdpr", authn_level=1,
                auth_age=timedelta(minutes=1),
            )
            assert weak.identity_verification_state == "PENDING"
            assert weak.deadline_at is None and weak.deadline_source == "COUNSEL_PENDING"
            with pytest.raises(PrivacyError, match="PRIVACY_VERIFICATION_REQUIRED"):
                await privacy.fulfil_request(c, tenant, str(weak.request_id), actor=agent)
            await privacy.verify_request(
                c, tenant, str(weak.request_id), verified=True, level=2, method="step-up", actor=agent
            )
            done = await privacy.fulfil_request(c, tenant, str(weak.request_id), actor=agent)
            assert done.status == "COMPLETED" and done.response_package_ref is not None
            raw = await privacy.read_package(c, tenant, str(weak.request_id), subject_principal_id=subject)
            pkg = json.loads(raw)
            assert pkg["manifest"]["format"] == "zid-privacy-export/1"
            assert pkg["records"]["test.records"] == [{"kind": "profile", "ref": "rec-1"}]
            assert pkg["preferences"][0]["preference_type"] == "OPT_OUT_SALE_SHARING"
            with pytest.raises(PrivacyError, match="NOT_FOUND"):  # nobody else downloads it
                await privacy.read_package(c, tenant, str(weak.request_id), subject_principal_id=agent)

            # a fresh strong self-request is verified by the session itself (§14)
            obj = await privacy.open_request(
                c, tenant, request_type="OBJECTION", subject_principal_id=subject,
                requester_principal_id=subject, jurisdiction_pack_id="uk-gdpr", authn_level=2,
                auth_age=timedelta(minutes=2), scope={"purpose_code": "ACCOUNT"},
            )
            assert obj.identity_verification_state == "VERIFIED"
            await privacy.fulfil_request(c, tenant, str(obj.request_id), actor=agent)
            d = await privacy.check_processing(
                c, tenant, activity_code="ACCOUNT_SERVICE", purpose_code="ACCOUNT", data_category="D1",
                subject_principal_id=subject,
            )
            assert d.reason == "PRIVACY_PROCESSING_RESTRICTED"  # PRIV-18

    asyncio.run(run())


def test_erasure_holds_processors_and_retention(rt_url: str, owner_url: str) -> None:
    """PRIV-06/07/08/09/12/17/20."""
    tenant, author, reviewer, subject, legal, approver = uid(), uid(), uid(), uid(), uid(), uid()
    DISPOSED.clear()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await governed_contract(c, tenant, author, reviewer)
            await privacy.create_retention_policy(
                c, tenant, code="SHORT", object_type="profile", jurisdiction_pack_id="uk-gdpr",
                trigger="CREATED_AT", retention_days=0, action="CRYPTO_SHRED", created_by=author,
                approved_by=reviewer, backup_erasure_window_days=35,
            )
            await privacy.create_retention_policy(
                c, tenant, code="STATUTORY", object_type="invoice", jurisdiction_pack_id="uk-gdpr",
                trigger="CREATED_AT", retention_days=3650, action="DELETE", created_by=author,
                approved_by=reviewer, legally_required=True,
            )
            past = datetime.now(UTC) - timedelta(days=1)
            reg = {"subject_principal_id": subject, "store_name": "test.records", "purpose_code": "ACCOUNT",
                   "data_category": "D1"}
            await privacy.register_personal_data(c, tenant, object_type="profile", object_ref="p-1",
                                                 retention_policy_code="SHORT", trigger_at=past, **reg)
            await privacy.register_personal_data(c, tenant, object_type="registry_extract",
                                                 object_ref="held-1", retention_policy_code="SHORT",
                                                 trigger_at=past, **reg)
            await privacy.register_personal_data(c, tenant, object_type="invoice", object_ref="inv-1",
                                                 retention_policy_code="STATUTORY", **reg)
            with pytest.raises(PrivacyError, match="PRIVACY_PROCESSOR_NOT_APPROVED"):  # §23
                await privacy.register_personal_data(c, tenant, object_type="crm_contact", object_ref="crm-0",
                                                     retention_policy_code=None, processor_id="crm-vendor", **reg)
            await registry.register_processor(
                c, tenant, processor_id="crm-vendor", name="CRM Vendor", role="PROCESSOR",
                data_categories=["D1"], locations=["GB"], created_by=author, deletion_sla_days=30,
                contract_ref="DPA-CRM-1",
            )
            await registry.set_processor_status(c, tenant, "crm-vendor", "APPROVED", actor=reviewer)
            await privacy.register_personal_data(c, tenant, object_type="crm_contact", object_ref="crm-1",
                                                 retention_policy_code=None, processor_id="crm-vendor", **reg)
            await vault.store_object(c, tenant, content=b"doc", object_type="verification.document",
                                     classification="D3", retention_class="raw", subject_principal_id=subject)
            hold = await vault.create_hold(
                c, tenant, basis="litigation", reference="m-9", requested_by=legal, approved_by=approver,
                subject_principal_ids=[subject], object_types=["registry_extract"],
                review_at=datetime.now(UTC) + timedelta(days=30),
            )
        async with engine(owner_url) as eng, eng.begin() as c:  # the retention worker
            counts = await privacy.retention_sweep(c)
            assert counts["disposed"] >= 1 and counts["held"] >= 1
            assert ("p-1", "CRYPTO_SHRED") in DISPOSED and all(r != "held-1" for r, _ in DISPOSED)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await vault.release_hold(c, tenant, hold.legal_hold_id, by=approver, reason="settled")
        async with engine(owner_url) as eng, eng.begin() as c:
            await privacy.retention_sweep(c)  # PRIV-08: original due date, no restart
            assert ("held-1", "CRYPTO_SHRED") in DISPOSED
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            req = await privacy.open_request(
                c, tenant, request_type="ERASURE", subject_principal_id=subject,
                requester_principal_id=subject, jurisdiction_pack_id="uk-gdpr", authn_level=2,
                auth_age=timedelta(minutes=1),
            )
            r = await privacy.fulfil_request(c, tenant, str(req.request_id), actor=reviewer)
            items = {i["item_id"]: i for i in r.fulfilment_items}
            assert items["test.records:inv-1"]["state"] == "RETAINED_RESTRICTED"
            assert items["test.records:inv-1"]["reason"] == "LEGAL_OBLIGATION"
            assert items["test.records:crm-1"]["state"] == "PENDING_PROCESSOR_ACK"
            assert items["test.records:crm-1"]["deletion_due"] is not None  # processor deletion SLA
            assert items["evidence-vault"]["outcome"] == "ERASED"
            assert items["backups"]["backup_erasure_due"] is not None
            assert r.status == "FULFILLING"  # PRIV-12: never complete while a processor is pending
            r = await privacy.acknowledge_item(
                c, tenant, str(req.request_id), "test.records:crm-1", ack_ref="crm-ticket-77", actor=reviewer
            )
            assert r.status == "PARTIALLY_DENIED" and r.completed_at is not None  # PRIV-17
            events = set(
                (await c.execute(text("SELECT event_type FROM evidence.audit_record"))).scalars()
            )
            assert {"privacy.deletion.started", "privacy.deletion.completed", "privacy.rights.completed",
                    "privacy.processor.deletion.confirmed", "privacy.retention.due"} <= events

    asyncio.run(run())


def test_residency_transfer_gateway(rt_url: str) -> None:
    """PRIV-24, TEN-21/22: restricted data leaves its boundary only under an approved rule."""
    tenant, admin, second, subject = uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(ResidencyError, match="RESIDENCY_PROFILE_UNSUPPORTED"):
                await residency.set_residency(
                    c, tenant, profile="SRP-2", home_cell_id="uk-1", recovery_cells=[], prohibited_cells=[],
                    operator_juris=["GB"], key_profile="PLATFORM_MANAGED", jurisdiction_pack_id="uk-gdpr",
                    approval_ref="LEGAL-1", actor=admin,
                )
            r = await residency.set_residency(
                c, tenant, profile="SRP-1", home_cell_id="uk-1", recovery_cells=[], prohibited_cells=[],
                operator_juris=[], key_profile="PLATFORM_MANAGED", jurisdiction_pack_id="uk-gdpr",
                approval_ref="LEGAL-1", actor=admin,
            )
            assert r.revision == 1 and r.placement_epoch == 1
            d = await residency.authorize_transfer(c, tenant, data_class="DC3", destination_cell_id="eu-1",
                                                   purpose_code="SUPPORT")
            assert not d.allowed and d.code == "RESIDENCY_TRANSFER_DENIED" and d.cross_border
            assert (await residency.authorize_transfer(c, tenant, data_class="DC0", destination_cell_id="eu-1",
                                                       purpose_code="SUPPORT")).allowed
            assert (await residency.authorize_transfer(c, tenant, data_class="DC2", destination_cell_id="mars-1",
                                                       purpose_code="SUPPORT")).code == "DEPENDENCY_RESIDENCY_UNKNOWN"
            with pytest.raises(ResidencyError, match="not a mechanism"):
                await residency.add_rule(c, tenant, data_class="DC3", destination_cell_id="eu-1",
                                         purpose_code="SUPPORT", mechanism="eu_sccs", field_allowlist=["ticket_id"],
                                         approval_ref="LEGAL-2", created_by=admin, approved_by=second, valid_to=None)
            await residency.add_rule(c, tenant, data_class="DC3", destination_cell_id="eu-1",
                                     purpose_code="SUPPORT", mechanism="uk_idta", field_allowlist=["ticket_id"],
                                     approval_ref="LEGAL-2", created_by=admin, approved_by=second, valid_to=None)
            d = await residency.authorize_transfer(c, tenant, data_class="DC3", destination_cell_id="eu-1",
                                                   purpose_code="SUPPORT")
            assert d.allowed and d.field_allowlist == ("ticket_id",)
            assert not (await residency.authorize_transfer(c, tenant, data_class="DC3", destination_cell_id="eu-1",
                                                           purpose_code="ANALYTICS")).allowed
            audits = [
                (row.event_type, row.outcome)
                for row in (await c.execute(text(
                    "SELECT event_type, outcome FROM evidence.audit_record WHERE event_type LIKE 'cross_cell%'"
                ))).all()
            ]
            assert ("cross_cell_transfer.denied", "DENIED") in audits
            assert ("cross_cell_transfer.authorized", "SUCCESS") in audits

    asyncio.run(run())

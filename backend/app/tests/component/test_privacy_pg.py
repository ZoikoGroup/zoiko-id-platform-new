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
    c: Any,
    tenant: str,
    author: str,
    reviewer: str,
    *,
    basis: str = "CONSENT",
    code: str = "MARKETING_EMAIL",
) -> str:
    pid = await privacy.create_purpose(
        c,
        tenant,
        code="NEWSLETTER",
        description="Product news to opted-in users",
        data_categories=["D1"],
        recipient_classes=["tenant"],
        actor=author,
    )
    with pytest.raises(PrivacyError, match="PRIVACY_APPROVAL_REQUIRED"):
        await privacy.activate_purpose(c, tenant, pid, approver=author)
    await privacy.activate_purpose(c, tenant, pid, approver=reviewer)
    return await privacy.create_activity(
        c,
        tenant,
        code=code,
        name="Newsletter",
        purpose_codes=["NEWSLETTER"],
        data_categories=["D1"],
        lawful_basis=basis,
        jurisdiction_pack_id="uk-gdpr",
        recipient_classes=["tenant"],
        launch_flow=True,
        actor=author,
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
                    c,
                    tenant,
                    aid,
                    data_controller_principal_id=tenant,
                    zoiko_role="PROCESSOR",
                    jurisdiction_pack_id="uk-gdpr",
                    approval_ref="COUNSEL-1",
                    created_by=author,
                    approved_by=author,
                    contract_or_dpa_ref="DPA-7",
                )
            await privacy.assign_role(
                c,
                tenant,
                aid,
                data_controller_principal_id=tenant,
                zoiko_role="PROCESSOR",
                jurisdiction_pack_id="uk-gdpr",
                approval_ref="COUNSEL-1",
                created_by=author,
                approved_by=reviewer,
                contract_or_dpa_ref="DPA-7",
            )
            await privacy.activate_activity(c, tenant, aid, actor=author)
            assert await privacy.release_check(c) == []

            def check(**kw: Any) -> Any:
                args = {
                    "activity_code": "MARKETING_EMAIL",
                    "purpose_code": "NEWSLETTER",
                    "data_category": "D1",
                    "subject_principal_id": subject,
                    **kw,
                }
                return privacy.check_processing(c, tenant, **args)

            assert (await check()).reason == "PRIVACY_CONSENT_REQUIRED"
            assert (await check(purpose_code="ANALYTICS")).reason == "PRIVACY_PURPOSE_NOT_DECLARED"
            assert (await check(data_category="D3")).reason == "PRIVACY_DATA_CATEGORY_NOT_ALLOWED"
            assert (await check(activity_code="UNKNOWN")).reason == "PRIVACY_ACTIVITY_NOT_ACTIVE"
            with pytest.raises(PrivacyError, match="PRIVACY_NOTICE_UNKNOWN"):  # PRIV-26
                await privacy.give_consent(
                    c,
                    tenant,
                    subject_principal_id=subject,
                    activity_code="MARKETING_EMAIL",
                    purpose_code="NEWSLETTER",
                    notice_version="NEWS_NOTICE@1",
                    capture_method="WEB",
                    affirmative_action="checkbox:newsletter",
                )
            await registry.publish_notice(
                c,
                tenant,
                code="NEWS_NOTICE",
                audience="end_user",
                content="We send product news monthly.",
                created_by=author,
                approved_by=reviewer,
                processing_activity_codes=["MARKETING_EMAIL"],
            )
            consent = await privacy.give_consent(
                c,
                tenant,
                subject_principal_id=subject,
                activity_code="MARKETING_EMAIL",
                purpose_code="NEWSLETTER",
                notice_version="NEWS_NOTICE@1",
                capture_method="WEB",
                affirmative_action="checkbox:newsletter",
            )
            ok = await check()
            assert ok.allowed and ok.consent_id == consent and ok.lawful_basis == "CONSENT"
            await privacy.withdraw_consent(
                c, tenant, consent, subject_principal_id=subject, reason="no"
            )
            assert (await check()).reason == "PRIVACY_CONSENT_REQUIRED"  # PRIV-02
            with pytest.raises(PrivacyError, match="PRIVACY_CONSENT_TERMINAL"):
                await privacy.withdraw_consent(
                    c, tenant, consent, subject_principal_id=subject, reason="x"
                )
            audits = set(
                (await c.execute(text("SELECT event_type FROM evidence.audit_record"))).scalars()
            )
            assert {
                "privacy.consent.given",
                "privacy.consent.withdrawn",
                "privacy.processing_activity.changed",
            } <= audits
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(DBAPIError, match="terminal"):  # PRIV-03 in the database too
                await c.execute(
                    text(
                        "UPDATE privacy.consent_receipt SET status = 'ACTIVE', withdrawn_at = NULL"
                    )
                )
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            contract = await governed_contract(c, tenant, author, reviewer)
            with pytest.raises(PrivacyError, match="PRIVACY_FALSE_CONSENT"):  # PRIV-04
                await privacy.give_consent(
                    c,
                    tenant,
                    subject_principal_id=subject,
                    activity_code=contract,
                    purpose_code="ACCOUNT",
                    notice_version="NEWS_NOTICE@1",
                    capture_method="WEB",
                    affirmative_action="checkbox",
                )

    asyncio.run(run())


async def governed_contract(c: Any, tenant: str, author: str, reviewer: str) -> str:
    pid = await privacy.create_purpose(
        c,
        tenant,
        code="ACCOUNT",
        description="Operate the user's account",
        data_categories=["D1"],
        actor=author,
    )
    await privacy.activate_purpose(c, tenant, pid, approver=reviewer)
    aid = await privacy.create_activity(
        c,
        tenant,
        code="ACCOUNT_SERVICE",
        name="Account",
        purpose_codes=["ACCOUNT"],
        data_categories=["D1"],
        lawful_basis="CONTRACT",
        jurisdiction_pack_id="uk-gdpr",
        actor=author,
    )
    await privacy.assign_role(
        c,
        tenant,
        aid,
        data_controller_principal_id=tenant,
        zoiko_role="CONTROLLER",
        jurisdiction_pack_id="uk-gdpr",
        approval_ref="COUNSEL-2",
        created_by=author,
        approved_by=reviewer,
    )
    await privacy.activate_activity(c, tenant, aid, actor=author)
    return "ACCOUNT_SERVICE"


def test_rights_requests_verification_access_and_opt_out(rt_url: str) -> None:
    """PRIV-13/14/15/18, PRIV-19 (a portable export without internal security material), deadlines never
    guessed (COUNSEL_PENDING)."""
    tenant, author, reviewer, subject, agent = uid(), uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await governed_contract(c, tenant, author, reviewer)
            await privacy.register_personal_data(
                c,
                tenant,
                subject_principal_id=subject,
                store_name="test.records",
                object_type="profile",
                object_ref="rec-1",
                purpose_code="ACCOUNT",
                data_category="D1",
                retention_policy_code=None,
            )
            opt = await privacy.open_request(
                c,
                tenant,
                request_type="OPT_OUT_SALE_SHARING",
                subject_principal_id=subject,
                requester_principal_id=None,
                jurisdiction_pack_id="us-ca-ccpa",
            )
            opt = await privacy.store.load_request(c, str(opt.request_id))
            assert opt.identity_verification_state == "NOT_REQUIRED" and opt.status == "COMPLETED"
            prefs = {
                p.preference_type: p.value for p in await privacy.store.preferences_of(c, subject)
            }
            assert prefs == {"OPT_OUT_SALE_SHARING": True}

            # a takeover with weak account evidence: stays unverified, no disclosure (PRIV-14)
            weak = await privacy.open_request(
                c,
                tenant,
                request_type="ACCESS",
                subject_principal_id=subject,
                requester_principal_id=subject,
                jurisdiction_pack_id="uk-gdpr",
                authn_level=1,
                auth_age=timedelta(minutes=1),
            )
            assert weak.identity_verification_state == "PENDING"
            assert weak.deadline_at is None and weak.deadline_source == "COUNSEL_PENDING"
            with pytest.raises(PrivacyError, match="PRIVACY_VERIFICATION_REQUIRED"):
                await privacy.fulfil_request(c, tenant, str(weak.request_id), actor=agent)
            await privacy.verify_request(
                c,
                tenant,
                str(weak.request_id),
                verified=True,
                level=2,
                method="step-up",
                actor=agent,
            )
            done = await privacy.fulfil_request(c, tenant, str(weak.request_id), actor=agent)
            assert done.status == "COMPLETED" and done.response_package_ref is not None
            raw = await privacy.read_package(
                c, tenant, str(weak.request_id), subject_principal_id=subject
            )
            pkg = json.loads(raw)
            assert pkg["manifest"]["format"] == "zid-privacy-export/1"
            assert pkg["records"]["test.records"] == [{"kind": "profile", "ref": "rec-1"}]
            assert pkg["preferences"][0]["preference_type"] == "OPT_OUT_SALE_SHARING"

            # PRIV-19: structured and machine-readable (it parsed as JSON above), and it carries nothing of the
            # platform's own security machinery: no keys, secrets, tokens, hashes or ciphertext
            def keys(node: Any) -> list[str]:
                if isinstance(node, dict):
                    return [str(k) for k in node] + [x for v in node.values() for x in keys(v)]
                if isinstance(node, list):
                    return [x for v in node for x in keys(v)]
                return []

            internal = (
                "secret",
                "token",
                "password",
                "hmac",
                "kek",
                "wrapped",
                "ciphertext",
                "salt",
                "signature",
            )
            assert not [k for k in keys(pkg) if any(w in k.lower() for w in internal)], keys(pkg)
            with pytest.raises(PrivacyError, match="NOT_FOUND"):  # nobody else downloads it
                await privacy.read_package(
                    c, tenant, str(weak.request_id), subject_principal_id=agent
                )

            # a fresh strong self-request is verified by the session itself (§14)
            obj = await privacy.open_request(
                c,
                tenant,
                request_type="OBJECTION",
                subject_principal_id=subject,
                requester_principal_id=subject,
                jurisdiction_pack_id="uk-gdpr",
                authn_level=2,
                auth_age=timedelta(minutes=2),
                scope={"purpose_code": "ACCOUNT"},
            )
            assert obj.identity_verification_state == "VERIFIED"
            await privacy.fulfil_request(c, tenant, str(obj.request_id), actor=agent)
            d = await privacy.check_processing(
                c,
                tenant,
                activity_code="ACCOUNT_SERVICE",
                purpose_code="ACCOUNT",
                data_category="D1",
                subject_principal_id=subject,
            )
            assert d.reason == "PRIVACY_PROCESSING_RESTRICTED"  # PRIV-18

    asyncio.run(run())


def test_erasure_holds_processors_and_retention(rt_url: str, owner_url: str) -> None:
    """PRIV-06/07/08/09/10/12/17/20."""
    tenant, author, reviewer, subject, legal, approver = uid(), uid(), uid(), uid(), uid(), uid()
    DISPOSED.clear()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await governed_contract(c, tenant, author, reviewer)
            await privacy.create_retention_policy(
                c,
                tenant,
                code="SHORT",
                object_type="profile",
                jurisdiction_pack_id="uk-gdpr",
                trigger="CREATED_AT",
                retention_days=0,
                action="CRYPTO_SHRED",
                created_by=author,
                approved_by=reviewer,
                backup_erasure_window_days=35,
            )
            await privacy.create_retention_policy(
                c,
                tenant,
                code="STATUTORY",
                object_type="invoice",
                jurisdiction_pack_id="uk-gdpr",
                trigger="CREATED_AT",
                retention_days=3650,
                action="DELETE",
                created_by=author,
                approved_by=reviewer,
                legally_required=True,
            )
            past = datetime.now(UTC) - timedelta(days=1)
            reg = {
                "subject_principal_id": subject,
                "store_name": "test.records",
                "purpose_code": "ACCOUNT",
                "data_category": "D1",
            }
            await privacy.register_personal_data(
                c,
                tenant,
                object_type="profile",
                object_ref="p-1",
                retention_policy_code="SHORT",
                trigger_at=past,
                **reg,
            )
            await privacy.register_personal_data(
                c,
                tenant,
                object_type="registry_extract",
                object_ref="held-1",
                retention_policy_code="SHORT",
                trigger_at=past,
                **reg,
            )
            await privacy.register_personal_data(
                c,
                tenant,
                object_type="invoice",
                object_ref="inv-1",
                retention_policy_code="STATUTORY",
                **reg,
            )
            with pytest.raises(PrivacyError, match="PRIVACY_PROCESSOR_NOT_APPROVED"):  # §23
                await privacy.register_personal_data(
                    c,
                    tenant,
                    object_type="crm_contact",
                    object_ref="crm-0",
                    retention_policy_code=None,
                    processor_id="crm-vendor",
                    **reg,
                )
            await registry.register_processor(
                c,
                tenant,
                processor_id="crm-vendor",
                name="CRM Vendor",
                role="PROCESSOR",
                data_categories=["D1"],
                locations=["GB"],
                created_by=author,
                deletion_sla_days=30,
                contract_ref="DPA-CRM-1",
            )
            await registry.set_processor_status(c, tenant, "crm-vendor", "APPROVED", actor=reviewer)
            await privacy.register_personal_data(
                c,
                tenant,
                object_type="crm_contact",
                object_ref="crm-1",
                retention_policy_code=None,
                processor_id="crm-vendor",
                **reg,
            )
            await vault.store_object(
                c,
                tenant,
                content=b"doc",
                object_type="verification.document",
                classification="D3",
                retention_class="raw",
                subject_principal_id=subject,
            )
            hold = await vault.create_hold(
                c,
                tenant,
                basis="litigation",
                reference="m-9",
                requested_by=legal,
                approved_by=approver,
                subject_principal_ids=[subject],
                object_types=["registry_extract"],
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
                c,
                tenant,
                request_type="ERASURE",
                subject_principal_id=subject,
                requester_principal_id=subject,
                jurisdiction_pack_id="uk-gdpr",
                authn_level=2,
                auth_age=timedelta(minutes=1),
            )
            from zid_platform.modules.privacy.ports import register_erasure_hook
            from zid_platform.modules.signals.ports import CACHE, forget_subject

            register_erasure_hook(
                "security-state-cache", forget_subject
            )  # what main.py wires at start-up
            CACHE.put(
                tenant, subject, None, datetime.now(UTC)
            )  # PRIV-10: a cached view of the subject
            assert CACHE.get(tenant, subject, risk_class=0, now=datetime.now(UTC)) is not None
            r = await privacy.fulfil_request(c, tenant, str(req.request_id), actor=reviewer)
            items = {i["item_id"]: i for i in r.fulfilment_items}
            assert items["caches"]["state"] == "DONE"
            assert CACHE.get(tenant, subject, risk_class=0, now=datetime.now(UTC)) is None
            assert items["test.records:inv-1"]["state"] == "RETAINED_RESTRICTED"
            assert items["test.records:inv-1"]["reason"] == "LEGAL_OBLIGATION"
            assert items["test.records:crm-1"]["state"] == "PENDING_PROCESSOR_ACK"
            assert items["test.records:crm-1"]["deletion_due"] is not None  # processor deletion SLA
            assert items["evidence-vault"]["outcome"] == "ERASED"
            assert items["backups"]["backup_erasure_due"] is not None
            assert r.status == "FULFILLING"  # PRIV-12: never complete while a processor is pending
            r = await privacy.acknowledge_item(
                c,
                tenant,
                str(req.request_id),
                "test.records:crm-1",
                ack_ref="crm-ticket-77",
                actor=reviewer,
            )
            assert r.status == "PARTIALLY_DENIED" and r.completed_at is not None  # PRIV-17
            events = set(
                (await c.execute(text("SELECT event_type FROM evidence.audit_record"))).scalars()
            )
            assert {
                "privacy.deletion.started",
                "privacy.deletion.completed",
                "privacy.rights.completed",
                "privacy.processor.deletion.confirmed",
                "privacy.retention.due",
            } <= events

    asyncio.run(run())


def test_residency_transfer_gateway(rt_url: str) -> None:
    """PRIV-24, TEN-21/22: restricted data leaves its boundary only under an approved rule."""
    tenant, admin, second = uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(ResidencyError, match="RESIDENCY_PROFILE_UNSUPPORTED"):
                await residency.set_residency(
                    c,
                    tenant,
                    profile="SRP-2",
                    home_cell_id="uk-1",
                    recovery_cells=[],
                    prohibited_cells=[],
                    operator_juris=["GB"],
                    key_profile="PLATFORM_MANAGED",
                    jurisdiction_pack_id="uk-gdpr",
                    approval_ref="LEGAL-1",
                    actor=admin,
                )
            r = await residency.set_residency(
                c,
                tenant,
                profile="SRP-1",
                home_cell_id="uk-1",
                recovery_cells=[],
                prohibited_cells=[],
                operator_juris=[],
                key_profile="PLATFORM_MANAGED",
                jurisdiction_pack_id="uk-gdpr",
                approval_ref="LEGAL-1",
                actor=admin,
            )
            assert r.revision == 1 and r.placement_epoch == 1
            d = await residency.authorize_transfer(
                c, tenant, data_class="DC3", destination_cell_id="eu-1", purpose_code="SUPPORT"
            )
            assert not d.allowed and d.code == "RESIDENCY_TRANSFER_DENIED" and d.cross_border
            assert (
                await residency.authorize_transfer(
                    c, tenant, data_class="DC0", destination_cell_id="eu-1", purpose_code="SUPPORT"
                )
            ).allowed
            assert (
                await residency.authorize_transfer(
                    c,
                    tenant,
                    data_class="DC2",
                    destination_cell_id="mars-1",
                    purpose_code="SUPPORT",
                )
            ).code == "DEPENDENCY_RESIDENCY_UNKNOWN"
            with pytest.raises(ResidencyError, match="not a mechanism"):
                await residency.add_rule(
                    c,
                    tenant,
                    data_class="DC3",
                    destination_cell_id="eu-1",
                    purpose_code="SUPPORT",
                    mechanism="eu_sccs",
                    field_allowlist=["ticket_id"],
                    approval_ref="LEGAL-2",
                    created_by=admin,
                    approved_by=second,
                    valid_to=None,
                )
            await residency.add_rule(
                c,
                tenant,
                data_class="DC3",
                destination_cell_id="eu-1",
                purpose_code="SUPPORT",
                mechanism="uk_idta",
                field_allowlist=["ticket_id"],
                approval_ref="LEGAL-2",
                created_by=admin,
                approved_by=second,
                valid_to=None,
            )
            d = await residency.authorize_transfer(
                c, tenant, data_class="DC3", destination_cell_id="eu-1", purpose_code="SUPPORT"
            )
            assert d.allowed and d.field_allowlist == ("ticket_id",)
            assert not (
                await residency.authorize_transfer(
                    c,
                    tenant,
                    data_class="DC3",
                    destination_cell_id="eu-1",
                    purpose_code="ANALYTICS",
                )
            ).allowed
            audits = [
                (row.event_type, row.outcome)
                for row in (
                    await c.execute(
                        text(
                            "SELECT event_type, outcome FROM evidence.audit_record WHERE event_type LIKE 'cross_cell%'"
                        )
                    )
                ).all()
            ]
            assert ("cross_cell_transfer.denied", "DENIED") in audits
            assert ("cross_cell_transfer.authorized", "SUCCESS") in audits

    asyncio.run(run())


def test_a_person_is_offered_only_consent_they_can_give_with_the_notice_to_cite(
    rt_url: str,
) -> None:
    """PRIV-26 (discovery half): consent options list a consent-based, active activity only once a notice naming it
    is published; they carry the exact `CODE@version` to cite and whether consent is already held. Activities on
    another lawful basis are never offered as a consent choice."""
    tenant, author, reviewer, subject, other = uid(), uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            aid = await governed_activity(c, tenant, author, reviewer)
            await privacy.assign_role(
                c,
                tenant,
                aid,
                data_controller_principal_id=tenant,
                zoiko_role="PROCESSOR",
                jurisdiction_pack_id="uk-gdpr",
                approval_ref="COUNSEL-1",
                created_by=author,
                approved_by=reviewer,
                contract_or_dpa_ref="DPA-7",
            )
            await privacy.activate_activity(c, tenant, aid, actor=author)
            assert (
                await privacy.consent_options(c, subject) == []
            )  # no published notice: nothing to cite, nothing offered
            await registry.publish_notice(
                c,
                tenant,
                code="NEWS_NOTICE",
                audience="end_user",
                content="We send product news monthly.",
                created_by=author,
                approved_by=reviewer,
                processing_activity_codes=["MARKETING_EMAIL"],
            )
            options = await privacy.consent_options(c, subject)
            assert len(options) == 1
            offer = options[0]
            assert (offer["activity_code"], offer["purpose_code"]) == (
                "MARKETING_EMAIL",
                "NEWSLETTER",
            )
            assert offer["notice_version"] == "NEWS_NOTICE@1" and offer["consented"] is False
            # the offer is exactly what give_consent needs: using it as given succeeds
            await privacy.give_consent(
                c,
                tenant,
                subject_principal_id=subject,
                activity_code=offer["activity_code"],
                purpose_code=offer["purpose_code"],
                notice_version=offer["notice_version"],
                capture_method="WEB",
                affirmative_action="checkbox:newsletter",
            )
            assert (await privacy.consent_options(c, subject))[0]["consented"] is True
            assert (await privacy.consent_options(c, other))[0][
                "consented"
            ] is False  # consent is personal
            await privacy.withdraw_consent(
                c,
                tenant,
                str((await privacy.store.consents_of(c, subject))[0].consent_id),
                subject_principal_id=subject,
                reason="no",
            )
            assert (await privacy.consent_options(c, subject))[0]["consented"] is False

    asyncio.run(run())


def test_a_processor_that_cannot_satisfy_the_residency_profile_is_not_approved(rt_url: str) -> None:
    """TEN-20: for an SRP-1 UK tenant, a processor operating in the US cannot be approved (so no feature that
    needs it can be enabled), while one operating in the UK can."""
    tenant, admin, author, reviewer = uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await residency.set_residency(
                c,
                tenant,
                profile="SRP-1",
                home_cell_id="uk-1",
                recovery_cells=[],
                prohibited_cells=[],
                operator_juris=[],
                key_profile="PLATFORM_MANAGED",
                jurisdiction_pack_id="uk-gdpr",
                approval_ref="LEGAL-1",
                actor=admin,
            )
            for pid, where in (("us-sms", ["US"]), ("uk-sms", ["GB"])):
                await registry.register_processor(
                    c,
                    tenant,
                    processor_id=pid,
                    name=pid,
                    role="SUBPROCESSOR",
                    data_categories=["D1"],
                    locations=where,
                    created_by=author,
                    deletion_sla_days=30,
                    contract_ref="DPA-1",
                )
            with pytest.raises(PrivacyError, match="PRIVACY_PROCESSOR_RESIDENCY"):
                await registry.set_processor_status(c, tenant, "us-sms", "APPROVED", actor=reviewer)
            await registry.set_processor_status(c, tenant, "uk-sms", "APPROVED", actor=reviewer)
            with pytest.raises(PrivacyError, match="PRIVACY_PROCESSOR_NOT_APPROVED"):
                await registry.require_processor(c, "us-sms", "D1")

    asyncio.run(run())


def test_rectifying_an_externally_sourced_fact_asks_the_source_instead_of_overwriting(
    rt_url: str,
) -> None:
    """PRIV-16: a person disputing a fact the company register supplied cannot have it edited by hand. The register is
    asked again (a refresh request, whose answer is appended as the superseding observation) and what the person says
    is kept as a statement. A self-asserted fact still goes to privacy operations."""
    from zid_platform.modules.organization.ports import (
        organization_exists,
        request_registry_refresh,
    )

    tenant, author, reviewer, subject, agent, org = uid(), uid(), uid(), uid(), uid(), uid()

    async def recheck(
        conn: Any, tenant_id: str, fact: Any, *, reason_ref: str, now: datetime
    ) -> str:
        assert await organization_exists(conn, fact["organization_id"])
        return await request_registry_refresh(
            conn, tenant_id, fact["organization_id"], reason_ref=reason_ref, now=now
        )

    async def open_verified(c: Any, scope: dict[str, Any]) -> Any:
        r = await privacy.open_request(
            c,
            tenant,
            request_type="RECTIFICATION",
            subject_principal_id=subject,
            requester_principal_id=subject,
            jurisdiction_pack_id="uk-gdpr",
            authn_level=1,
            auth_age=timedelta(minutes=1),
            scope=scope,
        )
        await privacy.verify_request(
            c, tenant, str(r.request_id), verified=True, level=2, method="step-up", actor=agent
        )
        return r

    async def run() -> None:
        privacy.register_rectification_source("REGISTRY", recheck)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await governed_contract(c, tenant, author, reviewer)
            await c.execute(
                text(
                    "INSERT INTO org.organization (organization_id, tenant_id, legal_name, valid_from) "
                    "VALUES (:o, :t, 'Acme Ltd', now())"
                ),
                {"o": uuid.UUID(org), "t": uuid.UUID(tenant)},
            )
            fact = {"kind": "REGISTRY", "organization_id": org, "field": "legal_name"}

            # a replacement value for a register fact is refused, not applied
            r = await open_verified(c, {"fact": {**fact, "new_value": "Acme Group Ltd"}})
            with pytest.raises(PrivacyError, match="PRIVACY_SOURCE_AUTHORITATIVE"):
                await privacy.fulfil_request(c, tenant, str(r.request_id), actor=agent)
            name = (await c.execute(text("SELECT legal_name FROM org.organization"))).scalar_one()
            assert name == "Acme Ltd"  # untouched

            # the dispute asks the register again, and is idempotent while a re-check is open
            r = await open_verified(c, {"fact": fact})
            out = await privacy.fulfil_request(c, tenant, str(r.request_id), actor=agent)
            kinds = {i["item_id"]: i for i in out.fulfilment_items}
            assert kinds["source-refresh"]["manual_overwrite"] is False
            assert kinds["statement-of-disagreement"]["state"] == "DONE"
            r2 = await open_verified(c, {"fact": fact})
            out2 = await privacy.fulfil_request(c, tenant, str(r2.request_id), actor=agent)
            first = kinds["source-refresh"]["refresh_request_id"]
            second = {i["item_id"]: i for i in out2.fulfilment_items}["source-refresh"][
                "refresh_request_id"
            ]
            assert first == second
            open_count = (
                await c.execute(
                    text("SELECT count(*) FROM org.registry_refresh_request WHERE status = 'OPEN'")
                )
            ).scalar_one()
            assert open_count == 1

            # a self-asserted fact goes to people
            r3 = await open_verified(
                c, {"fact": {"kind": "SELF_ASSERTED", "field": "display_name"}}
            )
            out3 = await privacy.fulfil_request(c, tenant, str(r3.request_id), actor=agent)
            assert out3.fulfilment_items[0]["route"] == "privacy-ops"

    try:
        asyncio.run(run())
    finally:
        privacy._RECTIFICATION_SOURCES.pop("REGISTRY", None)


async def _newsletter_consent(
    c: Any, tenant: str, author: str, reviewer: str, subject: str, *, also_on_contract: bool = False
) -> str:
    """A governed consent-based activity for the NEWSLETTER purpose, a notice, and one consent of `subject`."""
    aid = await governed_activity(c, tenant, author, reviewer)
    await privacy.assign_role(
        c,
        tenant,
        aid,
        data_controller_principal_id=tenant,
        zoiko_role="PROCESSOR",
        jurisdiction_pack_id="uk-gdpr",
        approval_ref="COUNSEL-1",
        created_by=author,
        approved_by=reviewer,
        contract_or_dpa_ref="DPA-7",
    )
    await privacy.activate_activity(c, tenant, aid, actor=author)
    if also_on_contract:  # the same purpose is also served on a basis that is not consent
        other = await privacy.create_activity(
            c,
            tenant,
            code="NEWS_BY_CONTRACT",
            name="Service notices",
            purpose_codes=["NEWSLETTER"],
            data_categories=["D1"],
            lawful_basis="CONTRACT",
            jurisdiction_pack_id="uk-gdpr",
            actor=author,
        )
        await privacy.assign_role(
            c,
            tenant,
            other,
            data_controller_principal_id=tenant,
            zoiko_role="CONTROLLER",
            jurisdiction_pack_id="uk-gdpr",
            approval_ref="COUNSEL-3",
            created_by=author,
            approved_by=reviewer,
        )
        await privacy.activate_activity(c, tenant, other, actor=author)
    await registry.publish_notice(
        c,
        tenant,
        code="NEWS_NOTICE",
        audience="end_user",
        content="We send product news monthly.",
        created_by=author,
        approved_by=reviewer,
        processing_activity_codes=["MARKETING_EMAIL"],
    )
    return await privacy.give_consent(
        c,
        tenant,
        subject_principal_id=subject,
        activity_code="MARKETING_EMAIL",
        purpose_code="NEWSLETTER",
        notice_version="NEWS_NOTICE@1",
        capture_method="WEB",
        affirmative_action="checkbox:newsletter",
    )


async def _register_newsletter_data(
    c: Any, tenant: str, subject: str, author: str, reviewer: str
) -> None:
    await privacy.create_retention_policy(
        c,
        tenant,
        code="NEWS_STATUTORY",
        object_type="invoice",
        jurisdiction_pack_id="uk-gdpr",
        trigger="CREATED_AT",
        retention_days=3650,
        action="DELETE",
        created_by=author,
        approved_by=reviewer,
        legally_required=True,
    )
    await registry.register_processor(
        c,
        tenant,
        processor_id="mail-vendor",
        name="Mail Vendor",
        role="PROCESSOR",
        data_categories=["D1"],
        locations=["GB"],
        created_by=author,
        deletion_sla_days=30,
        contract_ref="DPA-MAIL-1",
    )
    await registry.set_processor_status(c, tenant, "mail-vendor", "APPROVED", actor=reviewer)
    base = {
        "subject_principal_id": subject,
        "store_name": "test.records",
        "data_category": "D1",
        "retention_policy_code": None,
    }
    for ref, kind, purpose, extra in (
        ("news-profile", "subscriber_profile", "NEWSLETTER", {}),
        ("news-invoice", "invoice", "NEWSLETTER", {"retention_policy_code": "NEWS_STATUTORY"}),
        ("news-mailbox", "mail_contact", "NEWSLETTER", {"processor_id": "mail-vendor"}),
        ("account-profile", "profile", "ACCOUNT", {}),
    ):
        await privacy.register_personal_data(
            c, tenant, object_type=kind, object_ref=ref, purpose_code=purpose, **{**base, **extra}
        )


def test_t30_withdrawing_consent_erases_what_was_kept_only_for_it_with_the_usual_exemptions(
    rt_url: str,
) -> None:
    tenant, author, reviewer, subject = uid(), uid(), uid(), uid()
    DISPOSED.clear()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            consent = await _newsletter_consent(c, tenant, author, reviewer, subject)
            await _register_newsletter_data(c, tenant, subject, author, reviewer)
            await privacy.withdraw_consent(
                c,
                tenant,
                consent,
                subject_principal_id=subject,
                reason="no more news",
                authn_level=2,
                auth_age=timedelta(minutes=1),
            )
            request = (
                await c.execute(
                    text(
                        "SELECT request_id, status, request_type, identity_verification_state, fulfilment_items "
                        "FROM privacy.rights_request WHERE subject_principal_id = :s"
                    ),
                    {"s": uuid.UUID(subject)},
                )
            ).one()
            assert request.request_type == "ERASURE"
            assert request.identity_verification_state == "VERIFIED"
            items = {i["item_id"]: i for i in request.fulfilment_items}
            # what was kept for the withdrawn purpose, and nothing else
            assert items["test.records:news-profile"]["state"] == "DONE"
            assert items["test.records:news-invoice"]["state"] == "RETAINED_RESTRICTED"
            assert items["test.records:news-invoice"]["reason"] == "LEGAL_OBLIGATION"
            assert items["test.records:news-mailbox"]["state"] == "PENDING_PROCESSOR_ACK"
            assert (
                "test.records:account-profile" not in items
            )  # kept for another purpose: untouched
            assert "evidence-vault" not in items and "caches" not in items  # not erased as a whole
            assert request.status == "FULFILLING"  # never complete while a processor is pending
            assert ("news-profile", "DELETE") in DISPOSED
            assert all(r != "account-profile" for r, _ in DISPOSED)
            status = {
                r.object_ref: r.status
                for r in (
                    await c.execute(
                        text(
                            "SELECT object_ref, status FROM privacy.data_map_entry WHERE subject_principal_id = :s"
                        ),
                        {"s": uuid.UUID(subject)},
                    )
                ).all()
            }
            assert status["news-profile"] == "DISPOSED" and status["account-profile"] == "ACTIVE"
            assert status["news-invoice"] == "RESTRICTED"

            done = await privacy.acknowledge_item(
                c,
                tenant,
                str(request.request_id),
                "test.records:news-mailbox",
                ack_ref="mail-ticket-9",
                actor=reviewer,
            )
            assert done.status == "PARTIALLY_DENIED" and done.completed_at is not None
            kept = (
                await c.execute(
                    text("SELECT status FROM privacy.consent_receipt WHERE consent_id = :i"),
                    {"i": uuid.UUID(consent)},
                )
            ).scalar_one()
            assert kept == "WITHDRAWN"  # the consent record itself is evidence and stays
            events = set(
                (await c.execute(text("SELECT event_type FROM evidence.audit_record"))).scalars()
            )
            assert {
                "privacy.consent.withdrawn",
                "privacy.deletion.started",
                "privacy.deletion.completed",
                "privacy.processor.deletion.confirmed",
            } <= events  # the deletion proof

    asyncio.run(run())


def test_t30_an_unverified_withdrawal_waits_and_another_lawful_basis_keeps_the_data(
    rt_url: str,
) -> None:
    tenant, author, reviewer, subject = uid(), uid(), uid(), uid()
    DISPOSED.clear()

    async def waits() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            consent = await _newsletter_consent(c, tenant, author, reviewer, subject)
            await _register_newsletter_data(c, tenant, subject, author, reviewer)
            await privacy.withdraw_consent(
                c, tenant, consent, subject_principal_id=subject, reason="no"
            )
            request = (
                await c.execute(
                    text(
                        "SELECT identity_verification_state FROM privacy.rights_request WHERE subject_principal_id = :s"
                    ),
                    {"s": uuid.UUID(subject)},
                )
            ).one()
            assert request.identity_verification_state == "PENDING"  # no session proves the subject
            assert not any(r == "news-profile" for r, _ in DISPOSED)

    asyncio.run(waits())

    other_tenant, other_subject = uid(), uid()

    async def other_basis() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(other_tenant)) as c:
            consent = await _newsletter_consent(
                c, other_tenant, author, reviewer, other_subject, also_on_contract=True
            )
            await _register_newsletter_data(c, other_tenant, other_subject, author, reviewer)
            await privacy.withdraw_consent(
                c,
                other_tenant,
                consent,
                subject_principal_id=other_subject,
                reason="no",
                authn_level=2,
                auth_age=timedelta(minutes=1),
            )
            request = (
                await c.execute(
                    text(
                        "SELECT status, fulfilment_items FROM privacy.rights_request WHERE subject_principal_id = :s"
                    ),
                    {"s": uuid.UUID(other_subject)},
                )
            ).one()
            news = [
                i for i in request.fulfilment_items if i["item_id"].startswith("test.records:news-")
            ]
            assert news and all(
                i["state"] == "RETAINED_OTHER_BASIS" and i["reason"] == "OTHER_LAWFUL_BASIS"
                for i in news
            )
            assert request.status == "PARTIALLY_DENIED"
            assert not any(r.startswith("news-") for r, _ in DISPOSED)

    asyncio.run(other_basis())

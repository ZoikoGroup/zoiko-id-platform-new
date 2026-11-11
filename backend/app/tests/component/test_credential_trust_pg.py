"""Credential trust end to end on PostgreSQL (CR-069): gates, standing, eligibility, monitoring, disclosure, outreach, appeals.

GLB-01/04/31  eligibility is recomputed per place and service; a suspended license blocks
GLB-10/26     a later status change supersedes the check; an older late event changes nothing
GLB-14        a spoofed outreach answer is refused
GLB-15        a source without the permitted purpose is never asked
GLB-20/33     withdrawal ends the grant and its children; a consumer cannot reuse another's grant
"""

from __future__ import annotations

import asyncio
import hmac
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import claim_checks as checks
from zid_platform.modules.credential.application import trust_service as svc
from zid_platform.modules.credential.domain import eligibility as el
from zid_platform.modules.credential.domain import lifecycle as lc
from zid_platform.modules.credential.domain import outreach as oc
from zid_platform.modules.credential.domain import portable as pt
from zid_platform.modules.credential.domain.claims import CredentialStatus
from zid_platform.modules.credential.domain.sources import SourceError
from zid_platform.modules.credential.infrastructure import trust_store
from zid_platform.modules.credential.infrastructure.registries import (
    issuer_registry,
    policies,
    source_contracts,
)
from zid_platform.modules.verification.ports import pack_registry

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
JURIS = {"country": "US", "subdivision": "CA"}
POLICY = policies()[0][0]
RULE = policies()[1][0]


def uid() -> str:
    return str(uuid.uuid4())


def gate(**over) -> checks.Gate:
    args = {
        "registry": issuer_registry(),
        "contracts": source_contracts(),
        "processing_region": "local",
        **over,
    }
    return checks.Gate(**args)


def license_claim(tag: str = "L-1", regulator: str = "Sample State Licensing Board") -> dict:
    return {"license_ref": tag, "regulator": regulator, "activity_scope": "legal.advice"}


async def check(
    conn, tenant, subject, kind="PROFESSIONAL_LICENSE", claimed=None, source=None, **kw
):
    return await checks.run_check(
        conn,
        tenant_id=tenant,
        subject_principal_id=subject,
        kind=kind,
        claimed=claimed or license_claim(),
        jurisdiction=kw.pop("jurisdiction", JURIS),
        profession=None,
        packs=pack_registry(),
        source=source or checks.SimulatorSource("test"),
        now=kw.pop("now", NOW),
        environment="test",
        gate=kw.pop("gate", gate()),
        **kw,
    )


def run(coro):
    return asyncio.run(coro)


# --- gates ------------------------------------------------------------------------------------------------------------


def test_an_unknown_issuer_is_not_trusted_and_the_source_is_never_asked(rt_url: str) -> None:
    class Spy:
        source_id = "simulator"

        async def lookup(self, *a, **k):
            raise AssertionError("must not be asked")

    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await check(
                c,
                tenant,
                subject,
                claimed=license_claim(regulator="Totally Unknown Board"),
                source=Spy(),
            )
            a = r.assertion
            assert a.decision.value == "NOT_VERIFIED" and a.reason_codes == ("ISSUER_NOT_TRUSTED",)
            row = (
                await c.execute(text("SELECT source_result, source_id FROM cred.claim_check"))
            ).one()
            assert tuple(row) == ("NOT_QUERIED", "not-queried")

    run(go())


def test_a_lookalike_issuer_goes_to_a_person_without_asking_the_source(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await check(
                c, tenant, subject, claimed=license_claim(regulator="Sample State Licensing Boards")
            )
            assert r.assertion.decision.value == "REVIEW" and r.assertion.reason_codes == (
                "ISSUER_LOOKALIKE",
            )

    run(go())


def test_a_source_without_the_permitted_purpose_is_never_queried_and_nothing_is_stored(
    rt_url: str,
) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(SourceError) as e:
                await check(c, tenant, subject, gate=gate(purpose="MARKETING"))
            assert e.value.code == "SOURCE_PURPOSE_NOT_PERMITTED"
            with pytest.raises(SourceError) as e:
                await check(
                    c,
                    tenant,
                    subject,
                    gate=checks.Gate(
                        registry=issuer_registry(), contracts=(), processing_region="local"
                    ),
                )
            assert e.value.code == "SOURCE_NO_CONTRACT"
            with pytest.raises(SourceError) as e:
                await check(c, tenant, subject, gate=gate(processing_region="eu"))
            assert e.value.code == "SOURCE_REGION_BLOCKED"
            assert (
                await c.execute(text("SELECT count(*) FROM cred.claim_check"))
            ).scalar_one() == 0

    run(go())


def test_license_standing_comes_from_the_regulators_facts(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            ok = await check(c, tenant, subject)
            assert ok.assertion.decision.value == "VERIFIED"
            assert ok.extras["permitted_services"] == ["legal.advice"]
            expired = await check(
                c, tenant, uid(), claimed={**license_claim("L-2"), "effective_until": "2026-01-01"}
            )
            assert expired.assertion.credential_status is CredentialStatus.EXPIRED
            assert expired.assertion.decision.value == "NOT_VERIFIED"
            sus = await check(c, tenant, uid(), claimed=license_claim("SIM-SUSPENDED"))
            assert sus.assertion.reason_codes == ("CREDENTIAL_SUSPENDED",)

    run(go())


def test_cpd_without_a_configured_requirement_or_an_approved_provider_is_indeterminate(
    rt_url: str,
) -> None:
    tenant, subject = uid(), uid()
    claim = {"credits": 30, "cycle": "2026", "evidence_source": "board-a", "profession_code": "LAW"}

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await check(c, tenant, subject, "CPD_RECORD", claim)
            assert r.assertion.decision.value == "INDETERMINATE"
            assert r.assertion.reason_codes == ("CPD_REQUIREMENT_NOT_CONFIGURED",)
            ok = await check(
                c,
                tenant,
                subject,
                "CPD_RECORD",
                claim,
                gate=gate(cpd_required_credits=20, cpd_approved_providers=frozenset({"board-a"})),
            )
            assert ok.assertion.decision.value == "VERIFIED"
            bad = await check(
                c,
                tenant,
                subject,
                "CPD_RECORD",
                claim,
                gate=gate(cpd_required_credits=20, cpd_approved_providers=frozenset({"board-z"})),
            )
            assert (
                bad.assertion.decision.value == "INDETERMINATE"
                and "PROVIDER_NOT_APPROVED" in bad.assertion.reason_codes
            )

    run(go())


# --- eligibility, engagement, tiers ----------------------------------------------------------------------------------


def test_eligibility_is_recomputed_per_place_and_service_and_blocks_when_suspended(
    rt_url: str,
) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await check(c, tenant, subject)
            ok = await svc.evaluate_eligibility(
                c,
                tenant_id=tenant,
                subject=subject,
                policy=POLICY,
                registry=issuer_registry(),
                now=NOW,
            )
            assert ok.outcome.eligibility.value == "ELIGIBLE"
            ny = replace(POLICY, policy_id="ny", subdivision="NY")  # GLB-01: another state
            r = await svc.evaluate_eligibility(
                c, tenant_id=tenant, subject=subject, policy=ny, registry=issuer_registry(), now=NOW
            )
            assert (
                r.outcome.eligibility.value == "BLOCKED"
                and "PROFESSIONAL_LICENSE_WRONG_JURISDICTION" in r.outcome.reasons
            )
            tax = replace(
                POLICY, policy_id="tax", service_code="tax.advice"
            )  # GLB-31: another service
            r = await svc.evaluate_eligibility(
                c,
                tenant_id=tenant,
                subject=subject,
                policy=tax,
                registry=issuer_registry(),
                now=NOW,
            )
            assert "PROFESSIONAL_LICENSE_SERVICE_NOT_PERMITTED" in r.outcome.reasons
            n = (
                await c.execute(text("SELECT count(*) FROM cred.eligibility_decision"))
            ).scalar_one()
            assert n == 3  # every decision is a row

    run(go())


def test_eligibility_decisions_are_append_only(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await svc.evaluate_eligibility(
                    c,
                    tenant_id=tenant,
                    subject=subject,
                    policy=POLICY,
                    registry=issuer_registry(),
                    now=NOW,
                )
            for sql in (
                "UPDATE cred.eligibility_decision SET outcome = 'ELIGIBLE'",
                "DELETE FROM cred.eligibility_decision",
            ):
                with pytest.raises(DBAPIError):
                    async with tenant_transaction(eng, ctx(tenant)) as c:
                        await c.execute(text(sql))

    run(go())


def test_an_engagement_without_verified_identity_or_insurance_holds_and_tier_is_none(
    rt_url: str,
) -> None:
    tenant, subject = uid(), uid()
    req = el.EngagementRequest("US", "CA", "LAW", "legal.advice")

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await check(c, tenant, subject)
            r = await svc.evaluate_engagement(
                c,
                tenant_id=tenant,
                subject=subject,
                request=req,
                policy=POLICY,
                rule=RULE,
                registry=issuer_registry(),
                now=NOW,
                consumer="zoikorum",
            )
            assert r.outcome.engagement is el.Engagement.HOLD
            assert {"IDENTITY_NOT_VERIFIED", "INSURANCE_NOT_CHECKED"} <= set(r.outcome.reasons)
            assert (
                r.tier is el.Tier.NONE and r.tier_gaps[0] == "IDENTITY_NOT_VERIFIED"
            )  # identity is mandatory: nothing else can lift it
            assert (
                r.dimensions["ELIGIBILITY"] == "VERIFIED"
                and r.dimensions["IDENTITY"] == "NOT_VERIFIED"
            )

    run(go())


def test_personal_insurance_is_satisfied_only_when_it_names_the_person(rt_url: str) -> None:
    tenant, subject = uid(), uid()
    claim = {
        "insured_entity": "Dana Lee",
        "insurer": "Sample Indemnity Insurance",
        "coverage_type": "PI",
        "region": "US",
        "expires_on": "2028-01-01",
        "activities": "legal.advice",
    }
    holder = checks.Holder("Dana Lee")

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            mine = await check(c, tenant, subject, "INSURANCE_ASSERTION", claim, holder=holder)
            assert mine.extras["insurance_facts"]["entity_is_subject"] is True
            rows = await trust_store.live_checks(c, subject)
            assert (
                svc.insurance_satisfied(
                    rows, region="US", work="legal.advice", until=(NOW + timedelta(days=365)).date()
                )
                is True
            )
            firm_subject = uid()
            firm = await check(
                c,
                tenant,
                firm_subject,
                "INSURANCE_ASSERTION",
                {**claim, "insured_entity": "Lee & Partners LLP"},
                holder=holder,
            )
            assert firm.extras["insurance_facts"]["entity_is_subject"] is False  # GLB-11
            rows = await trust_store.live_checks(c, firm_subject)
            assert (
                svc.insurance_satisfied(rows, region="US", work="legal.advice", until=NOW.date())
                is False
            )
            assert svc.insurance_satisfied([], region="US", work="x", until=NOW.date()) is None

    run(go())


# --- monitoring --------------------------------------------------------------------------------------------------------


def test_a_newer_status_event_supersedes_the_check_and_an_older_late_one_changes_nothing(
    rt_url: str,
) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            first = await check(c, tenant, subject)
            ev = lambda i, status, seq, when: lc.SourceEvent(
                i, first.check_id, status, NOW + timedelta(hours=when), seq
            )
            changed, status = await svc.apply_source_event(
                c,
                tenant_id=tenant,
                check_id=first.check_id,
                event=ev("e5", CredentialStatus.SUSPENDED, 5, 1),
                now=NOW,
            )
            assert changed and status == "SUSPENDED"
            old = await trust_store.live_checks(c, subject)
            assert (
                len(old) == 1 and old[0]["assertion"]["decision"] == "NOT_VERIFIED"
            )  # the superseding check is the live one
            assert str(old[0]["check_id"]) != first.check_id
            # an older event for the first check: its check is superseded now, so it is refused rather than applied
            with pytest.raises(LookupError):
                await svc.apply_source_event(
                    c,
                    tenant_id=tenant,
                    check_id=first.check_id,
                    event=ev("e3", CredentialStatus.ACTIVE, 3, 0),
                    now=NOW,
                )
            # after the suspension the subject is no longer eligible
            r = await svc.evaluate_eligibility(
                c,
                tenant_id=tenant,
                subject=subject,
                policy=POLICY,
                registry=issuer_registry(),
                now=NOW,
            )
            assert r.outcome.eligibility.value == "BLOCKED"
            ev_count = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE event_type LIKE '%credential.status_changed%'"
                    )
                )
            ).scalar_one()
            assert ev_count == 1

    run(go())


def test_an_out_of_order_event_on_the_live_check_is_recorded_but_not_applied(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            first = await check(c, tenant, subject)
            mk = lambda i, status, seq: lc.SourceEvent(i, first.check_id, status, NOW, seq)
            await svc.apply_source_event(
                c,
                tenant_id=tenant,
                check_id=first.check_id,
                event=mk("e1", CredentialStatus.ACTIVE, 9),
                now=NOW,
            )
            changed, status = await svc.apply_source_event(
                c,
                tenant_id=tenant,
                check_id=first.check_id,
                event=mk("e0", CredentialStatus.REVOKED, 2),
                now=NOW,
            )
            assert not changed and status == "ACTIVE"  # sequence 2 is older than 9
            again, _ = await svc.apply_source_event(
                c,
                tenant_id=tenant,
                check_id=first.check_id,
                event=mk("e0", CredentialStatus.REVOKED, 99),
                now=NOW,
            )
            assert not again  # the same event id is the same event

    run(go())


def test_due_checks_are_announced_once_per_day(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await check(
                c, tenant, subject, claimed={**license_claim(), "effective_until": "2026-11-20"}
            )
            assert await svc.recheck_due(c, now=NOW) == 1  # expires inside the lead time: due now
            assert await svc.recheck_due(c, now=NOW) == 0  # pushed a day
            assert await svc.recheck_due(c, now=NOW + timedelta(days=2)) == 1

    run(go())


# --- disclosure --------------------------------------------------------------------------------------------------------


def test_a_grant_discloses_only_its_fields_and_withdrawal_ends_it_and_its_children(
    rt_url: str,
) -> None:
    tenant, subject, other = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await check(c, tenant, subject)
            g = await svc.create_grant(
                c,
                tenant_id=tenant,
                subject=subject,
                recipient="acme",
                consumer="zoikorum",
                purpose="ENGAGEMENT",
                fields=["claim_type", "decision"],
                days=30,
                parent_id=None,
                now=NOW,
            )
            got = await svc.disclose_check(
                c,
                grant_id=g.grant_id,
                check_id=r.check_id,
                recipient="acme",
                consumer="zoikorum",
                purpose="ENGAGEMENT",
                now=NOW,
            )
            assert got == {"claim_type": "PROFESSIONAL_LICENSE", "decision": "VERIFIED"}
            with pytest.raises(pt.DisclosureError) as e:  # GLB-33
                await svc.disclose_check(
                    c,
                    grant_id=g.grant_id,
                    check_id=r.check_id,
                    recipient="acme",
                    consumer="rooms",
                    purpose="ENGAGEMENT",
                    now=NOW,
                )
            assert e.value.code == "CONSUMER_SCOPE_MISMATCH"
            child = await svc.create_grant(
                c,
                tenant_id=tenant,
                subject=subject,
                recipient="partner",
                consumer="zoikorum",
                purpose="ENGAGEMENT",
                fields=["decision"],
                days=10,
                parent_id=g.grant_id,
                now=NOW,
            )
            with pytest.raises(pt.DisclosureError):  # nobody else can withdraw it
                await svc.withdraw_grant(c, grant_id=g.grant_id, subject=other, now=NOW)
            await svc.withdraw_grant(
                c, grant_id=g.grant_id, subject=subject, now=NOW + timedelta(hours=1)
            )
            for gid, rec in ((g.grant_id, "acme"), (child.grant_id, "partner")):  # GLB-20
                with pytest.raises(pt.DisclosureError) as e:
                    await svc.disclose_check(
                        c,
                        grant_id=gid,
                        check_id=r.check_id,
                        recipient=rec,
                        consumer="zoikorum",
                        purpose="ENGAGEMENT",
                        now=NOW + timedelta(hours=2),
                    )
                assert e.value.code == "GRANT_NOT_IN_FORCE"

    run(go())


def test_a_grant_never_reaches_another_persons_check_and_the_public_view_is_bounded(
    rt_url: str,
) -> None:
    tenant, subject, stranger = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            mine = await check(c, tenant, subject)
            theirs = await check(c, tenant, stranger, claimed=license_claim("L-9"))
            g = await svc.create_grant(
                c,
                tenant_id=tenant,
                subject=subject,
                recipient="anyone",
                consumer="PUBLIC_VERIFIER",
                purpose="VERIFY",
                fields=["claim_type", "decision", "assurance_tier", "source_checked_at"],
                days=5,
                parent_id=None,
                now=NOW,
            )
            view = await svc.public_assertion(
                c, grant_id=g.grant_id, check_id=mine.check_id, now=NOW
            )
            assert (
                set(view) <= {"claim_type", "decision", "assurance_tier", "source_checked_at"}
                and view["decision"] == "VERIFIED"
            )
            with pytest.raises(pt.DisclosureError):
                await svc.public_assertion(
                    c, grant_id=g.grant_id, check_id=theirs.check_id, now=NOW
                )
            normal = await svc.create_grant(
                c,
                tenant_id=tenant,
                subject=subject,
                recipient="x",
                consumer="zoikorum",
                purpose="P",
                fields=["decision"],
                days=5,
                parent_id=None,
                now=NOW,
            )
            with pytest.raises(
                pt.DisclosureError
            ):  # a non-public grant is not a verifier capability
                await svc.public_assertion(
                    c, grant_id=normal.grant_id, check_id=mine.check_id, now=NOW
                )

    run(go())


# --- outreach and appeals ----------------------------------------------------------------------------------------------

KEY = b"issuer-registered-secret"
CONTACT = issuer_registry().issuers["sample-university"].outreach_contacts


def _response(nonce_value: str, **over) -> oc.Response:
    payload = b'{"outcome":"CONFIRMED"}'
    base = {
        "sender": CONTACT[0],
        "nonce": nonce_value,
        "outcome": "CONFIRMED",
        "payload": payload,
        "signature": hmac.new(KEY, payload, "sha256").digest(),
        "received_at": NOW + timedelta(days=1),
    }
    base.update(over)
    return oc.Response(**base)


def test_outreach_needs_an_authenticated_answer_and_two_other_reviewers(rt_url: str) -> None:
    tenant, ops, r1, r2 = uid(), uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            req, nonce = await svc.open_outreach(
                c,
                tenant_id=tenant,
                issuer_id="sample-university",
                claim_type="ACADEMIC_AWARD",
                requested_by=ops,
                contacts=CONTACT,
                days=14,
                now=NOW,
            )
            for bad, code in (
                ({"sender": "attacker@evil.example"}, "SENDER_NOT_REGISTERED"),
                ({"signature": b"forged"}, "SIGNATURE_INVALID"),
                ({"nonce": "nope"}, "NONCE_MISMATCH"),
            ):
                with pytest.raises(oc.OutreachError) as e:  # GLB-14
                    await svc.record_response(
                        c,
                        request_id=req.request_id,
                        response=_response(nonce, **bad),
                        contacts=CONTACT,
                        verify=oc.hmac_verifier(KEY),
                        now=NOW,
                    )
                assert e.value.code == code
            done = await svc.record_response(
                c,
                request_id=req.request_id,
                response=_response(nonce),
                contacts=CONTACT,
                verify=oc.hmac_verifier(KEY),
                now=NOW,
            )
            assert done.state is oc.State.RESPONDED
            with pytest.raises(oc.OutreachError) as e:
                await svc.review_outreach(c, request_id=req.request_id, reviewer=ops, approve=True)
            assert e.value.code == "REQUESTER_CANNOT_REVIEW"
            first = await svc.review_outreach(
                c, request_id=req.request_id, reviewer=r1, approve=True
            )
            assert first.state is oc.State.RESPONDED  # one approval is not enough
            second = await svc.review_outreach(
                c, request_id=req.request_id, reviewer=r2, approve=True
            )
            assert second.state is oc.State.ACCEPTED and oc.verdict(second) == ("CONFIRMED", ())

    run(go())


def test_an_unanswered_outreach_expires_to_unverifiable(rt_url: str) -> None:
    tenant, ops = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            req, nonce = await svc.open_outreach(
                c,
                tenant_id=tenant,
                issuer_id="sample-university",
                claim_type="ACADEMIC_AWARD",
                requested_by=ops,
                contacts=CONTACT,
                days=2,
                now=NOW,
            )
            with pytest.raises(oc.OutreachError) as e:
                await svc.record_response(
                    c,
                    request_id=req.request_id,
                    response=_response(nonce, received_at=NOW + timedelta(days=5)),
                    contacts=CONTACT,
                    verify=oc.hmac_verifier(KEY),
                    now=NOW + timedelta(days=5),
                )
            assert e.value.code == "REQUEST_EXPIRED"
            state = (await c.execute(text("SELECT state FROM cred.outreach_request"))).scalar_one()
            assert state == "EXPIRED"
            with pytest.raises(oc.OutreachError) as e:
                await svc.open_outreach(
                    c,
                    tenant_id=tenant,
                    issuer_id="x",
                    claim_type="X",
                    requested_by=ops,
                    contacts=(),
                    days=2,
                    now=NOW,
                )
            assert e.value.code == "NO_REGISTERED_CONTACT"

    run(go())


def test_an_appeal_is_resolved_by_someone_else_and_never_edits_the_check(rt_url: str) -> None:
    tenant, subject, reviewer = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await check(c, tenant, subject, claimed=license_claim("SIM-SUSPENDED"))
            aid = await svc.raise_appeal(
                c,
                tenant_id=tenant,
                check_id=r.check_id,
                raised_by=subject,
                reason="the board record is wrong",
                correction={"license_ref": "L-1"},
            )
            assert not await trust_store.resolve_appeal(
                c, aid, state="UPHELD", resolved_by=subject, resolution="self"
            )  # not by the person who raised it
            assert await trust_store.resolve_appeal(
                c, aid, state="UPHELD", resolved_by=reviewer, resolution="record corrected, recheck"
            )
            assert not await trust_store.resolve_appeal(
                c, aid, state="REJECTED", resolved_by=reviewer, resolution="again"
            )  # once
            decision = (
                await c.execute(
                    text("SELECT decision FROM cred.claim_check WHERE check_id = :i"),
                    {"i": uuid.UUID(r.check_id)},
                )
            ).scalar_one()
            assert decision == "NOT_VERIFIED"  # the check is as it was
            with pytest.raises(LookupError):
                await svc.raise_appeal(
                    c,
                    tenant_id=tenant,
                    check_id=uid(),
                    raised_by=subject,
                    reason="x",
                    correction={},
                )

    run(go())


def test_the_new_tables_are_invisible_to_another_tenant(rt_url: str) -> None:
    t1, t2, subject = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(t1)) as c:
                r = await check(c, t1, subject)
                await svc.create_grant(
                    c,
                    tenant_id=t1,
                    subject=subject,
                    recipient="a",
                    consumer="zoikorum",
                    purpose="P",
                    fields=["decision"],
                    days=5,
                    parent_id=None,
                    now=NOW,
                )
                await svc.raise_appeal(
                    c,
                    tenant_id=t1,
                    check_id=r.check_id,
                    raised_by=subject,
                    reason="x",
                    correction={},
                )
                await svc.evaluate_eligibility(
                    c,
                    tenant_id=t1,
                    subject=subject,
                    policy=POLICY,
                    registry=issuer_registry(),
                    now=NOW,
                )
            async with tenant_transaction(eng, ctx(t2)) as c:
                for table in (
                    "claim_check",
                    "claim_monitor",
                    "consent_grant",
                    "claim_appeal",
                    "eligibility_decision",
                    "outreach_request",
                ):
                    assert (
                        await c.execute(text(f"SELECT count(*) FROM cred.{table}"))
                    ).scalar_one() == 0, table

    run(go())

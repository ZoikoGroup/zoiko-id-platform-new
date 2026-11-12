"""An issuer outreach becomes a claim check (GID-054, CR-069), and the claimed values are dropped once it has.

GLB-13  an issuer that never answers leaves the claim UNVERIFIABLE, never false
GLB-14  (see test_credential_trust_pg) a spoofed answer never reaches this stage
"""

from __future__ import annotations

import asyncio
import hmac
import uuid
from datetime import UTC, datetime, timedelta

from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import trust_service as svc
from zid_platform.modules.credential.domain import outreach as oc
from zid_platform.modules.credential.infrastructure.registries import issuer_registry
from zid_platform.modules.verification.ports import pack_registry

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
KEY = b"issuer-registered-secret"
CONTACT = issuer_registry().issuers["sample-university"].outreach_contacts
CLAIM = {
    "fields": {
        "institution": "Sample University",
        "program": "BSc Computing",
        "award_status": "CONFERRED",
    },
    "jurisdiction": {"country": "US"},
    "profession": None,
}


def uid() -> str:
    return str(uuid.uuid4())


def run(coro):
    return asyncio.run(coro)


async def open_(c, tenant, ops, subject, claim=CLAIM, days=14):
    return await svc.open_outreach(
        c,
        tenant_id=tenant,
        issuer_id="sample-university",
        claim_type="ACADEMIC_AWARD",
        requested_by=ops,
        contacts=CONTACT,
        days=days,
        now=NOW,
        subject=subject,
        claim=claim,
    )


async def answer(c, req, nonce, outcome="CONFIRMED"):
    payload = f'{{"outcome":"{outcome}"}}'.encode()
    resp = oc.Response(
        CONTACT[0],
        nonce,
        outcome,
        payload,
        hmac.new(KEY, payload, "sha256").digest(),
        NOW + timedelta(days=1),
    )
    await svc.record_response(
        c,
        request_id=req.request_id,
        response=resp,
        contacts=CONTACT,
        verify=oc.hmac_verifier(KEY),
        now=NOW,
    )


async def conclude(c, tenant, req, now=NOW):
    return await svc.conclude_outreach(
        c,
        tenant_id=tenant,
        request_id=req.request_id,
        packs=pack_registry(),
        registry=issuer_registry(),
        environment="test",
        now=now,
    )


async def review_both(c, req, r1, r2):
    await svc.review_outreach(c, request_id=req.request_id, reviewer=r1, approve=True)
    await svc.review_outreach(c, request_id=req.request_id, reviewer=r2, approve=True)


def test_a_confirmed_and_dual_reviewed_answer_becomes_a_primary_source_check_and_the_claim_is_forgotten(
    rt_url: str,
) -> None:
    tenant, ops, r1, r2, subject = uid(), uid(), uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            req, nonce = await open_(c, tenant, ops, subject)
            assert await conclude(c, tenant, req) is None  # still pending: nothing to conclude
            await answer(c, req, nonce)
            await svc.review_outreach(c, request_id=req.request_id, reviewer=r1, approve=True)
            assert await conclude(c, tenant, req) is None  # one approval is not enough
            await svc.review_outreach(c, request_id=req.request_id, reviewer=r2, approve=True)
            check_id = await conclude(c, tenant, req)
            assert check_id is not None
            row = (
                await c.execute(
                    text(
                        "SELECT decision, assurance_tier, source_id, assertion::text FROM cred.claim_check"
                    )
                )
            ).one()
            assert (
                row[0] == "VERIFIED"
                and row[1] == "PRIMARY_SOURCE_CONFIRMED"
                and row[2] == "outreach"
            )
            assert (
                "Sample University" not in row[3] and "BSc Computing" not in row[3]
            )  # no claimed value in the check
            o = (
                await c.execute(
                    text(
                        "SELECT claim IS NULL, check_id, concluded_at IS NOT NULL FROM cred.outreach_request"
                    )
                )
            ).one()
            assert (
                o[0] is True and str(o[1]) == check_id and o[2] is True
            )  # the question is forgotten, the link kept
            assert await conclude(c, tenant, req) is None  # once only
            n = (await c.execute(text("SELECT count(*) FROM cred.claim_check"))).scalar_one()
            assert n == 1

    run(go())


def test_an_issuers_denial_is_a_reachable_source_with_no_record_not_a_primary_confirmation(
    rt_url: str,
) -> None:
    tenant, ops, r1, r2, subject = uid(), uid(), uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            req, nonce = await open_(c, tenant, ops, subject)
            await answer(c, req, nonce, "NOT_HELD")
            await review_both(c, req, r1, r2)
            assert await conclude(c, tenant, req) is not None
            d = (
                await c.execute(
                    text("SELECT decision, assertion->'reason_codes' FROM cred.claim_check")
                )
            ).one()
            assert d[0] == "NOT_VERIFIED" and d[1] == ["SOURCE_HAS_NO_RECORD"]

    run(go())


def test_glb_13_silence_after_the_deadline_is_unverifiable_never_false(rt_url: str) -> None:
    tenant, ops, subject = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            req, _ = await open_(c, tenant, ops, subject, days=2)
            assert (
                await conclude(c, tenant, req, now=NOW + timedelta(days=1)) is None
            )  # inside the deadline
            check_id = await conclude(c, tenant, req, now=NOW + timedelta(days=3))
            assert check_id is not None
            assert (
                await c.execute(text("SELECT decision FROM cred.claim_check"))
            ).scalar_one() == "UNVERIFIABLE"
            assert (
                await c.execute(text("SELECT state FROM cred.outreach_request"))
            ).scalar_one() == "EXPIRED"

    run(go())


def test_a_rejected_answer_leaves_the_claim_unverifiable(rt_url: str) -> None:
    tenant, ops, r1, subject = uid(), uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            req, nonce = await open_(c, tenant, ops, subject)
            await answer(c, req, nonce)
            await svc.review_outreach(
                c, request_id=req.request_id, reviewer=r1, approve=False
            )  # one rejection rejects
            assert await conclude(c, tenant, req) is not None
            assert (
                await c.execute(text("SELECT decision FROM cred.claim_check"))
            ).scalar_one() == "UNVERIFIABLE"

    run(go())


def test_an_outreach_without_an_attached_claim_concludes_to_nothing(rt_url: str) -> None:
    tenant, ops, _subject = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            req, _ = await svc.open_outreach(
                c,
                tenant_id=tenant,
                issuer_id="sample-university",
                claim_type="ACADEMIC_AWARD",
                requested_by=ops,
                contacts=CONTACT,
                days=2,
                now=NOW,
            )
            assert await conclude(c, tenant, req, now=NOW + timedelta(days=5)) is None
            assert (
                await c.execute(text("SELECT count(*) FROM cred.claim_check"))
            ).scalar_one() == 0

    run(go())

"""Identity proofing cases on PostgreSQL (WF03; CR-110): the stored case, evidence in, decision re-derived, history, events, replay and closure."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.verification.application import idv_case as svc
from zid_platform.modules.verification.domain import idv_policy
from zid_platform.modules.verification.domain.idv_decision import (
    ChipAuth,
    Decision,
    FaceMatch,
    Liveness,
    NfcAccess,
)
from zid_platform.modules.verification.domain.idv_evidence import Envelope

NOW = datetime.now(UTC).replace(microsecond=0)
DOC = {
    "surname": "MUELLER",
    "given_names": "ANNA",
    "document_number": "X123",
    "birth_date": "1990-01-02",
}
GOOD_CHIP = ("SOD_SIGNED_BY_CONFIGURED_CSCA_CHAIN", "REVOCATION_CHECKED_GOOD")


def rules(**extra: Any) -> idv_policy.MarketRules:
    entry = {
        "policy_version": "gb-test-1",
        "purpose": "KYC",
        "retries": {"nfc": 2, "capture": 3},
        "optical_checks": ["TEMPLATE"],
        "biometric": {
            "lawful_basis": "CONSENT",
            "impact_assessment_ref": "DPIA-1",
            "notice_version": "n-1",
            "self_hosted_engine": False,
        },
        "alternates": [
            {"route_id": "PASSPORT_OPTICAL_REVIEW", "may_approve": True, "reviewers_needed": 2}
        ],
        **extra,
    }
    return idv_policy.parse({"markets": {"GB": entry}})["GB"]


def uid() -> str:
    return str(uuid.uuid4())


def env(nonce: str, digest: str, *, at: datetime) -> Envelope:
    return Envelope(nonce, digest, at, virtual_camera=False, device_attested=True)


async def _events(c: Any, tenant: str) -> list[str]:
    rows = await c.execute(
        text(
            "SELECT event_type FROM events.outbox_event WHERE tenant_id = :t AND event_type LIKE 'com.zoiko.id.verification.idv.%' ORDER BY created_at, event_id"
        ),
        {"t": uuid.UUID(tenant)},
    )
    return [r.event_type for r in rows]


def test_a_case_is_approved_only_when_every_signal_supports_it_and_is_then_closed(
    rt_url: str,
) -> None:
    tenant, applicant = uid(), uid()
    r = rules()

    async def run() -> None:
        async with engine(rt_url) as eng:

            async def tx(fn: Any) -> Any:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return await fn(c)

            c = await tx(
                lambda c: svc.open_case(
                    c, tenant_id=tenant, applicant=applicant, route="CHIP", rules=r, now=NOW
                )
            )
            cid = c.case_id
            assert (
                c.decision is Decision.INDETERMINATE
                and c.state == "OPEN"
                and c.policy_hash.startswith("sha256:")
            )

            nonce, _ = await tx(lambda c: svc.issue_nonce(c, cid, NOW))
            c = await tx(
                lambda c: svc.submit_capture(
                    c, cid, r, env(nonce, "a" * 64, at=NOW), now=NOW + timedelta(seconds=30)
                )
            )
            assert c.signals.injection_risk is False
            c = await tx(lambda c: svc.submit_optical(c, cid, r, {"TEMPLATE": "PASS"}, now=NOW))
            c = await tx(
                lambda c: svc.submit_chip(
                    c,
                    cid,
                    r,
                    status="VERIFIED_LIMITED",
                    reasons=GOOD_CHIP,
                    document=DOC,
                    printed=DOC,
                    chip_auth=ChipAuth.NOT_TESTED,
                    now=NOW,
                )
            )
            assert (
                c.signals.optical_matches_chip == "MATCH" and c.decision is Decision.INDETERMINATE
            )  # no person signal yet

            # biometrics are off until the notice was shown and consent given: the engine result is refused
            with pytest.raises(svc.CaseError, match="BIOMETRICS_OFF"):
                await tx(
                    lambda c: svc.record_engine_result(
                        c, cid, r, liveness=Liveness.PASS, face=FaceMatch.MATCH, now=NOW
                    )
                )
            c = await tx(
                lambda c: svc.record_notice_and_consent(
                    c, cid, r, notice_version="n-1", consented=True, now=NOW
                )
            )
            c = await tx(
                lambda c: svc.record_engine_result(
                    c, cid, r, liveness=Liveness.PASS, face=FaceMatch.MATCH, now=NOW
                )
            )
            assert c.decision is Decision.APPROVED and c.state == "CLOSED", c.reasons

            # a closed case takes nothing more, and the database says the same
            with pytest.raises(svc.CaseError, match="CASE_CLOSED"):
                await tx(lambda c: svc.abandon(c, cid, r, now=NOW))
            with pytest.raises(DBAPIError):
                async with tenant_transaction(eng, ctx(tenant)) as conn:
                    await conn.execute(
                        text(
                            "UPDATE verify.idv_case SET decision = 'REJECTED', state = 'CLOSED' WHERE case_id = :c"
                        ),
                        {"c": uuid.UUID(cid)},
                    )

            hist = await tx(lambda c: svc.history(c, cid))
            versions = [h["version"] for h in hist]
            assert (
                versions == sorted(versions)
                and hist[-1]["decision"] == "APPROVED"
                and hist[0]["trigger"] == "opened"
            )
            assert "HOLDER_MATCHES_TRUSTED_CHIP_PORTRAIT" in hist[-1]["allowed"]
            events = await tx(lambda c: _events(c, tenant))
            assert (
                events == ["com.zoiko.id.verification.idv.case_decided.v1"] * 1
            )  # INDETERMINATE to APPROVED is the one change

            with pytest.raises(DBAPIError):  # history is append-only
                async with tenant_transaction(eng, ctx(tenant)) as conn:
                    await conn.execute(
                        text(
                            "UPDATE verify.idv_case_decision SET decision = 'REJECTED' WHERE case_id = :c"
                        ),
                        {"c": uuid.UUID(cid)},
                    )

    asyncio.run(run())


def test_replay_missing_trust_withdrawal_retries_and_routes_are_all_enforced(rt_url: str) -> None:
    tenant, applicant, reviewer_a, reviewer_b = uid(), uid(), uid(), uid()
    r = rules()

    async def run() -> None:
        async with engine(rt_url) as eng:

            async def tx(fn: Any) -> Any:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return await fn(c)

            # the same capture twice (new nonce each time) is a replay, and a finding stays
            c = await tx(
                lambda c: svc.open_case(
                    c, tenant_id=tenant, applicant=applicant, route="CHIP", rules=r, now=NOW
                )
            )
            cid = c.case_id
            n1, _ = await tx(lambda c: svc.issue_nonce(c, cid, NOW))
            c = await tx(
                lambda c: svc.submit_capture(c, cid, r, env(n1, "b" * 64, at=NOW), now=NOW)
            )
            assert c.signals.injection_risk is False
            n2, _ = await tx(lambda c: svc.issue_nonce(c, cid, NOW))
            c = await tx(
                lambda c: svc.submit_capture(c, cid, r, env(n2, "b" * 64, at=NOW), now=NOW)
            )
            assert c.signals.injection_risk is True and "CAPTURE_MEDIA_REPLAYED" in c.reasons
            n3, _ = await tx(lambda c: svc.issue_nonce(c, cid, NOW))
            c = await tx(
                lambda c: svc.submit_capture(c, cid, r, env(n3, "c" * 64, at=NOW), now=NOW)
            )
            assert c.signals.injection_risk is True  # a clean capture later does not clear it
            with pytest.raises(
                svc.CaseError, match="RETRY_CEILING_REACHED"
            ):  # three capture attempts used
                await tx(
                    lambda c: svc.submit_capture(c, cid, r, env(n3, "d" * 64, at=NOW), now=NOW)
                )

            # a used nonce cannot be used again
            c2 = await tx(
                lambda c: svc.open_case(
                    c, tenant_id=tenant, applicant=applicant, route="CHIP", rules=r, now=NOW
                )
            )
            n, _ = await tx(lambda c: svc.issue_nonce(c, c2.case_id, NOW))
            await tx(
                lambda c: svc.submit_capture(c, c2.case_id, r, env(n, "e" * 64, at=NOW), now=NOW)
            )
            again = await tx(
                lambda c: svc.submit_capture(c, c2.case_id, r, env(n, "f" * 64, at=NOW), now=NOW)
            )
            assert "CAPTURE_NONCE_ALREADY_USED" in again.reasons

            # no root configured: the chip was read and is not a verified chip; nothing approves
            c3 = await tx(
                lambda c: svc.open_case(
                    c, tenant_id=tenant, applicant=applicant, route="CHIP", rules=r, now=NOW
                )
            )
            c3 = await tx(
                lambda c: svc.submit_chip(
                    c,
                    c3.case_id,
                    r,
                    status="INDETERMINATE",
                    reasons=("NO_CSCA_CONFIGURED",),
                    document=None,
                    printed=None,
                    chip_auth=ChipAuth.NOT_TESTED,
                    now=NOW,
                )
            )
            assert c3.decision is Decision.INDETERMINATE and "CHIP_TRUST_ROOT_MISSING" in c3.reasons
            # NFC retries stop at the ceiling of two
            await tx(
                lambda c: svc.chip_not_read(c, c3.case_id, r, access=NfcAccess.READ_FAILED, now=NOW)
            )
            with pytest.raises(svc.CaseError, match="RETRY_CEILING_REACHED"):
                await tx(
                    lambda c: svc.chip_not_read(
                        c, c3.case_id, r, access=NfcAccess.READ_FAILED, now=NOW
                    )
                )
            with pytest.raises(svc.CaseError, match="INVALID_ACCESS_STATE"):
                await tx(
                    lambda c: svc.chip_not_read(c, c3.case_id, r, access=NfcAccess.READ_OK, now=NOW)
                )

            # withdrawing consent switches the person signals off again
            c4 = await tx(
                lambda c: svc.open_case(
                    c, tenant_id=tenant, applicant=applicant, route="CHIP", rules=r, now=NOW
                )
            )
            await tx(
                lambda c: svc.submit_chip(
                    c,
                    c4.case_id,
                    r,
                    status="VERIFIED_LIMITED",
                    reasons=GOOD_CHIP,
                    document=DOC,
                    printed=DOC,
                    chip_auth=ChipAuth.NOT_TESTED,
                    now=NOW,
                )
            )
            await tx(
                lambda c: svc.record_notice_and_consent(
                    c, c4.case_id, r, notice_version="n-1", consented=True, now=NOW
                )
            )
            c4 = await tx(
                lambda c: svc.record_engine_result(
                    c, c4.case_id, r, liveness=Liveness.PASS, face=FaceMatch.MATCH, now=NOW
                )
            )
            assert c4.signals.liveness is Liveness.PASS
            c4 = await tx(
                lambda c: svc.withdraw_consent(c, c4.case_id, r, now=NOW + timedelta(seconds=1))
            )
            assert c4.signals.liveness is Liveness.NOT_RUN and c4.decision is not Decision.APPROVED
            assert any(x.startswith("BIOMETRICS_OFF") for x in c4.reasons)

            # an alternative route exists only if the policy names it, and needs two distinct reviewers, never the applicant
            with pytest.raises(svc.CaseError, match="ROUTE_NOT_ALLOWED_BY_POLICY"):
                await tx(
                    lambda c: svc.open_case(
                        c,
                        tenant_id=tenant,
                        applicant=applicant,
                        route="OWN_ROUTE",
                        rules=r,
                        now=NOW,
                    )
                )
            alt = await tx(
                lambda c: svc.open_case(
                    c,
                    tenant_id=tenant,
                    applicant=applicant,
                    route="PASSPORT_OPTICAL_REVIEW",
                    rules=r,
                    now=NOW,
                )
            )
            with pytest.raises(svc.CaseError, match="ROUTE_HAS_NO_CHIP"):
                await tx(
                    lambda c: svc.submit_chip(
                        c,
                        alt.case_id,
                        r,
                        status="VERIFIED_LIMITED",
                        reasons=GOOD_CHIP,
                        document=DOC,
                        printed=DOC,
                        chip_auth=ChipAuth.NOT_TESTED,
                        now=NOW,
                    )
                )
            with pytest.raises(svc.CaseError, match="REVIEWER_IS_THE_APPLICANT"):
                await tx(lambda c: svc.add_reviewer(c, alt.case_id, r, reviewer=applicant, now=NOW))
            await tx(lambda c: svc.add_reviewer(c, alt.case_id, r, reviewer=reviewer_a, now=NOW))
            with pytest.raises(svc.CaseError, match="ALREADY_REVIEWED"):
                await tx(
                    lambda c: svc.add_reviewer(c, alt.case_id, r, reviewer=reviewer_a, now=NOW)
                )
            alt = await tx(
                lambda c: svc.add_reviewer(c, alt.case_id, r, reviewer=reviewer_b, now=NOW)
            )
            assert alt.signals.reviewers == (reviewer_a, reviewer_b)

            # another company sees none of it
            other = uid()
            async with tenant_transaction(eng, ctx(other)) as conn:
                assert await svc.list_cases(conn) == []
                assert await svc.get(conn, cid) is None

    asyncio.run(run())


def test_abandonment_is_its_own_closed_outcome(rt_url: str) -> None:
    tenant, applicant = uid(), uid()
    r = rules()

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as conn:
                c = await svc.open_case(
                    conn, tenant_id=tenant, applicant=applicant, route="CHIP", rules=r, now=NOW
                )
                c = await svc.abandon(conn, c.case_id, r, now=NOW)
            assert c.decision is Decision.ABANDONED and c.state == "CLOSED"

    asyncio.run(run())

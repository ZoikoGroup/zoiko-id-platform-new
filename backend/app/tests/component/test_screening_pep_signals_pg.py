"""PEP hits, velocity breaches and adverse media on PostgreSQL (CR-068; ZID-GAP-001 WP-14): each opens one review item that keeps the list
version or the counts and the rationale, a repeat does not duplicate, a failed notification leaves the item open, and a person's
resolution is recorded without touching the evidence."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_screening_pg import CFG, GRAPH, NOW, OFFICER, load_lists, sign, subjects, uid
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import screening as service
from zid_platform.modules.screening.application import signals
from zid_platform.modules.screening.domain.decision import (
    Ruling,
    ScreeningError,
    ScreeningStatus,
)
from zid_platform.modules.screening.domain.lists import ListId, PartyKind
from zid_platform.modules.screening.domain.matching import Party
from zid_platform.modules.screening.domain.signals import (
    Action,
    Dimension,
    MediaOutcome,
    RiskTier,
    hash_identifier,
)
from zid_platform.modules.screening.infrastructure import signal_store
from zid_platform.modules.screening.infrastructure.adverse_media_fake import FakeAdverseMedia

PEP_CSV = (
    b"source_ref,name,aliases,birth,country,position,level\n"
    b"pep-1,Rita Minister,R. Minister,1961,GB,Minister of Finance,NATIONAL\n"
    b"pep-2,Omar Mayor,,1975,US,Mayor,LOCAL\n"
)
HIGH_RISK_CFG = replace(CFG, policy=replace(CFG.policy, high_risk_required_lists=(ListId.PEP,)))
EVIDENCE = "doc:passport-scan-17"
WHY = "Namesake: the passport shows another birth year and another country."


def test_pep_hit_goes_to_review_and_a_stale_pep_list_blocks_high_risk(rt_url: str) -> None:
    tenant, requester, reviewer = uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng)
            async with eng.begin() as c:
                assert (
                    await service.ingest_list(
                        c, ListId.PEP, {"file": PEP_CSV}, fetched_at=NOW, source_ref="t", now=NOW
                    )
                    == "LOADED"
                )

            async def open_case(key: str, rep: str, *, at=NOW):  # type: ignore[no-untyped-def]
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return await service.open_case(
                        c, tenant, country="GB", subjects=subjects(rep=rep), graph=GRAPH, authority=OFFICER,
                        requested_by=requester, idempotency_key=key, cfg=HIGH_RISK_CFG, sign=sign, now=at,
                        elevated_risk=True,
                    )  # fmt: skip

            hit = await open_case("pep-case-0001", "Rita Minister")
            assert hit.status is ScreeningStatus.REVIEW_REQUIRED  # never rejected, never verified
            again = await open_case("pep-case-0001", "Rita Minister")
            assert (
                again.case_id == hit.case_id and again.version == 1
            )  # the same hit is the same case
            async with tenant_transaction(eng, ctx(tenant)) as c:
                detail = await service.restricted_detail(c, tenant, hit.case_id)
                assert "PEP_MATCH" in detail["reasons"]
                alert = detail["alerts"][0]
                assert alert["list_id"] == "PEP" and alert["score"] >= 0.88 and alert["reasons"]
                rows = await service.history(c, tenant, hit.case_id)
                pep_version = next(x for x in rows[0].body["lists"] if x["list_id"] == "PEP")[
                    "version"
                ]
                assert alert["list_version"] == pep_version

            # a clear person on a fresh list passes the high-risk path; the same list 49 hours old blocks it
            clear = await open_case("pep-case-0002", "Alice Smith")
            assert clear.status is ScreeningStatus.VERIFIED
            blocked = await open_case("pep-case-0003", "Alice Smith", at=NOW + timedelta(hours=49))
            assert blocked.status is ScreeningStatus.UNAVAILABLE

            # a person rules with a rationale: the outcome is recorded and the alert and evidence stay
            async with tenant_transaction(eng, ctx(tenant)) as c:
                ruled = await service.review(
                    c, tenant, hit.case_id, ruling=Ruling(reviewer, ScreeningStatus.VERIFIED, WHY), cfg=CFG, sign=sign, now=NOW
                )  # fmt: skip
                assert ruled.status is ScreeningStatus.VERIFIED and ruled.version == 2
                rows = await service.history(c, tenant, hit.case_id)
                assert [r.decision_status for r in rows] == [
                    ScreeningStatus.REVIEW_REQUIRED,
                    ScreeningStatus.VERIFIED,
                ]
                assert rows[1].decided_by == reviewer and rows[1].rationale == WHY
                assert (
                    rows[1].body["matches"] == rows[0].body["matches"]
                )  # the PEP alert is carried, not deleted
                n = (
                    await c.execute(
                        text(
                            "SELECT count(*) AS n FROM verify.screening_match WHERE case_id = :c AND list_id = 'PEP'"
                        ),
                        {"c": uuid.UUID(hit.case_id)},
                    )
                ).one()
                assert n.n >= 1
                with pytest.raises(DBAPIError, match="append-only|permission denied"):
                    await c.execute(text("DELETE FROM verify.screening_match"))

    asyncio.run(run())


def test_velocity_breach_opens_one_review_that_is_resolved_with_evidence(rt_url: str) -> None:
    tenant, reviewer = uid(), uid()
    key = hash_identifier("device-fingerprint-123", tenant_id=tenant, kind="device")
    sent: list[str] = []

    async def notify(row: signal_store.ReviewRow) -> None:
        sent.append(row.review_id)

    async def run() -> None:
        async with engine(rt_url) as eng:
            raised = []
            for i in range(5):  # the proposed device sign-up rule fires at 3 in 24 hours
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    raised += await signals.record_attempt(
                        c, tenant, subject_ref=f"signup-{i}", action=Action.SIGNUP, keys={Dimension.DEVICE: key},
                        now=NOW + timedelta(minutes=i), notify=notify,
                    )  # fmt: skip
            assert [r.created for r in raised] == [
                True,
                False,
                False,
            ]  # attempts 3, 4 and 5 breach; one item, no duplicates
            assert len(sent) == 1
            async with tenant_transaction(eng, ctx(tenant)) as c:
                queue = await signals.open_queue(c, tenant)
                assert len(queue) == 1
                item = queue[0]
                assert item.source == "VELOCITY" and item.rule.startswith("VELOCITY_DEVICE_SIGNUP")
                assert (
                    item.detail["count"] == 3
                    and item.detail["threshold"] == 3
                    and item.detail["key_hash"] == key
                )
                assert item.notify_state == "SENT" and item.status == "OPEN"
                blob = repr(item.detail)
                assert "device-fingerprint" not in blob  # hashed identifiers only
                with pytest.raises(ScreeningError, match="IDENTIFIER_NOT_HASHED"):
                    await signals.record_attempt(
                        c, tenant, subject_ref="x", action=Action.SIGNUP, keys={Dimension.CONTACT: "a@example.com"}, now=NOW
                    )  # fmt: skip

            async with tenant_transaction(eng, ctx(tenant)) as c:
                ch = await signals.challenge(
                    c, tenant, item.review_id, statement="This is a shared office device used by three staff.", evidence_ref="doc:office-letter-3", now=NOW,
                )  # fmt: skip
                assert ch.status == "CHALLENGED"  # still open: only a person closes it
                with pytest.raises(ScreeningError, match="EVIDENCE_REF_REQUIRED"):
                    await signals.resolve(c, tenant, item.review_id, reviewer_id=reviewer, outcome="CLEARED_BY_REVIEWER", rationale=WHY, evidence_ref=" ", now=NOW)  # fmt: skip
                done = await signals.resolve(
                    c, tenant, item.review_id, reviewer_id=reviewer, outcome="CLEARED_BY_REVIEWER", rationale=WHY,
                    evidence_ref=EVIDENCE, now=NOW + timedelta(hours=1),
                )  # fmt: skip
                assert (
                    done.status == "RESOLVED"
                    and done.outcome == "CLEARED_BY_REVIEWER"
                    and done.evidence_ref == EVIDENCE
                )
                assert done.resolved_by == reviewer and done.rationale == WHY
                assert done.detail == item.detail  # the evidence the rule recorded is untouched
                with pytest.raises(ScreeningError, match="SIGNAL_ALREADY_RESOLVED"):
                    await signals.resolve(c, tenant, item.review_id, reviewer_id=reviewer, outcome="CONFIRMED_RISK", rationale=WHY, evidence_ref=EVIDENCE, now=NOW)  # fmt: skip
                assert await signals.open_queue(c, tenant) == []
                with pytest.raises(DBAPIError, match="append-only|permission denied"):
                    await c.execute(text("DELETE FROM verify.screening_signal"))

    asyncio.run(run())


def test_notification_failure_and_provider_failure_leave_the_item_open(rt_url: str) -> None:
    tenant = uid()
    party = Party("rep", PartyKind.PERSON, "Alice Smith")

    async def broken(row: signal_store.ReviewRow) -> None:
        raise ConnectionError("queue down")

    async def run() -> None:
        async with engine(rt_url) as eng:
            hang = FakeAdverseMedia(hangs=True)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                effect, raised = await signals.check_adverse_media(
                    c, tenant, subject_ref="subj-1", party=party, port=hang, current_tier=RiskTier.LOW, now=NOW,
                    timeout_s=0.05, notify=broken,
                )  # fmt: skip
                assert (
                    effect.review_required
                    and not effect.cleared
                    and raised is not None
                    and raised.created
                )
                assert raised.review.status == "OPEN" and raised.review.notify_state == "FAILED"
                assert (
                    raised.review.detail["provider"] == "fake-media"
                    and raised.review.detail["outcome"] == "TIMEOUT"
                )
                # the same failure again does not duplicate the item
                _, twice = await signals.check_adverse_media(
                    c, tenant, subject_ref="subj-1", party=party, port=hang, current_tier=RiskTier.LOW, now=NOW, timeout_s=0.05
                )  # fmt: skip
                assert (
                    twice is not None
                    and not twice.created
                    and twice.review.review_id == raised.review.review_id
                )
                hits = FakeAdverseMedia(
                    outcome=MediaOutcome.HITS,
                    hit_count=2,
                    severity=RiskTier.HIGH,
                    rationale="two reports",
                )
                effect, hit = await signals.check_adverse_media(
                    c, tenant, subject_ref="subj-2", party=party, port=hits, current_tier=RiskTier.LOW, now=NOW
                )  # fmt: skip
                assert effect.tier is RiskTier.HIGH and hit is not None
                assert (
                    hit.review.detail["rationale"] == "two reports"
                    and "queried_at" in hit.review.detail
                )
                none = FakeAdverseMedia()
                effect, nothing = await signals.check_adverse_media(
                    c, tenant, subject_ref="subj-3", party=party, port=none, current_tier=RiskTier.LOW, now=NOW
                )  # fmt: skip
                assert nothing is None and not effect.review_required
                assert (
                    len(await signals.open_queue(c, tenant)) == 2
                )  # the failed-notice item is still waiting, nothing cleared

    asyncio.run(run())

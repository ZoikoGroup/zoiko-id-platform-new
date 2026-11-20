"""Publishing company-verification market policy packs on PostgreSQL (WF02 K09; CR-109; acceptance T12, T23, T39).

Draft, approve, publish with every control, refuse each missing control by name, replace a version without rewriting the old interval, roll back, and replay: the pack in force at an earlier
moment is the pack that was in force then."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.organization.application import kyb_market_policy as svc
from zid_platform.modules.organization.domain import company_documents as cd
from zid_platform.modules.organization.domain import market_policy as mp

T0 = datetime.now(UTC).replace(microsecond=0) - timedelta(days=60)
GOOD = mp.MarketPolicy(
    jurisdiction="GB",
    region=None,
    legal_form="LTD",
    purpose="ORG_ONBOARDING",
    risk_tier="LOW",
    version="gb-ltd-v1",
    sources=(mp.Source("companies_house", 1, "LB-1", "kms://ch", True, "terms-2026-01"),),
    documents=(cd.DocumentRule("REGISTRY_EXTRACT", cd.Claim.CURRENT_STATUS, timedelta(days=90)),),
    ubo=mp.UboPolicy(Decimal(25), True, "compliance-gb"),
    reviewers_for_document_first=2,
    recheck_days={"K1": 180, "K2": 365},
    retention=mp.Retention({c: 365 for c in mp.RECORD_CLASSES}, "LB-RET-1", True, "job-1"),
)


def uid() -> str:
    return str(uuid.uuid4())


def test_policy_packs_are_proposed_approved_published_and_rolled_back(rt_url: str) -> None:
    tenant = uid()
    author, lena, pete, sam, cora, zed = (uid() for _ in range(6))
    key = svc.market_key(GOOD)

    async def run() -> None:
        async with engine(rt_url) as eng:

            async def tx(fn):
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return await fn(c)

            async def sign_off(h: str, *people: tuple[str, str]) -> None:
                for who, role in people:
                    await tx(lambda c, w=who, r=role: svc.approve(c, h, person=w, role=r))

            # 0. nothing published: the market has no pack, so it is not enabled
            assert await tx(lambda c: svc.effective_at(c, key, T0)) is None
            assert (
                await tx(lambda c: svc.retention_days_for(c, "GB", "ltd", now=T0)) is None
            )  # no pack, no period: documents keep the proof policy's own setting

            # 1. propose: the same content is the same version, the same name with other content is refused
            h1 = await tx(lambda c: svc.propose(c, GOOD, author=author))
            assert (
                await tx(lambda c: svc.propose(c, GOOD, author=author))
                == h1
                == mp.policy_hash(GOOD)
            )
            with pytest.raises(svc.PolicyError, match="POLICY_VERSION_EXISTS"):
                await tx(
                    lambda c: svc.propose(
                        c, replace(GOOD, reviewers_for_document_first=3), author=author
                    )
                )

            # 2. publishing with nothing signed names every missing control
            with pytest.raises(svc.PolicyError) as e:
                await tx(lambda c: svc.activate(c, h1, by=zed, tenant_id=tenant, now=T0))
            assert e.value.code == "POLICY_NOT_PUBLISHABLE"
            assert {
                "SIGN_OFF_LEGAL_MISSING",
                "SIGN_OFF_PRIVACY_MISSING",
                "SIGN_OFF_SECURITY_MISSING",
                "SIGN_OFF_COMPLIANCE_MISSING",
            } <= set(e.value.problems)

            # 3. the author cannot approve their own version (the table says so too), and a bad role is refused
            with pytest.raises(DBAPIError, match="author of a policy version cannot approve"):
                await tx(lambda c: svc.approve(c, h1, person=author, role="LEGAL"))
            with pytest.raises(svc.PolicyError, match="SIGN_OFF_ROLE_INVALID"):
                await tx(lambda c: svc.approve(c, h1, person=lena, role="CEO"))

            # 4. three of four is not enough, and the first version is a high-impact change that needs two different people (it has four)
            await sign_off(h1, (lena, "LEGAL"), (pete, "PRIVACY"), (sam, "SECURITY"))
            with pytest.raises(svc.PolicyError) as e2:
                await tx(lambda c: svc.activate(c, h1, by=zed, tenant_id=tenant, now=T0))
            assert e2.value.problems == ("SIGN_OFF_COMPLIANCE_MISSING",)
            await sign_off(h1, (cora, "COMPLIANCE"))
            await tx(lambda c: svc.activate(c, h1, by=zed, tenant_id=tenant, now=T0))
            h, pack = await tx(lambda c: svc.effective_at(c, key, T0 + timedelta(days=1)))
            assert h == h1 and pack == GOOD
            later = T0 + timedelta(days=1)
            assert (
                await tx(lambda c: svc.retention_days_for(c, "GB", "ltd", now=later)) == 365
            )  # the published pack now sets how long the documents are kept
            assert (
                await tx(
                    lambda c: svc.retention_days_for(
                        c, "GB", "ltd", record_class="SCREENING_EVIDENCE", now=later
                    )
                )
                == 365
            )
            assert (
                await tx(lambda c: svc.retention_days_for(c, "GB", "plc", now=later)) is None
            )  # another legal form has no pack
            assert (
                await tx(lambda c: svc.retention_days_for(c, "FR", "ltd", now=later)) is None
            )  # nor does another market
            with pytest.raises(svc.PolicyError, match="POLICY_ALREADY_ACTIVE"):
                await tx(lambda c: svc.activate(c, h1, by=zed, tenant_id=tenant, now=T0))

            # 5. a pack that fails its own checks cannot be published however many people sign (T23: no retention schedule)
            no_ret = replace(
                GOOD,
                version="gb-ltd-v2",
                retention=replace(
                    GOOD.retention, days={**GOOD.retention.days, "RAW_DOCUMENTS": None}
                ),
            )
            h_bad = await tx(lambda c: svc.propose(c, no_ret, author=author))
            await sign_off(
                h_bad, (lena, "LEGAL"), (pete, "PRIVACY"), (sam, "SECURITY"), (cora, "COMPLIANCE")
            )
            with pytest.raises(svc.PolicyError) as e3:
                await tx(
                    lambda c: svc.activate(
                        c, h_bad, by=zed, tenant_id=tenant, now=T0 + timedelta(days=10)
                    )
                )
            assert (
                "RETENTION_RAW_DOCUMENTS_MISSING" in e3.value.problems
                and "PUBLISH_TESTS_NOT_PASSED" in e3.value.problems
            )

            # 6. a good replacement: a high-impact change (the ownership threshold) needs two different approvers, which the four give
            v2 = replace(
                GOOD, version="gb-ltd-v3", ubo=mp.UboPolicy(Decimal(10), True, "compliance-gb")
            )
            h2 = await tx(lambda c: svc.propose(c, v2, author=author))
            await sign_off(
                h2, (lena, "LEGAL"), (pete, "PRIVACY"), (sam, "SECURITY"), (cora, "COMPLIANCE")
            )
            await tx(
                lambda c: svc.activate(c, h2, by=zed, tenant_id=tenant, now=T0 + timedelta(days=20))
            )

            # 7. replay: last month's decision replays against last month's pack; the old interval was ended, never rewritten
            assert (await tx(lambda c: svc.effective_at(c, key, T0 + timedelta(days=5))))[0] == h1
            assert (await tx(lambda c: svc.effective_at(c, key, T0 + timedelta(days=25))))[0] == h2
            assert await tx(lambda c: svc.effective_at(c, key, T0 - timedelta(days=1))) is None
            rows = await tx(lambda c: _intervals(c, key))
            assert [(r.kind, r.ended_at is None) for r in rows] == [
                ("PUBLISH", False),
                ("PUBLISH", True),
            ]

            # 8. roll back to the first version: a new interval with the reason, the second one ended, nothing deleted
            with pytest.raises(svc.PolicyError, match="REASON_REQUIRED"):
                await tx(
                    lambda c: svc.rollback(
                        c,
                        key,
                        h1,
                        by=zed,
                        reason="no",
                        tenant_id=tenant,
                        now=T0 + timedelta(days=30),
                    )
                )
            with pytest.raises(svc.PolicyError, match="POLICY_NEVER_ACTIVE"):
                await tx(
                    lambda c: svc.rollback(
                        c,
                        key,
                        h_bad,
                        by=zed,
                        reason="Restore the earlier threshold.",
                        tenant_id=tenant,
                        now=T0 + timedelta(days=30),
                    )
                )
            with pytest.raises(svc.PolicyError, match="POLICY_ALREADY_ACTIVE"):
                await tx(
                    lambda c: svc.rollback(
                        c,
                        key,
                        h2,
                        by=zed,
                        reason="Already the one in force.",
                        tenant_id=tenant,
                        now=T0 + timedelta(days=30),
                    )
                )
            await tx(
                lambda c: svc.rollback(
                    c,
                    key,
                    h1,
                    by=zed,
                    reason="The lower ownership threshold was not agreed with the market.",
                    tenant_id=tenant,
                    now=T0 + timedelta(days=30),
                )
            )
            assert (await tx(lambda c: svc.effective_at(c, key, T0 + timedelta(days=35))))[0] == h1
            assert (await tx(lambda c: svc.effective_at(c, key, T0 + timedelta(days=25))))[
                0
            ] == h2  # what happened in between stays as it was
            rows = await tx(lambda c: _intervals(c, key))
            assert [r.kind for r in rows] == ["PUBLISH", "PUBLISH", "ROLLBACK"] and sum(
                r.ended_at is None for r in rows
            ) == 1

            # 9. the record cannot be edited behind the service
            for sql, why in (
                ("UPDATE org.kyb_market_policy SET version = 'x'", "never changes"),
                ("DELETE FROM org.kyb_market_policy_approval", "never changes"),
                ("DELETE FROM org.kyb_market_policy_activation", "never deleted"),
                (
                    "UPDATE org.kyb_market_policy_activation SET kind = 'PUBLISH' WHERE kind = 'ROLLBACK'",
                    "only by ending",
                ),
            ):
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    with pytest.raises(DBAPIError, match=why + "|permission denied"):
                        await c.execute(text(sql))

            # 10. a tampered stored pack is refused, not used (the digest no longer matches its content)
            with pytest.raises(svc.PolicyError, match="POLICY_NOT_FOUND"):
                await tx(lambda c: svc.load(c, "sha256:" + "0" * 64))

    async def _intervals(c, key: str):
        return (
            await c.execute(
                text(
                    "SELECT kind, ended_at FROM org.kyb_market_policy_activation WHERE market_key = :k ORDER BY activated_at"
                ),
                {"k": key},
            )
        ).all()

    asyncio.run(run())


def test_an_unsigned_pack_can_be_activated_by_the_owner_override_and_the_record_names_what_was_skipped(rt_url: str) -> None:
    """CR-133: `without_signoff` puts the pack in force with nothing signed. The activation says what it lacked; it signs and fills in nothing."""
    tenant = uid()
    author, zed = uid(), uid()
    pack_in = replace(GOOD, jurisdiction="IE", version="ie-ltd-v1")  # its own market: the markets of the other tests stay as they are
    key = svc.market_key(pack_in)

    async def run() -> None:
        async with engine(rt_url) as eng:

            async def tx(fn):
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return await fn(c)

            h1 = await tx(lambda c: svc.propose(c, pack_in, author=author))
            assert await tx(lambda c: svc.effective_at(c, key, T0)) is None
            with pytest.raises(svc.PolicyError, match="POLICY_NOT_PUBLISHABLE"):  # the normal route still refuses
                await tx(lambda c: svc.activate(c, h1, by=zed, tenant_id=tenant, now=T0))
            skipped = await tx(lambda c: svc.activate(c, h1, by=zed, tenant_id=tenant, now=T0, without_signoff=True))
            assert "SIGN_OFF_LEGAL_MISSING" in skipped and "SIGN_OFF_COMPLIANCE_MISSING" in skipped
            h, pack = await tx(lambda c: svc.effective_at(c, key, T0 + timedelta(days=1)))
            assert h == h1 and pack == pack_in
            note = await tx(lambda c: c.execute(text("SELECT note FROM org.kyb_market_policy_activation WHERE market_key = :k"), {"k": key}))
            assert note.scalar_one().startswith("ACTIVATED_WITHOUT_SIGNOFF: ")
            approvals = await tx(lambda c: c.execute(text("SELECT count(*) FROM org.kyb_market_policy_approval WHERE policy_hash = :h"), {"h": h1}))
            assert approvals.scalar_one() == 0, "nothing was signed on anyone's behalf"
            with pytest.raises(svc.PolicyError, match="POLICY_ALREADY_ACTIVE"):
                await tx(lambda c: svc.activate(c, h1, by=zed, tenant_id=tenant, now=T0, without_signoff=True))

    asyncio.run(run())

"""Two independent reviewers for a company case no authoritative source backs, on PostgreSQL (WF02 K07; acceptance T10, T11).

A case the register could not find has no authoritative record: one reviewer's approval is only a first finding, a second different reviewer completes it, a disagreement escalates, and an
escalated case needs the escalation permission. A case a register DID return keeps the single-reviewer path."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.modules.organization.application import kyb as service
from zid_platform.modules.organization.domain.kyb import (
    Applicant,
    Capabilities,
    CompanyFacts,
    KybError,
    KybStatus,
    Policy,
    Review,
    Route,
    Routing,
    SourceKind,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
SIGNER = LocalSigner("kyb-dual-test")
RING = KeyRing(SIGNER)
ROUTING = Routing(
    "r1",
    Policy("p1", timedelta(days=365)),
    {"GB": Route(("companies-house",), None, ())},
    {"companies-house": "ENABLED"},
    {},
)
GOOD = "Certificate of incorporation and a recent extract sighted and checked."


def uid() -> str:
    return str(uuid.uuid4())


def sign(body: dict[str, Any]) -> str:
    return RING.sign(body, typ="zid-kyb-evidence+jwt")


class Register:
    """A register that finds nothing (no authoritative record), or one that finds the company under another name."""

    def __init__(self, facts: CompanyFacts | None) -> None:
        self.adapter_id, self.kind = "companies-house", SourceKind.OFFICIAL_REGISTER
        self.capabilities = Capabilities(frozenset({"GB"}))
        self.facts = facts

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Any]:
        return []

    async def get(self, number: str, country: str, now: datetime) -> CompanyFacts | None:
        return self.facts


def facts(number: str, name: str) -> CompanyFacts:
    return CompanyFacts(
        "companies-house", SourceKind.OFFICIAL_REGISTER, "GB", "companies-house", number, name, "ltd", "active", True,
        "1 High St", f"https://x/{number}", NOW, "b" * 64,
    )  # fmt: skip


def test_two_reviewers_for_a_case_without_an_authoritative_record(rt_url: str) -> None:
    tenant, requester, a, b, c3 = uid(), uid(), uid(), uid(), uid()

    async def open_case(eng, key: str, number: str, register: Register):
        async with tenant_transaction(eng, ctx(tenant)) as c:
            return await service.request_verification(
                c, tenant, applicant=Applicant("GB", number, "Acme Widgets Limited"), requested_by=requester,
                idempotency_key=key, adapters={"companies-house": register}, routing=ROUTING, sign=sign, now=NOW,
            )  # fmt: skip

    async def rule(eng, case_id: str, who: str, outcome: KybStatus, **kw):
        async with tenant_transaction(eng, ctx(tenant)) as c:
            return await service.review(
                c,
                tenant,
                case_id,
                ruling=Review(who, outcome, GOOD),
                routing=ROUTING,
                sign=sign,
                now=NOW,
                **kw,
            )

    async def run() -> None:
        async with engine(rt_url) as eng:
            # the register finds nothing: the case is in review with no authoritative record
            case = await open_case(eng, "key-dual-0001", "00555555", Register(None))
            assert case.status is KybStatus.NEEDS_REVIEW

            # 1. the first approval is a first finding: the case stays in review
            first = await rule(eng, case.case_id, a, KybStatus.VERIFIED)
            assert first.status is KybStatus.NEEDS_REVIEW and "REVIEWER_1_ACCEPTED" in first.reasons

            # 2. the same person cannot give the second opinion (T10)
            with pytest.raises(KybError, match="REVIEWER_NOT_INDEPENDENT"):
                await rule(eng, case.case_id, a, KybStatus.VERIFIED)

            # 3. a different person who agrees completes it, with an expiry and the agreed marker
            done = await rule(eng, case.case_id, b, KybStatus.VERIFIED)
            assert done.status is KybStatus.VERIFIED and done.expiry_at == NOW + timedelta(days=365)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                rows = await service.history(c, tenant, case.case_id)
                assert [r.decision_status for r in rows] == [
                    KybStatus.NEEDS_REVIEW,
                    KybStatus.NEEDS_REVIEW,
                    KybStatus.VERIFIED,
                ]
                assert [r.decided_by for r in rows] == [None, a, b]
                assert (
                    "DUAL_REVIEW_AGREED" in rows[2].reasons
                    and "REVIEWER_1_ACCEPTED" not in rows[2].reasons
                )  # markers are not inherited

            # 4. a disagreement escalates and is never last-write-wins (T11); a decision then needs the permission and a third person
            case2 = await open_case(eng, "key-dual-0002", "00666666", Register(None))
            await rule(eng, case2.case_id, a, KybStatus.VERIFIED)
            esc = await rule(eng, case2.case_id, b, KybStatus.REJECTED)
            assert esc.status is KybStatus.NEEDS_REVIEW and {
                "ESCALATED_TO_COMPLIANCE",
                "REVIEWER_DISAGREEMENT",
            } <= set(esc.reasons)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert await service.awaiting_compliance(c, tenant, case2.case_id) is True
                assert await service.awaiting_compliance(c, tenant, case.case_id) is False
            with pytest.raises(KybError, match="ESCALATION_NEEDS_COMPLIANCE"):
                await rule(
                    eng, case2.case_id, c3, KybStatus.VERIFIED
                )  # without the escalation permission
            with pytest.raises(KybError, match="REVIEWER_NOT_INDEPENDENT"):
                await rule(eng, case2.case_id, a, KybStatus.VERIFIED, escalation_authorized=True)
            final = await rule(
                eng, case2.case_id, c3, KybStatus.REJECTED, escalation_authorized=True
            )
            assert final.status is KybStatus.REJECTED and "COMPLIANCE_DECISION" in final.reasons

            # 5. a rejection by one reviewer is final (it protects), and nothing is ruled on after it
            case3 = await open_case(eng, "key-dual-0003", "00777777", Register(None))
            assert (
                await rule(eng, case3.case_id, a, KybStatus.REJECTED)
            ).status is KybStatus.REJECTED
            with pytest.raises(KybError, match="CASE_NOT_IN_REVIEW"):
                await rule(eng, case3.case_id, b, KybStatus.VERIFIED)

            # 6. a case a register DID return keeps the single-reviewer path (a name the register contradicts)
            case4 = await open_case(
                eng,
                "key-dual-0004",
                "00888888",
                Register(facts("00888888", "Acme Global Industries Limited")),
            )
            assert case4.status is KybStatus.NEEDS_REVIEW
            assert (
                await rule(eng, case4.case_id, a, KybStatus.VERIFIED)
            ).status is KybStatus.VERIFIED

    asyncio.run(run())

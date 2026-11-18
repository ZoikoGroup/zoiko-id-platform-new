"""Assessments of one company that happen at the same moment (WF02 KYB-15): a recheck, an event and a person's request can coincide. Each gets its own version, none fails, none is lost."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.modules.organization.application import kyb as kyb_service
from zid_platform.modules.organization.application import kyb_assessment as assessment
from zid_platform.modules.organization.application import kyb_company as company
from zid_platform.modules.organization.application import kyb_operations as ops
from zid_platform.modules.organization.domain.kyb import (
    Applicant,
    Capabilities,
    CompanyFacts,
    Policy,
    Route,
    Routing,
    SourceKind,
)

NOW = datetime.now(UTC).replace(microsecond=0)
RING = KeyRing(LocalSigner("kyb-concurrency-test"))
ROUTING = Routing(
    "r1",
    Policy("p1", timedelta(days=365)),
    {"GB": Route(("companies-house",), None, ())},
    {"companies-house": "ENABLED"},
    {},
)
PARALLEL = 8


def sign(body: dict[str, Any]) -> str:
    return RING.sign(body, typ="zid-kyb-evidence+jwt")


class Register:
    adapter_id, kind = "companies-house", SourceKind.OFFICIAL_REGISTER
    capabilities = Capabilities(frozenset({"GB"}))

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Any]:
        return []

    async def get(self, number: str, country: str, now: datetime) -> CompanyFacts:
        return CompanyFacts(
            "companies-house", SourceKind.OFFICIAL_REGISTER, "GB", "companies-house", number, "Acme Widgets Limited", "ltd", "active", True,
            "1 High St", f"https://x/{number}", NOW - timedelta(days=2), "b" * 64,
        )  # fmt: skip


def test_parallel_assessments_of_one_company_take_distinct_versions(rt_url: str) -> None:
    tenant, requester = str(uuid.uuid4()), str(uuid.uuid4())

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await kyb_service.request_verification(
                    c, tenant, applicant=Applicant("GB", "00123456", "Acme Widgets Limited"), requested_by=requester, idempotency_key="key-conc-0001",
                    adapters={"companies-house": Register()}, routing=ROUTING, sign=sign, now=NOW,
                )  # fmt: skip

            async def one(i: int) -> int:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    if i % 2:
                        return (
                            await company.reassess(
                                c, tenant, trigger=f"TEST{i}", actor=None, now=NOW
                            )
                        ).version
                    stored = await ops.apply_event(c, tenant, "REGISTRY_STATUS_CHANGED", now=NOW)
                    assert stored is not None
                    return stored.version

            versions = await asyncio.gather(*(one(i) for i in range(PARALLEL)))
            assert sorted(versions) == list(
                range(1, PARALLEL + 1)
            )  # eight assessments, eight versions, no gaps, no duplicates, no failure
            async with tenant_transaction(eng, ctx(tenant)) as c:
                rows = await assessment.history(c, tenant, limit=50)
            assert [r.version for r in rows] == list(range(PARALLEL, 0, -1))
            assert all(
                r.grade.value == "K2" for r in rows
            )  # the same facts give the same grade whichever got there first

    asyncio.run(run())

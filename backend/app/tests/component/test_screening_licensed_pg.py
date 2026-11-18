"""Licensed providers for PEP, associates and adverse media on PostgreSQL (WF06 S06, S07, S11; ENG-10, ENG-11, ENG-13; QA-021, QA-022, QA-045 to QA-047).

A licensed class is asked only through an approved, contracted provider in a signed region; it is billed once per case; a hit is enhanced due diligence and never a refusal; a provider that is down, over
budget, off or not contracted makes the class unavailable and is never charged for it; and an outage opens a circuit."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest  # noqa: F401
from conftest import ctx, engine
from sqlalchemy import text
from test_screening_requests_pg import MAX_AGE, NOW, load_lists, open_case, person, request, uid
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import licensed
from zid_platform.modules.screening.application import screening_requests as sr
from zid_platform.modules.screening.domain import coverage as cov
from zid_platform.modules.screening.domain import eligibility as elig
from zid_platform.modules.screening.infrastructure import store
from zid_platform.modules.screening.infrastructure.market_policies import parse
from zid_platform.modules.screening.infrastructure.providers import parse as providers_parse


class FakeProvider:
    """A recording stand-in for a contracted adapter. It can hit, miss or fail, and it counts every call it receives."""

    def __init__(self, provider_id: str = "vendor-a", *, hits: int = 0, fail: bool = False) -> None:
        self.provider_id = provider_id
        self.hits, self.fail = hits, fail
        self.calls: list[tuple[str, str]] = []

    async def screen(self, *, klass: Any, case_ref: str, party: Any, region: str) -> Any:
        self.calls.append((case_ref, klass.value))
        if self.fail:
            raise RuntimeError("provider is down")
        found = tuple(
            licensed.LicensedHit(
                f"r{i}", "Possible associate", 0.93, {"office": "Minister", "status": "FORMER"}
            )
            for i in range(self.hits)
        )
        return licensed.LicensedResult(found, "2026-11", "req-1", "resp-1")


def full_pack(tenant: str) -> Any:
    return parse(
        {
            "markets": {
                "GB": {
                    "version": "gb-2",
                    "purposes": ["KYB"],
                    "regions": ["uk-1"],
                    "subject_types": ["BUSINESS", "UBO", "INDIVIDUAL"],
                    "classes": {"SANCTIONS": "OFFICIAL", "PEP_RCA": "LICENSED"},
                    "legal_basis": "lawful-basis-ref-1",
                    "signed_by": ["legal-owner", "aml-owner"],
                    "valid_from": "2026-01-01T00:00:00Z",
                    "enabled_tenants": [tenant],
                }
            }
        }
    )["GB"]


def registry(**kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "state": "APPROVED",
        "adapter": "vendor-a-v1",
        "classes": ["PEP_RCA"],
        "markets": ["GB"],
        "regions": ["uk-1"],
        "contract_version": "c1",
        "price_version": "p1",
        "finance_authorised_by": "finance-owner",
        "unit_cost": "0.40",
        "monthly_budget": "100",
        "monthly_quota": 100,
    }
    body.update(kw)
    return providers_parse({"providers": {"vendor-a": body}})


def both(tenant: str) -> elig.Request:
    return request(tenant, requested=frozenset({cov.Klass.SANCTIONS, cov.Klass.PEP_RCA}))


async def spend(c: Any) -> tuple[int, float]:
    row = (
        await c.execute(
            text(
                "SELECT count(*) AS n, coalesce(sum(cost), 0) AS cost FROM verify.screening_supplier_call"
            )
        )
    ).one()
    return int(row.n), float(row.cost)


def test_a_licensed_class_is_asked_only_through_an_approved_provider_and_billed_once(
    rt_url: str,
) -> None:
    tenant = uid()

    async def run() -> None:
        licensed.reset_circuits()
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW - timedelta(hours=1))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                base = {
                    "request": both(tenant),
                    "pack": full_pack(tenant),
                    "snapshots": await store.snapshots(c),
                }
                # no provider is registered: the class cannot run, and the case does not clear without a class the tenant asked for
                none = await open_case(
                    c, tenant, "key-lic-none01", person("Alice Wonderland"), **base
                )
                assert (
                    none["finding"] == "Further information is needed"
                    and none["pep_rca"] == "Not enabled"
                )
                detail = await sr.restricted_detail(c, none["request_id"], now=NOW, max_age=MAX_AGE)
                assert "UNSCREENED_REQUIRED_CLASSES" in detail["model"]["decision"]["reason_codes"]
                assert await spend(c) == (0, 0.0)
                p = FakeProvider()
                clear = await open_case(
                    c,
                    tenant,
                    "key-lic-clear1",
                    person("Alice Wonderland"),
                    registry=registry(),
                    adapters={"vendor-a": p},
                    **base,
                )
                assert (
                    clear["finding"] == "No candidate was found in the checked sources"
                    and clear["pep_rca"] == "Contracted checks completed"
                )
                assert len(p.calls) == 1 and await spend(c) == (1, 0.4)
                again = await open_case(
                    c,
                    tenant,
                    "key-lic-clear1",
                    person("Alice Wonderland"),
                    registry=registry(),
                    adapters={"vendor-a": p},
                    **base,
                )
                assert (
                    again["request_id"] == clear["request_id"]
                    and len(p.calls) == 1
                    and await spend(c) == (1, 0.4)
                )  # not asked, not billed twice
                hit = await open_case(
                    c,
                    tenant,
                    "key-lic-hit-01",
                    person("Bob Builder"),
                    registry=registry(),
                    adapters={"vendor-a": FakeProvider(hits=1)},
                    **base,
                )
                assert (
                    hit["finding"] == "Further information is needed"
                    and "Bob" not in str(hit)
                    and "Minister" not in str(hit)
                )
                detail = await sr.restricted_detail(c, hit["request_id"], now=NOW, max_age=MAX_AGE)
                assert detail["model"]["decision"]["status"] == "EDD_REQUIRED" and detail["model"][
                    "decision"
                ]["reason_codes"] == ["PEP_OR_RCA_CANDIDATE"]
                assert (
                    detail["model"]["match_status"] == "NO_CANDIDATE"
                )  # a PEP candidate is not a sanctions candidate
                assert (
                    detail["candidates"][0]["list_id"] == "vendor-a:PEP_RCA"
                    and detail["state"] == "REVIEW_REQUIRED"
                )

    asyncio.run(run())


def test_a_provider_that_is_down_capped_or_off_makes_the_class_unavailable_and_costs_nothing(
    rt_url: str,
) -> None:
    tenant = uid()

    async def run() -> None:
        licensed.reset_circuits()
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW - timedelta(hours=1))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                base = {
                    "request": both(tenant),
                    "pack": full_pack(tenant),
                    "snapshots": await store.snapshots(c),
                }
                down = FakeProvider(fail=True)
                out = await open_case(
                    c,
                    tenant,
                    "key-lic-down-01",
                    person("Alice Wonderland"),
                    registry=registry(),
                    adapters={"vendor-a": down},
                    **base,
                )
                assert (
                    out["finding"] == "Further information is needed"
                    and out["pep_rca"] == "A source is unavailable"
                )  # never clear
                detail = await sr.restricted_detail(c, out["request_id"], now=NOW, max_age=MAX_AGE)
                assert (
                    detail["state"] == "SOURCE_UNAVAILABLE"
                    and detail["model"]["pep_rca"]["result"] is None
                )
                assert await spend(c) == (1, 0.0)  # the outage is recorded and is never chargeable
                for i in range(5):  # outages open the circuit
                    await open_case(
                        c,
                        tenant,
                        f"key-lic-down-{i + 2:02d}",
                        person("Alice Wonderland"),
                        registry=registry(),
                        adapters={"vendor-a": down},
                        **base,
                    )
                asked = len(down.calls)
                await open_case(
                    c,
                    tenant,
                    "key-lic-down-99",
                    person("Alice Wonderland"),
                    registry=registry(),
                    adapters={"vendor-a": down},
                    **base,
                )
                assert (
                    len(down.calls) == asked
                )  # the circuit is open: the provider is not asked again
                licensed.reset_circuits()
                p = FakeProvider()
                capped = await open_case(
                    c,
                    tenant,
                    "key-lic-cap-001",
                    person("Alice Wonderland"),
                    registry=registry(monthly_budget="0.30"),
                    adapters={"vendor-a": p},
                    **base,
                )
                assert (
                    capped["pep_rca"] == "A source is unavailable" and p.calls == []
                )  # the budget stops the call before the charge
                for reg in (
                    registry(state="DISABLED"),
                    registry(state="SANDBOX"),
                    registry(markets=["DE"]),
                    registry(finance_authorised_by=None),
                ):
                    q = FakeProvider()
                    off = await open_case(
                        c,
                        tenant,
                        f"key-lic-off-{uuid.uuid4().hex[:6]}",
                        person("Alice Wonderland"),
                        registry=reg,
                        adapters={"vendor-a": q},
                        **base,
                    )
                    assert off["finding"] == "Further information is needed"  # never clear
                    assert (
                        q.calls == []
                    )  # an unapproved, sandbox, uncontracted or unauthorised provider is never called

    asyncio.run(run())

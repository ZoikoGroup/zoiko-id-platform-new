"""Reading directors and persons with significant control from the official register (ZID-ED-04 section 04) and the guards that keep
screening a security control (section 07: it must not be disabled by plan tier)."""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from zid_platform.modules.organization.application.kyb import register_people
from zid_platform.modules.organization.domain.kyb import KybError, RegisterRead
from zid_platform.modules.organization.infrastructure.kyb_adapters import CompaniesHouseKyb
from zid_platform.modules.screening.domain.decision import ScreeningInput, decide

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
OFFICERS = {
    "total_results": 3,
    "items": [
        {
            "name": "SMITH, Alice Jane",
            "officer_role": "director",
            "date_of_birth": {"month": 4, "year": 1980},
        },
        {"name": "OLD, Oliver", "officer_role": "director", "resigned_on": "2020-01-01"},
        {"name": "HOLDINGS SECRETARIAL LIMITED", "officer_role": "corporate-secretary"},
    ],
}
PSCS = {
    "total_results": 3,
    "items": [
        {
            "name": "OWNER, Olivia",
            "kind": "individual-person-with-significant-control",
            "natures_of_control": ["ownership-of-shares-25-to-50-percent"],
            "date_of_birth": {"year": 1975},
        },
        {
            "name": "Parent Holdings Limited",
            "kind": "corporate-entity-person-with-significant-control",
            "natures_of_control": ["voting-rights-75-to-100-percent"],
        },
        {
            "name": "GONE, Gerald",
            "kind": "individual-person-with-significant-control",
            "ceased": True,
            "ceased_on": "2021-02-02",
        },
    ],
}


def adapter(handler: Any) -> CompaniesHouseKyb:
    return CompaniesHouseKyb(lambda: "key", httpx.MockTransport(handler))


def serve(officers: dict[str, Any], pscs: dict[str, Any]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/officers"):
            return httpx.Response(200, json=officers)
        if path.endswith("/persons-with-significant-control"):
            return httpx.Response(200, json=pscs)
        return httpx.Response(404)

    return handler


def read(a: CompaniesHouseKyb, country: str = "GB") -> RegisterRead:
    return asyncio.run(a.register_people("00123456", country, NOW))


def test_officers_and_persons_with_significant_control_are_read_in_natural_order_with_who_has_ceased() -> (
    None
):
    got = read(adapter(serve(OFFICERS, PSCS)))
    assert got.status == "OK" and got.source == "companies-house" and len(got.content_hash) == 64
    by = {(p.kind, p.name): p for p in got.people}
    alice = by[("OFFICER", "Alice Jane SMITH")]
    assert not alice.ceased and alice.birth_year == "1980" and not alice.entity
    assert by[("OFFICER", "Oliver OLD")].ceased, "a resigned officer is not current"
    assert by[("OFFICER", "HOLDINGS SECRETARIAL LIMITED")].entity
    olivia = by[("PSC", "Olivia OWNER")]
    assert (
        olivia.natures == ("ownership-of-shares-25-to-50-percent",) and olivia.birth_year == "1975"
    )
    assert by[("PSC", "Parent Holdings Limited")].entity and by[("PSC", "Gerald GONE")].ceased


def test_the_same_register_always_has_the_same_hash_and_a_change_changes_it() -> None:
    a = read(adapter(serve(OFFICERS, PSCS)))
    assert a.content_hash == read(adapter(serve(OFFICERS, PSCS))).content_hash
    more = {
        **PSCS,
        "total_results": 4,
        "items": [
            *PSCS["items"],
            {"name": "LATE, Laura", "kind": "individual-person-with-significant-control"},
        ],
    }
    assert read(adapter(serve(OFFICERS, more))).content_hash != a.content_hash


def test_a_list_longer_than_the_page_is_unavailable_never_a_partial_answer_that_looks_complete() -> (
    None
):
    long = {**PSCS, "total_results": 250}
    with pytest.raises(KybError) as e:
        read(adapter(serve(OFFICERS, long)))
    assert e.value.code == "SOURCE_UNAVAILABLE"


def test_a_country_without_a_source_is_not_applicable_and_a_register_that_fails_is_unavailable() -> (
    None
):
    assert read(adapter(serve(OFFICERS, PSCS)), "FR").status == "NOT_APPLICABLE"
    ok = adapter(serve(OFFICERS, PSCS))
    assert (
        asyncio.run(register_people({"companies-house": ok}, "00123456", "GB", NOW)).status == "OK"
    )
    assert (
        asyncio.run(register_people({"companies-house": ok}, "00123456", "FR", NOW)).status
        == "NOT_APPLICABLE"
    )
    assert asyncio.run(register_people({}, "00123456", "GB", NOW)).status == "NOT_APPLICABLE"
    down = adapter(lambda request: httpx.Response(503))
    assert (
        asyncio.run(register_people({"companies-house": down}, "00123456", "GB", NOW)).status
        == "UNAVAILABLE"
    )
    keyless = CompaniesHouseKyb(lambda: None, httpx.MockTransport(serve(OFFICERS, PSCS)))
    assert (
        asyncio.run(register_people({"companies-house": keyless}, "00123456", "GB", NOW)).status
        == "UNAVAILABLE"
    )


# ---- screening is a security control: no plan, tier or entitlement can switch it off

SCREENING = Path(__file__).resolve().parents[2] / "src" / "zid_platform" / "modules" / "screening"
CLAIM_SCREENING = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "zid_platform"
    / "modules"
    / "onboarding"
    / "api"
    / "org_claim_screening.py"
)


def test_the_decision_takes_no_plan_tier_or_entitlement() -> None:
    fields = set(ScreeningInput.__dataclass_fields__)
    assert not {
        f
        for f in fields
        if any(w in f for w in ("plan", "tier", "entitlement", "sku", "subscription"))
    }
    assert not {
        p
        for p in inspect.signature(decide).parameters
        if any(w in p for w in ("plan", "tier", "entitlement"))
    }


def test_nothing_in_screening_reads_a_plan_or_an_entitlement() -> None:
    sources = [*SCREENING.rglob("*.py"), CLAIM_SCREENING]
    assert len(sources) > 10
    for path in sources:
        text = path.read_text(encoding="utf-8").lower()
        assert "modules.commercial" not in text, f"{path.name} imports the commercial module"
        for word in ("entitlement", "plan_code", "subscription", "billing"):
            assert word not in text, f"{path.name} mentions {word}"

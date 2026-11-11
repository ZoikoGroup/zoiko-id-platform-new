"""The policy pack check against PostgreSQL (ZID-RCG-003 T01, T02): an ENABLED country also needs an approved pack, or it is refused before
any provider is asked (enforce) or only reported (shadow). The country registry stays the baseline.

Each test uses its own country, because the database is shared and the capability and purpose are the same ones every time.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_policy_pack_service_pg import _publish
from test_policy_packs_pg import NOW as PACK_NOW
from test_policy_packs_pg import _draft
from zid_errors import ZidError
from zid_platform.country_gate import check_packs
from zid_platform.modules.verification.application import policy_packs as svc
from zid_platform.modules.verification.domain.countries import APPROVALS, Capability
from zid_platform.modules.verification.domain.policy_packs import PACK_PURPOSE
from zid_platform.modules.verification.infrastructure.country_registry import parse

NOW = PACK_NOW
CAP = Capability.INDIVIDUAL_VERIFICATION


def _registry(status: str, *countries: str, effective: str = "2026-01-01") -> Any:
    entry: dict[str, Any] = {"status": status, "provider": "simulator"}
    if status == "ENABLED":
        entry |= {"effective_from": effective, "approvals": {a: f"ref-{a}" for a in APPROVALS}}
    return parse(
        {
            "version": "t",
            "countries": {
                c: {"name": c, "wave": 1, "capabilities": {"INDIVIDUAL_VERIFICATION": entry}}
                for c in countries
            },
        }
    )


def _check(
    rt_url: str,
    country: str,
    *,
    mode: str = "enforce",
    reg: Any = None,
    environment: str = "production",
) -> None:
    async def go() -> None:
        async with engine(rt_url) as eng:
            await check_packs(
                eng,
                ctx(uuid.uuid4()),
                mode=mode,
                environment=environment,
                reg=reg or _registry("ENABLED", country),
                country=country,
                capability=CAP,
                now=NOW,
            )

    asyncio.run(go())


def _pack(country: str, **key: Any) -> Any:
    key = {
        "product": CAP.value,
        "purpose": PACK_PURPOSE,
        "subdivision": None,
        "profession": None,
    } | key
    return _draft(country=country, **key)


def _publish_it(rt_url: str, pack: Any) -> None:
    async def go() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await _publish(c, pack)

    asyncio.run(go())


def _refused(rt_url: str, country: str, code: str, **kw: Any) -> None:
    with pytest.raises(ZidError) as e:
        _check(rt_url, country, **kw)
    assert (e.value.code, e.value.http_status) == (code, 422)


def test_t01_an_enabled_market_with_no_approved_pack_is_refused_in_enforce(rt_url: str) -> None:
    _refused(rt_url, "IE", "REGULATORY_POLICY_UNAVAILABLE")


def test_the_same_market_in_shadow_goes_on_and_says_it_would_have_been_refused(
    rt_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="zid.regulatory"):
        _check(rt_url, "NL", mode="shadow")
    assert any(
        "would refuse (shadow)" in r.getMessage()
        and "REGULATORY_POLICY_UNAVAILABLE" in r.getMessage()
        for r in caplog.records
    )


def test_an_approved_country_pack_lets_the_request_go_on_and_retiring_it_closes_the_market_again(
    rt_url: str,
) -> None:
    pack = _pack("DE")
    _publish_it(rt_url, pack)
    _check(rt_url, "DE")  # no error: a pack governs

    async def retire() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await svc.retire_pack(c, pack.pack_id, 1, now=NOW)

    asyncio.run(retire())
    _refused(rt_url, "DE", "REGULATORY_POLICY_UNAVAILABLE")


def test_a_draft_that_was_never_published_does_not_open_the_market(rt_url: str) -> None:
    async def go() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await svc.open_draft(c, _pack("ES"), now=NOW)

    asyncio.run(go())
    _refused(rt_url, "ES", "REGULATORY_POLICY_UNAVAILABLE")


def test_t02_a_market_with_only_a_profession_pack_needs_a_review_not_a_pass(rt_url: str) -> None:
    _publish_it(rt_url, _pack("FR", profession="nurse"))
    _refused(rt_url, "FR", "REGULATORY_REVIEW_REQUIRED")


def test_a_sandbox_capability_needs_no_pack_in_a_development_environment(rt_url: str) -> None:
    _check(rt_url, "BE", reg=_registry("SANDBOX", "BE"), environment="test")


def test_a_capability_the_country_registry_has_not_enabled_is_left_to_the_country_gate(
    rt_url: str,
) -> None:
    _check(
        rt_url, "AT", reg=_registry("DISABLED", "AT")
    )  # `enforce` refuses it; no pack is read, so no second refusal here
    _check(
        rt_url, "SE", reg=_registry("ENABLED", "AT")
    )  # a country the registry does not list is DISABLED too


def test_off_never_asks(rt_url: str) -> None:
    _check(rt_url, "PT", mode="off")


def test_a_published_pack_edited_behind_the_applications_back_closes_the_market_loudly(
    rt_url: str, owner_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    pack = _pack("IT")
    _publish_it(rt_url, pack)
    _check(rt_url, "IT")

    async def tamper() -> None:
        async with (
            engine(owner_url) as eng,
            eng.begin() as c,
        ):  # only the owner can, and only with the guard trigger off
            await c.execute(
                text("ALTER TABLE verify.policy_pack DISABLE TRIGGER policy_pack_guard")
            )
            try:
                await c.execute(
                    text(
                        "UPDATE verify.policy_pack SET rules = jsonb_set(rules, '{lawful_basis}', '\"nothing\"') WHERE pack_id = :p"
                    ),
                    {"p": pack.pack_id},
                )
            finally:
                await c.execute(
                    text("ALTER TABLE verify.policy_pack ENABLE TRIGGER policy_pack_guard")
                )

    asyncio.run(tamper())
    with caplog.at_level(logging.WARNING, logger="zid.regulatory"):
        _refused(rt_url, "IT", "REGULATORY_POLICY_UNAVAILABLE")
    assert any(
        "PACK_STORED_HASH_MISMATCH" in r.getMessage() for r in caplog.records
    )  # the reason is logged, not hidden
    _check(rt_url, "IT", mode="shadow")  # shadow still goes on


def test_the_clock_decides_whether_a_source_is_in_force(rt_url: str) -> None:
    _publish_it(rt_url, _pack("LU"))
    _check(rt_url, "LU")  # in force at NOW
    before = datetime(2025, 1, 1, tzinfo=UTC)  # before the pack's source took effect

    async def go() -> None:
        async with engine(rt_url) as eng:
            await check_packs(
                eng,
                ctx(uuid.uuid4()),
                mode="enforce",
                environment="production",
                reg=_registry("ENABLED", "LU", effective="2024-01-01"),
                country="LU",
                capability=CAP,
                now=before,
            )

    with pytest.raises(ZidError) as e:
        asyncio.run(go())
    assert e.value.code == "REGULATORY_POLICY_UNAVAILABLE"

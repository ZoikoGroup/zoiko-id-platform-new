"""The market release check at the country gate (ZID-RCG-003 RCG-G10; CR-072) against PostgreSQL: an ENABLED market with an approved pack
is still closed until a market release is LAUNCHED, and it closes again when the packs in force change or the release is withdrawn.

The database is shared, so the test cleans up after itself: its release is withdrawn and its packs retired even when an assertion fails.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from conftest import ctx, engine
from pack_test_support import captured
from test_policy_pack_service_pg import _publish
from test_policy_packs_pg import NOW, _draft
from zid_errors import ZidError
from zid_platform.country_gate import check_packs
from zid_platform.modules.verification.application import market_releases as rel
from zid_platform.modules.verification.application import policy_packs as svc
from zid_platform.modules.verification.domain import market_release as md
from zid_platform.modules.verification.domain.countries import APPROVALS, Capability
from zid_platform.modules.verification.domain.policy_packs import PACK_PURPOSE
from zid_platform.modules.verification.infrastructure.country_registry import parse

CAP = Capability.INDIVIDUAL_VERIFICATION
COUNTRY = "SE"
MARKET = md.MarketKey(COUNTRY, CAP.value, PACK_PURPOSE)
APPROVERS = {"legal": "al", "security_grc": "gus", "qa": "qa-lead", "product_owner": "pat"}


def _registry() -> Any:
    entry = {
        "status": "ENABLED",
        "provider": "simulator",
        "effective_from": "2026-01-01",
        "approvals": {a: f"ref-{a}" for a in APPROVALS},
    }
    return parse(
        {
            "version": "t",
            "countries": {
                COUNTRY: {
                    "name": COUNTRY,
                    "wave": 1,
                    "capabilities": {"INDIVIDUAL_VERIFICATION": entry},
                }
            },
        }
    )


def _check(rt_url: str, *, release_mode: str = "enforce") -> None:
    async def go() -> None:
        async with engine(rt_url) as eng:
            await check_packs(
                eng,
                ctx(uuid.uuid4()),
                mode="enforce",
                environment="production",
                reg=_registry(),
                country=COUNTRY,
                capability=CAP,
                now=NOW,
                release_mode=release_mode,
            )

    asyncio.run(go())


def _run(rt_url: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, eng.begin() as c:
            return await work(c)

    return asyncio.run(go())


def _pack(**key: Any) -> Any:
    key = {
        "product": CAP.value,
        "purpose": PACK_PURPOSE,
        "subdivision": None,
        "profession": None,
    } | key
    return _draft(country=COUNTRY, **key)


async def _launch(c: Any, rid: str) -> None:
    await rel.open_release(c, rid, MARKET, author="maker", now=NOW)
    for g in md.GATES:
        await rel.attest_gate(
            c,
            rid,
            1,
            gate=g.gate_id,
            status=md.GateStatus.PASSED,
            evidence_ref=f"EVID-{g.gate_id}",
            role=g.owner_role,
            by=f"rec-{g.owner_role}",
            now=NOW,
        )
    frozen = await rel.freeze_release(c, rid, 1, suppliers=[], now=NOW)
    for role in md.RELEASE_APPROVER_ROLES:
        await rel.approve_release(
            c,
            rid,
            1,
            role=role,
            approver=APPROVERS[role],
            signed_hash=frozen.release_hash or "",
            now=NOW,
        )
    await rel.launch_release(c, rid, 1, now=NOW)


async def _withdraw(c: Any, rid: str) -> None:
    for r in await rel.list_releases(c, country=COUNTRY):
        if r.release_id == rid and r.status is md.ReleaseStatus.LAUNCHED:
            await rel.withdraw_release(c, r.release_id, r.version, now=NOW)


async def _retire(c: Any, pack: Any) -> None:
    try:
        async with c.begin_nested():
            await svc.retire_pack(c, pack.pack_id, 1, now=NOW)
    except Exception:  # noqa: BLE001  (never published, or already retired)
        return


@contextmanager
def _cleanup(rt_url: str, packs: list[Any], rid: str) -> Iterator[None]:
    try:
        yield
    finally:
        _run(rt_url, lambda c: _withdraw(c, rid))
        for p in packs:
            _run(rt_url, lambda c, p=p: _retire(c, p))


def test_an_approved_pack_alone_does_not_open_the_market_a_launched_release_does_and_a_new_pack_or_a_withdrawal_closes_it_again(
    rt_url: str,
) -> None:
    country_pack, nurses = _pack(), _pack(profession="nurse")
    rid = f"gate-{uuid.uuid4().hex[:10]}"
    with _cleanup(rt_url, [country_pack, nurses], rid):
        _run(rt_url, lambda c: _publish(c, country_pack))
        _check(
            rt_url, release_mode="off"
        )  # the pack alone is enough while the release check is off

        with pytest.raises(ZidError) as e:
            _check(rt_url)
        assert (e.value.code, e.value.http_status) == ("MARKET_NOT_RELEASED", 422)
        with captured("zid.regulatory") as logs:
            _check(rt_url, release_mode="shadow")  # reported, not refused
        assert any(
            "market release check would refuse (shadow)" in m and "no_release" in m for m in logs
        )

        _run(rt_url, lambda c: _launch(c, rid))
        _check(rt_url)  # pack and release: open

        # a pack approved after the launch means the release no longer covers the packs in force
        _run(rt_url, lambda c: _publish(c, nurses))
        with pytest.raises(ZidError) as e:
            _check(rt_url)
        assert e.value.code == "MARKET_NOT_RELEASED"
        with captured("zid.regulatory") as logs:
            _check(rt_url, release_mode="shadow")
        assert any("pack_changed_after_release" in m for m in logs)

        # retiring the new pack puts the market back as it was released
        _run(rt_url, lambda c: svc.retire_pack(c, nurses.pack_id, 1, now=NOW))
        _check(rt_url)

        # withdrawing closes it at once
        _run(rt_url, lambda c: _withdraw(c, rid))
        with pytest.raises(ZidError) as e:
            _check(rt_url)
        assert e.value.code == "MARKET_NOT_RELEASED"
        with captured("zid.regulatory") as logs:
            _check(rt_url, release_mode="shadow")
        assert any("withdrawn" in m for m in logs)

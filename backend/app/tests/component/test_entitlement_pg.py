"""Partner entitlements in PostgreSQL and at the country gate (ZID-RCG-003 RCG-G03; CR-074): append-only history that answers for any moment, the rules
the table keeps even when the domain code is skipped, and a provider that is certified but not entitled being refused before it is called."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from pack_test_support import captured
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_errors import ZidError
from zid_platform.country_gate import check_packs
from zid_platform.modules.verification.domain.countries import APPROVALS, Capability
from zid_platform.modules.verification.domain.entitlement import (
    EntitlementError,
    Grant,
    Outcome,
    Scope,
    Status,
    changes,
)
from zid_platform.modules.verification.infrastructure import contract_entitlement_store as store
from zid_platform.modules.verification.infrastructure.country_registry import parse

NOW = datetime.now(UTC)
CAP = Capability.INDIVIDUAL_VERIFICATION


def _scope(country: str = "GB") -> Scope:
    return Scope(f"prov-{uuid.uuid4().hex[:8]}", country, CAP.value, "STANDARD")


def _grant(scope: Scope, *, ago: timedelta, status: Status = Status.GRANTED, **kw: Any) -> Grant:
    base: dict[str, Any] = {
        "grant_id": f"g-{uuid.uuid4().hex[:12]}",
        "scope": scope,
        "status": status,
        "effective_at": NOW - ago,
        "valid_from": NOW - timedelta(days=30),
        "valid_until": NOW + timedelta(days=300),
        "contract_ref": "CONTRACT-1",
        "terms_ref": "TERMS-1",
        "recorded_by": "vera",
        "recorded_at": NOW - ago,
    }
    base.update(kw)
    return Grant(**base)


def _run(url: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(url) as eng, eng.begin() as c:
            return await work(c)

    return asyncio.run(go())


def _refused(url: str, work: Any, *fragments: str) -> None:
    with pytest.raises(DBAPIError) as e:
        _run(url, work)
    assert any(f in str(e.value) for f in fragments), str(e.value)


def test_statements_build_a_history_that_answers_for_any_moment(rt_url: str) -> None:
    scope = _scope()
    first = _grant(scope, ago=timedelta(days=3))
    suspended = _grant(scope, ago=timedelta(hours=1), status=Status.SUSPENDED)

    async def work(c: Any) -> tuple[Any, ...]:
        await store.record(c, first, now=NOW)
        await store.record(c, suspended, now=NOW)
        return (
            await store.history(c, scope),
            await store.decide_scope(c, scope, at=NOW - timedelta(days=2)),
            await store.decide_scope(c, scope, at=NOW),
            await store.decide_scope(c, scope, at=NOW - timedelta(days=4)),
        )

    hist, then, now_, before = _run(rt_url, work)
    assert [g.grant_id for g in hist] == [first.grant_id, suspended.grant_id]
    assert then.entitled and then.grant is not None and then.grant.contract_ref == "CONTRACT-1"
    assert (now_.outcome, now_.reason) == (Outcome.DENIED, "suspended")
    assert (before.outcome, before.reason) == (Outcome.DENIED, "no_entitlement")


def test_losing_one_scope_changes_only_that_one(rt_url: str) -> None:
    de = _scope("DE")
    fr = Scope(de.provider, "FR", de.capability, de.purpose)

    async def work(c: Any) -> tuple[Any, Any]:
        await store.record(c, _grant(de, ago=timedelta(days=2)), now=NOW)
        await store.record(c, _grant(fr, ago=timedelta(days=2)), now=NOW)
        before = await store.decide_scopes(c, [de, fr], at=NOW - timedelta(days=1))
        await store.record(
            c, _grant(de, ago=timedelta(minutes=5), status=Status.WITHDRAWN), now=NOW
        )
        return before, await store.decide_scopes(c, [de, fr], at=NOW)

    before, after = _run(rt_url, work)
    assert changes(before, after) == {de: (Outcome.ENTITLED, Outcome.DENIED)}
    assert _run(rt_url, lambda c: store.scopes_of(c, provider=de.provider)) == [de, fr]


def test_the_store_refuses_out_of_order_future_and_unreferenced_statements(rt_url: str) -> None:
    scope = _scope()
    _run(rt_url, lambda c: store.record(c, _grant(scope, ago=timedelta(hours=2)), now=NOW))

    def code_of(g: Grant) -> str:
        with pytest.raises(EntitlementError) as e:
            _run(rt_url, lambda c: store.record(c, g, now=NOW))
        return e.value.code

    assert code_of(_grant(scope, ago=timedelta(hours=3))) == "ENTITLEMENT_OUT_OF_ORDER"
    assert code_of(_grant(scope, ago=-timedelta(hours=1))) == "ENTITLEMENT_FROM_THE_FUTURE"
    assert (
        code_of(_grant(scope, ago=timedelta(minutes=1), contract_ref="x"))
        == "ENTITLEMENT_CONTRACT_REQUIRED"
    )


def _raw_insert(scope: Scope, *, effective_at: datetime, terms: str = "TERMS-1") -> Any:
    async def work(c: Any) -> None:
        await c.execute(
            text(
                "INSERT INTO verify.provider_contract_entitlement (grant_id, provider, country, capability, purpose, status, effective_at, valid_from, "
                "valid_until, contract_ref, terms_ref, recorded_by, recorded_at) VALUES (:id, :p, :c, :k, :u, 'GRANTED', :ea, :ea, NULL, "
                "'CONTRACT-1', :t, 'vera', now())"
            ),
            {
                "id": f"raw-{uuid.uuid4().hex[:10]}",
                "p": scope.provider,
                "c": scope.country,
                "k": scope.capability,
                "u": scope.purpose,
                "ea": effective_at,
                "t": terms,
            },
        )

    return work


def test_the_table_refuses_what_the_domain_would_refuse_even_when_the_domain_is_skipped(
    rt_url: str, owner_url: str
) -> None:
    scope = _scope()
    _run(rt_url, lambda c: store.record(c, _grant(scope, ago=timedelta(hours=2)), now=NOW))
    _refused(
        rt_url,
        _raw_insert(scope, effective_at=NOW - timedelta(hours=5)),
        "newer than the one before",
    )
    _refused(rt_url, _raw_insert(scope, effective_at=NOW + timedelta(hours=2)), "from the future")
    _refused(
        rt_url,
        _raw_insert(scope, effective_at=NOW - timedelta(minutes=1), terms="x"),
        "terms_ref",
        "violates",
    )

    def sql(statement: str) -> Any:
        async def work(c: Any) -> None:
            await c.execute(text(statement), {"p": scope.provider})

        return work

    _refused(
        rt_url,
        sql(
            "UPDATE verify.provider_contract_entitlement SET status = 'GRANTED' WHERE provider = :p"
        ),
        "append-only",
    )
    _refused(
        owner_url,
        sql("DELETE FROM verify.provider_contract_entitlement WHERE provider = :p"),
        "append-only",
    )


# ---- the gate


def _registry(provider: str, country: str) -> Any:
    entry = {
        "status": "ENABLED",
        "provider": provider,
        "effective_from": "2026-01-01",
        "approvals": {a: f"ref-{a}" for a in APPROVALS},
    }
    return parse(
        {
            "version": "t",
            "countries": {
                country: {
                    "name": country,
                    "wave": 1,
                    "capabilities": {"INDIVIDUAL_VERIFICATION": entry},
                }
            },
        }
    )


def _check(rt_url: str, scope: Scope, *, mode: str = "enforce") -> None:
    async def go() -> None:
        async with engine(rt_url) as eng:
            await check_packs(
                eng,
                ctx(uuid.uuid4()),
                mode="off",  # the pack and release checks are not under test here
                environment="production",
                reg=_registry(scope.provider, scope.country),
                country=scope.country,
                capability=CAP,
                now=NOW,
                entitlement_mode=mode,
            )

    asyncio.run(go())


def test_a_certified_provider_without_an_entitlement_is_refused_before_it_is_called(
    rt_url: str,
) -> None:
    scope = _scope("NO")
    with pytest.raises(ZidError) as e:
        _check(rt_url, scope)
    assert (e.value.code, e.value.http_status) == ("PROVIDER_NOT_ENTITLED", 422)
    with captured("zid.regulatory") as logs:
        _check(rt_url, scope, mode="shadow")  # reported, not refused
    assert any(
        "provider entitlement check would refuse (shadow)" in m and "no_entitlement" in m
        for m in logs
    )
    _check(rt_url, scope, mode="off")


def test_a_granted_entitlement_lets_it_through_and_a_suspension_closes_it_again(
    rt_url: str,
) -> None:
    scope = _scope("DK")
    _run(rt_url, lambda c: store.record(c, _grant(scope, ago=timedelta(days=1)), now=NOW))
    _check(rt_url, scope)  # entitled: no error

    other_country = Scope(scope.provider, "SE", scope.capability, scope.purpose)
    with pytest.raises(ZidError):  # the same provider in another country is not covered
        _check(rt_url, other_country)

    _run(
        rt_url,
        lambda c: store.record(
            c, _grant(scope, ago=timedelta(minutes=1), status=Status.SUSPENDED), now=NOW
        ),
    )
    with pytest.raises(ZidError) as e:
        _check(rt_url, scope)
    assert e.value.code == "PROVIDER_NOT_ENTITLED"

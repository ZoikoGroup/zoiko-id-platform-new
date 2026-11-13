"""Certification attestations in PostgreSQL (ZID-RCG-003 RCG-G02): append-only history that answers for any past moment, freshness that turns an
unreachable register into HOLD, and the rules the table keeps even when the domain code is skipped."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from conftest import engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_platform.modules.verification.domain.attestation import (
    AttestationError,
    Outcome,
    Scope,
    Snapshot,
    changes,
)
from zid_platform.modules.verification.domain.certification import CertStatus
from zid_platform.modules.verification.infrastructure import attestation_store as store

NOW = datetime.now(UTC)


def _scope(service: str = "RTR") -> Scope:
    return Scope(f"PARTNER-{uuid.uuid4().hex[:8]}", "UK_DVS", "GB", service)


def _snap(
    scope: Scope, *, ago: timedelta, status: CertStatus = CertStatus.ACTIVE, **kw: Any
) -> Snapshot:
    base: dict[str, Any] = {
        "snapshot_id": f"s-{uuid.uuid4().hex[:12]}",
        "scope": scope,
        "scheme_version": "2026.1",
        "certificate_ref": "CERT-1",
        "component_only": False,
        "status": status,
        "valid_from": date(2025, 1, 1),
        "valid_until": date(2030, 1, 1),
        "register_url": "https://register.example.test/entry/1",
        "register_entry_id": "ENTRY-1",
        "cab": "CAB-X",
        "fetched_at": NOW - ago,
        "proof_hash": "a" * 64,
        "evidence_locator": "vault:attestations/x",
        "recorded_by": "ravi",
        "recorded_at": NOW - ago,
    }
    base.update(kw)
    return Snapshot(**base)


def _run(url: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(url) as eng, eng.begin() as c:
            return await work(c)

    return asyncio.run(go())


def _refused(url: str, work: Any, *fragments: str) -> None:
    with pytest.raises(DBAPIError) as e:
        _run(url, work)
    assert any(f in str(e.value) for f in fragments), str(e.value)


def test_snapshots_build_a_history_that_answers_for_any_moment(rt_url: str) -> None:
    scope = _scope()
    first = _snap(scope, ago=timedelta(days=3))
    withdrawn = _snap(
        scope, ago=timedelta(hours=1), status=CertStatus.WITHDRAWN, proof_hash="b" * 64
    )

    async def work(c: Any) -> tuple[Any, ...]:
        await store.record(c, first, now=NOW)
        await store.record(c, withdrawn, now=NOW)
        return (
            await store.history(c, scope),
            await store.decide_scope(
                c, scope, at=NOW - timedelta(days=2)
            ),  # between the two readings
            await store.decide_scope(c, scope, at=NOW),
            await store.decide_scope(c, scope, at=NOW - timedelta(days=4)),  # before anyone looked
        )

    hist, then, now_, before = _run(rt_url, work)
    assert [s.snapshot_id for s in hist] == [first.snapshot_id, withdrawn.snapshot_id]
    assert (
        then.certified and then.snapshot is not None and then.snapshot.proof_hash == "a" * 64
    )  # the check made then stays valid, with its proof
    assert (now_.outcome, now_.reason) == (Outcome.HOLD, "withdrawn")
    assert (before.outcome, before.reason) == (Outcome.HOLD, "no_attestation")


def test_a_stale_snapshot_is_hold_so_an_unreachable_register_cannot_keep_a_certification_alive(
    rt_url: str,
) -> None:
    scope = _scope()

    async def work(c: Any) -> tuple[Any, Any]:
        await store.record(c, _snap(scope, ago=timedelta(days=10)), now=NOW)
        return await store.decide_scope(c, scope, at=NOW), await store.decide_scope(
            c, scope, at=NOW - timedelta(days=9)
        )

    stale, then = _run(rt_url, work)
    assert (stale.outcome, stale.reason) == (Outcome.HOLD, "attestation_stale")
    assert then.certified  # nine days ago it was one day old


def test_a_neighbouring_scope_is_not_covered_and_losing_one_scope_changes_only_that_one(
    rt_url: str,
) -> None:
    rtr, idv = _scope("RTR"), None
    idv = Scope(rtr.entity, rtr.scheme, rtr.country, "IDV")

    async def work(c: Any) -> tuple[Any, Any, Any]:
        await store.record(c, _snap(rtr, ago=timedelta(days=2)), now=NOW)
        await store.record(c, _snap(idv, ago=timedelta(days=2)), now=NOW)
        before = await store.decide_scopes(c, [rtr, idv], at=NOW - timedelta(days=1))
        await store.record(
            c, _snap(rtr, ago=timedelta(minutes=30), status=CertStatus.SUSPENDED), now=NOW
        )
        after = await store.decide_scopes(c, [rtr, idv], at=NOW)
        other = await store.decide_scope(
            c, Scope(rtr.entity, rtr.scheme, "IE", "RTR"), at=NOW
        )  # the same service in another country
        return before, after, other

    before, after, other = _run(rt_url, work)
    assert changes(before, after) == {rtr: (Outcome.CERTIFIED, Outcome.HOLD)}
    assert (other.outcome, other.reason) == (Outcome.HOLD, "no_attestation")


def test_the_store_refuses_out_of_order_future_and_malformed_snapshots_with_the_domain_codes(
    rt_url: str,
) -> None:
    scope = _scope()
    _run(rt_url, lambda c: store.record(c, _snap(scope, ago=timedelta(hours=2)), now=NOW))

    def code_of(snapshot: Snapshot) -> str:
        with pytest.raises(AttestationError) as e:
            _run(rt_url, lambda c: store.record(c, snapshot, now=NOW))
        return e.value.code

    assert code_of(_snap(scope, ago=timedelta(hours=3))) == "ATTESTATION_OUT_OF_ORDER"
    assert code_of(_snap(scope, ago=-timedelta(hours=1))) == "ATTESTATION_FROM_THE_FUTURE"
    assert (
        code_of(
            _snap(scope, ago=timedelta(minutes=1), register_url="http://insecure.example.test/x")
        )
        == "ATTESTATION_REGISTER_URL_INVALID"
    )
    assert (
        code_of(_snap(scope, ago=timedelta(minutes=1), proof_hash="nothex"))
        == "ATTESTATION_PROOF_REQUIRED"
    )


def _raw_insert(
    scope: Scope, *, fetched_at: datetime, url: str = "https://register.example.test/entry/1"
) -> Any:
    async def work(c: Any) -> None:
        await c.execute(
            text(
                "INSERT INTO verify.certification_attestation (snapshot_id, entity, scheme, country, service_code, scheme_version, certificate_ref, "
                "component_only, status, valid_from, valid_until, register_url, register_entry_id, cab, fetched_at, proof_hash, evidence_locator, "
                "recorded_by, recorded_at) VALUES (:id, :e, :s, :c, :v, '1', 'C', false, 'ACTIVE', '2025-01-01', NULL, :url, 'E', NULL, :fa, :ph, "
                "'vault:x/y', 'ravi', now())"
            ),
            {
                "id": f"raw-{uuid.uuid4().hex[:10]}",
                "e": scope.entity,
                "s": scope.scheme,
                "c": scope.country,
                "v": scope.service_code,
                "url": url,
                "fa": fetched_at,
                "ph": "c" * 64,
            },
        )

    return work


def test_the_table_refuses_what_the_domain_would_refuse_even_when_the_domain_is_skipped(
    rt_url: str, owner_url: str
) -> None:
    scope = _scope()
    _run(rt_url, lambda c: store.record(c, _snap(scope, ago=timedelta(hours=2)), now=NOW))
    _refused(
        rt_url, _raw_insert(scope, fetched_at=NOW - timedelta(hours=5)), "newer than the one before"
    )
    _refused(rt_url, _raw_insert(scope, fetched_at=NOW + timedelta(hours=2)), "from the future")
    _refused(
        rt_url,
        _raw_insert(
            scope, fetched_at=NOW - timedelta(minutes=1), url="http://insecure.example.test/x"
        ),
        "register_url",
        "violates",
    )

    def sql(statement: str) -> Any:
        async def work(c: Any) -> None:
            await c.execute(text(statement), {"e": scope.entity})

        return work

    _refused(
        rt_url,
        sql("UPDATE verify.certification_attestation SET status = 'ACTIVE' WHERE entity = :e"),
        "append-only",
    )
    _refused(
        owner_url,
        sql("DELETE FROM verify.certification_attestation WHERE entity = :e"),
        "append-only",
    )


def test_the_scopes_anyone_recorded_can_be_listed_for_one_provider(rt_url: str) -> None:
    rtr = _scope("RTR")
    idv = Scope(rtr.entity, rtr.scheme, rtr.country, "IDV")

    async def work(c: Any) -> list[Scope]:
        await store.record(c, _snap(rtr, ago=timedelta(hours=1)), now=NOW)
        await store.record(c, _snap(idv, ago=timedelta(hours=1)), now=NOW)
        return await store.scopes_of(c, entity=rtr.entity)

    assert _run(rt_url, work) == [idv, rtr]  # ordered by service code

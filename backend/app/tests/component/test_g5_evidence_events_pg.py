"""Evidence and event-platform acceptance rows closed in group g5 (PostgreSQL, real RLS).

EVT-06  consumers never depend on cross-partition wall-clock or broker order
EVID-11 replay never substitutes the present-day world for the historic evidence
EVID-25 a checkpoint outage leaves records preserved and never reports them as sealed
EVID-29 telemetry loss cannot destroy evidence, and evidence loss is not masked by logs
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import tenant_transaction
from zid_evidence import Digest
from zid_outbox import CloudEvent, OutOfOrder, consume
from zid_platform.modules.authority.ports import Request as AuthorityRequest
from zid_platform.modules.decision.ports import (
    AuthnFact,
    DecisionRequest,
    Outcome,
    action_spec,
    evaluate_request,
    register_evidence_replayer,
)
from zid_platform.modules.evidence.application import ledger, replay
from zid_platform.modules.evidence.application.checkpointer import seal_partition
from zid_platform.modules.evidence.application.runtime import (
    configure_runtime,
    current_runtime,
    development_runtime,
)
from zid_platform.modules.evidence.domain.ledger import LedgerError, Partition
from zid_platform.modules.evidence.infrastructure.tsa import TimestampError
from zid_platform.modules.evidence.ports import sequence_pending, worker_pass
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    set_status,
)
from zid_platform.protected import catalog


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def runtime() -> Any:
    rt = development_runtime("uk-1")
    configure_runtime(rt)
    register_evidence_replayer(catalog())
    yield rt
    configure_runtime(None)


def _event(tenant: str, aggregate: str, version: int, at: datetime) -> CloudEvent:
    return CloudEvent(
        id=str(uuid.uuid4()),
        type="com.zoiko.id.mandate.paused.v1",
        source="urn:zoiko:id:mandate",
        subject=f"mandate/{aggregate}",
        time=at,
        tenantid=tenant,
        cellid="uk-1",
        aggregatetype="mandate",
        aggregateid=aggregate,
        aggregateversion=version,
        data={},
        correlationid="c",
    )


def test_consumers_depend_only_on_per_aggregate_version_never_on_global_order(
    rt_url: str,
) -> None:
    """EVT-06: two aggregates whose events arrive interleaved, with wall-clock timestamps that
    contradict arrival order, are each applied in aggregate_version order. Only a version gap
    inside one aggregate is refused; another aggregate's progress or a later `time` never is."""
    tenant = uid()
    t0 = datetime.now(UTC)
    applied: list[tuple[str, int]] = []

    async def handler(c: AsyncConnection, e: CloudEvent) -> None:
        applied.append((e.aggregateid, e.aggregateversion))

    async def run() -> None:
        # a1 v2 carries the EARLIEST wall-clock time of all; b1 v1 the latest, and arrives first
        b1 = _event(tenant, "b", 1, t0 + timedelta(hours=1))
        a1 = _event(tenant, "a", 1, t0 + timedelta(minutes=10))
        a2 = _event(tenant, "a", 2, t0 - timedelta(hours=1))
        b2 = _event(tenant, "b", 2, t0 + timedelta(minutes=5))
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for e in (b1, a1):
                assert await consume(c, "proj.v1", e, handler, ordered=True) == "PROCESSED"
            # b's version 3 before b2: refused by the aggregate gap rule, whatever its timestamp
            b3 = _event(tenant, "b", 3, t0 - timedelta(days=1))
            with pytest.raises(OutOfOrder):
                await consume(c, "proj.v1", b3, handler, ordered=True)
            # a2 is older by the clock than everything already applied and still goes straight in
            for e in (a2, b2, b3):
                assert await consume(c, "proj.v1", e, handler, ordered=True) == "PROCESSED"

    asyncio.run(run())
    per_aggregate = {a: [v for x, v in applied if x == a] for a in ("a", "b")}
    assert per_aggregate == {"a": [1, 2], "b": [1, 2, 3]}  # in version order within each aggregate


async def _seed_admin(c: Any, tenant: str, admin: str) -> None:
    await create_principal(
        c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
    )
    await make_admin(c, tenant, admin, ["evidence.export"], ["receipt:*"])


async def _decide(c: Any, tenant: str, admin: str) -> Any:
    now = datetime.now(UTC)
    return await evaluate_request(
        c,
        req=DecisionRequest(
            decision_id=uid(),
            tenant_id=tenant,
            action=action_spec("evidence.export", catalog()),
            resource_id="receipt:1",
            represented_principal_id=None,
            purpose=None,
            tool_id=None,
            at=now,
        ),
        executor_id=admin,
        authority_request=AuthorityRequest("evidence.export", "receipt:1", now, actor_assurance=3),
        authn=AuthnFact(3, now - timedelta(minutes=1)),
        security_state="NORMAL",
        catalog=catalog(),
    )


async def _receipt_id(c: Any, decision_id: str) -> str:
    return str(
        (
            await c.execute(
                text("SELECT receipt_id FROM evidence.decision_receipt WHERE decision_id = :d"),
                {"d": uuid.UUID(decision_id)},
            )
        ).scalar_one()
    )


def test_replay_judges_the_historic_decision_not_the_present_day_world(
    rt_url: str, owner_url: str
) -> None:
    """EVID-11: an E3 decision is ALLOW. Afterwards the actor is suspended, so the *present-day*
    provider (principal store) would now DENY the identical request. Replay of the historic
    receipt still reproduces ALLOW from the retained inputs and the pinned policy/action artifacts:
    it never consults the mutable current state."""
    tenant, admin = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await _seed_admin(c, tenant, admin)
            first = await _decide(c, tenant, admin)
            assert first.outcome is Outcome.ALLOW, first.reason_codes
            receipt_id = await _receipt_id(c, first.decision_id)
        async with engine(owner_url) as eng, eng.begin() as c:
            await worker_pass(c, force_seal=True)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await set_status(c, admin, PrincipalStatus.SUSPENDED)
            today = await _decide(c, tenant, admin)  # the present-day world disagrees
            assert today.outcome is not Outcome.ALLOW
            out = await replay.replay_receipt(c, tenant, receipt_id)
            assert out["result"] == "VERIFIED_MATCH", out  # historic outcome, reasons, obligations

    asyncio.run(run())


class _TsaOutage:
    def __init__(self) -> None:
        self.up = False
        self.calls = 0

    async def timestamp(self, digest: Digest) -> str:
        self.calls += 1
        if not self.up:
            raise TimestampError("TSA unreachable")
        return "rfc3161:dGVzdA=="


def test_checkpoint_outage_preserves_records_and_never_reports_them_sealed(
    rt_url: str, owner_url: str
) -> None:
    """EVID-25: with a synchronous timestamp required and the TSA down, nothing is sealed, yet
    the E3 receipt stays committed and ledgered; verification reports it ledgered but NOT sealed.
    When the TSA recovers the same record is sealed with no loss and verifies."""
    tenant, admin = uid(), uid()
    tsa = _TsaOutage()

    async def run() -> None:
        pid = Partition(tenant, "uk-1").id
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await _seed_admin(c, tenant, admin)
            d = await _decide(c, tenant, admin)
            receipt_id = await _receipt_id(c, d.decision_id)
        async with engine(owner_url) as eng, eng.begin() as c:
            await sequence_pending(c)  # the receipt enters the hash-chained ledger
        signer = current_runtime().signer
        async with engine(owner_url) as eng, eng.begin() as c:
            with pytest.raises(LedgerError, match="EVIDENCE_TIME_UNTRUSTED"):
                await seal_partition(c, pid, signer=signer, tsa=tsa, require_timestamp=True)
        assert tsa.calls == 1
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            during = await replay.verify_receipt(c, receipt_id)
            assert during.hash_ok and during.ledgered  # the record survived the outage
            assert not during.sealed
            assert during.to_dict()["sealed"] is False  # never silently "fully sealed"
            report = await ledger.verify_partition(c, pid, keys=current_runtime().trusted_keys())
            assert report.sealed_through == 0  # no checkpoint covers it yet
        tsa.up = True
        async with engine(owner_url) as eng, eng.begin() as c:
            cp = await seal_partition(c, pid, signer=signer, tsa=tsa, require_timestamp=True)
            assert cp is not None and cp.external_timestamp_ref is not None
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            after = await replay.verify_receipt(c, receipt_id)
            assert after.sealed and after.ok

    asyncio.run(run())


class _BrokenCounter:
    def inc(self, **labels: str) -> None:
        raise RuntimeError("metrics pipeline is down")


class _RaisingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        raise OSError("log sink / OTLP collector unreachable")


def test_telemetry_loss_cannot_destroy_evidence_and_logs_cannot_mask_evidence_loss(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVID-29: with the metrics pipeline raising and every log sink failing (collector loss),
    decisions still commit their E3 receipts, which seal and verify. Then an entry removed from
    the ledger by a DBA is detected by hash-chain verification even though no log line records it."""
    tenant, admin = uid(), uid()
    monkeypatch.setattr(ledger, "RECORDS", _BrokenCounter())
    monkeypatch.setattr(ledger, "VERIFY_FAILURES", _BrokenCounter())
    handler = _RaisingHandler()
    root = logging.getLogger()
    monkeypatch.setattr(logging, "raiseExceptions", False)
    root.addHandler(handler)

    async def run() -> None:
        pid = Partition(tenant, "uk-1").id
        try:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await _seed_admin(c, tenant, admin)
                ids = []
                for _ in range(3):
                    d = await _decide(c, tenant, admin)
                    assert d.outcome is Outcome.ALLOW
                    ids.append(await _receipt_id(c, d.decision_id))
            async with engine(owner_url) as eng, eng.begin() as c:
                await worker_pass(c, force_seal=True)
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                for rid in ids:
                    v = await replay.verify_receipt(c, rid)
                    assert v.ok and v.sealed  # evidence intact with telemetry down
                keys = current_runtime().trusted_keys()
                assert (await ledger.verify_partition(c, pid, keys=keys)).ok
            # evidence loss: a DBA deletes the middle ledger entry; there is no log of it at all
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(text("ALTER TABLE evidence.ledger_entry DISABLE TRIGGER USER"))
                await c.execute(
                    text(
                        "DELETE FROM evidence.ledger_entry WHERE partition_id = :p AND sequence_no = 2"
                    ),
                    {"p": pid},
                )
                await c.execute(text("ALTER TABLE evidence.ledger_entry ENABLE TRIGGER USER"))
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                report = await ledger.verify_partition(c, pid, keys=keys, emit_incident=False)
                assert not report.ok  # loss is detected by the evidence itself, not by telemetry
        finally:
            root.removeHandler(handler)

    asyncio.run(run())

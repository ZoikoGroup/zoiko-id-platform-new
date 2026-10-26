"""Event platform guarantees (ZID-VERIFY-MASTER-001 EVT-02, 04, 08, 10, 11, 12; ZID-EVENTS-001 §5-12; DEC-D2).

What the existing outbox suite does not already prove: a broker outage loses nothing and republishes the
SAME events; a consumer crashing before or after its commit never doubles an effect; a payload that names a
secret, a credential, key material or a biometric is refused at the source; oversized and cross-tenant events
cannot be written; a consumer ignores fields it does not know.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import tenant_transaction
from zid_outbox import CloudEvent, consume, emit, prohibited_content, relay_once
from zid_outbox.kafka import decode, encode

NOW = datetime.now(UTC)


def uid() -> str:
    return str(uuid.uuid4())


class Broker:
    """A broker that is down until told otherwise, remembering everything it was given."""

    def __init__(self, up: bool) -> None:
        self.up, self.sent = up, []

    async def publish(self, topic: str, key: str, event: CloudEvent) -> None:
        if not self.up:
            raise ConnectionError("broker down")
        self.sent.append((topic, key, event))


async def emit_n(eng, tenant: str, n: int) -> list[str]:  # type: ignore[no-untyped-def]
    async with tenant_transaction(eng, ctx(tenant)) as c:
        return [
            await emit(
                c,
                name="mandate.reservation.created",
                tenant_id=tenant,
                aggregate_type="t_outage",
                aggregate_id=f"a{i}",
                aggregate_version=1,
                data={"i": i},
            )
            for i in range(n)
        ]


def test_broker_outage_loses_nothing_and_republishes_the_same_events(
    rt_url: str, owner_url: str
) -> None:
    """EVT-02: writes keep succeeding while the broker is down; after recovery every event is published with
    the identity it was born with."""
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            ids = await emit_n(eng, tenant, 20)  # the domain writes succeed with no broker at all
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "UPDATE events.outbox_event SET published_at = now() "
                    "WHERE published_at IS NULL AND tenant_id <> :t"
                ),
                {"t": uuid.UUID(tenant)},
            )
        down = Broker(up=False)
        async with engine(owner_url) as eng, eng.begin() as c:
            await relay_once(c, down, env="test", region="uk-1", batch=50, max_attempts=50)
        assert down.sent == []
        async with engine(owner_url) as eng, eng.begin() as c:
            pending = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE tenant_id = :t AND published_at IS NULL "
                        "AND quarantined_reason IS NULL"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).scalar_one()
            await c.execute(
                text("UPDATE events.outbox_event SET lease_until = NULL WHERE tenant_id = :t"),
                {"t": uuid.UUID(tenant)},
            )
        assert pending == 20, (
            "an outage must leave every event durably waiting, none dropped or quarantined"
        )

        up = Broker(up=True)
        async with engine(owner_url) as eng, eng.begin() as c:
            await relay_once(c, up, env="test", region="uk-1", batch=50)
        mine = [e for _, _, e in up.sent if e.tenantid == tenant]
        assert {e.id for e in mine} == set(ids), "republished events must keep their original ids"
        assert len(mine) == 20

    asyncio.run(run())


def event(tenant: str, version: int) -> CloudEvent:
    return CloudEvent(
        id=uid(),
        type="com.zoiko.id.mandate.paused.v1",
        source="urn:zoiko:id:mandate",
        subject="mandate/m1",
        time=NOW,
        tenantid=tenant,
        cellid="uk-1",
        aggregatetype="mandate",
        aggregateid="m1",
        aggregateversion=version,
        data={},
        correlationid="c",
    )


def test_consumer_crash_before_and_after_commit_never_doubles_an_effect(rt_url: str) -> None:
    """EVT-04: the effect and the inbox record commit together. A crash before the commit leaves neither (the
    redelivery applies it once); a crash after the commit but before the broker ack leaves both (the redelivery is
    a duplicate)."""
    tenant = uid()
    effects: list[str] = []

    async def effect(c: AsyncConnection, e: CloudEvent) -> None:
        effects.append(e.id)
        await c.execute(text("SELECT 1"))

    async def run() -> None:
        ev = event(tenant, 1)
        # crash before the commit: the handler ran, then the process died
        with pytest.raises(RuntimeError):
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                assert await consume(c, "crashy.v1", ev, effect) == "PROCESSED"
                raise RuntimeError("process crashed before COMMIT")
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            seen = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.inbox_message WHERE consumer = 'crashy.v1' AND event_id = :e"
                    ),
                    {"e": uuid.UUID(ev.id)},
                )
            ).scalar_one()
        assert seen == 0, "an uncommitted effect must leave no inbox record"
        # the broker redelivers; this time it commits
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert await consume(c, "crashy.v1", ev, effect) == "PROCESSED"
        # crash after the commit, before the ack: the broker redelivers again
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert await consume(c, "crashy.v1", ev, effect) == "DUPLICATE"
        assert (
            len(effects) == 2
        )  # the rolled-back attempt and the one that committed: exactly one lasting effect

    asyncio.run(run())


def test_a_producer_cannot_write_another_tenants_event(rt_url: str) -> None:
    """EVT-10: row-level security refuses an outbox row whose tenant is not the session's, so a forged tenantid
    cannot put an event into another tenant's stream."""
    mine, theirs = uid(), uid()

    async def run() -> None:
        with pytest.raises(DBAPIError, match="row-level security"):
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(mine)) as c:
                await emit(
                    c,
                    name="mandate.paused",
                    tenant_id=theirs,  # forged
                    aggregate_type="t_forge",
                    aggregate_id="x",
                    aggregate_version=1,
                    data={},
                )

    asyncio.run(run())


def test_oversized_and_prohibited_payloads_are_refused_at_the_source(rt_url: str) -> None:
    """EVT-11 and EVT-12: a payload over 256 KiB, or one naming a secret, credential, key material or biometric,
    never reaches the outbox."""
    tenant = uid()

    async def attempt(data: dict) -> None:  # type: ignore[type-arg]
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await emit(
                c,
                name="mandate.paused",
                tenant_id=tenant,
                aggregate_type="t_guard",
                aggregate_id=uid(),
                aggregate_version=1,
                data=data,
            )

    async def run() -> None:
        await attempt({"mandate_id": "m1", "revision": 3})  # ids and revisions are fine
        with pytest.raises(ValueError, match="256 KiB"):
            await attempt({"blob": "x" * 300_000})
        for bad in (
            {"password": "hunter2"},
            {"nested": {"client_secret": "x"}},
            {"items": [{"Refresh_Token": "x"}]},
            {"face_template": "..."},
            {
                "note": "-----BEGIN PRIVATE KEY-----\nMIGH..."
            },  # zid-secret-scan: allow (synthetic fixture)
        ):
            with pytest.raises(ValueError, match="prohibited"):
                await attempt(bad)

    asyncio.run(run())
    assert (
        prohibited_content({"token_count": 3, "security_revision": 9, "status": "ACTIVE"}) is None
    )


def test_a_consumer_ignores_fields_it_does_not_know() -> None:
    """EVT-08: additive evolution is safe, so a newer producer's extra fields never break an older consumer."""
    ev = event(uid(), 1)
    wire = json.loads(encode(ev))
    wire["added_in_a_later_minor_release"] = {"anything": [1, 2, 3]}
    wire["data"]["new_field"] = "x"
    back = decode(json.dumps(wire).encode())
    assert back.id == ev.id and back.type == ev.type and back.aggregateversion == 1

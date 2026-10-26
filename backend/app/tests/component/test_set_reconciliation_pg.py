"""A dropped outbound SET is detected and repaired from authoritative state (ZID-SIGNALS-001 §15; SIG-25)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from conftest import ctx, engine
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.modules.signals.application.ssf import build_set, reconcile_outbound
from zid_platform.modules.signals.infrastructure import signal_store as store

ISS = "https://id.zoiko.test"
URI = "https://schemas.openid.net/secevent/caep/event-type/session-revoked"


def uid() -> str:
    return str(uuid.uuid4())


async def _stream_with_set(c, tenant: str, principal: str, committed: datetime):  # type: ignore[no-untyped-def]
    ring = KeyRing(LocalSigner("tx-1"))
    stream = store.Stream(
        uid(), tenant, "TRANSMITTER", ISS, "https://rx.example", None, ("ES256",), (URI,),
        ("opaque",), "poll", None, "TRUSTED", None, "rx-client", "ACTIVE", 1,
    )  # fmt: skip
    await store.create_stream(c, stream)
    await store.map_subject(
        c, stream_id=stream.stream_id, tenant_id=tenant, opaque_id="op-1", principal_id=principal
    )
    jti, jwt = build_set(
        ring, issuer=ISS, audience=stream.audience, event_uri=URI,
        subject={"format": "opaque", "id": "op-1"}, event={}, now=committed,
    )  # fmt: skip
    await store.enqueue_set(
        c,
        stream_id=stream.stream_id,
        tenant_id=tenant,
        jti=jti,
        set_jwt=jwt,
        committed_at=committed,
        risk_floor=3,
    )
    return ring, stream, jti


def test_unacknowledged_set_past_its_slo_is_replaced_and_the_original_withdrawn(
    rt_url: str,
) -> None:
    """SIG-25: nothing is done inside the SLO; after it the SET is re-issued once, old one superseded."""
    tenant, principal = uid(), uid()
    now = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            ring, stream, jti = await _stream_with_set(c, tenant, principal, now)
            early = await reconcile_outbound(c, ring, ISS, stream, now + timedelta(seconds=1))
            assert (early.detected, early.repaired) == (0, 0)

            late = now + timedelta(seconds=30)
            out = await reconcile_outbound(c, ring, ISS, stream, late)
            assert (out.detected, out.repaired, out.withdrawn) == (1, 1, 0)

            handed = await store.poll(c, stream.stream_id, ack=[], max_events=10, now=late)
            assert len(handed) == 1 and handed[0][0] != jti  # only the replacement is offered
            again = await reconcile_outbound(c, ring, ISS, stream, late + timedelta(seconds=1))
            assert again.detected == 0  # the replacement is not yet stale: no repair storm

    asyncio.run(run())


def test_a_fact_that_no_longer_holds_is_withdrawn_not_resent(rt_url: str) -> None:
    """SIG-25: the authoritative refresh says the session is not revoked any more, so the stale SET is dropped."""
    tenant, principal = uid(), uid()
    now = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            ring, stream, _ = await _stream_with_set(c, tenant, principal, now)

            async def nothing_holds(p: str, u: str) -> bool:
                return False

            out = await reconcile_outbound(
                c, ring, ISS, stream, now + timedelta(minutes=1), nothing_holds
            )
            assert (out.detected, out.repaired, out.withdrawn) == (1, 0, 1)
            assert await store.poll(c, stream.stream_id, ack=[], max_events=10, now=now) == []

    asyncio.run(run())


def test_remote_is_told_to_fail_closed_when_an_r3_revocation_is_overdue(rt_url: str) -> None:
    """SIG-22: an R3 revocation not acknowledged inside its 5 s target flags the stream fail-closed; an
    acknowledgement clears it."""
    tenant, principal = uid(), uid()
    now = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            _, stream, jti = await _stream_with_set(c, tenant, principal, now)
            inside = await store.propagation(c, stream.stream_id, now + timedelta(seconds=2))
            assert inside["remote_fail_closed"] is False
            overdue = await store.propagation(c, stream.stream_id, now + timedelta(seconds=6))
            assert overdue["remote_fail_closed"] is True and overdue["overdue_restricting"] == 1
            await store.poll(
                c, stream.stream_id, ack=[jti], max_events=1, now=now + timedelta(seconds=7)
            )
            acked = await store.propagation(c, stream.stream_id, now + timedelta(seconds=8))
            assert acked["remote_fail_closed"] is False

    asyncio.run(run())

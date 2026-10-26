"""Consumer scoping and the cross-cell loop guard (ZID-EVENTS-001; TEN-08, EVT-16)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from zid_outbox import BridgeLoop, CloudEvent, ConsumerScope, bridge_forward, consume
from zid_outbox.scope import MAX_BRIDGE_HOPS, bridge_path


def event(tenant: str = "t-a", cell: str = "cell-1") -> CloudEvent:
    return CloudEvent(
        id=str(uuid.uuid4()),
        type="com.zoiko.id.mandate.revoked.v1",
        source="/test",
        subject="s",
        time=datetime.now(UTC),
        tenantid=tenant,
        cellid=cell,
        aggregatetype="mandate",
        aggregateid="m1",
        aggregateversion=1,
        data={},
        correlationid="c",
    )


class NeverTouched:
    """A connection that fails the test if the consumer reaches the database."""

    async def execute(self, *a: object, **k: object) -> None:
        raise AssertionError("a refused event must not touch the inbox")


async def handler(conn: object, ev: CloudEvent) -> None:
    raise AssertionError("a refused event must not reach the handler")


@pytest.mark.parametrize(
    ("scope", "tenant", "cell", "expected"),
    [
        (ConsumerScope(tenants=frozenset({"t-a"})), "t-a", "cell-1", True),
        (ConsumerScope(tenants=frozenset({"t-a"})), "t-b", "cell-1", False),
        (ConsumerScope(cells=frozenset({"cell-1"})), "t-a", "cell-2", False),
        (
            ConsumerScope(tenants=frozenset({"t-a"}), cells=frozenset({"cell-1"})),
            "t-a",
            "cell-1",
            True,
        ),
        (ConsumerScope(), "anyone", "anywhere", True),
    ],
)
def test_scope_permits(scope: ConsumerScope, tenant: str, cell: str, expected: bool) -> None:
    assert scope.permits(tenant, cell) is expected


def test_consumer_scoped_to_one_tenant_refuses_another_tenants_event() -> None:
    """TEN-08: an event for an unauthorised tenant is refused before the inbox or handler is touched."""
    outcome = asyncio.run(
        consume(
            NeverTouched(),  # type: ignore[arg-type]
            "billing",
            event(tenant="t-b"),
            handler,
            scope=ConsumerScope(tenants=frozenset({"t-a"})),
        )
    )
    assert outcome == "REFUSED"


def test_forwarding_records_each_cell_and_refuses_a_revisit() -> None:
    """EVT-16: the path grows by one cell per hop; sending back to a visited cell is refused."""
    e = event(cell="cell-1")
    e2 = bridge_forward(e, "cell-2")
    assert bridge_path(e2.extensions) == ("cell-1", "cell-2")
    with pytest.raises(BridgeLoop):
        bridge_forward(e2, "cell-1")  # the origin
    with pytest.raises(BridgeLoop):
        bridge_forward(e2, "cell-2")  # where it already is


def test_a_ring_topology_terminates() -> None:
    """EVT-16: a ring of bridges can never recirculate: forwarding around it stops before the origin repeats."""
    ring = [f"cell-{i}" for i in range(6)]
    e, hops = event(cell=ring[0]), 0
    with pytest.raises(BridgeLoop):
        for i in range(1000):
            e = bridge_forward(e, ring[(i + 1) % len(ring)])
            hops += 1
    assert hops < len(ring)


def test_hop_ceiling_stops_a_long_chain() -> None:
    e = event(cell="c0")
    with pytest.raises(BridgeLoop):
        for i in range(1, MAX_BRIDGE_HOPS + 5):
            e = bridge_forward(e, f"c{i}")
    assert len(bridge_path(e.extensions)) <= MAX_BRIDGE_HOPS + 1


def test_the_original_event_is_not_mutated() -> None:
    e = event()
    bridge_forward(e, "cell-2")
    assert bridge_path(e.extensions) == ()

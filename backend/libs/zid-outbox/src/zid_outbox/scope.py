"""Who may consume an event, and how a cross-cell bridge keeps an event from circulating (EVENTS-001).

`ConsumerScope` is the tenant/cell a consumer is entitled to; an event outside it is refused before any effect is
recorded (TEN-08). `bridge_forward` stamps each hop an event takes between cells and refuses to send it anywhere it
has already been, or past a hop ceiling, so a mis-wired topology cannot recirculate an event forever (EVT-16).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

MAX_BRIDGE_HOPS = 4
PATH_KEY = "bridgepath"


class BridgeLoop(RuntimeError):
    """The event was about to revisit a cell or exceed the hop ceiling."""


@dataclass(frozen=True)
class ConsumerScope:
    """None means unrestricted on that axis; a set restricts to exactly those ids."""

    tenants: frozenset[str] | None = None
    cells: frozenset[str] | None = None

    def permits(self, tenant_id: str, cell_id: str) -> bool:
        return (self.tenants is None or tenant_id in self.tenants) and (
            self.cells is None or cell_id in self.cells
        )


def bridge_path(extensions: Mapping[str, Any]) -> tuple[str, ...]:
    raw = extensions.get(PATH_KEY, ())
    return tuple(str(c) for c in raw) if isinstance(raw, (list, tuple)) else ()


def bridge_forward[E: Any](event: E, to_cell: str, *, max_hops: int = MAX_BRIDGE_HOPS) -> E:
    """Return `event` stamped as forwarded to `to_cell`, or raise BridgeLoop. The origin cell counts as visited."""
    path = bridge_path(event.extensions) or (event.cellid,)
    if to_cell in path:
        raise BridgeLoop(f"event {event.id} already visited cell {to_cell}")
    if len(path) > max_hops:
        raise BridgeLoop(f"event {event.id} exceeded {max_hops} bridge hops")
    return replace(event, extensions={**event.extensions, PATH_KEY: [*path, to_cell]})

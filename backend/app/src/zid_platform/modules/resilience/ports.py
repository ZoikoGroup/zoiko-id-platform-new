"""Public interface of the resilience module. Other modules import ONLY this.

- `cell_accepts_placement(conn, cell_id)`: False while the cell is QUARANTINED or DRAINING; tenant
  provisioning refuses new tenants there (RESILIENCE §28).
- `cell_view(conn, cell_id)` the cell's state and latest readiness; `operation_in_progress(conn,
  cell_id)` whether a recovery operation is moving a tenant out of or into it (SRE-001 §29
  `/ops/cells/{id}/health`).

The recovery operations themselves (failover, migration, failback, restore, DR exercises) are
operator-plane endpoints of this module (`/platform/...`), not a port: no other module starts one.
"""

from zid_platform.modules.resilience.infrastructure.store import (
    accepts_placement as cell_accepts_placement,
)
from zid_platform.modules.resilience.infrastructure.store import cell_view, operation_in_progress

__all__ = ["cell_accepts_placement", "cell_view", "operation_in_progress"]

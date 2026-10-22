"""Public Protocol interface of the machine module. Other modules import ONLY this.

Workload identity (DEC-F7; F-W6):
- `resolve_workload(conn, spiffe_id=<mesh-verified SpiffeId>, environment_id=..., artifact_digest=...)`
  -> WorkloadPrincipal (the logical principal it may act as) or MachineError with every blocker.
"""

from __future__ import annotations

from zid_platform.modules.machine.application.workload import WorkloadPrincipal, resolve_workload
from zid_platform.modules.machine.domain.agents import MachineError

__all__ = ["MachineError", "WorkloadPrincipal", "resolve_workload"]

"""Public Protocol interface of the operator_access module. Other modules import ONLY this.

- `has_capability` / `check_capability`: the ephemeral operator capability, re-checked on every
  use (ACTIVE unexpired JIT grant for this capability/tenant/resource, operator still registered
  and NORMAL in the signals security state, customer lockbox approval still standing).
- `expire_due`: the sweeper for the worker entrypoint (marks elapsed grants EXPIRED and emits
  `operator_access.expired`); expiry is enforced on use whether or not it has run.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.operator_access.application.service import (
    CapabilityCheck,
    OperatorSettings,
    check_capability,
    expire_due,
)
from zid_platform.modules.operator_access.domain.model import CAPABILITIES, CONFLICT_SETS

__all__ = [
    "CAPABILITIES",
    "CONFLICT_SETS",
    "CapabilityCheck",
    "OperatorSettings",
    "check_capability",
    "expire_due",
    "has_capability",
]


async def has_capability(
    conn: AsyncConnection,
    operator: str,
    capability: str,
    tenant: str | None,
    resource: str,
    now: datetime,
) -> bool:
    """`conn` is scoped to the operator tenant; `tenant` is the customer tenant (None: platform)."""
    result = await check_capability(
        conn,
        operator_principal_id=operator,
        capability_name=capability,
        tenant_id=tenant,
        resource=resource,
        now=now,
    )
    return result.allowed

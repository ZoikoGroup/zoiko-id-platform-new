"""Public Protocol interface of the operator_access module. Other modules import ONLY this.

- `has_capability` / `check_capability`: the ephemeral operator capability, re-checked on every
  use (ACTIVE unexpired JIT grant for this capability/tenant/resource, operator still registered
  and NORMAL in the signals security state, customer lockbox approval still standing).
- `require_operator(request, caller)`: the operator-plane gate (operator client and tenant, fresh
  AAL-Z3, no delegation) for other modules' operator endpoints (G-W7 platform operations).
- `is_active_operator(conn, principal)`: registered, ACTIVE and NORMAL - a qualified responder
  (incident declaration needs no JIT grant, SRE-001 §16.1).
- `expire_due`: the sweeper for the worker entrypoint (marks elapsed grants EXPIRED and emits
  `operator_access.expired`); expiry is enforced on use whether or not it has run.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.operator_access.api.operator import operator_caller as require_operator
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
    "is_active_operator",
    "require_operator",
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


async def is_active_operator(conn: AsyncConnection, tenant_id: str, principal_id: str) -> bool:
    """`conn` is scoped to the operator tenant."""
    from zid_platform.modules.operator_access.application.service import _security_ok
    from zid_platform.modules.operator_access.infrastructure import store

    profile = await store.load_profile(conn, principal_id)
    return (
        profile is not None and profile.active and await _security_ok(conn, tenant_id, principal_id)
    )

"""Public Protocol interface of the tenant module. Other modules import ONLY this.

Residency (F-W5; ZID-TENANCY-001):
- `authorize_transfer(conn, tenant, data_class=..., destination_cell_id=..., purpose_code=...)`
  the cross-cell transfer gateway decision (default deny for restricted classes, evidenced);
- `check_category_transfer` the same for a PRIV-001 data category (D0-D4);
- `authorize_remote_access` support staff reading data from outside the company's region need an approved transfer;
- `check_dependency_locations` refuses a dependency (subprocessor) whose locations cannot satisfy the profile;
- `effective_operator_jurisdictions(conn, tenant, configured)` SRP-2/3 operator restrictions.
Lifecycle (G-W0; ZID-OVERLAY-001 §2-4):
- `load_tenant(conn, tenant)` the tenant record; `transition(conn, tenant, Move, ...)` the only way
  its lifecycle moves (the onboarding orchestrator supplies the gate report);
- `activate_bootstrap_owner` once the organization gate passes; `bump_configuration` appends a
  configuration revision; `live_roles` a principal's tenant roles.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.tenant.application.lifecycle import (
    BootstrapResult,
    ConsoleClient,
    Declaration,
    activate_bootstrap_owner,
    declared_registration,
    ensure_member,
    live_roles,
    register_acceptance_hook,
    self_signup,
    transition,
)
from zid_platform.modules.tenant.application.residency import (
    authorize_remote_access,
    authorize_transfer,
    check_category_transfer,
    check_dependency_locations,
    effective_operator_jurisdictions,
)
from zid_platform.modules.tenant.application.routing import (
    cached_home_cell,
    cached_placement,
    cached_tenant_state,
    forget,
    home_cell,
    placement,
    tenant_state,
)
from zid_platform.modules.tenant.domain.lifecycle import (
    Move,
    Role,
    RoleStatus,
    TenantError,
    TenantStatus,
    enforcement_for,
)
from zid_platform.modules.tenant.domain.residency import (
    CATEGORY_CLASS,
    ResidencyError,
    TransferDecision,
    minimize,
)
from zid_platform.modules.tenant.infrastructure.catalog import catalog as cell_catalog
from zid_platform.modules.tenant.infrastructure.domain_store import (
    verified_count as verified_domain_count,
)
from zid_platform.modules.tenant.infrastructure.residency_store import advance_placement
from zid_platform.modules.tenant.infrastructure.residency_store import load as load_residency
from zid_platform.modules.tenant.infrastructure.tenant_store import (
    RoleAssignment,
    Tenant,
    active_owner_count,
    bump_configuration,
    load_tenant,
    set_fields,
    workspace,
)

__all__ = [
    "CATEGORY_CLASS",
    "BootstrapResult",
    "ConsoleClient",
    "Declaration",
    "Move",
    "ResidencyError",
    "Role",
    "RoleAssignment",
    "RoleStatus",
    "Tenant",
    "TenantError",
    "TenantStatus",
    "TransferDecision",
    "activate_bootstrap_owner",
    "active_owner_count",
    "advance_placement",
    "authorize_remote_access",
    "authorize_transfer",
    "bump_configuration",
    "cached_home_cell",
    "cached_placement",
    "cached_tenant_state",
    "cell_catalog",
    "check_category_transfer",
    "check_dependency_locations",
    "declared_registration",
    "effective_operator_jurisdictions",
    "enforcement_for",
    "ensure_member",
    "forget",
    "home_cell",
    "live_roles",
    "load_residency",
    "load_tenant",
    "minimize",
    "placement",
    "register_acceptance_hook",
    "self_signup",
    "set_fields",
    "tenant_state",
    "transition",
    "verified_domain_count",
    "workspace",
]


async def searchable_domains(conn: AsyncConnection) -> list[tuple[str, str, str]]:
    """(id, domain, hint) of the tenant's domains, for authorization-aware search."""
    from zid_platform.modules.tenant.infrastructure.domain_store import list_domains

    return [(d.domain_id, d.domain, f"domain · {d.status}") for d in await list_domains(conn)]

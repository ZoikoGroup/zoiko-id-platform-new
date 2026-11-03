"""Public Protocol interface of the tenant module. Other modules import ONLY this.

Residency (F-W5; ZID-TENANCY-001):
- `authorize_transfer(conn, tenant, data_class=..., destination_cell_id=..., purpose_code=...)`
  the cross-cell transfer gateway decision (default deny for restricted classes, evidenced);
- `check_category_transfer` the same for a PRIV-001 data category (D0-D4);
- `authorize_remote_access` support staff reading data from outside the company's region need an approved transfer;
- `check_dependency_locations` refuses a dependency (subprocessor) whose locations cannot satisfy the profile;
- `effective_operator_jurisdictions(conn, tenant, configured)` SRP-2/3 operator restrictions.
Approvals (L-10; ZID-ENG-DEC-010 to 012): `create_case`, `cast_vote`, `execute_case`, `contain` are the one place a critical or sensitive
change is requested, voted and applied (jobs, scripts and support tools call these, never the tables); `register_executor` adds what
applies a kind of approved case; `legacy_path_gate` is the owners' enforcement switch for the earlier two-person path.
Lifecycle (G-W0; ZID-OVERLAY-001 §2-4):
- `load_tenant(conn, tenant)` the tenant record; `transition(conn, tenant, Move, ...)` the only way
  its lifecycle moves (the onboarding orchestrator supplies the gate report);
- `activate_bootstrap_owner` once the organization gate passes; `bump_configuration` appends a
  configuration revision; `live_roles` a principal's tenant roles.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.tenant.application.approvals import (
    CaseAccess,
    ExecContext,
    Executor,
    Gate,
    case_access,
    cast_vote,
    configure_standing_check,
    contain,
    create_case,
    execute_case,
    legacy_path_gate,
    register_executor,
)
from zid_platform.modules.tenant.application.lifecycle import (
    BootstrapResult,
    ConsoleClient,
    Declaration,
    activate_bootstrap_owner,
    declared_registration,
    ensure_member,
    live_roles,
    register_acceptance_hook,
    register_invitation_hook,
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
from zid_platform.modules.tenant.domain.approvals import ApprovalError
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
from zid_platform.modules.tenant.domain.role_matrix import Mode
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
    console_client_for,
    load_tenant,
    set_fields,
    workspace,
)


def matrix_gate(action_id: str, mode: Mode) -> Callable[..., Awaitable[None]]:
    """The role matrix as a route dependency (see `api/gate.py`). Imported on first use: the API layer needs the request
    machinery, which in turn imports the modules that import these ports."""
    from zid_platform.modules.tenant.api.gate import matrix_gate as gate

    return gate(action_id, mode)


def billing_permission_checker() -> Callable[..., Awaitable[None]]:
    """The billing permission decision (see `api/billing_access.py`), handed to the commercial module at start-up. Imported on first
    use for the same reason as `matrix_gate`."""
    from zid_platform.modules.tenant.api.billing_access import check_permission

    return check_permission


__all__ = [
    "CATEGORY_CLASS",
    "ApprovalError",
    "BootstrapResult",
    "CaseAccess",
    "ConsoleClient",
    "Declaration",
    "ExecContext",
    "Executor",
    "Gate",
    "Mode",
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
    "billing_permission_checker",
    "bump_configuration",
    "cached_home_cell",
    "cached_placement",
    "cached_tenant_state",
    "case_access",
    "cast_vote",
    "cell_catalog",
    "check_category_transfer",
    "check_dependency_locations",
    "configure_standing_check",
    "console_client_for",
    "contain",
    "create_case",
    "declared_registration",
    "effective_operator_jurisdictions",
    "enforcement_for",
    "ensure_member",
    "execute_case",
    "forget",
    "governance_status",
    "home_cell",
    "legacy_path_gate",
    "live_roles",
    "load_residency",
    "load_tenant",
    "matrix_gate",
    "minimize",
    "placement",
    "register_acceptance_hook",
    "register_executor",
    "register_invitation_hook",
    "roles_of_principal",
    "self_signup",
    "set_fields",
    "tenant_roles",
    "tenant_state",
    "transition",
    "verified_domain_count",
    "workspace",
]


async def searchable_domains(conn: AsyncConnection) -> list[tuple[str, str, str]]:
    """(id, domain, hint) of the tenant's domains, for authorization-aware search."""
    from zid_platform.modules.tenant.infrastructure.domain_store import list_domains

    return [(d.domain_id, d.domain, f"domain · {d.status}") for d in await list_domains(conn)]


async def tenant_roles(conn: AsyncConnection) -> list[RoleAssignment]:
    """Every role assignment of the caller's tenant that is not revoked (row-level security scopes the read)."""
    from zid_platform.modules.tenant.infrastructure import tenant_store

    return await tenant_store.roles(conn)


async def governance_status(conn: AsyncConnection, tenant_id: str) -> Any:
    """How many eligible human approvers the organization has and whether it is solo (never client-controlled)."""
    from zid_platform.modules.tenant.application.approvals import governance_status as status

    return await status(conn, tenant_id)


async def roles_of_principal(conn: AsyncConnection, principal_id: str) -> list[RoleAssignment]:
    """The tenant role assignments one account holds (any status but revoked), read in the caller's tenant scope."""
    from zid_platform.modules.tenant.infrastructure import tenant_store

    return await tenant_store.roles(conn, principal_id=principal_id)

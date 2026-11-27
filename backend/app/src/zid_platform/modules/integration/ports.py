"""Public Protocol interface of the integration module. Other modules import ONLY this.

- `record_interaction(conn, source_ref, ok=...)`: the federation / provisioning modules report each
  interaction of a source they run (`federation_connection:<id>`, `provisioning_source:<id>`), so
  the connector framework tracks health and freshness (OVERLAY-001 §15, §24).
- `connector_fresh(conn, source_ref, now)`: whether a connector-supplied fact may still back a
  high-risk decision (False when stale or quarantined; True when no connector governs the source).
- `map_record(conn, connector_id, record)`: the connector ACTIVE mapping release applied to one
  source record (profile facts, relation facts and role candidates only; never authority).
- `route_login(conn, application_id=..., login_domain=...)`: the deterministic multi-IdP route.
- `pep_application(conn, client_id)`: the registered application whose PEP this client is.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.integration.application.mapping import map_record, route_login
from zid_platform.modules.integration.application.service import (
    offboard_integrations,
    purge_connector_secrets,
    record_interaction,
    register_connection_status_setter,
)
from zid_platform.modules.integration.domain.model import Health, fresh
from zid_platform.modules.integration.infrastructure.store import (
    Application,
    active_application_count,
)
from zid_platform.modules.integration.infrastructure.store import (
    application_for_pep as pep_application,
)


async def connector_fresh(conn: AsyncConnection, source_ref: str, now: datetime) -> bool:
    from zid_platform.modules.integration.infrastructure.store import connector_for_source

    c = await connector_for_source(conn, source_ref)
    if c is None:
        return True
    return c.health is not Health.QUARANTINED and fresh(c.fresh_until, now)


__all__ = [
    "Application",
    "active_application_count",
    "connector_fresh",
    "connector_secret",
    "map_record",
    "offboard_integrations",
    "pep_application",
    "purge_connector_secrets",
    "record_interaction",
    "register_connection_status_setter",
    "register_directory_connector",
    "register_signin_connector",
    "route_login",
    "set_connector_secret",
]


async def searchable_applications(conn: AsyncConnection) -> list[tuple[str, str, str]]:
    """(id, name, hint) of the tenant's registered applications, for authorization-aware search."""
    from zid_platform.modules.integration.infrastructure.store import list_applications

    return [
        (a.application_id, a.name, f"{a.environment} · {a.status}")
        for a in await list_applications(conn)
    ]


async def searchable_connectors(conn: AsyncConnection) -> list[tuple[str, str, str]]:
    from zid_platform.modules.integration.infrastructure.store import list_connectors

    return [
        (c.connector_id, c.display_name, f"{c.connector_type} connector")
        for c in await list_connectors(conn)
    ]


async def register_directory_connector(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    display_name: str,
    source_ref: str,
    owner: str,
    actor: str,
) -> str:
    """A DIRECTORY connector (a company's own directory read by a sync job) with a secret as its credential; returns its id.

    The provisioning module runs the sync and reports each run through `record_interaction(source_ref)`; this module holds the sealed secret,
    its rotation and expiry alerts, and the connector's health, freshness and quarantine."""
    from datetime import timedelta

    from zid_platform.modules.integration.application.service import register_connector
    from zid_platform.modules.integration.domain.model import (
        AuthProfile,
        Capability,
        ConnectorType,
        DisableBehavior,
    )

    c = await register_connector(
        conn,
        tenant_id,
        connector_type=ConnectorType.DIRECTORY,
        display_name=display_name,
        source_ref=source_ref,
        capabilities=[Capability.PROVISIONING, Capability.DIRECTORY_LOOKUP],
        source_of_truth_scope=["identity.profile"],
        auth_profile=AuthProfile.SECRET,
        freshness=timedelta(
            hours=26
        ),  # a daily-or-better sync that has missed a whole day is stale
        owner=owner,
        disable_behavior=DisableBehavior.FAIL_CLOSED_HIGH_RISK,
        actor=actor,
    )
    return c.connector_id


async def register_signin_connector(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    display_name: str,
    connection_id: str,
    owner: str,
    actor: str,
) -> str:
    """An OIDC identity-provider connector that holds the company's client secret for one federation connection (CR-138): the secret is sealed here, write-only, under the cell's
    data key, instead of being kept in an operator-run secret store. Returns the connector id; the federation connection refers to it as `connector:<id>`."""
    from datetime import timedelta

    from zid_platform.modules.integration.application.service import register_connector
    from zid_platform.modules.integration.domain.model import (
        AuthProfile,
        Capability,
        ConnectorType,
        DisableBehavior,
    )

    c = await register_connector(
        conn,
        tenant_id,
        connector_type=ConnectorType.OIDC_IDP,
        display_name=display_name,
        source_ref=f"federation_connection:{connection_id}",
        capabilities=[Capability.AUTHN],
        source_of_truth_scope=["identity.authentication"],
        auth_profile=AuthProfile.SECRET,
        freshness=timedelta(days=400),
        owner=owner,
        disable_behavior=DisableBehavior.FAIL_CLOSED_HIGH_RISK,
        actor=actor,
    )
    return c.connector_id


async def set_connector_secret(
    conn: AsyncConnection, tenant_id: str, connector_id: str, *, secret: str, actor: str
) -> int:
    """Write-only: seal a new secret for the connector (the previous one stops working at once); returns its version."""
    from datetime import timedelta

    from zid_platform.modules.integration.application.service import set_credential

    return await set_credential(
        conn,
        tenant_id,
        connector_id,
        secret=secret,
        not_after=None,
        overlap=timedelta(0),
        actor=actor,
    )


async def connector_secret(
    conn: AsyncConnection, tenant_id: str, connector_id: str, now: datetime
) -> str | None:
    """For the connector's own runtime only: the secret that is in force now, or None when there is none."""
    from zid_platform.modules.integration.application.service import credential_values

    values = await credential_values(conn, tenant_id, connector_id, now)
    return values[0] if values else None

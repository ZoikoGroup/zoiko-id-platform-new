"""PostgreSQL persistence of applications, connectors and connector credentials (G-W2; CR-015)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.integration.domain.model import (
    AppStatus,
    ConnectorType,
    CredentialMeta,
    Health,
    IntegrationError,
)


def _s(v: Any) -> str | None:
    return str(v) if v is not None else None


@dataclass(frozen=True, slots=True)
class Application:
    application_id: str
    tenant_id: str
    name: str
    environment: str
    protocols: tuple[str, ...]
    redirect_uris: tuple[str, ...]
    resource_ids: tuple[str, ...]
    credential_profile: str
    pep_obligations: frozenset[str]
    pep_context_fields: frozenset[str]
    risk_floor: int
    evidence_floor: int
    owner_principal_id: str
    status: AppStatus
    configuration_revision: int
    pep_client_id: str | None = None


def _app(r: Row[Any]) -> Application:
    m = r._mapping
    return Application(
        str(m["application_id"]),
        str(m["tenant_id"]),
        m["name"],
        m["environment"],
        tuple(m["protocols"]),
        tuple(m["redirect_uris"]),
        tuple(m["resource_ids"]),
        m["credential_profile"],
        frozenset(m["pep_obligations"]),
        frozenset(m["pep_context_fields"]),
        int(m["risk_floor"]),
        int(m["evidence_floor"]),
        str(m["owner_principal_id"]),
        AppStatus(m["status"]),
        int(m["configuration_revision"]),
        m["pep_client_id"],
    )


async def insert_application(conn: AsyncConnection, v: Mapping[str, Any]) -> Application:
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.integration_application (application_id, tenant_id, name,
                    environment, protocols, redirect_uris, resource_ids, credential_profile,
                    credential_justification, pep_obligations, pep_context_fields, risk_floor,
                    evidence_floor, owner_principal_id, pep_client_id)
                VALUES (:id, :t, :name, :env, :protocols, :redirects, :resources, :cred, :just,
                    :obligations, :context, :risk, :evidence, :owner, :pep)
                RETURNING *
            """),
            {**v, "id": uuid.UUID(v["id"]), "t": uuid.UUID(v["t"]), "owner": uuid.UUID(v["owner"])},
        )
    ).one()
    return _app(row)


async def load_application(
    conn: AsyncConnection, application_id: str, *, lock: bool = False
) -> Application | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.integration_application WHERE application_id = :id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": uuid.UUID(application_id)},
        )
    ).first()
    return _app(row) if row else None


async def application_for_pep(conn: AsyncConnection, client_id: str) -> Application | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.integration_application WHERE pep_client_id = :c "
                "AND status IN ('TESTING', 'ACTIVE', 'SUSPENDED')"
            ),
            {"c": client_id},
        )
    ).first()
    return _app(row) if row else None


async def active_application_count(conn: AsyncConnection) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM platform.integration_application WHERE status = 'ACTIVE'"
                )
            )
        ).scalar_one()
    )


async def list_applications(
    conn: AsyncConnection, *, tenant_id: str | None = None
) -> list[Application]:
    """Row-level security scopes this to the connection's tenant; a worker that bypasses it names the tenant."""
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.integration_application "
                "WHERE CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid) ORDER BY created_at DESC"
            ),
            {"t": tenant_id},
        )
    ).all()
    return [_app(r) for r in rows]


async def set_application_status(
    conn: AsyncConnection, application_id: str, status: AppStatus
) -> Application:
    row = (
        await conn.execute(
            text(
                "UPDATE platform.integration_application SET status = :s, updated_at = now(), "
                "configuration_revision = configuration_revision + 1 "
                "WHERE application_id = :id RETURNING *"
            ),
            {"s": status.value, "id": uuid.UUID(application_id)},
        )
    ).one()
    return _app(row)


@dataclass(frozen=True, slots=True)
class Connector:
    connector_id: str
    tenant_id: str
    connector_type: ConnectorType
    display_name: str
    source_ref: str | None
    capabilities: frozenset[str]
    source_of_truth_scope: frozenset[str]
    auth_profile: str
    schema_version: str
    mapping_release_id: str | None
    rate_per_minute: int
    health: Health
    consecutive_failures: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    freshness_seconds: int
    fresh_until: datetime | None
    owner_principal_id: str
    disable_behavior: str
    quarantine_reason: str | None
    revision: int


def _connector(r: Row[Any]) -> Connector:
    m = r._mapping
    return Connector(
        str(m["connector_id"]),
        str(m["tenant_id"]),
        ConnectorType(m["connector_type"]),
        m["display_name"],
        m["source_ref"],
        frozenset(m["capabilities"]),
        frozenset(m["source_of_truth_scope"]),
        m["auth_profile"],
        m["schema_version"],
        _s(m["mapping_release_id"]),
        int(m["rate_per_minute"]),
        Health(m["health"]),
        int(m["consecutive_failures"]),
        m["last_success_at"],
        m["last_failure_at"],
        int(m["freshness_seconds"]),
        m["fresh_until"],
        str(m["owner_principal_id"]),
        m["disable_behavior"],
        m["quarantine_reason"],
        int(m["revision"]),
    )


async def insert_connector(conn: AsyncConnection, v: Mapping[str, Any]) -> Connector:
    try:
        row = (
            await conn.execute(
                text("""
                    INSERT INTO platform.integration_connector (connector_id, tenant_id,
                        connector_type, display_name, source_ref, capabilities,
                        source_of_truth_scope, auth_profile, schema_version, rate_per_minute,
                        freshness_seconds, owner_principal_id, disable_behavior)
                    VALUES (:id, :t, :type, :name, :ref, :caps, :scope, :auth, :schema, :rate,
                        :fresh, :owner, :disable)
                    RETURNING *
                """),
                {
                    **v,
                    "id": uuid.UUID(v["id"]),
                    "t": uuid.UUID(v["t"]),
                    "owner": uuid.UUID(v["owner"]),
                },
            )
        ).one()
    except IntegrityError as e:
        if "connector_source_once" in str(e.orig):
            raise IntegrationError(
                "CONNECTOR_EXISTS", "That source already has a connector."
            ) from e
        raise
    return _connector(row)


async def load_connector(
    conn: AsyncConnection, connector_id: str, *, lock: bool = False
) -> Connector | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.integration_connector WHERE connector_id = :id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": uuid.UUID(connector_id)},
        )
    ).first()
    return _connector(row) if row else None


async def connector_for_source(conn: AsyncConnection, source_ref: str) -> Connector | None:
    row = (
        await conn.execute(
            text("SELECT * FROM platform.integration_connector WHERE source_ref = :r FOR UPDATE"),
            {"r": source_ref},
        )
    ).first()
    return _connector(row) if row else None


async def list_connectors(
    conn: AsyncConnection, *, tenant_id: str | None = None
) -> list[Connector]:
    """Row-level security scopes this to the connection's tenant; a worker that bypasses it names the tenant."""
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.integration_connector "
                "WHERE CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid) ORDER BY created_at"
            ),
            {"t": tenant_id},
        )
    ).all()
    return [_connector(r) for r in rows]


async def update_health(
    conn: AsyncConnection,
    connector_id: str,
    *,
    health: Health,
    failures: int,
    success_at: datetime | None,
    failure_at: datetime | None,
    fresh_until: datetime | None,
    quarantine_reason: str | None,
) -> Connector:
    row = (
        await conn.execute(
            text("""
                UPDATE platform.integration_connector SET health = :h, consecutive_failures = :f,
                    last_success_at = COALESCE(CAST(:ok AS timestamptz), last_success_at),
                    last_failure_at = COALESCE(CAST(:bad AS timestamptz), last_failure_at),
                    fresh_until = CAST(:fresh AS timestamptz), quarantine_reason = :q,
                    revision = revision + 1
                WHERE connector_id = :id RETURNING *
            """),
            {
                "h": health.value,
                "f": failures,
                "ok": success_at,
                "bad": failure_at,
                "fresh": fresh_until,
                "q": quarantine_reason,
                "id": uuid.UUID(connector_id),
            },
        )
    ).one()
    return _connector(row)


# ------------------------------------------------------------------------------- credentials


async def credentials(conn: AsyncConnection, connector_id: str) -> list[CredentialMeta]:
    rows = (
        await conn.execute(
            text(
                "SELECT version, status, fingerprint, not_after, overlap_until "
                "FROM platform.connector_credential WHERE connector_id = :c ORDER BY version DESC"
            ),
            {"c": uuid.UUID(connector_id)},
        )
    ).all()
    return [
        CredentialMeta(int(r.version), r.status, r.fingerprint, r.not_after, r.overlap_until)
        for r in rows
    ]


async def rotate_credential(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    connector_id: str,
    envelope: bytes,
    fingerprint: str,
    not_after: datetime | None,
    overlap_until: datetime,
    created_by: str,
) -> int:
    """The previous ACTIVE version becomes RETIRING until `overlap_until`; older RETIRING versions
    are revoked (at most one overlapping credential)."""
    c = uuid.UUID(connector_id)
    await conn.execute(
        text(
            "UPDATE platform.connector_credential SET status = 'REVOKED' "
            "WHERE connector_id = :c AND status = 'RETIRING'"
        ),
        {"c": c},
    )
    await conn.execute(
        text(
            "UPDATE platform.connector_credential SET status = 'RETIRING', overlap_until = :o "
            "WHERE connector_id = :c AND status = 'ACTIVE'"
        ),
        {"c": c, "o": overlap_until},
    )
    version = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(version), 0) + 1 FROM platform.connector_credential "
                    "WHERE connector_id = :c"
                ),
                {"c": c},
            )
        ).scalar_one()
    )
    await conn.execute(
        text("""
            INSERT INTO platform.connector_credential (connector_id, version, tenant_id, status,
                envelope, fingerprint, not_after, created_by)
            VALUES (:c, :v, :t, 'ACTIVE', :env, :fp, :na, :by)
        """),
        {
            "c": c,
            "v": version,
            "t": uuid.UUID(tenant_id),
            "env": envelope,
            "fp": fingerprint,
            "na": not_after,
            "by": uuid.UUID(created_by),
        },
    )
    return version


async def revoke_credentials(conn: AsyncConnection, connector_id: str) -> int:
    r = await conn.execute(
        text(
            "UPDATE platform.connector_credential SET status = 'REVOKED' "
            "WHERE connector_id = :c AND status <> 'REVOKED'"
        ),
        {"c": uuid.UUID(connector_id)},
    )
    return int(r.rowcount or 0)


async def usable_envelopes(
    conn: AsyncConnection, connector_id: str, now: datetime
) -> list[tuple[int, bytes]]:
    rows = (
        await conn.execute(
            text(
                "SELECT version, envelope FROM platform.connector_credential WHERE connector_id = :c "
                "AND (status = 'ACTIVE' OR (status = 'RETIRING' AND overlap_until > :now)) "
                "AND (not_after IS NULL OR not_after > :now) ORDER BY version DESC"
            ),
            {"c": uuid.UUID(connector_id), "now": now},
        )
    ).all()
    return [(int(r.version), bytes(r.envelope)) for r in rows]


async def expiring(conn: AsyncConnection, horizon: datetime) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT connector_id, tenant_id, version, not_after, alerted_days "
                "FROM platform.connector_credential WHERE status = 'ACTIVE' "
                "AND not_after IS NOT NULL AND not_after <= :h"
            ),
            {"h": horizon},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def mark_alerted(conn: AsyncConnection, connector_id: str, version: int, day: int) -> None:
    await conn.execute(
        text(
            "UPDATE platform.connector_credential SET alerted_days = array_append(alerted_days, :d) "
            "WHERE connector_id = :c AND version = :v"
        ),
        {"c": uuid.UUID(connector_id), "v": version, "d": day},
    )

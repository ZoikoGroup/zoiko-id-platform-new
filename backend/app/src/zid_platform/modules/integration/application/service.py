"""Application registry and connector framework use cases (ZID-OVERLAY-001 §14-16, §20, §24; G-W2).

- Applications: register, move through DRAFT -> TESTING -> ACTIVE; activation bumps the tenant's
  application_registry_revision and emits `application.activated`.
- Connectors: register over an existing federation connection or SCIM source; every interaction
  the underlying module reports moves health and freshness (`record_interaction`).
- Quarantine (R3): health QUARANTINED, credentials revoked, and the underlying source itself is
  quarantined/suspended so it stops updating trust state at once (OVL-23); release needs a second
  administrator and returns the source to service with health re-earned by the next success.
- Credentials: write-only. The value is sealed under the cell's DATA_KEK with AAD bound to tenant,
  connector and version, never returned; rotation keeps one RETIRING version for a bounded overlap.
Every change emits its event and a protected AuditRecord (E3 for quarantine/release/credentials).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from typing import Protocol as StructuralType

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_crypto.envelope import Aad, Envelope, open_envelope, seal
from zid_outbox import emit

from zid_platform.modules.evidence.ports import (
    AuditFacts,
    EvidenceClass,
    commitment,
    current_runtime,
    record_audit,
)
from zid_platform.modules.federation.ports import set_connection_status
from zid_platform.modules.integration.domain.model import (
    DEFAULT_FRESHNESS,
    MAX_OVERLAP,
    AppStatus,
    AuthProfile,
    Capability,
    ConnectorType,
    CredentialProfile,
    DisableBehavior,
    Environment,
    Health,
    IntegrationError,
    Protocol,
    check_app_transition,
    check_context_fields,
    check_credential_profile,
    check_endpoint,
    check_obligations,
    check_source_ref,
    expiry_alert,
    health_after,
)
from zid_platform.modules.integration.infrastructure import store
from zid_platform.modules.integration.infrastructure.store import Application, Connector
from zid_platform.modules.provisioning.ports import set_source_status
from zid_platform.modules.tenant.ports import bump_configuration


def _now() -> datetime:
    return datetime.now(UTC)


async def _record(
    conn: AsyncConnection,
    tenant_id: str,
    name: str,
    *,
    target: tuple[str, str],
    version: int,
    actor: str | None,
    evidence: EvidenceClass,
    data: dict[str, Any],
    approvals: Sequence[str] = (),
    outcome: str = "SUCCESS",
) -> None:
    payload = {**data, "evidence_class": evidence.value}
    event_id = await emit(
        conn,
        name=name,
        tenant_id=tenant_id,
        aggregate_type=target[0],
        aggregate_id=target[1],
        aggregate_version=version,
        data=payload,
        classification="RESTRICTED",
    )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=name,
            actor_type="HUMAN" if actor else "SERVICE",
            action=name.rsplit(".", 1)[-1],
            target_type=target[0],
            target_ref=target[1],
            outcome=outcome,
            evidence_class=evidence,
            occurred_at=_now(),
            actor_principal_id=actor,
            source_service="integration",
            approval_refs=tuple(approvals),
            source_event_id=event_id,
            details={k: v for k, v in data.items() if isinstance(v, str | int | bool | type(None))},
        ),
    )


# -------------------------------------------------------------------------------- applications


async def register_application(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    name: str,
    environment: Environment,
    protocols: Sequence[Protocol],
    redirect_uris: Sequence[str],
    resource_ids: Sequence[str],
    credential_profile: CredentialProfile,
    justification: str | None,
    pep_obligations: Sequence[str],
    pep_context_fields: Sequence[str],
    risk_floor: int,
    evidence_floor: int,
    owner: str,
    actor: str,
    pep_client_id: str | None = None,
) -> Application:
    check_credential_profile(credential_profile, justification)
    check_context_fields(frozenset(pep_context_fields))
    obligations = check_obligations(frozenset(pep_obligations))
    if evidence_floor < risk_floor:
        raise IntegrationError("VALIDATION_FAILED", "The evidence floor follows the risk floor.")
    app = await store.insert_application(
        conn,
        {
            "id": str(uuid7()),
            "t": tenant_id,
            "name": name,
            "env": environment.value,
            "protocols": sorted({p.value for p in protocols}),
            "redirects": [check_endpoint(u, environment) for u in redirect_uris],
            "resources": sorted(set(resource_ids)),
            "cred": credential_profile.value,
            "just": justification,
            "obligations": sorted(obligations),
            "context": sorted(set(pep_context_fields)),
            "risk": risk_floor,
            "evidence": evidence_floor,
            "owner": owner,
            "pep": pep_client_id,
        },
    )
    await _record(
        conn,
        tenant_id,
        "integration.application.changed",
        target=("integration_application", app.application_id),
        version=app.configuration_revision,
        actor=actor,
        evidence=EvidenceClass.E2,
        data=app_data(app),
    )
    return app


class ClientGateway(StructuralType):
    """What the OAuth client registry offers the application registry. The session module implements it and main.py
    hands it over at start-up, so integration never imports session (no dependency cycle)."""

    async def register(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        name: str,
        client_type: str,
        redirect_uris: Sequence[str],
        jwks: dict[str, Any] | None,
        scopes: Sequence[str],
        audience: str,
    ) -> dict[str, Any]: ...

    async def register_machine(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        name: str,
        jwks: dict[str, Any],
        scopes: Sequence[str],
        audience: str,
        home_cell: str,
    ) -> dict[str, Any]: ...

    async def get(self, conn: AsyncConnection, client_id: str) -> dict[str, Any] | None: ...

    async def retire(self, conn: AsyncConnection, client_id: str) -> bool: ...

    async def listing(self, conn: AsyncConnection) -> list[dict[str, Any]]: ...


_CLIENTS: ClientGateway | None = None


def configure_client_gateway(gateway: ClientGateway | None) -> None:
    global _CLIENTS
    _CLIENTS = gateway


def client_gateway() -> ClientGateway:
    if _CLIENTS is None:
        raise IntegrationError(
            "OAUTH_CLIENTS_UNAVAILABLE", "The client registry is not configured."
        )
    return _CLIENTS


async def register_oauth_client(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    name: str,
    client_type: str,
    redirect_uris: Sequence[str],
    jwks: dict[str, Any] | None,
    scopes: Sequence[str],
    audience: str,
    actor: str,
) -> dict[str, Any]:
    """Register an application's OAuth client (ZID-AUTH-001 §6-7). No secret exists to hand out: a public client uses
    PKCE and a confidential one proves itself with a key pair whose public half is registered here."""
    try:
        client = await client_gateway().register(
            conn,
            tenant_id=tenant_id,
            name=name,
            client_type=client_type,
            redirect_uris=redirect_uris,
            jwks=jwks,
            scopes=scopes,
            audience=audience,
        )
    except ValueError as e:  # the registry's own refusal, with a stable code
        raise IntegrationError(
            getattr(e, "code", "VALIDATION_FAILED"), getattr(e, "detail", str(e))
        ) from e
    await _record(
        conn,
        tenant_id,
        "integration.application.changed",
        target=("oauth_client", client["client_id"]),
        version=1,
        actor=actor,
        evidence=EvidenceClass.E2,
        data={"client_id": client["client_id"], "client_type": client_type, "status": "ACTIVE"},
    )
    return client


DECISION_SCOPE = "access:evaluate"  # what a decision caller's token is for (the decision API itself is open to any of the tenant's clients)


async def register_decision_client(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    name: str,
    jwks: dict[str, Any],
    audience: str,
    home_cell: str,
    actor: str,
) -> dict[str, Any]:
    """Register the OAuth client an application uses to ask the decision API (ZID-AUTH-001 §6-7). It is a machine client: `client_credentials`
    only, no redirect and no secret, proving itself with a key pair whose public half is registered here."""
    try:
        client = await client_gateway().register_machine(
            conn, tenant_id=tenant_id, name=name, jwks=jwks, scopes=[DECISION_SCOPE], audience=audience, home_cell=home_cell
        )
    except ValueError as e:  # the registry's own refusal, with a stable code
        raise IntegrationError(getattr(e, "code", "VALIDATION_FAILED"), getattr(e, "detail", str(e))) from e
    await _record(
        conn,
        tenant_id,
        "integration.application.changed",
        target=("oauth_client", client["client_id"]),
        version=1,
        actor=actor,
        evidence=EvidenceClass.E2,
        data={"client_id": client["client_id"], "client_type": "MACHINE", "status": "ACTIVE"},
    )
    return client


async def retire_oauth_client(
    conn: AsyncConnection, tenant_id: str, client_id: str, *, actor: str
) -> None:
    """Stop an application signing people in. The client's status is re-read on every token request, so its refresh
    tokens stop working too; the id stays reserved, so it can never be reissued to someone else."""
    client = await client_gateway().get(conn, client_id)
    if client is None or client["first_party"]:
        raise IntegrationError("OAUTH_CLIENT_NOT_FOUND")
    if await client_gateway().retire(conn, client_id):
        await _record(
            conn,
            tenant_id,
            "integration.application.changed",
            target=("oauth_client", client_id),
            version=2,
            actor=actor,
            evidence=EvidenceClass.E2,
            data={"client_id": client_id, "status": "RETIRED"},
        )


def app_data(a: Application) -> dict[str, Any]:
    return {
        "application_id": a.application_id,
        "environment": a.environment,
        "status": a.status.value,
        "risk_floor": f"R{a.risk_floor}",
        "configuration_revision": a.configuration_revision,
    }


async def move_application(
    conn: AsyncConnection, tenant_id: str, application_id: str, target: AppStatus, *, actor: str
) -> Application:
    a = await store.load_application(conn, application_id, lock=True)
    if a is None:
        raise IntegrationError("APPLICATION_NOT_FOUND")
    check_app_transition(a.status, target)
    a = await store.set_application_status(conn, application_id, target)
    if target in (AppStatus.ACTIVE, AppStatus.SUSPENDED, AppStatus.RETIRED):
        await bump_configuration(
            conn,
            tenant_id,
            change_kind=f"application.{target.value.lower()}",
            change_commitment=commitment(application_id, tenant_id=tenant_id, kind="application"),
            actor_principal_id=actor,
            approval_ref=None,
            now=_now(),
            applications=True,
        )
    name = (
        "application.activated" if target is AppStatus.ACTIVE else "integration.application.changed"
    )
    await _record(
        conn,
        tenant_id,
        name,
        target=("integration_application", application_id),
        version=a.configuration_revision,
        actor=actor,
        evidence=EvidenceClass.E2,
        data=app_data(a),
    )
    return a


# ---------------------------------------------------------------------------------- connectors


def connector_data(c: Connector, **extra: Any) -> dict[str, Any]:
    return {
        "connector_id": c.connector_id,
        "connector_type": c.connector_type.value,
        "source_ref": c.source_ref,
        "health": c.health.value,
        "revision": c.revision,
        **extra,
    }


def _changed_event(c: Connector) -> str:
    if c.connector_type in (ConnectorType.SCIM, ConnectorType.HRIS):
        return "provisioning.connector_changed"
    if c.connector_type in (ConnectorType.OIDC_IDP, ConnectorType.SAML_IDP):
        return "identity_source.trust_changed"
    return "integration.connector.changed"


async def register_connector(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    connector_type: ConnectorType,
    display_name: str,
    source_ref: str | None,
    capabilities: Sequence[Capability],
    source_of_truth_scope: Sequence[str],
    auth_profile: AuthProfile,
    freshness: timedelta | None,
    owner: str,
    disable_behavior: DisableBehavior,
    actor: str,
) -> Connector:
    ref = check_source_ref(connector_type, source_ref)
    fresh_for = freshness or DEFAULT_FRESHNESS.get(connector_type, timedelta(hours=1))
    c = await store.insert_connector(
        conn,
        {
            "id": str(uuid7()),
            "t": tenant_id,
            "type": connector_type.value,
            "name": display_name,
            "ref": ref,
            "caps": sorted({c.value for c in capabilities}),
            "scope": sorted(set(source_of_truth_scope)),
            "auth": auth_profile.value,
            "schema": "1",
            "rate": 600,
            "fresh": int(fresh_for.total_seconds()),
            "owner": owner,
            "disable": disable_behavior.value,
        },
    )
    name = (
        "identity_source.created"
        if connector_type in (ConnectorType.OIDC_IDP, ConnectorType.SAML_IDP)
        else _changed_event(c)
    )
    await _record(
        conn,
        tenant_id,
        name,
        target=("integration_connector", c.connector_id),
        version=c.revision,
        actor=actor,
        evidence=EvidenceClass.E2,
        data=connector_data(c),
    )
    return c


async def record_interaction(
    conn: AsyncConnection, source_ref: str, *, ok: bool, now: datetime | None = None
) -> Connector | None:
    """The underlying module reports one interaction (a SCIM write, a federated login)."""
    now = now or _now()
    c = await store.connector_for_source(conn, source_ref)
    if c is None:
        return None
    failures = 0 if ok else c.consecutive_failures + 1
    health = health_after(c.health, ok=ok, consecutive_failures=failures)
    return await store.update_health(
        conn,
        c.connector_id,
        health=health,
        failures=failures,
        success_at=now if ok else None,
        failure_at=None if ok else now,
        fresh_until=now + timedelta(seconds=c.freshness_seconds) if ok else c.fresh_until,
        quarantine_reason=c.quarantine_reason,
    )


async def _underlying(conn: AsyncConnection, c: Connector, *, quarantine: bool) -> None:
    if not c.source_ref:
        return
    kind, _, ident = c.source_ref.partition(":")
    if kind == "federation_connection":
        await set_connection_status(conn, ident, "QUARANTINED" if quarantine else "ACTIVE")
    elif kind == "provisioning_source":
        await set_source_status(conn, ident, "SUSPENDED" if quarantine else "ACTIVE")


async def quarantine(
    conn: AsyncConnection, tenant_id: str, connector_id: str, *, reason: str, actor: str | None
) -> Connector:
    """OVL-23: stops updating trust state at once; never fallen back to (§20)."""
    c = await store.load_connector(conn, connector_id, lock=True)
    if c is None:
        raise IntegrationError("CONNECTOR_NOT_FOUND")
    revoked = await store.revoke_credentials(conn, connector_id)
    c = await store.update_health(
        conn,
        connector_id,
        health=Health.QUARANTINED,
        failures=c.consecutive_failures,
        success_at=None,
        failure_at=None,
        fresh_until=None,  # whatever it supplied is no longer fresh
        quarantine_reason=reason,
    )
    await _underlying(conn, c, quarantine=True)
    await bump_configuration(
        conn,
        tenant_id,
        change_kind="connector.quarantined",
        change_commitment=commitment(connector_id, tenant_id=tenant_id, kind="connector"),
        actor_principal_id=actor,
        approval_ref=None,
        now=_now(),
        security=True,
    )
    await _record(
        conn,
        tenant_id,
        "connector.quarantined",
        target=("integration_connector", connector_id),
        version=c.revision,
        actor=actor,
        evidence=EvidenceClass.E3,
        data=connector_data(c, reason_code=reason, credentials_revoked=revoked),
    )
    return c


async def release(
    conn: AsyncConnection, tenant_id: str, connector_id: str, *, actor: str, approver: str
) -> Connector:
    c = await store.load_connector(conn, connector_id, lock=True)
    if c is None:
        raise IntegrationError("CONNECTOR_NOT_FOUND")
    if c.health is not Health.QUARANTINED:
        raise IntegrationError(
            "CONNECTOR_STATE_INVALID", "Only a quarantined connector is released."
        )
    c = await store.update_health(
        conn,
        connector_id,
        health=Health.DEGRADED,  # health is re-earned by the next successful interaction
        failures=0,
        success_at=None,
        failure_at=None,
        fresh_until=None,
        quarantine_reason=None,
    )
    await _underlying(conn, c, quarantine=False)
    await _record(
        conn,
        tenant_id,
        _changed_event(c),
        target=("integration_connector", connector_id),
        version=c.revision,
        actor=actor,
        evidence=EvidenceClass.E3,
        data=connector_data(c, released=True),
        approvals=(approver,),
    )
    return c


# --------------------------------------------------------------------------------- credentials


def _aad(tenant_id: str, connector_id: str, version: int) -> Aad:
    return Aad(tenant_id, "connector_credential", connector_id, f"v{version}", "1")


async def set_credential(
    conn: AsyncConnection,
    tenant_id: str,
    connector_id: str,
    *,
    secret: str,
    not_after: datetime | None,
    overlap: timedelta,
    actor: str,
    now: datetime | None = None,
) -> int:
    """§16 write-only: sealed at once, never re-displayed; returns the new version."""
    now = now or _now()
    c = await store.load_connector(conn, connector_id, lock=True)
    if c is None:
        raise IntegrationError("CONNECTOR_NOT_FOUND")
    if c.health is Health.QUARANTINED:
        raise IntegrationError("CONNECTOR_QUARANTINED", "Release the connector first.")
    if overlap < timedelta(0) or overlap > MAX_OVERLAP:
        raise IntegrationError("VALIDATION_FAILED", "Overlap is 0-7 days.")
    if not_after is not None and not_after <= now:
        raise IntegrationError("VALIDATION_FAILED", "not_after is in the past.")
    existing = await store.credentials(conn, connector_id)
    version = (existing[0].version + 1) if existing else 1
    rt = current_runtime()
    sealed: Envelope = await seal(
        secret.encode(), _aad(tenant_id, connector_id, version), rt.provider, rt.data_kek
    )
    fingerprint = (
        "sha256:"
        + hashlib.sha256(f"{connector_id}|{version}|".encode() + secret.encode()).hexdigest()[:16]
    )
    stored = await store.rotate_credential(
        conn,
        tenant_id=tenant_id,
        connector_id=connector_id,
        envelope=sealed.to_bytes(),
        fingerprint=fingerprint,
        not_after=not_after,
        overlap_until=now + overlap,
        created_by=actor,
    )
    assert stored == version
    await _record(
        conn,
        tenant_id,
        "integration.credential.rotated",
        target=("connector_credential", f"{connector_id}:{version}"),
        version=version,
        actor=actor,
        evidence=EvidenceClass.E3,
        data={
            "connector_id": connector_id,
            "version": version,
            "credential_commitment": fingerprint,
        },
    )
    return version


async def credential_values(
    conn: AsyncConnection, tenant_id: str, connector_id: str, now: datetime | None = None
) -> list[str]:
    """For the connector runtime only (never an API): the ACTIVE value first, then one still in
    its overlap window, so a peer mid-rotation keeps working (§16)."""
    now = now or _now()
    rt = current_runtime()
    out: list[str] = []
    for version, raw in await store.usable_envelopes(conn, connector_id, now):
        plain = await open_envelope(
            Envelope.from_json(raw), _aad(tenant_id, connector_id, version), rt.provider
        )
        out.append(plain.decode())
    return out


async def expiry_alerts(conn: AsyncConnection, now: datetime) -> list[tuple[str, int]]:
    """Worker pass (BYPASSRLS): 30/14/7/1-day credential expiry alerts, each emitted once."""
    from sqlalchemy import text

    out: list[tuple[str, int]] = []
    for r in await store.expiring(conn, now + timedelta(days=30)):
        day = expiry_alert(r["not_after"], now, frozenset(r["alerted_days"] or ()))
        if day is None:
            continue
        tenant, connector = str(r["tenant_id"]), str(r["connector_id"])
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        await store.mark_alerted(conn, connector, int(r["version"]), day)
        await emit(
            conn,
            name="integration.credential.expiring",
            tenant_id=tenant,
            aggregate_type="connector_credential_alert",
            aggregate_id=f"{connector}:{r['version']}",
            aggregate_version=day,
            data={
                "connector_id": connector,
                "version": int(r["version"]),
                "days_left": day,
                "not_after": r["not_after"].isoformat(),
            },
        )
        out.append((connector, day))
    return out


async def offboard_integrations(
    conn: AsyncConnection, tenant_id: str, *, actor: str | None
) -> dict[str, int]:
    """OVL-30: every connector quarantined (credentials revoked, sources suspended) and every
    application retired. Idempotent."""
    quarantined = retired = 0
    for c in await store.list_connectors(conn):
        if c.health is not Health.QUARANTINED:
            await quarantine(
                conn, tenant_id, c.connector_id, reason="TENANT_OFFBOARDING", actor=actor
            )
            quarantined += 1
    for a in await store.list_applications(conn):
        if a.status is not AppStatus.RETIRED:
            await move_application(
                conn, tenant_id, a.application_id, AppStatus.RETIRED, actor=actor or ""
            )
            retired += 1
    return {"connectors_quarantined": quarantined, "applications_retired": retired}


async def purge_connector_secrets(conn: AsyncConnection, tenant_id: str) -> int:
    """TEN-25: destroy the encrypted credentials a company's connectors held. The fingerprint stays so history can
    say which credential was in use; the secret itself cannot be recovered. Idempotent."""
    from sqlalchemy import text

    res = await conn.execute(
        text(
            "UPDATE platform.connector_credential SET envelope = ''::bytea, status = 'REVOKED' "
            "WHERE tenant_id = :t AND (envelope <> ''::bytea OR status <> 'REVOKED')"
        ),
        {"t": tenant_id},
    )
    return int(res.rowcount or 0)


async def stale_sweep(conn: AsyncConnection, now: datetime) -> list[str]:
    """Worker pass (BYPASSRLS; OVERLAY §24 SCIM health / §31): a connector whose facts are past
    `fresh_until` drops to DEGRADED, so high-risk consumers see it stale (connector_fresh)."""
    from sqlalchemy import text

    rows = (
        await conn.execute(
            text(
                "SELECT connector_id, tenant_id FROM platform.integration_connector "
                "WHERE health = 'HEALTHY' AND fresh_until IS NOT NULL AND fresh_until < :now"
            ),
            {"now": now},
        )
    ).all()
    out = []
    for r in rows:
        tenant = str(r.tenant_id)
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        c = await store.load_connector(conn, str(r.connector_id), lock=True)
        if c is None or c.health is not Health.HEALTHY:
            continue
        c = await store.update_health(
            conn,
            c.connector_id,
            health=Health.DEGRADED,
            failures=c.consecutive_failures,
            success_at=None,
            failure_at=None,
            fresh_until=c.fresh_until,
            quarantine_reason=None,
        )
        await emit(
            conn,
            name="integration.connector.stale",
            tenant_id=tenant,
            aggregate_type="integration_connector",
            aggregate_id=c.connector_id,
            aggregate_version=c.revision,
            data=connector_data(c, reason_code="SCIM_SOURCE_STALE"),
        )
        out.append(c.connector_id)
    return out

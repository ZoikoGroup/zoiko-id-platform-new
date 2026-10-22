"""Mapping release and routing use cases (ZID-OVERLAY-001 §8, §11, §20-21; OVL-10/11/27; G-W3).

create -> simulate (DRAFT -> REVIEW, report stored) -> activate (an administrator other than the
author approves; the report must be conflict-free) -> a later activation of another version retires
it. Rollback is exactly that: activating an earlier immutable version through the same governed
path, so evidence always records both versions. `map_record` applies the ACTIVE release at runtime
with the same deterministic engine the simulation used.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.integration.domain.mapping import (
    MappingResult,
    ReleaseStatus,
    apply,
    check_activation,
    content_hash,
    parse_rules,
    simulate,
)
from zid_platform.modules.integration.domain.model import IntegrationError
from zid_platform.modules.integration.domain.routing import (
    RouteKind,
    RoutingRule,
    check_rule,
    resolve,
)
from zid_platform.modules.integration.infrastructure import mapping_store as store
from zid_platform.modules.integration.infrastructure import store as connector_store
from zid_platform.modules.integration.infrastructure.mapping_store import Release
from zid_platform.modules.tenant.ports import bump_configuration, set_fields


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    name: str,
    r: Release,
    *,
    actor: str,
    approvals: Sequence[str] = (),
    evidence: EvidenceClass = EvidenceClass.E2,
) -> None:
    data = {
        "release_id": r.release_id,
        "connector_id": r.connector_id,
        "version": r.version,
        "status": r.status.value,
        "content_hash": r.content_hash,
        "evidence_class": evidence.value,
    }
    event_id = await emit(
        conn,
        name=name,
        tenant_id=tenant_id,
        aggregate_type="mapping_release",
        aggregate_id=r.release_id,
        # monotonic even when an earlier version is re-activated (rollback)
        aggregate_version=int(datetime.now(UTC).timestamp() * 1_000_000),
        data=data,
    )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=name,
            actor_type="HUMAN",
            action=name.rsplit(".", 1)[-1],
            target_type="mapping_release",
            target_ref=r.release_id,
            outcome="SUCCESS",
            evidence_class=evidence,
            occurred_at=datetime.now(UTC),
            actor_principal_id=actor,
            source_service="integration",
            approval_refs=tuple(approvals),
            source_event_id=event_id,
            details={k: v for k, v in data.items() if k != "evidence_class"},
        ),
    )


async def create_release(
    conn: AsyncConnection,
    tenant_id: str,
    connector_id: str,
    *,
    rules: Sequence[Mapping[str, Any]],
    author: str,
) -> Release:
    if await connector_store.load_connector(conn, connector_id) is None:
        raise IntegrationError("CONNECTOR_NOT_FOUND")
    parsed = parse_rules(rules)
    r = await store.insert_release(
        conn,
        tenant_id=tenant_id,
        connector_id=connector_id,
        rules=[dict(x) for x in rules],
        content_hash=content_hash(parsed),
        author=author,
    )
    await _audit(conn, tenant_id, "integration.mapping_release.changed", r, actor=author)
    return r


async def simulate_release(
    conn: AsyncConnection,
    tenant_id: str,
    release_id: str,
    *,
    records: Sequence[Mapping[str, Any]],
    actor: str,
) -> Release:
    r = await store.load_release(conn, release_id, lock=True)
    if r is None:
        raise IntegrationError("MAPPING_RELEASE_NOT_FOUND")
    if r.status not in (ReleaseStatus.DRAFT, ReleaseStatus.REVIEW):
        raise IntegrationError(
            "MAPPING_STATE_INVALID", f"A {r.status} release is not re-simulated."
        )
    report = simulate(parse_rules(r.rules), records)
    r = await store.record_simulation(conn, release_id, report, datetime.now(UTC))
    await _audit(conn, tenant_id, "integration.mapping_release.changed", r, actor=actor)
    return r


async def activate_release(
    conn: AsyncConnection, tenant_id: str, release_id: str, *, actor: str, approver: str
) -> Release:
    """Activation, or rollback to an earlier version (OVERLAY §20 "Rollback immutable mapping
    release"); the approver must not be the author (DB CHECK as well)."""
    r = await store.load_release(conn, release_id, lock=True)
    if r is None:
        raise IntegrationError("MAPPING_RELEASE_NOT_FOUND")
    if r.status is ReleaseStatus.ACTIVE:
        return r
    if r.status is ReleaseStatus.DRAFT:
        raise IntegrationError("MAPPING_SIMULATION_REQUIRED", "Simulate the release first.")
    check_activation(r.simulation_report)
    if approver == r.author:
        raise IntegrationError("APPROVER_IS_AUTHOR", "The author cannot approve their own release.")
    now = datetime.now(UTC)
    r = await store.activate(conn, r, approved_by=approver, now=now)
    await set_fields(conn, tenant_id, mapping_release_id=r.release_id)
    await bump_configuration(
        conn,
        tenant_id,
        change_kind="mapping_release.activated",
        change_commitment=r.content_hash,
        actor_principal_id=actor,
        approval_ref=approver,
        now=now,
        security=True,
    )
    await _audit(
        conn,
        tenant_id,
        "mapping_release.activated",
        r,
        actor=actor,
        approvals=(approver,),
        evidence=EvidenceClass.E3,
    )
    return r


async def map_record(
    conn: AsyncConnection, connector_id: str, record: Mapping[str, Any]
) -> MappingResult | None:
    """Runtime: the connector's ACTIVE release applied to one source record (None: no release)."""
    r = await store.active_release(conn, connector_id)
    return apply(parse_rules(r.rules), record) if r else None


# ---------------------------------------------------------------------------------- routing


async def add_route(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    kind: RouteKind,
    match: str,
    connection_ref: str,
    actor: str,
) -> RoutingRule:
    value = check_rule(kind, match, connection_ref)
    rule = await store.insert_route(
        conn,
        tenant_id=tenant_id,
        kind=kind,
        match=value,
        connection_ref=connection_ref,
        created_by=actor,
    )
    await bump_configuration(
        conn,
        tenant_id,
        change_kind="routing.added",
        change_commitment=f"{kind.value}:{value}->{connection_ref}",
        actor_principal_id=actor,
        approval_ref=None,
        now=datetime.now(UTC),
    )
    return rule


async def route_login(
    conn: AsyncConnection, *, application_id: str | None, login_domain: str | None
) -> RoutingRule | None:
    return resolve(
        await store.routes(conn), application_id=application_id, login_domain=login_domain
    )

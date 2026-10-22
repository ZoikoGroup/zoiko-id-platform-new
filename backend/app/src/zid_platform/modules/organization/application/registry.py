"""Root establishment from a registry source, and its refresh (ZID-ROOT-001 §4-6, §18-19; G-W1).

`establish_registry_root` runs §19 steps 1-11 for the registry path: resolve the official record,
check legal status, identify the representative among the *current* official officers by an exact
match of the representative's verified legal-name claim, build an AR-3 REGISTRY root with provenance
and a freshness bound, and activate it. Only then may the organization issue grants (step 12).

`refresh_registry_roots` re-checks every registry root before it lapses: still true -> freshness
extended; officer ceased or company dissolved -> the source event is applied (root REVOKED) and the
dependent grants cascade (MANDATE §5.1). A source outage changes nothing: the root simply lapses at
`fresh_until` and R2/R3 decisions fail closed (§4.1 "missing data is not revocation").
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_outbox import emit

from zid_platform.modules.organization.domain.registry import (
    REGISTRY_FRESHNESS,
    RefreshOutcome,
    RegistryEntity,
    RegistryError,
    check_can_originate,
    name_renderings,
    officer_key,
    refresh_outcome,
    registry_root,
    representative_officers,
)
from zid_platform.modules.organization.domain.roots import OrgEvent, RootStatus, SourceEvent
from zid_platform.modules.organization.infrastructure import root_store
from zid_platform.modules.organization.infrastructure.registry_adapters import RegistryAdapter

NameMatcher = Callable[[tuple[str, ...]], Awaitable[bool]]
RootChanged = Callable[[AsyncConnection, str], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class EstablishedRoot:
    organization_id: str
    root_id: str
    source_id: str
    namespace: str
    identifier: str
    legal_name: str
    jurisdiction: str
    fresh_until: datetime


def _hash(identifier: str) -> bytes:
    return hashlib.sha256(identifier.encode()).digest()


async def _observe(
    conn: AsyncConnection,
    tenant_id: str,
    organization_id: str,
    e: RegistryEntity,
    outcome: str,
    *,
    root_id: str | None = None,
    officer: str | None = None,
) -> None:
    await conn.execute(
        text("""
            INSERT INTO org.registry_observation (observation_id, tenant_id, organization_id,
                root_assertion_id, source_id, source_type, namespace, identifier, source_status,
                source_revision, officer_key, outcome, observed_at)
            VALUES (:id, :t, :o, :r, :src, :st, :ns, :ident, :status, :rev, :off, :outcome, :at)
        """),
        {
            "id": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "o": uuid.UUID(organization_id),
            "r": uuid.UUID(root_id) if root_id else None,
            "src": e.source_id,
            "st": e.source_type.value,
            "ns": e.namespace,
            "ident": e.identifier,
            "status": e.source_status[:64],
            "rev": e.source_revision[:256],
            "off": officer,
            "outcome": outcome,
            "at": e.observed_at,
        },
    )


async def _organization(
    conn: AsyncConnection, tenant_id: str, e: RegistryEntity, now: datetime
) -> str:
    """The canonical organization for this registered entity (one per tenant per registration)."""
    row = (
        await conn.execute(
            text(
                "SELECT organization_id FROM org.organization WHERE jurisdiction = :j "
                "AND registration_namespace = :ns AND registration_id_hash = :h"
            ),
            {"j": e.jurisdiction, "ns": e.namespace, "h": _hash(e.identifier)},
        )
    ).first()
    if row is not None:
        org = str(row.organization_id)
        await conn.execute(
            text(
                "UPDATE org.organization SET legal_name = :n, verification_status = 'VERIFIED' "
                "WHERE organization_id = :o"
            ),
            {"n": e.legal_name or "unknown", "o": uuid.UUID(org)},
        )
        return org
    org = str(uuid7())
    await root_store.create_organization(
        conn,
        organization_id=org,
        tenant_id=tenant_id,
        legal_name=e.legal_name or "unknown",
        jurisdiction=e.jurisdiction,
        valid_from=now,
    )
    await conn.execute(
        text(
            "UPDATE org.organization SET registration_namespace = :ns, registration_id_hash = :h, "
            "verification_status = 'VERIFIED' WHERE organization_id = :o"
        ),
        {"ns": e.namespace, "h": _hash(e.identifier), "o": uuid.UUID(org)},
    )
    await emit(
        conn,
        name="organization.resolved",
        tenant_id=tenant_id,
        aggregate_type="organization",
        aggregate_id=org,
        aggregate_version=1,
        data={
            "organization_id": org,
            "source_id": e.source_id,
            "namespace": e.namespace,
            "jurisdiction": e.jurisdiction,
            "source_status": e.source_status,
        },
    )
    return org


async def establish_registry_root(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    adapter: RegistryAdapter,
    identifier: str,
    holder_principal_id: str,
    name_matches: NameMatcher,
    now: datetime,
    corroborate: Sequence[tuple[RegistryAdapter, str]] = (),
) -> EstablishedRoot:
    entity = await adapter.lookup(identifier, now)
    check_can_originate(entity)
    officer = None
    for o in representative_officers(entity, now.date()):
        if await name_matches(name_renderings(o.name)):
            officer = o
            break
    if officer is None:
        raise RegistryError(
            "ORGANIZATION_ROLE_UNVERIFIED",
            "No current official officer of this company matches your verified legal name.",
        )
    org = await _organization(conn, tenant_id, entity, now)
    for extra, ident in corroborate:  # e.g. GLEIF: same entity, its own provenance (§5)
        other = await extra.lookup(ident, now)
        if not other.active or other.jurisdiction not in ("", entity.jurisdiction):
            raise RegistryError(
                "ORGANIZATION_SOURCE_CONFLICT",
                f"{other.source_id} does not corroborate this entity ({other.source_status}).",
            )
        await _observe(conn, tenant_id, org, other, "CORROBORATED")
    root_id = str(uuid7())
    root = registry_root(
        entity,
        root_id=root_id,
        organization_id=org,
        holder_principal_id=holder_principal_id,
        now=now,
    )
    await root_store.save_root(conn, tenant_id, root)
    key = officer_key(officer.name)
    await _observe(conn, tenant_id, org, entity, "ESTABLISHED", root_id=root_id, officer=key)
    await conn.execute(
        text(
            "UPDATE org.organization SET authority_root_revision = authority_root_revision + 1 "
            "WHERE organization_id = :o"
        ),
        {"o": uuid.UUID(org)},
    )
    # One event at the root's initial row_version (0): later status changes use 1, 2, ...
    await emit(
        conn,
        name="authority_root.created",
        tenant_id=tenant_id,
        aggregate_type="authority_root",
        aggregate_id=root_id,
        aggregate_version=0,
        data={
            "root_assertion_id": root_id,
            "organization_id": org,
            "root_type": root.root_type.value,
            "assurance": root.assurance.name,
            "status": RootStatus.ACTIVE.value,
            "fresh_until": root.fresh_until.isoformat(),
        },
    )
    return EstablishedRoot(
        org,
        root_id,
        entity.source_id,
        entity.namespace,
        entity.identifier,
        entity.legal_name,
        entity.jurisdiction,
        root.fresh_until,
    )


async def refresh_registry_roots(
    conn: AsyncConnection,
    *,
    adapter_for: Callable[[str], RegistryAdapter],
    now: datetime,
    on_root_changed: RootChanged,
    horizon: float = 0.25,
) -> dict[str, str]:
    """Worker pass (BYPASSRLS): re-check registry roots within `horizon` of their freshness bound.

    Returns root id -> outcome (or the source error code; the root is then left to lapse)."""
    window = REGISTRY_FRESHNESS * horizon
    rows = (
        await conn.execute(
            text("""
                SELECT DISTINCT ON (o.root_assertion_id) o.root_assertion_id, o.tenant_id,
                    o.organization_id, o.namespace, o.identifier, o.officer_key, r.holder_principal_id
                FROM org.registry_observation o
                JOIN org.authority_root_assertion r ON r.root_assertion_id = o.root_assertion_id
                WHERE o.root_assertion_id IS NOT NULL AND r.status IN ('ACTIVE', 'STALE')
                  AND r.fresh_until < :due
                ORDER BY o.root_assertion_id, o.observed_at DESC
            """),
            {"due": now + window},
        )
    ).all()
    out: dict[str, str] = {}
    for r in rows:
        root_id, tenant = str(r.root_assertion_id), str(r.tenant_id)
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        try:
            entity = await adapter_for(r.namespace).lookup(r.identifier, now)
        except RegistryError as e:
            out[root_id] = e.code
            continue
        outcome = refresh_outcome(entity, r.officer_key, now.date())
        await _observe(
            conn,
            tenant,
            str(r.organization_id),
            entity,
            outcome.value,
            root_id=root_id,
            officer=r.officer_key,
        )
        if outcome is RefreshOutcome.FRESH:
            await conn.execute(
                text(
                    "UPDATE org.authority_root_assertion SET fresh_until = :f, "
                    "source_revision = :rev, status = 'ACTIVE', row_version = row_version + 1 "
                    "WHERE root_assertion_id = :r"
                ),
                {
                    "f": now + REGISTRY_FRESHNESS,
                    "rev": entity.source_revision,
                    "r": uuid.UUID(root_id),
                },
            )
            await emit(
                conn,
                name="organization.source_refreshed",
                tenant_id=tenant,
                aggregate_type="organization_source",
                aggregate_id=root_id,
                aggregate_version=int(now.timestamp()),
                data={"root_assertion_id": root_id, "source_id": entity.source_id},
            )
        else:
            kind = (
                OrgEvent.DISSOLUTION
                if outcome is RefreshOutcome.DISSOLVED
                else OrgEvent.OFFICER_CESSATION
            )
            await root_store.apply_source_event(
                conn,
                tenant,
                SourceEvent(
                    event_id=f"{entity.source_id}:{root_id}:{entity.source_revision or now.isoformat()}",
                    organization_id=str(r.organization_id),
                    kind=kind,
                    holder_principal_id=str(r.holder_principal_id),
                ),
            )
            await on_root_changed(conn, root_id)
        out[root_id] = outcome.value
    return out


async def establish_development_root(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    holder_principal_id: str,
    legal_name: str,
    now: datetime,
) -> EstablishedRoot:
    """LOCAL/TEST ONLY (the onboarding caller refuses other environments): a synthetic root that
    names itself as unverified, so a signed-up development tenant can be administered."""
    from zid_platform.modules.organization.domain.roots import (
        Assurance,
        Fact,
        RootAssertion,
        RootType,
    )

    org = str(uuid7())
    await root_store.create_organization(
        conn,
        organization_id=org,
        tenant_id=tenant_id,
        legal_name=legal_name,
        jurisdiction="ZZ",
        valid_from=now,
    )
    root_id = str(uuid7())
    await root_store.save_root(
        conn,
        tenant_id,
        RootAssertion(
            root_id=root_id,
            organization_id=org,
            holder_principal_id=holder_principal_id,
            root_type=RootType.REGISTRY,
            facts=frozenset({Fact.ENTITY_EXISTENCE, Fact.OFFICIAL_ROLE}),
            assurance=Assurance.AR3,
            status=RootStatus.ACTIVE,
            jurisdiction="ZZ",
            valid_from=now,
            valid_to=None,
            fresh_until=now + timedelta(days=365),
            source_revision="development-unverified",
        ),
    )
    return EstablishedRoot(
        org,
        root_id,
        "development",
        "DEV",
        "unverified",
        legal_name,
        "ZZ",
        now + timedelta(days=365),
    )


__all__ = [
    "EstablishedRoot",
    "NameMatcher",
    "establish_development_root",
    "establish_registry_root",
    "refresh_registry_roots",
]

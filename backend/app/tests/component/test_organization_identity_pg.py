"""Organization identity links, name history and source quarantine against PostgreSQL (ZID-ROOT-001).

ROOT-01: one canonical organization is reached through its company number, its LEI and its EUID, and a second
claim on an identifier is a conflict, never a merge. ROOT-03: a rename adds history and leaves the organization and
its roots alone. ROOT-25: a quarantined source stops producing roots and its active roots are suspended, while what
it observed stays as evidence.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.organization.domain.registry import (
    RegistryEntity,
    RegistryError,
    RegistryOfficer,
    SourceType,
)
from zid_platform.modules.organization.domain.roots import RootRequest, RootStatus, resolve_root
from zid_platform.modules.organization.infrastructure import root_store
from zid_platform.modules.organization.ports import (
    establish_registry_root,
    identifiers_of,
    legal_name_history,
    link_identifier,
    quarantine_source,
    refresh_registry_roots,
    release_source,
    resolve_organization,
    source_is_quarantined,
)

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
JANE = "DOE, Jane Mary"


def uid() -> str:
    return str(uuid.uuid4())


def lei(base: str) -> str:
    digits = "".join(str(int(c, 36)) for c in base + "00")
    return f"{base}{98 - int(digits) % 97:02d}"


class Fake:
    """A registry adapter that answers with one fixed entity."""

    def __init__(self, source_id: str, namespace: str, entity: RegistryEntity) -> None:
        self.source_id, self.namespace, self.entity = source_id, namespace, entity

    async def lookup(self, identifier: str, now: datetime) -> RegistryEntity:
        return self.entity


def company(
    number: str, name: str = "ACME WIDGETS LIMITED", source: str = "companies-house"
) -> Fake:
    return Fake(
        source,
        "GB-COH",
        RegistryEntity(
            source_id=source,
            source_type=SourceType.NATIONAL_REGISTER,
            namespace="GB-COH",
            identifier=number,
            jurisdiction="GB",
            legal_name=name,
            source_status="active",
            active=True,
            incorporated_on=date(2001, 2, 3),
            officers=(RegistryOfficer(JANE, "director", date(2015, 1, 1), None),),
            source_revision=f"rev-{name}",
            observed_at=NOW,
        ),
    )


def gleif(number: str, name: str = "ACME WIDGETS LIMITED") -> Fake:
    return Fake(
        "gleif",
        "LEI",
        RegistryEntity(
            source_id="gleif",
            source_type=SourceType.GLEIF,
            namespace="LEI",
            identifier=number,
            jurisdiction="GB",
            legal_name=name,
            source_status="ISSUED",
            active=True,
            incorporated_on=None,
            officers=(),
            source_revision="g1",
            observed_at=NOW,
        ),
    )


async def matches(_: tuple[str, ...]) -> bool:
    return True


def test_one_organization_is_reached_by_number_lei_and_euid_and_a_second_claim_is_a_conflict(
    rt_url: str,
) -> None:
    """ROOT-01: the company number, the LEI and the EUID resolve to one organization; claiming an identifier
    that belongs to another organization stops the establishment and merges nothing."""
    tenant, holder = uid(), uid()
    acme_lei, other_lei = lei("5493001KJTIIGC8Y1R"), lei("5493009999999999ZZ"[:18])

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            first = await establish_registry_root(
                c,
                tenant,
                adapter=company("01234567"),
                identifier="01234567",
                holder_principal_id=holder,
                name_matches=matches,
                now=NOW,
                corroborate=[(gleif(acme_lei), acme_lei)],
            )
            org = first.organization_id
            await link_identifier(
                c,
                tenant,
                org,
                namespace="EUID",
                identifier="GBCOH.01234567",
                source_id="brisinfo",
                now=NOW,
            )
            for ns, ident, jur in (
                ("GB-COH", "01234567", "GB"),
                ("LEI", acme_lei, ""),
                ("LEI", acme_lei.lower(), "ignored"),  # global: case and jurisdiction do not matter
                ("EUID", "gbcoh.01234567", ""),
            ):
                assert (
                    await resolve_organization(c, namespace=ns, identifier=ident, jurisdiction=jur)
                    == org
                )
            assert {ns for ns, _ in await identifiers_of(c, org)} == {"GB-COH", "LEI", "EUID"}
            # the same company again is the same organization, not a second one
            again = await establish_registry_root(
                c,
                tenant,
                adapter=company("01234567"),
                identifier="01234567",
                holder_principal_id=uid(),
                name_matches=matches,
                now=NOW,
            )
            assert again.organization_id == org
            # a national number only means something in its own jurisdiction
            assert (
                await resolve_organization(
                    c, namespace="GB-COH", identifier="01234567", jurisdiction="FR"
                )
                is None
            )
            # a different company claiming the same LEI is refused; nothing is merged or half-linked
            with pytest.raises(RegistryError) as e:
                await establish_registry_root(
                    c,
                    tenant,
                    adapter=company("02222222", "NORTHWIND TRADING LTD"),
                    identifier="02222222",
                    holder_principal_id=uid(),
                    name_matches=matches,
                    now=NOW,
                    corroborate=[(gleif(acme_lei, "NORTHWIND TRADING LTD"), acme_lei)],
                )
            assert e.value.code == "ORGANIZATION_IDENTIFIER_CONFLICT"
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert await resolve_organization(c, namespace="LEI", identifier=acme_lei) == org
            assert (
                await link_identifier(  # a repeat link by the owner is a no-op
                    c, tenant, org, namespace="LEI", identifier=acme_lei, source_id="gleif", now=NOW
                )
                is False
            )
            with pytest.raises(RegistryError) as e2:  # a malformed identifier never links
                await link_identifier(
                    c,
                    tenant,
                    org,
                    namespace="LEI",
                    identifier=other_lei[:-1] + "0",
                    source_id="gleif",
                    now=NOW,
                )
            assert e2.value.code == "ORGANIZATION_IDENTIFIER_INVALID"

    asyncio.run(run())


def test_a_rename_adds_history_and_leaves_the_organization_and_its_roots_alone(
    rt_url: str, owner_url: str
) -> None:
    """ROOT-03: the official name changes; the name history is append-only and the root is untouched."""
    tenant, holder = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            first = await establish_registry_root(
                c,
                tenant,
                adapter=company("01234567"),
                identifier="01234567",
                holder_principal_id=holder,
                name_matches=matches,
                now=NOW,
            )
            renamed = await establish_registry_root(
                c,
                tenant,
                adapter=company("01234567", "ACME WIDGETS HOLDINGS LIMITED"),
                identifier="01234567",
                holder_principal_id=holder,
                name_matches=matches,
                now=NOW + timedelta(days=30),
            )
            assert renamed.organization_id == first.organization_id  # the same organization
            history = await legal_name_history(c, first.organization_id)
            assert [h[0] for h in history] == [
                "ACME WIDGETS LIMITED",
                "ACME WIDGETS HOLDINGS LIMITED",
            ]
            assert (
                history[0][2] == NOW + timedelta(days=30) and history[1][2] is None
            )  # old closed, new open
            roots = {r.root_id: r for r in await root_store.load_roots(c, first.organization_id)}
            assert (
                roots[first.root_id].status is RootStatus.ACTIVE
            )  # the earlier root was not touched
            codes = resolve_root(
                RootRequest(first.organization_id, holder, "x", NOW + timedelta(hours=1), 1, "GB"),
                [roots[first.root_id]],
            ).codes
            # the earlier root still proves the entity and the role; only signing power is missing, as before
            assert "ROOT_ENTITY_UNVERIFIED" not in codes and "ROOT_ROLE_UNVERIFIED" not in codes
        async with engine(owner_url) as eng, eng.begin() as c:
            for sql in (
                "UPDATE org.organization_name SET legal_name = 'EDITED' WHERE tenant_id = :t",
                "DELETE FROM org.organization_name WHERE tenant_id = :t",
            ):
                with pytest.raises(DBAPIError):
                    async with c.begin_nested():
                        await c.execute(text(sql), {"t": uuid.UUID(tenant)})

    asyncio.run(run())


def test_a_quarantined_source_yields_no_roots_and_loses_its_active_ones_but_keeps_its_evidence(
    rt_url: str, owner_url: str
) -> None:
    """ROOT-25: quarantine suspends the roots a source established, blocks new ones, is skipped by the refresh
    worker and leaves the observations as evidence; another source is unaffected; release lets new roots form."""
    tenant, holder = uid(), uid()

    async def run() -> dict[str, Any]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            bad = await establish_registry_root(
                c,
                tenant,
                adapter=company("01234567"),
                identifier="01234567",
                holder_principal_id=holder,
                name_matches=matches,
                now=NOW,
            )
            good = await establish_registry_root(
                c,
                tenant,
                adapter=company("02222222", "NORTHWIND TRADING LTD", source="other-register"),
                identifier="02222222",
                holder_principal_id=holder,
                name_matches=matches,
                now=NOW,
            )
            suspended = await quarantine_source(
                c, tenant, "companies-house", reason="SOURCE_COMPROMISED", now=NOW
            )
            assert suspended == [bad.root_id] and await source_is_quarantined(
                c, tenant, "companies-house"
            )
            statuses = {
                r.root_id: r.status
                for org in (bad.organization_id, good.organization_id)
                for r in await root_store.load_roots(c, org)
            }
            assert statuses[bad.root_id] is RootStatus.SUSPENDED
            assert statuses[good.root_id] is RootStatus.ACTIVE  # a different source is not affected
            roots = await root_store.load_roots(c, bad.organization_id)
            request = RootRequest(bad.organization_id, holder, "x", NOW, 2, "GB")
            assert not resolve_root(
                request, roots
            ).valid  # nothing can lean on the quarantined source
            with pytest.raises(RegistryError) as e:
                await establish_registry_root(
                    c,
                    tenant,
                    adapter=company("01234567"),
                    identifier="01234567",
                    holder_principal_id=uid(),
                    name_matches=matches,
                    now=NOW,
                )
            assert e.value.code == "ORGANIZATION_SOURCE_QUARANTINED"
            with pytest.raises(RegistryError):  # it cannot corroborate another entity either
                await establish_registry_root(
                    c,
                    tenant,
                    adapter=company("02222222", "NORTHWIND TRADING LTD", source="other-register"),
                    identifier="02222222",
                    holder_principal_id=uid(),
                    name_matches=matches,
                    now=NOW,
                    corroborate=[(company("01234567"), "01234567")],
                )
        async with engine(owner_url) as eng, eng.begin() as c:  # the worker role sees every tenant

            class Down:
                async def lookup(self, identifier: str, now: datetime) -> RegistryEntity:
                    raise RegistryError("SOURCE_DOWN")  # concludes nothing; mutates nothing

            def adapter_for(namespace: str) -> Any:
                return Down()

            async def noop(_: Any, __: str) -> None:
                return None

            outcome = await refresh_registry_roots(
                c, adapter_for=adapter_for, now=NOW + timedelta(days=2), on_root_changed=noop
            )
            observed = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM org.registry_observation "
                        "WHERE tenant_id = :t AND root_assertion_id = :r"
                    ),
                    {"t": uuid.UUID(tenant), "r": uuid.UUID(bad.root_id)},
                )
            ).scalar_one()
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert await release_source(c, tenant, "companies-house", now=NOW)
            assert (
                await release_source(c, tenant, "companies-house", now=NOW) is False
            )  # nothing left to release
            fresh = await establish_registry_root(
                c,
                tenant,
                adapter=company("01234567"),
                identifier="01234567",
                holder_principal_id=holder,
                name_matches=matches,
                now=NOW + timedelta(days=3),
            )
            states = {
                r.root_id: r.status for r in await root_store.load_roots(c, bad.organization_id)
            }
        return {
            "observed": observed,
            "states": states,
            "bad": bad.root_id,
            "fresh": fresh.root_id,
            "good": good.root_id,
            "outcome": outcome,
        }

    result = asyncio.run(run())
    assert (
        result["bad"] not in result["outcome"]
    )  # the worker did not consult the quarantined source
    assert (
        result["outcome"].get(result["good"]) == "SOURCE_DOWN"
    )  # but it did consult the healthy one
    assert result["observed"] >= 1  # historic evidence kept
    assert result["states"][result["bad"]] is RootStatus.SUSPENDED  # release does not revive it
    assert (
        result["states"][result["fresh"]] is RootStatus.ACTIVE
    )  # a new root forms from the released source

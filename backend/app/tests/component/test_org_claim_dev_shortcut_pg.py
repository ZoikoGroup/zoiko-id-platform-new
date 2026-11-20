"""LOCAL/TEST-ONLY company claim shortcut, establishing the organization (ZID_ORG_CLAIM_DEV_AUTOVERIFY).

With the shortcut on and the claimant's identity complete, the organization is established with no register lookup, as a `development-unverified` root,
the workspace is named after what the applicant typed, and the claimant's pending OWNER is activated. With it off, nothing changes: the claim must be
VERIFIED by all four checks, as before.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_org_claim_establish_pg import sign_up
from zid_db import tenant_transaction
from zid_platform.modules.onboarding.application import claims
from zid_platform.modules.onboarding.application import organization as org
from zid_platform.modules.onboarding.domain.gates import OnboardingError
from zid_platform.modules.organization import ports as org_ports
from zid_platform.modules.tenant import ports as tenant_ports
from zid_platform.protected import catalog

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _reset() -> Any:
    claims.configure_dev_shortcut(False, "test")
    yield
    claims.configure_dev_shortcut(False, "test")


def patch(monkeypatch: pytest.MonkeyPatch, requested_by: str, *, identity: bool) -> None:
    from zid_platform.modules.organization.application.kyb import CaseView
    from zid_platform.modules.organization.domain.kyb import KybStatus

    async def case(conn: Any, tenant_id: str, case_id: str, *, now: datetime) -> Any:
        # the real case type, not a stand-in: a field the code reads must exist on it
        return CaseView(
            case_id=case_id, status=KybStatus.UNAVAILABLE, review_reason=None, reasons=(), version=1, evidence_hash=None,
            expiry_at=None, jurisdiction="GB", registration_number="00123456", legal_name="Acme Widgets Limited",
            requested_by=requested_by,
        )  # fmt: skip

    async def screening(*_a: Any, **_k: Any) -> list[Any]:
        return []

    async def assurance(*_a: Any, **_k: Any) -> bool:
        return identity

    monkeypatch.setattr(claims, "kyb_get_case", case)
    monkeypatch.setattr(org_ports, "kyb_get_case", case)
    monkeypatch.setattr(claims, "case_for_kyb", screening)
    monkeypatch.setattr(claims, "person_assurance_current", assurance)


def test_a_claimant_with_a_complete_identity_is_established_without_any_company_check(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        tenant, owner = await sign_up(rt_url)
        patch(monkeypatch, owner, identity=True)
        claims.configure_dev_shortcut(True, "local")
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                result = await org.establish_from_verified_claim(
                    c, tenant, str(uuid.uuid4()), adapters={}, catalog=catalog(), now=NOW
                )  # no register adapter at all: nothing is looked up
                assert result.root is not None and result.root.legal_name == "Acme Widgets Limited"
            async with tenant_transaction(eng, ctx(tenant)) as c:
                after = await tenant_ports.load_tenant(c, tenant)
                assert (
                    after.display_name == "Acme Widgets Limited"
                    and after.organization_id == result.root.organization_id
                )
                assert any(
                    a.role.value == "OWNER" and a.status.value == "ACTIVE"
                    for a in await tenant_ports.live_roles(c, owner)
                )
                row = (
                    await c.execute(
                        text(
                            "SELECT jurisdiction, source_revision FROM org.authority_root_assertion WHERE tenant_id = :t"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).one()
                assert (row.jurisdiction, row.source_revision) == (
                    "ZZ",
                    "development-unverified",
                )  # the root says what it is, never a register

    asyncio.run(run())


@pytest.mark.parametrize("case", ["switch_off", "identity_not_complete"])
def test_without_the_switch_or_without_a_complete_identity_nothing_is_established(
    rt_url: str, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    async def run() -> None:
        tenant, owner = await sign_up(rt_url)
        patch(monkeypatch, owner, identity=(case != "identity_not_complete"))
        claims.configure_dev_shortcut(case != "switch_off", "local")
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(OnboardingError) as err:
                await org.establish_from_verified_claim(
                    c, tenant, str(uuid.uuid4()), adapters={}, catalog=catalog(), now=NOW
                )
            assert err.value.code == "CLAIM_NOT_VERIFIED"
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert not (await tenant_ports.load_tenant(c, tenant)).organization_id

    asyncio.run(run())


def test_the_worker_creates_the_organization_by_itself_once_the_identity_check_is_complete(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody opens a page or presses a button: a worker pass establishes the organization for a claim that reads as verified, once."""

    async def run() -> None:
        tenant, owner = await sign_up(rt_url)
        claim = str(uuid.uuid4())
        patch(monkeypatch, owner, identity=True)

        async def open_claims(conn: Any) -> list[tuple[str, str]]:
            return [(tenant, claim)]

        monkeypatch.setattr(claims, "kyb_open_claims", open_claims)
        claims.configure_dev_shortcut(True, "local")
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                first = await org.auto_establish_pass(c, NOW, catalog=catalog())
            assert first == {tenant: 1}
            async with tenant_transaction(eng, ctx(tenant)) as c:
                after = await tenant_ports.load_tenant(c, tenant)
                assert after.organization_id and after.display_name == "Acme Widgets Limited"
                assert any(
                    a.role.value == "OWNER" and a.status.value == "ACTIVE"
                    for a in await tenant_ports.live_roles(c, owner)
                )
            async with eng.begin() as c:
                assert (
                    await org.auto_establish_pass(c, NOW, catalog=catalog()) == {}
                )  # already established: nothing more to do

    asyncio.run(run())


@pytest.mark.parametrize("case", ["switch_off", "identity_not_complete"])
def test_the_worker_does_nothing_without_the_switch_or_a_complete_identity(
    rt_url: str, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    async def run() -> None:
        tenant, owner = await sign_up(rt_url)
        patch(monkeypatch, owner, identity=(case != "identity_not_complete"))

        async def open_claims(conn: Any) -> list[tuple[str, str]]:
            return [(tenant, str(uuid.uuid4()))]

        monkeypatch.setattr(claims, "kyb_open_claims", open_claims)
        claims.configure_dev_shortcut(case != "switch_off", "local")
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                assert await org.auto_establish_pass(c, NOW, catalog=catalog()) == {}
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert not (await tenant_ports.load_tenant(c, tenant)).organization_id

    asyncio.run(run())


@pytest.mark.parametrize("reading", [True, False])
def test_the_worker_creates_the_organization_for_a_claim_verified_from_a_certificate_reading(
    rt_url: str, monkeypatch: pytest.MonkeyPatch, reading: bool
) -> None:
    """CR-136: with the development shortcut OFF, a claim whose company check rests on a stored certificate reading and whose identity is complete is established by the worker, with
    no page and no reviewer; without such a reading nothing is established."""

    async def run() -> None:
        tenant, owner = await sign_up(rt_url)
        claim = str(uuid.uuid4())
        patch(monkeypatch, owner, identity=True)

        async def has_reading(*_a: Any, **_k: Any) -> bool:
            return reading

        async def open_claims(conn: Any) -> list[tuple[str, str]]:
            return [(tenant, claim)]

        monkeypatch.setattr(claims.proof_service, "has_consistent_reading", has_reading)
        monkeypatch.setattr(claims, "kyb_open_claims", open_claims)
        claims.configure_dev_shortcut(False, "test")
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                done = await org.auto_establish_pass(c, NOW, catalog=catalog())
            assert done == ({tenant: 1} if reading else {})
            async with tenant_transaction(eng, ctx(tenant)) as c:
                after = await tenant_ports.load_tenant(c, tenant)
                assert bool(after.organization_id) is reading
                if reading:
                    row = (
                        await c.execute(
                            text("SELECT source_revision FROM org.authority_root_assertion WHERE tenant_id = :t"),
                            {"t": uuid.UUID(tenant)},
                        )
                    ).one()
                    assert row.source_revision.startswith("certificate-ocr:")

    asyncio.run(run())


def test_a_connection_that_bypasses_row_level_security_never_activates_another_companys_owner(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CR-136 bug: the worker bypasses row-level security, and the OWNER lookup had no company filter, so it activated the first pending OWNER of ANY company. Two companies sign up, the older
    one is not being established; the worker pass over the newer one must activate the newer one's owner only."""

    async def run() -> None:
        older, older_owner = await sign_up(rt_url)
        newer, newer_owner = await sign_up(rt_url)
        claim = str(uuid.uuid4())
        patch(monkeypatch, newer_owner, identity=True)

        async def open_claims(conn: Any) -> list[tuple[str, str]]:
            return [(newer, claim)]

        monkeypatch.setattr(claims, "kyb_open_claims", open_claims)
        claims.configure_dev_shortcut(True, "local")
        async with engine(owner_url) as eng:  # the owner role bypasses row-level security, as the worker's does
            async with eng.begin() as c:
                assert await org.auto_establish_pass(c, NOW, catalog=catalog()) == {newer: 1}
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(newer)) as c:
                assert any(a.role.value == "OWNER" and a.status.value == "ACTIVE" for a in await tenant_ports.live_roles(c, newer_owner))
            async with tenant_transaction(eng, ctx(older)) as c:
                still = [a for a in await tenant_ports.live_roles(c, older_owner) if a.role.value == "OWNER"]
                assert still and all(a.status.value == "PENDING_VERIFICATION" for a in still), "another company's OWNER must stay pending"

    asyncio.run(run())

"""A VERIFIED company claim establishes the organization (ZID-UX-WF-013B to 13C entry contract).

Sign-up creates a personal workspace whose bootstrap OWNER is pending and whose workspace carries no company. Only when all four checks of a claim are complete does the
claim create the organization from the register, name the workspace after the register's legal name (never the applicant's typed name) and activate the claimant as owner.
A claim that is not VERIFIED, a source that cannot be read, or another person's pending role never establishes anything, and a repeat changes nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.onboarding.application import claims
from zid_platform.modules.onboarding.application import organization as org
from zid_platform.modules.onboarding.domain.gates import OnboardingError
from zid_platform.modules.organization import ports as org_ports
from zid_platform.modules.organization.domain.kyb import (
    Capabilities,
    CompanyFacts,
    KybError,
    SourceKind,
)
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.tenant import ports as tenant_ports
from zid_platform.modules.tenant.application import lifecycle
from zid_platform.protected import catalog

NOW = datetime.now(UTC)


def uid() -> str:
    return str(uuid.uuid4())


class Register:
    adapter_id, kind = "companies-house", SourceKind.OFFICIAL_REGISTER
    capabilities = Capabilities(frozenset({"GB"}))

    def __init__(self) -> None:
        self.down = False
        self.active = True

    async def get(self, number: str, country: str, now: datetime) -> CompanyFacts | None:
        if self.down:
            raise KybError("SOURCE_UNAVAILABLE")
        return CompanyFacts(
            self.adapter_id, self.kind, "GB", "companies-house", number, "Acme Widgets Limited", "ltd", "active" if self.active else "dissolved", self.active,
            "1 High St, London", f"https://x/{number}", now, "c" * 64,
        )  # fmt: skip


async def sign_up(rt_url: str) -> tuple[str, str]:
    """A person-first sign-up as the server makes it: an active principal, a personal workspace, a pending bootstrap OWNER."""
    signup, creator = uid(), uid()
    async with engine(rt_url) as eng, tenant_transaction(eng, ctx(signup)) as c:
        await create_principal(
            c, Principal(creator, signup, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
        )
        r = await lifecycle.bootstrap_tenant(
            c, creating_tenant_id=signup, creator_principal_id=creator, display_name="Personal workspace", tenant_slug=f"u-{uid()[:12]}",
            deployment_mode="O0", home_cell_id="uk-1", console=None,
        )  # fmt: skip
    return r.tenant.tenant_id, r.owner_principal_id


def verified(monkeypatch: pytest.MonkeyPatch, requested_by: str, outcome: str = "VERIFIED") -> None:
    async def status(conn: Any, tenant_id: str, case_id: str, now: datetime) -> dict[str, Any]:
        return {"outcome": outcome, "checks": {}}

    async def case(conn: Any, tenant_id: str, case_id: str, *, now: datetime) -> Any:
        return SimpleNamespace(
            requested_by=requested_by, jurisdiction="GB", registration_number="00123456"
        )

    monkeypatch.setattr(claims, "status", status)
    monkeypatch.setattr(org_ports, "kyb_get_case", case)


def test_a_verified_claim_creates_the_organization_names_the_workspace_and_activates_the_owner(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        tenant, owner = await sign_up(rt_url)
        verified(monkeypatch, owner)
        register = Register()
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                before = await tenant_ports.load_tenant(c, tenant)
                assert before.display_name == "Personal workspace" and not before.organization_id
                assert (
                    await c.execute(
                        text("SELECT count(*) FROM org.organization WHERE tenant_id = :t"),
                        {"t": uuid.UUID(tenant)},
                    )
                ).scalar_one() == 0
            async with tenant_transaction(eng, ctx(tenant)) as c:
                result = await org.establish_from_verified_claim(
                    c,
                    tenant,
                    uid(),
                    adapters={"companies-house": register},
                    catalog=catalog(),
                    now=NOW,
                )
                assert result.root is not None and result.root.legal_name == "Acme Widgets Limited"
            async with tenant_transaction(eng, ctx(tenant)) as c:
                after = await tenant_ports.load_tenant(c, tenant)
                assert (
                    after.display_name == "Acme Widgets Limited"
                    and after.organization_id == result.root.organization_id
                )
                assert after.status.value == "CONFIGURING"
                roles = await tenant_ports.live_roles(c, owner)
                assert any(a.role.value == "OWNER" and a.status.value == "ACTIVE" for a in roles)
            async with tenant_transaction(
                eng, ctx(tenant)
            ) as c:  # a repeat answers as it stands and creates nothing more
                again = await org.establish_from_verified_claim(
                    c,
                    tenant,
                    uid(),
                    adapters={"companies-house": register},
                    catalog=catalog(),
                    now=NOW,
                )
                assert again.root is None
                assert (
                    await c.execute(
                        text("SELECT count(*) FROM org.organization WHERE tenant_id = :t"),
                        {"t": uuid.UUID(tenant)},
                    )
                ).scalar_one() == 1

    asyncio.run(run())


@pytest.mark.parametrize("case", ["not_verified", "source_down", "dissolved", "someone_else"])
def test_nothing_is_established_unless_every_check_is_complete_and_the_source_answers(
    rt_url: str, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    async def run() -> None:
        tenant, owner = await sign_up(rt_url)
        verified(
            monkeypatch,
            uid() if case == "someone_else" else owner,
            "IN_REVIEW" if case == "not_verified" else "VERIFIED",
        )
        register = Register()
        register.down = case == "source_down"
        register.active = case != "dissolved"
        wanted = {
            "not_verified": "CLAIM_NOT_VERIFIED",
            "source_down": "SOURCE_UNAVAILABLE",
            "dissolved": "SOURCE_UNAVAILABLE",
            "someone_else": "TENANT_OWNER_UNAUTHORIZED",
        }[case]
        async with engine(rt_url) as eng:
            with pytest.raises(OnboardingError) as e:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    await org.establish_from_verified_claim(
                        c,
                        tenant,
                        uid(),
                        adapters={"companies-house": register},
                        catalog=catalog(),
                        now=NOW,
                    )
            assert e.value.code == wanted
            async with tenant_transaction(eng, ctx(tenant)) as c:
                still = await tenant_ports.load_tenant(c, tenant)
                assert still.display_name == "Personal workspace" and not still.organization_id
                assert any(
                    a.status.value == "PENDING_VERIFICATION"
                    for a in await tenant_ports.live_roles(c, owner)
                )

    asyncio.run(run())


def test_a_claim_whose_company_check_came_from_a_certificate_reading_establishes_a_lower_assurance_root(
    rt_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CR-132: no register record exists, so the organization is named by the applicant's legal name and rooted at AR2 (institutional KYB), never as a register root."""

    async def run() -> None:
        tenant, owner = await sign_up(rt_url)

        async def status(conn: Any, tenant_id: str, case_id: str, now: datetime) -> dict[str, Any]:
            return {"outcome": "VERIFIED", "checks": {}, "company_basis": "DOCUMENT_OCR"}

        async def case(conn: Any, tenant_id: str, case_id: str, *, now: datetime) -> Any:
            return SimpleNamespace(requested_by=owner, jurisdiction="GB", registration_number="00123456", legal_name="Acme Widgets Limited")

        monkeypatch.setattr(claims, "status", status)
        monkeypatch.setattr(org_ports, "kyb_get_case", case)
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                result = await org.establish_from_verified_claim(c, tenant, uid(), adapters={}, catalog=catalog(), now=NOW)
                assert result.root is not None and result.root.legal_name == "Acme Widgets Limited"
                assert (result.root.source_id, result.root.namespace) == ("certificate-ocr", "CERTIFICATE_OCR")
            async with tenant_transaction(eng, ctx(tenant)) as c:
                roles = await tenant_ports.live_roles(c, owner)
                assert any(a.role.value == "OWNER" and a.status.value == "ACTIVE" for a in roles)
                row = (
                    await c.execute(
                        text("SELECT root_type::text AS root_type, assurance_class::text AS assurance FROM org.authority_root_assertion WHERE tenant_id = :t"), {"t": uuid.UUID(tenant)}
                    )
                ).first()
                assert row is not None and row.root_type == "INSTITUTIONAL_KYB" and row.assurance != "AR3"

    asyncio.run(run())

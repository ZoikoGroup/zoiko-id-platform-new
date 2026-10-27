"""A residency profile is placed on the cell that serves the company (ZID-TENANCY-001 §13; TEN-10)."""

from __future__ import annotations

import asyncio

import pytest
from conftest import ctx, engine
from onboarding_fixture import onboard
from zid_db import tenant_transaction
from zid_platform.modules.tenant.application import residency
from zid_platform.modules.tenant.domain.residency import ResidencyError


def test_the_profile_cannot_point_at_a_cell_that_does_not_serve_the_company(rt_url: str) -> None:
    """Pointing the profile at another cell would make every request for the company fail as 'not the home cell'.
    Moving a company is a migration, so the profile edit refuses it and the company's own cell is accepted."""
    o = onboard(rt_url)  # the company's home cell is uk-1

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            args = {
                "profile": "SRP-1", "recovery_cells": [], "prohibited_cells": [], "operator_juris": [],
                "key_profile": "PLATFORM_MANAGED", "jurisdiction_pack_id": "eu-gdpr", "approval_ref": "LEGAL-1", "actor": o.owner,
            }  # fmt: skip
            with pytest.raises(ResidencyError, match="PLACEMENT_MOVE_NOT_ALLOWED"):
                await residency.set_residency(c, o.tenant, home_cell_id="eu-1", **args)  # type: ignore[arg-type]
            r = await residency.set_residency(
                c, o.tenant, home_cell_id="uk-1", **{**args, "jurisdiction_pack_id": "uk-gdpr"}
            )  # type: ignore[arg-type]
            assert r.home_cell_id == "uk-1"

    asyncio.run(run())

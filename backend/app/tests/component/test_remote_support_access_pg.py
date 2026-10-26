"""Support staff outside a company's region need an approved transfer to read its data (ZID-PRIV-001 PRIV-25).

Reading customer data from a country is a cross-border transfer into it. Inside the company's region it needs nothing
more than the usual just-in-time grant; from outside it also needs an active, independently approved transfer rule for
REMOTE_SUPPORT, and with none the request is refused before any grant exists.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from conftest import ctx, engine
from zid_db import tenant_transaction
from zid_platform.modules.tenant.application import residency
from zid_platform.modules.tenant.domain.residency import ResidencyError


def uid() -> str:
    return str(uuid.uuid4())


def test_foreign_support_access_needs_an_approved_transfer_rule(rt_url: str) -> None:
    tenant, admin, approver = uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            # no residency commitment yet: nothing regional is promised, so nothing extra is asked
            await residency.authorize_remote_access(c, tenant, "US")
            await residency.set_residency(
                c, tenant, profile="SRP-1", home_cell_id="uk-1", recovery_cells=[], prohibited_cells=[],
                operator_juris=[], key_profile="PLATFORM_MANAGED", jurisdiction_pack_id="uk-gdpr",
                approval_ref="LEGAL-1", actor=admin,
            )  # fmt: skip
            # inside the home region: only the usual JIT controls apply
            await residency.authorize_remote_access(c, tenant, "GB")
            # outside it, with no approved route: refused, whether or not we have a cell there
            for where in ("US", "DE"):
                with pytest.raises(ResidencyError, match="REMOTE_ACCESS_TRANSFER_REQUIRED"):
                    await residency.authorize_remote_access(c, tenant, where)
            # an independently approved rule for REMOTE_SUPPORT to a cell in Germany opens exactly that route
            await residency.add_rule(
                c, tenant, data_class="DC2", destination_cell_id="eu-1", purpose_code=residency.REMOTE_SUPPORT,
                mechanism="uk_idta", field_allowlist=["ticket_id"], approval_ref="LEGAL-2", created_by=admin,
                approved_by=approver, valid_to=None,
            )  # fmt: skip
            await residency.authorize_remote_access(c, tenant, "DE")
            with pytest.raises(ResidencyError, match="REMOTE_ACCESS_TRANSFER_REQUIRED"):
                await residency.authorize_remote_access(
                    c, tenant, "US"
                )  # the rule names Germany only

    asyncio.run(run())


def test_a_rule_for_another_purpose_does_not_open_support_access(rt_url: str) -> None:
    tenant, admin, approver = uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await residency.set_residency(
                c, tenant, profile="SRP-1", home_cell_id="uk-1", recovery_cells=[], prohibited_cells=[],
                operator_juris=[], key_profile="PLATFORM_MANAGED", jurisdiction_pack_id="uk-gdpr",
                approval_ref="LEGAL-1", actor=admin,
            )  # fmt: skip
            await residency.add_rule(
                c, tenant, data_class="DC2", destination_cell_id="eu-1", purpose_code="ANALYTICS",
                mechanism="uk_idta", field_allowlist=["ticket_id"], approval_ref="LEGAL-2", created_by=admin,
                approved_by=approver, valid_to=None,
            )  # fmt: skip
            with pytest.raises(ResidencyError, match="REMOTE_ACCESS_TRANSFER_REQUIRED"):
                await residency.authorize_remote_access(c, tenant, "DE")

    asyncio.run(run())

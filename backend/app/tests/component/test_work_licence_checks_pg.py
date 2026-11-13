"""Public pricing page acceptance rows P12 and P13 (ZID-UX-PRICE-004 section 08), proved against a real PostgreSQL.

P12  a professional licence check shows the regulator, the jurisdiction and the expiry scope, and a lapsed licence is not active
P13  an employment (WORK) check needs a consent or legal basis, effective source rights, and discloses only what the employer permits
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from conftest import ctx, engine
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import claim_checks as checks
from zid_platform.modules.credential.domain.sources import SourceError
from zid_platform.modules.credential.infrastructure.registries import (
    issuer_registry,
    source_contracts,
)
from zid_platform.modules.verification.ports import pack_registry

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
JURIS = {"country": "US", "subdivision": "CA"}


def uid() -> str:
    return str(uuid.uuid4())


def licence(**over: str) -> dict[str, str]:
    return {
        "license_ref": "L-100",
        "regulator": "Sample State Licensing Board",
        "activity_scope": "statutory audit",
        "effective_until": "2027-06-30",
        **over,
    }


EMPLOYMENT = {"employer_id": "acme-1", "start_date": "2015-01-01", "title": "Principal Engineer"}


async def run(c, tenant: str, kind: str, claimed: dict[str, str], gate: checks.Gate | None = None):
    return await checks.run_check(
        c,
        tenant_id=tenant,
        subject_principal_id=uid(),
        kind=kind,
        claimed=claimed,
        jurisdiction=JURIS,
        profession=None,
        packs=pack_registry(),
        source=checks.SimulatorSource("test"),
        now=NOW,
        environment="test",
        gate=gate,
    )


def gate(**over) -> checks.Gate:
    args = {
        "registry": issuer_registry(),
        "contracts": source_contracts(),
        "processing_region": "local",
        **over,
    }
    return checks.Gate(**args)


def test_p12_a_licence_result_names_the_regulator_the_jurisdiction_the_scope_and_the_expiry(
    rt_url: str,
) -> None:
    tenant = uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await run(c, tenant, "PROFESSIONAL_LICENSE", licence())
            body = r.extras
            assert r.assertion.decision.value == "VERIFIED"
            assert body["regulator"] == "Sample State Licensing Board"
            assert r.assertion.jurisdiction == JURIS
            assert body["permitted_services"] == ["statutory audit"]
            assert body["expires_at"].startswith("2027-06-30")

    asyncio.run(go())


def test_p12_a_licence_that_has_lapsed_or_is_not_in_good_standing_is_not_active(
    rt_url: str,
) -> None:
    tenant = uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            lapsed = await run(
                c, tenant, "PROFESSIONAL_LICENSE", licence(effective_until="2026-01-31")
            )
            assert lapsed.assertion.credential_status.value == "EXPIRED"
            assert (
                "LICENSE_PERIOD_ENDED" in lapsed.assertion.reason_codes
                or lapsed.assertion.decision.value != "VERIFIED"
            )
            suspended = await run(
                c, tenant, "PROFESSIONAL_LICENSE", licence(regulator="SIM-SUSPENDED")
            )
            assert suspended.assertion.credential_status.value == "SUSPENDED"
            assert suspended.assertion.decision.value == "NOT_VERIFIED"

    asyncio.run(go())


def test_p13_an_employment_check_without_a_consent_or_legal_basis_is_refused_and_nothing_is_asked(
    rt_url: str,
) -> None:
    tenant = uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(SourceError) as e:
                await run(c, tenant, "EMPLOYMENT_ASSERTION", dict(EMPLOYMENT), gate())
            assert e.value.code == "EMPLOYMENT_NO_BASIS"
            ok = await run(
                c, tenant, "EMPLOYMENT_ASSERTION", dict(EMPLOYMENT), gate(consent_ref="consent-9")
            )
            assert ok.assertion.decision.value == "VERIFIED"
            on_basis = await run(
                c,
                tenant,
                "EMPLOYMENT_ASSERTION",
                dict(EMPLOYMENT),
                gate(legal_basis_ref="statute-9"),
            )
            assert on_basis.assertion.decision.value == "VERIFIED"

    asyncio.run(go())


def test_p13_an_employment_check_needs_source_rights_in_force_and_keeps_only_what_the_employer_permits(
    rt_url: str,
) -> None:
    tenant = uid()
    expired = [replace(k, valid_until=date(2026, 1, 1)) for k in source_contracts()]

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(SourceError) as e:
                await run(
                    c,
                    tenant,
                    "EMPLOYMENT_ASSERTION",
                    dict(EMPLOYMENT),
                    gate(consent_ref="c", contracts=expired),
                )
            assert e.value.code in {"SOURCE_CONTRACT_NOT_IN_FORCE", "SOURCE_NO_CONTRACT"}
            limited = await run(
                c,
                tenant,
                "EMPLOYMENT_ASSERTION",
                dict(EMPLOYMENT),
                gate(
                    consent_ref="c",
                    employment_permitted_fields=frozenset({"employer_id", "start_date"}),
                ),
            )
            assert limited.extras["employment"] == {
                "employer_id": "acme-1",
                "start_date": "2015-01-01",
            }

    asyncio.run(go())

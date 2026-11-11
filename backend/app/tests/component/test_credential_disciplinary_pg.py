"""Disciplinary checks (GID-046, GID-057) and employment disclosure limits (GID-044), CR-069.

GLB-18  a sensitive check without a basis, an authorized user or a reviewer is refused and nothing is asked
GLB-22  a same-name match is a REVIEW, never an adverse decision
GLB-13  employment: only the fields the employer permits are kept
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import claim_checks as checks
from zid_platform.modules.credential.application import trust_service as svc
from zid_platform.modules.credential.domain import eligibility as el
from zid_platform.modules.credential.domain.claims import ClaimError
from zid_platform.modules.credential.domain.sources import SourceError
from zid_platform.modules.credential.infrastructure.registries import (
    issuer_registry,
    policies,
    source_contracts,
)
from zid_platform.modules.verification.ports import pack_registry

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
JURIS = {"country": "US", "subdivision": "CA"}
BOARD = "Sample State Licensing Board"


def uid() -> str:
    return str(uuid.uuid4())


def gate(**over) -> checks.Gate:
    args = {
        "registry": issuer_registry(),
        "contracts": source_contracts(),
        "processing_region": "local",
        "consent_ref": "consent-1",
        "authorized_user": True,
        "reviewer_assigned": True,
        **over,
    }
    return checks.Gate(**args)


def source() -> checks.SimulatorSource:
    return checks.SimulatorSource("test", "simulator-disciplinary")


async def disciplinary(conn, tenant, subject, reference=None, regulator=BOARD, g=None, src=None):
    return await checks.run_disciplinary_check(
        conn,
        tenant_id=tenant,
        subject_principal_id=subject,
        regulator=regulator,
        reference=reference,
        jurisdiction=JURIS,
        packs=pack_registry(),
        source=src or source(),
        now=NOW,
        environment="test",
        gate=g or gate(),
    )


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"consent_ref": None}, "SENSITIVE_NO_BASIS"),
        ({"authorized_user": False}, "SENSITIVE_USER_NOT_AUTHORIZED"),
        ({"reviewer_assigned": False}, "SENSITIVE_NO_REVIEWER"),
    ],
)
def test_glb_18_a_sensitive_check_needs_a_basis_an_authorized_user_and_a_reviewer(
    rt_url: str, over: dict, code: str
) -> None:
    tenant, subject = uid(), uid()

    class Spy:
        source_id = "simulator-disciplinary"

        async def lookup(self, *a, **k):
            raise AssertionError("must not be asked")

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(SourceError) as e:
                await disciplinary(c, tenant, subject, g=gate(**over), src=Spy())
            assert e.value.code == code
            n = (await c.execute(text("SELECT count(*) FROM cred.claim_check"))).scalar_one()
            assert n == 0

    run(go())


def test_a_legal_basis_instead_of_consent_is_enough(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await disciplinary(
                c, tenant, subject, g=gate(consent_ref=None, legal_basis_ref="statute-9")
            )
            assert r.extras["disciplinary"] == "NO_ADVERSE_FINDING"

    run(go())


def test_the_regulator_must_be_a_registered_one_and_without_a_contract_nothing_is_asked(
    rt_url: str,
) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(ClaimError) as e:
                await disciplinary(c, tenant, subject, regulator="Some Random Website")
            assert e.value.code == "REGULATOR_NOT_TRUSTED"
            with pytest.raises(SourceError) as e:
                await disciplinary(c, tenant, subject, g=checks.Gate(registry=issuer_registry()))
            assert e.value.code == "SOURCE_NO_CONTRACT"
            with pytest.raises(
                SourceError
            ) as e:  # the ordinary simulator has no contract for findings
                await disciplinary(c, tenant, subject, src=checks.SimulatorSource("test"))
            assert e.value.code in {"SOURCE_NO_CONTRACT", "SOURCE_NOT_COVERED"}

    run(go())


@pytest.mark.parametrize(
    ("ref", "decision", "outcome"),
    [
        ("SIM-ADVERSE", "VERIFIED", "ADVERSE_FINDING"),
        ("SIM-APPEAL", "REVIEW", "ADVERSE_UNDER_APPEAL"),
        ("SIM-NOTFINAL", "REVIEW", "REVIEW"),
        ("SIM-SAMENAME", "REVIEW", "REVIEW"),  # GLB-22
        (None, "NOT_VERIFIED", "NO_ADVERSE_FINDING"),
        ("SIM-DOWN", "SOURCE_UNAVAILABLE", "SOURCE_UNAVAILABLE"),
    ],
)
def test_each_regulator_answer_gives_its_own_outcome(
    rt_url: str, ref, decision: str, outcome: str
) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await disciplinary(c, tenant, subject, reference=ref)
            assert r.assertion.decision.value == decision and r.extras["disciplinary"] == outcome
            if decision != "VERIFIED":
                assert r.assertion.primary_source_verified is False  # nothing adverse is confirmed

    run(go())


def test_an_adverse_finding_restricts_the_engagement_and_an_appeal_holds_it(rt_url: str) -> None:
    tenant, adverse, appeal, clean = uid(), uid(), uid(), uid()
    policy, rule = policies()[0][0], policies()[1][0]
    req = el.EngagementRequest("US", "CA", "LAW", "legal.advice")
    lic = {"license_ref": "L-1", "regulator": BOARD, "activity_scope": "legal.advice"}

    async def engage(c, subject):
        return await svc.evaluate_engagement(
            c,
            tenant_id=tenant,
            subject=subject,
            request=req,
            policy=policy,
            rule=rule,
            registry=issuer_registry(),
            now=NOW,
            consumer="zoikorum",
        )

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for s in (adverse, appeal, clean):
                await checks.run_check(
                    c,
                    tenant_id=tenant,
                    subject_principal_id=s,
                    kind="PROFESSIONAL_LICENSE",
                    claimed=lic,
                    jurisdiction=JURIS,
                    profession=None,
                    packs=pack_registry(),
                    source=checks.SimulatorSource("test"),
                    now=NOW,
                    environment="test",
                    gate=checks.Gate(registry=issuer_registry(), contracts=source_contracts()),
                )
            await disciplinary(c, tenant, adverse, "SIM-ADVERSE")
            await disciplinary(c, tenant, appeal, "SIM-APPEAL")
            await disciplinary(c, tenant, clean)
            a = await engage(c, adverse)
            assert (
                a.outcome.engagement is el.Engagement.BLOCKED
                and "RESTRICTION_ON_RECORD" in a.outcome.reasons
            )
            assert a.dimensions["RESTRICTIONS"] == "NOT_VERIFIED"
            p = await engage(c, appeal)
            assert (
                p.dimensions["RESTRICTIONS"] == "NOT_VERIFIED"
            )  # under appeal: a person decides, not a pass
            assert (
                "RESTRICTION_ON_RECORD" not in p.outcome.reasons
            )  # not final, so it does not restrict
            n = await engage(c, clean)
            assert (
                n.dimensions["RESTRICTIONS"] == "VERIFIED"
            )  # clean at this source, said for this source only

    run(go())


def test_employment_keeps_only_the_fields_the_employer_permits(rt_url: str) -> None:
    tenant = uid()
    claim = {
        "employer_id": "acme-1",
        "start_date": "2015-01-01",
        "title": "Principal Engineer",
        "contract_category": "FT",
    }

    async def employ(c, g):
        return await checks.run_check(
            c,
            tenant_id=tenant,
            subject_principal_id=uid(),
            kind="EMPLOYMENT_ASSERTION",
            claimed=claim,
            jurisdiction={"country": "US"},
            profession=None,
            packs=pack_registry(),
            source=checks.SimulatorSource("test"),
            now=NOW,
            environment="test",
            gate=g,
        )

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            limited = await employ(
                c, gate(employment_permitted_fields=frozenset({"employer_id", "start_date"}))
            )
            assert limited.extras["employment"] == {
                "employer_id": "acme-1",
                "start_date": "2015-01-01",
            }
            stored = (
                (
                    await c.execute(
                        text("SELECT assertion::text FROM cred.claim_check ORDER BY created_at")
                    )
                )
                .scalars()
                .all()
            )
            assert "Principal Engineer" not in stored[0] and "FT" not in stored[0]
            unlimited = await employ(
                c, gate()
            )  # no limit configured: nothing about the job is kept
            assert "employment" not in unlimited.extras

    run(go())

"""Governed erasure of a person's credential-check data (credential_0008, the credential privacy adapter).

The guards stay: an ordinary DELETE or UPDATE is still refused. `cred.erase_subject` is the one controlled path, it is scoped to the
caller's tenant and to one subject, it is idempotent, and it turns its bypass flag off again before it returns.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import case_service
from zid_platform.modules.credential.application import claim_checks as checks
from zid_platform.modules.credential.application import trust_service as svc
from zid_platform.modules.credential.infrastructure import privacy_adapter as adapter
from zid_platform.modules.credential.infrastructure.registries import (
    issuer_registry,
    policies,
    source_contracts,
)
from zid_platform.modules.privacy.application.service import registered_stores
from zid_platform.modules.verification.ports import pack_registry

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
TABLES = (
    "claim_check",
    "claim_monitor",
    "consent_grant",
    "eligibility_decision",
    "claim_appeal",
    "outreach_request",
    "verification_case",
    "verification_case_task",
)
LIC = {
    "license_ref": "L-1",
    "regulator": "Sample State Licensing Board",
    "activity_scope": "legal.advice",
}


def uid() -> str:
    return str(uuid.uuid4())


def run(coro):
    return asyncio.run(coro)


async def populate(c, tenant: str, subject: str) -> str:
    """One of everything about this subject: a check (and its monitor), a grant, a decision, an appeal, an outreach."""
    r = await checks.run_check(
        c,
        tenant_id=tenant,
        subject_principal_id=subject,
        kind="PROFESSIONAL_LICENSE",
        claimed=LIC,
        jurisdiction={"country": "US", "subdivision": "CA"},
        profession=None,
        packs=pack_registry(),
        source=checks.SimulatorSource("test"),
        now=NOW,
        environment="test",
        gate=checks.Gate(registry=issuer_registry(), contracts=source_contracts()),
    )
    await svc.create_grant(
        c,
        tenant_id=tenant,
        subject=subject,
        recipient="acme",
        consumer="zoikorum",
        purpose="P",
        fields=["decision"],
        days=5,
        parent_id=None,
        now=NOW,
    )
    await svc.evaluate_eligibility(
        c,
        tenant_id=tenant,
        subject=subject,
        policy=policies()[0][0],
        registry=issuer_registry(),
        now=NOW,
    )
    await svc.raise_appeal(
        c, tenant_id=tenant, check_id=r.check_id, raised_by=subject, reason="wrong", correction={}
    )
    await svc.open_outreach(c, tenant_id=tenant, issuer_id="sample-university", claim_type="ACADEMIC_AWARD", requested_by=uid(),
                            contacts=("registrar@sample-university.example",), days=5, now=NOW, subject=subject,
                            claim={"fields": {"institution": "Sample University", "program": "BSc Computing", "award_status": "CONFERRED"},
                                   "jurisdiction": {"country": "US"}, "profession": None})  # fmt: skip
    await case_service.open_case(
        c, tenant_id=tenant, subject=subject, purpose="X", country="US", subdivision=None, profile_id="p",
        profile_version="1", consent_ref=None, callback_endpoint_id=None, client_ref=None,
        checks=["identity", "license"], policy_packs=[], created_by=subject, created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )  # fmt: skip
    return r.check_id


async def counts(c, subject: str) -> dict[str, int]:
    s = uuid.UUID(subject)
    q = {
        "claim_check": "SELECT count(*) FROM cred.claim_check WHERE subject_principal_id = :s",
        "claim_monitor": "SELECT count(*) FROM cred.claim_monitor m JOIN cred.claim_check c USING (check_id) WHERE c.subject_principal_id = :s",
        "consent_grant": "SELECT count(*) FROM cred.consent_grant WHERE subject_principal_id = :s",
        "eligibility_decision": "SELECT count(*) FROM cred.eligibility_decision WHERE subject_principal_id = :s",
        "claim_appeal": "SELECT count(*) FROM cred.claim_appeal a JOIN cred.claim_check c USING (check_id) WHERE c.subject_principal_id = :s",
        "outreach_request": "SELECT count(*) FROM cred.outreach_request WHERE subject_principal_id = :s",
        "verification_case": "SELECT count(*) FROM cred.verification_case WHERE subject_principal_id = :s",
        "verification_case_task": "SELECT count(*) FROM cred.verification_case_task t JOIN cred.verification_case v USING (case_id) WHERE v.subject_principal_id = :s",
    }
    return {k: (await c.execute(text(v), {"s": s})).scalar_one() for k, v in q.items()}


def entry(tenant: str, subject: str):
    return SimpleNamespace(tenant_id=tenant, subject_principal_id=subject)


def test_erasing_one_subject_removes_everything_about_them_and_nothing_about_anyone_else(
    rt_url: str,
) -> None:
    tenant, a, b = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await populate(c, tenant, a)
            await populate(c, tenant, b)
            assert all(v >= 1 for v in (await counts(c, a)).values()), await counts(c, a)
            await adapter._erase(c, entry(tenant, a), "DELETE")
            assert set((await counts(c, a)).values()) == {0}
            assert all(
                v >= 1 for v in (await counts(c, b)).values()
            )  # the other person is untouched

    run(go())


def test_the_guards_still_refuse_an_ordinary_delete_or_update_before_and_after_an_erasure(
    rt_url: str,
) -> None:
    tenant, a, b = uid(), uid(), uid()

    async def refused(eng, sql: str) -> None:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await c.execute(text(sql))

    async def go() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await populate(c, tenant, a)
                await populate(c, tenant, b)
            for sql in ("DELETE FROM cred.claim_check", "UPDATE cred.claim_check SET decision = 'REVIEW'",
                        "DELETE FROM cred.eligibility_decision", "UPDATE cred.eligibility_decision SET outcome = 'X'"):  # fmt: skip
                await refused(eng, sql)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await adapter._erase(c, entry(tenant, a), "DELETE")
                # inside the same transaction the flag is already off again
                assert (
                    await c.execute(text("SELECT current_setting('cred.erasing', true)"))
                ).scalar_one() == "off"
            for sql in ("DELETE FROM cred.claim_check", "DELETE FROM cred.eligibility_decision"):
                await refused(eng, sql)  # still refused after an erasure ran

    run(go())


def test_erasure_is_idempotent_and_refuses_another_tenant(rt_url: str) -> None:
    t1, t2, a = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(t1)) as c:
                await populate(c, t1, a)
                n1 = (
                    await c.execute(
                        text("SELECT cred.erase_subject(:t, :s)"),
                        {"t": uuid.UUID(t1), "s": uuid.UUID(a)},
                    )
                ).scalar_one()
                n2 = (
                    await c.execute(
                        text("SELECT cred.erase_subject(:t, :s)"),
                        {"t": uuid.UUID(t1), "s": uuid.UUID(a)},
                    )
                ).scalar_one()
                assert n1 >= 9 and n2 == 0
            with pytest.raises(DBAPIError):  # a caller in tenant 2 cannot name tenant 1
                async with tenant_transaction(eng, ctx(t2)) as c:
                    await c.execute(
                        text("SELECT cred.erase_subject(:t, :s)"),
                        {"t": uuid.UUID(t1), "s": uuid.UUID(a)},
                    )
            async with tenant_transaction(eng, ctx(t2)) as c:
                await populate(c, t2, uid())
            async with tenant_transaction(
                eng, ctx(t1)
            ) as c:  # and tenant 1 still sees none of tenant 2's rows
                assert (
                    await c.execute(text("SELECT count(*) FROM cred.claim_check"))
                ).scalar_one() == 0

    run(go())


def test_the_data_map_finds_and_exports_a_readable_summary_without_hashes_or_keys(
    rt_url: str,
) -> None:
    tenant, a = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await populate(c, tenant, a)
            seen = {}
            for name in adapter._CHECK_STORES:
                found = await adapter._locator(name)(c, a)
                assert found, name
                assert {f["data_category"] for f in found} == {"D3"} and found[0][
                    "retention_policy_code"
                ] is None
                row = await adapter._exporter(name)(
                    c, SimpleNamespace(object_ref=found[0]["object_ref"])
                )
                seen[name] = row
            flat = str(seen)
            for secret in ("claimed_digest", "nonce_hash", "response_digest", "b'", "bytea"):
                assert secret not in flat, secret
            assert seen["cred.claim_check"]["decision"] == "VERIFIED"
            assert seen["cred.claim_check"]["reason_codes"] == []
            assert seen["cred.eligibility_decision"]["outcome"] == "ELIGIBLE"

    run(go())


def test_every_credential_check_table_is_registered_with_the_privacy_data_map() -> None:
    adapter.register()
    names = set(registered_stores())
    for store in (
        "cred.claim_check",
        "cred.consent_grant",
        "cred.eligibility_decision",
        "cred.claim_appeal",
        "cred.outreach_request",
        "cred.verification_case",
    ):
        assert store in names, store


def test_erasing_a_subject_in_one_tenant_never_touches_the_same_subject_id_in_another(
    rt_url: str,
) -> None:
    """credential_0011: the SECURITY DEFINER function filters on its tenant, whatever role owns it."""
    t1, t2, a = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(t1)) as c:
                await populate(c, t1, a)
            async with tenant_transaction(eng, ctx(t2)) as c:
                await populate(c, t2, a)  # the very same subject id, in another tenant
            async with tenant_transaction(eng, ctx(t1)) as c:
                await adapter._erase(c, entry(t1, a), "DELETE")
                assert set((await counts(c, a)).values()) == {0}
            async with tenant_transaction(eng, ctx(t2)) as c:
                assert all(v >= 1 for v in (await counts(c, a)).values()), await counts(c, a)

    run(go())

"""GID-060: a source event that cannot be applied is kept, once, and a person can replay it (credential_0009)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import claim_checks as checks
from zid_platform.modules.credential.application import trust_service as svc
from zid_platform.modules.credential.domain import lifecycle as lc
from zid_platform.modules.credential.domain.claims import CredentialStatus
from zid_platform.modules.credential.infrastructure.registries import (
    issuer_registry,
    source_contracts,
)
from zid_platform.modules.verification.ports import pack_registry

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
LIC = {
    "license_ref": "L-1",
    "regulator": "Sample State Licensing Board",
    "activity_scope": "legal.advice",
}


def uid() -> str:
    return str(uuid.uuid4())


async def lic_check(c, tenant, subject):
    return await checks.run_check(
        c, tenant_id=tenant, subject_principal_id=subject, kind="PROFESSIONAL_LICENSE", claimed=LIC,
        jurisdiction={"country": "US", "subdivision": "CA"}, profession=None, packs=pack_registry(),
        source=checks.SimulatorSource("test"), now=NOW, environment="test",
        gate=checks.Gate(registry=issuer_registry(), contracts=source_contracts()),
    )  # fmt: skip


def event(check_id: str, event_id: str = "e1", seq: int = 1) -> lc.SourceEvent:
    return lc.SourceEvent(
        event_id, check_id, CredentialStatus.REVOKED, NOW + timedelta(hours=1), seq
    )


def test_an_event_for_an_unknown_check_is_kept_once_and_counted(rt_url: str) -> None:
    tenant, ghost = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            first = await svc.keep_unapplied_event(
                c, tenant_id=tenant, check_id=ghost, event=event(ghost), reason="CHECK_NOT_FOUND"
            )
            again = await svc.keep_unapplied_event(
                c, tenant_id=tenant, check_id=ghost, event=event(ghost), reason="CHECK_NOT_FOUND"
            )
            assert first[1] is True and again[1] is False and first[0] == again[0]
            rows = (
                await c.execute(text("SELECT attempts, state FROM cred.source_dead_letter"))
            ).all()
            assert [tuple(r) for r in rows] == [(2, "OPEN")]

    asyncio.run(go())


def test_replay_applies_the_event_once_the_check_exists_and_closes_the_letter(rt_url: str) -> None:
    tenant, subject, ops = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            future_check = uid()
            letter, _ = await svc.keep_unapplied_event(
                c,
                tenant_id=tenant,
                check_id=future_check,
                event=event(future_check),
                reason="CHECK_NOT_FOUND",
            )
            # the check is still not there: the letter stays open
            assert (
                await svc.replay_dead_letter(c, tenant_id=tenant, letter_id=letter, by=ops, now=NOW)
                == "OPEN"
            )
            real = await lic_check(c, tenant, subject)
            late, _ = await svc.keep_unapplied_event(
                c, tenant_id=tenant, check_id=real.check_id, event=event(real.check_id, "e2", 2),
                reason="CHECK_NOT_FOUND",
            )  # fmt: skip
            assert (
                await svc.replay_dead_letter(c, tenant_id=tenant, letter_id=late, by=ops, now=NOW)
                == "REPLAYED"
            )
            status = (
                await c.execute(
                    text("SELECT status FROM cred.claim_monitor WHERE check_id = :c"),
                    {"c": uuid.UUID(real.check_id)},
                )
            ).scalar_one()
            assert status == "REVOKED"
            try:
                await svc.replay_dead_letter(c, tenant_id=tenant, letter_id=late, by=ops, now=NOW)
                raise AssertionError("a closed letter must not replay")
            except LookupError:
                pass

    asyncio.run(go())


def test_a_letter_for_a_superseded_check_is_discarded_with_the_reason_kept(rt_url: str) -> None:
    tenant, subject, ops = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            first = await lic_check(c, tenant, subject)
            await svc.apply_source_event(
                c, tenant_id=tenant, check_id=first.check_id, event=event(first.check_id), now=NOW
            )
            letter, _ = await svc.keep_unapplied_event(
                c, tenant_id=tenant, check_id=first.check_id, event=event(first.check_id, "e9", 9), reason="CHECK_SUPERSEDED"
            )  # fmt: skip
            assert (
                await svc.replay_dead_letter(c, tenant_id=tenant, letter_id=letter, by=ops, now=NOW)
                == "DISCARDED"
            )
            row = (
                await c.execute(
                    text("SELECT state, reason, resolved_by FROM cred.source_dead_letter")
                )
            ).one()
            assert tuple(row) == ("DISCARDED", "CHECK_SUPERSEDED", uuid.UUID(ops))

    asyncio.run(go())


def test_another_tenant_cannot_see_the_letters(rt_url: str) -> None:
    a, b, ghost = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(a)) as c:
                await svc.keep_unapplied_event(
                    c, tenant_id=a, check_id=ghost, event=event(ghost), reason="CHECK_NOT_FOUND"
                )
            async with tenant_transaction(eng, ctx(b)) as c:
                assert (
                    await c.execute(text("SELECT count(*) FROM cred.source_dead_letter"))
                ).scalar_one() == 0

    asyncio.run(go())


def test_the_dossier_queries_return_one_subjects_current_checks_and_appeals_only(
    rt_url: str,
) -> None:
    from zid_platform.modules.credential.infrastructure import trust_store

    tenant, a, b, other = uid(), uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            mine = await lic_check(c, tenant, a)
            await lic_check(c, tenant, b)
            await svc.raise_appeal(
                c,
                tenant_id=tenant,
                check_id=mine.check_id,
                raised_by=a,
                reason="wrong name",
                correction={},
            )
            live = await trust_store.live_checks(c, a)
            assert [str(x["check_id"]) for x in live] == [mine.check_id]
            assert await trust_store.live_checks(c, other) == []
            assert await trust_store.subject_decisions(c, other) == []
            assert [str(x["check_id"]) for x in await trust_store.subject_appeals(c, a)] == [
                mine.check_id
            ]
            assert await trust_store.subject_appeals(c, b) == []

    asyncio.run(go())

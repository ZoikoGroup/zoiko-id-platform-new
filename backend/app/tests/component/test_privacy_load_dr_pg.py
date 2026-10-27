"""Privacy under load and after a restore (ZID-PRIV-001; PRIV-30, PRIV-31, PRIV-32).

PRIV-30: a flood of rights requests is bounded per subject and per requester, opt-outs are never throttled, and one
tenant's data never appears in another's view.
PRIV-31: a retention backlog of twice the batch size drains in bounded batches with nothing skipped, held entries do
not starve the rest, and every entry ends disposed or held.
PRIV-32: after a restore, the durable journal is replayed (erasures, opt-outs, holds) and processing stays gated until
nothing is outstanding.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application import vault
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.evidence.domain.ledger import LedgerError
from zid_platform.modules.privacy.application import restore
from zid_platform.modules.privacy.application import service as privacy
from zid_platform.modules.privacy.domain.model import PrivacyError

DISPOSED: list[str] = []


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def runtime() -> Any:
    configure_runtime(development_runtime("uk-1"))
    DISPOSED.clear()

    async def dispose(conn: Any, entry: Any, action: str) -> None:
        DISPOSED.append(entry.object_ref)

    async def export(conn: Any, entry: Any) -> Any:
        return {"ref": entry.object_ref}

    privacy.register_store("test.load", disposer=dispose, exporter=export)
    yield
    privacy.RESTORE.pending = False
    configure_runtime(None)


def test_a_flood_of_rights_requests_is_bounded_and_tenants_stay_apart(rt_url: str) -> None:
    a, b, person, other, agent = uid(), uid(), uid(), uid(), uid()

    async def ask(
        c: Any, tenant: str, subject: str, kind: str = "ACCESS", requester: str | None = None
    ) -> Any:
        return await privacy.open_request(
            c,
            tenant,
            request_type=kind,
            subject_principal_id=subject,
            requester_principal_id=requester or subject,
            jurisdiction_pack_id="uk-gdpr",
        )

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(a)) as c:
            for _ in range(privacy.MAX_REQUESTS_ABOUT_SUBJECT):
                await ask(c, a, person)
            with pytest.raises(PrivacyError, match="PRIVACY_RATE_LIMITED"):
                await ask(c, a, person)
            await ask(c, a, person, "OPT_OUT_SALE_SHARING")  # an opt-out is never throttled
            await ask(c, a, other)  # another person is unaffected

            # one agent filing for many people is bounded too
            for _ in range(privacy.MAX_REQUESTS_BY_REQUESTER):
                await ask(c, a, uid(), requester=agent)
            with pytest.raises(PrivacyError, match="PRIVACY_RATE_LIMITED"):
                await ask(c, a, uid(), requester=agent)

        # another tenant's identical subject id neither shares the count nor sees these rows
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(b)) as c:
            await ask(c, b, person)
            await privacy.register_personal_data(
                c, b, subject_principal_id=person, store_name="test.load", object_type="profile",
                object_ref="b-only", purpose_code="ACCOUNT", data_category="D1", retention_policy_code=None,
            )  # fmt: skip
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(a)) as c:
            seen = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM privacy.data_map_entry WHERE subject_principal_id = :s"
                    ),
                    {"s": uuid.UUID(person)},
                )
            ).scalar_one()
            assert seen == 0  # tenant B's data map is invisible to tenant A

    asyncio.run(run())


def test_a_backlog_of_twice_the_batch_drains_in_bounded_batches_with_nothing_orphaned(
    rt_url: str, owner_url: str
) -> None:
    tenant, held_person = uid(), uid()
    n_free, n_held, batch = 1000, 50, 500
    now = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for i in range(n_free):
                await privacy.register_personal_data(
                    c, tenant, subject_principal_id=uid(), store_name="test.load", object_type="profile",
                    object_ref=f"free-{i}", purpose_code="ACCOUNT", data_category="D1", retention_policy_code=None,
                )  # fmt: skip
            for i in range(n_held):
                await privacy.register_personal_data(
                    c, tenant, subject_principal_id=held_person, store_name="test.load", object_type="profile",
                    object_ref=f"held-{i}", purpose_code="ACCOUNT", data_category="D1", retention_policy_code=None,
                )  # fmt: skip
            await vault.create_hold(
                c, tenant, basis="litigation", reference="m-31", requested_by=uid(), approved_by=uid(),
                subject_principal_ids=[held_person], review_at=now + timedelta(days=30), now=now,
            )  # fmt: skip
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "UPDATE privacy.data_map_entry SET due_at = now() - interval '1 day' "
                    "WHERE tenant_id = :t"
                ),
                {"t": uuid.UUID(tenant)},
            )
        started = time.monotonic()
        async with engine(owner_url) as eng, eng.begin() as c:
            report = await privacy.drain_retention(
                c, datetime.now(UTC), batch=batch, max_batches=10
            )
        elapsed = time.monotonic() - started
        assert (
            report.complete and report.batches == 3
        )  # 1050 entries (+ any other tenant's) in pages of 500
        assert report.disposed >= n_free and report.held >= n_held
        mine = [r for r in DISPOSED if r.startswith(("free-", "held-"))]
        assert sorted(r for r in mine if r.startswith("free-")) == sorted(
            f"free-{i}" for i in range(n_free)
        )
        assert not any(r.startswith("held-") for r in mine)  # the hold protected every held entry
        assert len(mine) == len(set(mine))  # no entry disposed twice
        assert elapsed < 60, elapsed  # the SLO for the backlog at twice the batch
        async with engine(owner_url) as eng, eng.begin() as c:
            left = (
                await c.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE status = 'ACTIVE') AS active, "
                        "count(*) FILTER (WHERE status = 'HELD') AS held "
                        "FROM privacy.data_map_entry WHERE tenant_id = :t AND due_at < now()"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).one()
            assert left.active == 0 and left.held == n_held  # nothing left half-done

    asyncio.run(run())


def test_after_a_restore_the_journal_is_replayed_and_processing_waits_for_it(
    rt_url: str, owner_url: str
) -> None:
    tenant, person, requester, approver = uid(), uid(), uid(), uid()
    now = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            object_id, _ = await vault.store_object(
                c, tenant, content=b"back from the dead", object_type="verification.document",
                classification="D3", retention_class="raw", subject_principal_id=person,
            )  # fmt: skip
            hold = await vault.create_hold(
                c, tenant, basis="litigation", reference="m-32", requested_by=requester, approved_by=approver,
                subject_principal_ids=[uid()], review_at=now + timedelta(days=30), now=now,
            )  # fmt: skip
        ref = await vault.subject_ref(tenant, person)
        journal = [
            {"kind": "ERASURE", "tenant_id": tenant, "subject_ref": ref, "request_id": "r-1"},
            {
                "kind": "PREFERENCE",
                "tenant_id": tenant,
                "subject_principal_id": person,
                "preference_type": "OPT_OUT_SALE_SHARING",
                "value": False,
            },
            {
                "kind": "PREFERENCE",
                "tenant_id": tenant,
                "subject_principal_id": person,
                "preference_type": "OPT_OUT_SALE_SHARING",
                "value": True,
            },
            {"kind": "HOLD", "tenant_id": tenant, "legal_hold_id": str(hold.legal_hold_id)},
            {
                "kind": "HOLD",
                "tenant_id": tenant,
                "legal_hold_id": str(uuid.uuid4()),
            },  # lost in the backup
        ]  # the backup predates the erasure and the opt-out, and lost one hold

        restore.mark_restored()
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            gated = await privacy.check_processing(
                c, tenant, activity_code="ACCOUNT_SERVICE", purpose_code="ACCOUNT", data_category="D1",
                subject_principal_id=person,
            )  # fmt: skip
        assert not gated.allowed and gated.reason == "PRIVACY_RECONCILIATION_PENDING"

        async with engine(owner_url) as eng, eng.begin() as c:
            first = await restore.reconcile_after_restore(c, journal, now=now)
        assert (
            first.erasures_applied == 1 and first.preferences_restored == 1 and first.holds_ok == 1
        )
        assert len(first.holds_missing) == 1 and not first.ready
        assert (
            privacy.RESTORE.pending
        )  # a lost hold keeps service gated until a person re-creates it

        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(LedgerError):  # the erased subject stays erased
                await vault.read_object(c, tenant, object_id)
            prefs = {
                p.preference_type: p.value for p in await privacy.store.preferences_of(c, person)
            }
            assert prefs == {"OPT_OUT_SALE_SHARING": True}  # the last journalled value won

        async with (
            engine(owner_url) as eng,
            eng.begin() as c,
        ):  # a person re-created the hold; replay is idempotent
            second = await restore.reconcile_after_restore(c, journal[:-1], now=now)
        assert second.erasures_applied == 0 and second.erasures_already_done == 1
        assert second.preferences_restored == 0 and second.ready
        assert not privacy.RESTORE.pending

    asyncio.run(run())

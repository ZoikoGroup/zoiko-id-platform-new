"""Real Postgres and Valkey (skipped when they are not running; CI provides them). Proves the schema, the least-privilege role and the stores.

Local defaults match `docker compose up -d postgres` with ZID_DB_HOST_PORT=5440 and a throwaway Valkey on 6390.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from assistant_helpers import make_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from zid_assistant.infrastructure.sessions import ValkeySessionStore
from zid_assistant.infrastructure.store import IntakeRow, OutboxRow, SqlStore
from zid_assistant.migrate import migrate

OWNER = os.environ.get(
    "ZID_ASSISTANT_TEST_OWNER_URL",
    "postgresql+asyncpg://zid:zid_local_only@localhost:5440/zid_assistant_test",
)
RUNTIME = os.environ.get(
    "ZID_ASSISTANT_TEST_RUNTIME_URL",
    "postgresql+asyncpg://zid_assistant:zid_assistant_local_only@localhost:5440/zid_assistant_test",
)
PLATFORM_DB = os.environ.get(
    "ZID_ASSISTANT_TEST_PLATFORM_URL",
    "postgresql+asyncpg://zid_assistant:zid_assistant_local_only@localhost:5440/zid_cell",
)
VALKEY = os.environ.get("ZID_ASSISTANT_TEST_VALKEY_URL", "redis://localhost:6390/0")


def _db_up() -> bool:
    async def probe() -> bool:
        try:
            e = create_async_engine(OWNER.rsplit("/", 1)[0] + "/postgres")
            async with e.connect() as c:
                await c.execute(text("SELECT 1"))
            await e.dispose()
            return True
        except Exception:  # noqa: BLE001
            return False

    return asyncio.run(probe())


def _valkey_up() -> bool:
    async def probe() -> bool:
        try:
            import valkey.asyncio as v

            r = v.from_url(VALKEY)
            await r.ping()
            await r.aclose()
            return True
        except Exception:  # noqa: BLE001
            return False

    return asyncio.run(probe())


needs_db = pytest.mark.skipif(
    not _db_up(), reason="Postgres is not running (docker compose up -d postgres)"
)
needs_valkey = pytest.mark.skipif(not _valkey_up(), reason="Valkey is not running")


@pytest.fixture(scope="module")
def migrated() -> None:
    asyncio.run(migrate(make_settings(migration_database_url=OWNER)))


def row(key: str, digest: str = "d") -> tuple[IntakeRow, OutboxRow]:
    rid = str(uuid.uuid4())
    return (
        IntakeRow(
            rid,
            "SALES",
            "US",
            "intake-sales-v1",
            "n1",
            key,
            digest,
            b"cipher",
            "ZR-" + uuid.uuid4().hex[:8].upper(),
        ),
        OutboxRow(str(uuid.uuid4()), rid, "sales-receiver", "adapter-v1"),
    )


@needs_db
@pytest.mark.anyio
async def test_migration_is_idempotent_and_creates_the_schema(migrated: None) -> None:
    assert await migrate(make_settings(migration_database_url=OWNER)) == []  # already current
    e = create_async_engine(OWNER)
    async with e.connect() as c:
        names = {
            r[0]
            for r in (
                await c.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema='public_assistant'"
                    )
                )
            ).all()
        }
    await e.dispose()
    assert {
        "feedback_event",
        "message_metric",
        "safety_event",
        "audit_event",
        "intake_request",
        "delivery_outbox",
        "receiver_receipt",
        "marketing_evidence",
        "schema_version",
    } <= names


@needs_db
@pytest.mark.anyio
async def test_the_runtime_role_cannot_reach_the_platform_or_change_the_schema(
    migrated: None,
) -> None:
    e = create_async_engine(PLATFORM_DB)
    try:
        async with e.connect() as c:
            for sql in (
                "SELECT 1 FROM core.principal LIMIT 1",
                "SELECT 1 FROM authn.authenticator LIMIT 1",
                "SELECT 1 FROM org.tenant LIMIT 1",
            ):
                with pytest.raises(Exception, match="permission denied|does not exist"):
                    await c.execute(text(sql))
                await c.rollback()
    finally:
        await e.dispose()
    own = create_async_engine(RUNTIME)
    try:
        async with own.connect() as c:
            with pytest.raises(Exception, match="permission denied"):
                await c.execute(text("CREATE TABLE public_assistant.hack (x int)"))
            await c.rollback()
            with pytest.raises(Exception, match="must be owner|permission denied"):
                await c.execute(text("DROP TABLE public_assistant.intake_request"))
            await c.rollback()
            with pytest.raises(Exception, match="permission denied"):
                await c.execute(text("CREATE ROLE evil LOGIN"))
            await c.rollback()
            role = (
                await c.execute(
                    text(
                        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
            assert tuple(role) == (False, False, False, False)
    finally:
        await own.dispose()


@needs_db
@pytest.mark.anyio
async def test_sql_store_metadata_roundtrip_and_purge(migrated: None) -> None:
    s = SqlStore(RUNTIME)
    try:
        assert await s.ready()
        before = await s.counts()
        await s.record_feedback("rsp_" + "a" * 32, "USEFUL", "INT-01", "snap-1")
        await s.record_metric("INT-03", "ANSWER", "OK", "DETERMINISTIC", False, 12, "snap-1")
        await s.record_safety("PASSWORD", "messages")
        await s.audit("TEST", None, "pub-policy-1", "cor_1", "OK")
        after = await s.counts()
        assert (
            after["feedback"] == before["feedback"] + 1
            and after["metrics"] == before["metrics"] + 1
            and after["safety"] == before["safety"] + 1
        )
        out = await s.purge(datetime.now(UTC) + timedelta(days=400), 90, 90)
        assert out["feedback"] >= 1 and out["metrics"] >= 1 and out["safety"] >= 1
    finally:
        await s.dispose()


@needs_db
@pytest.mark.anyio
async def test_sql_store_constraints_reject_free_text_in_analytics(migrated: None) -> None:
    s = SqlStore(RUNTIME)
    try:
        with pytest.raises(Exception, match="check constraint|violates"):
            await s.record_feedback("this is a sentence the visitor typed", "USEFUL", None, "s")
        with pytest.raises(Exception, match="check constraint|violates"):
            await s.record_metric("not-an-intent", "ANSWER", "OK", "R", False, 1, "s")
        with pytest.raises(Exception, match="check constraint|violates"):
            await s.record_metric("INT-01", "MAYBE", "OK", "R", False, 1, "s")
    finally:
        await s.dispose()


@needs_db
@pytest.mark.anyio
async def test_sql_store_intake_outbox_is_transactional_idempotent_and_erasable(
    migrated: None,
) -> None:
    s = SqlStore(RUNTIME)
    try:
        key = "k-" + uuid.uuid4().hex
        r, o = row(key)
        await s.create_intake(r, o)
        found = await s.intake_by_key(key)
        assert found is not None and found.reference == r.reference and found.status == "QUEUED"
        r2, o2 = row(key)  # the same idempotency key can never create a second record
        with pytest.raises(Exception, match="unique|duplicate"):
            await s.create_intake(r2, o2)
        assert (await s.intake_by_key(key)).request_id == r.request_id  # type: ignore[union-attr]
        assert not [
            d
            for d in await s.due_outbox(datetime.now(UTC) + timedelta(seconds=5), 100)
            if d.request_id == r2.request_id
        ], "a failed transaction leaves no orphan outbox row"
        due = [
            d
            for d in await s.due_outbox(datetime.now(UTC) + timedelta(seconds=5), 100)
            if d.request_id == r.request_id
        ]
        assert len(due) == 1
        assert await s.intake_fields(r.request_id) == ("SALES", "US", b"cipher")
        await s.mark_failed(
            due[0].outbox_id,
            r.request_id,
            "RETRYING",
            1,
            datetime.now(UTC) + timedelta(hours=1),
            "status_503",
        )
        assert not [
            d
            for d in await s.due_outbox(datetime.now(UTC) + timedelta(seconds=5), 100)
            if d.request_id == r.request_id
        ], "backoff is honoured"
        assert await s.erase_intake(r.reference)
        assert await s.intake_fields(r.request_id) is None
        await s.mark_delivered(due[0].outbox_id, r.request_id, "CASE-1", 200, "cor")
        assert (await s.intake_by_key(key)).status == "ERASED"  # type: ignore[union-attr]  # an erasure is final
    finally:
        await s.dispose()


@needs_db
@pytest.mark.anyio
async def test_the_full_app_runs_on_real_postgres(migrated: None) -> None:
    from assistant_helpers import H, P
    from fastapi.testclient import TestClient
    from zid_assistant.main import create_app

    app = create_app(make_settings(database_url=RUNTIME))
    with TestClient(app, base_url="http://localhost:8210") as c:
        assert c.get("/health/ready").status_code == 200
        assert c.post(f"{P}/sessions", headers=H).status_code == 201
        r = c.post(
            f"{P}/messages",
            headers=H,
            json={"client_message_id": "pg-test-0001", "text": "What is Zoiko iD?"},
        )
        assert r.status_code == 200
        assert (
            c.post(
                f"{P}/feedback",
                headers=H,
                json={"response_id": r.json()["response_id"], "rating": "USEFUL"},
            ).status_code
            == 204
        )
    s = SqlStore(RUNTIME)
    try:
        assert (await s.counts())["metrics"] >= 1
    finally:
        await s.dispose()


@needs_valkey
@pytest.mark.anyio
async def test_valkey_sessions_expire_forget_and_dedupe() -> None:
    import time

    now = [time.time()]
    s = ValkeySessionStore(VALKEY, idle=3, absolute=5, clock=lambda: now[0])
    h, _d = await s.create("en-US")
    assert await s.get(h) is not None and not await s.get("nope")
    await s.put_message(h, "m-1", "digest", '{"a":1}')
    await s.add_response(h, "rsp_" + "b" * 32)
    assert await s.get_message(h, "m-1") == ("digest", '{"a":1}')
    assert await s.has_response(h, "rsp_" + "b" * 32)
    assert await s.mark_feedback(h, "rsp_x") is True and await s.mark_feedback(h, "rsp_x") is False
    await s.delete(h)
    assert (
        await s.get(h) is None
        and await s.get_message(h, "m-1") is None
        and not await s.has_response(h, "rsp_" + "b" * 32)
    )
    h2, _ = await s.create("en-US")
    await asyncio.sleep(3.5)
    assert await s.get(h2) is None, (
        "the key expires on its own (idle TTL), nothing outlives the session"
    )
    h3, _ = await s.create("en-US")
    now[0] += 6  # past the absolute limit
    assert await s.get(h3) is None


@needs_valkey
@pytest.mark.anyio
async def test_valkey_flags_are_shared_and_a_store_outage_fails_safe() -> None:
    from zid_assistant.infrastructure.flags import FlagStore

    a = FlagStore(make_settings(valkey_url=VALKEY, enabled=True), ttl=0)
    b = FlagStore(make_settings(valkey_url=VALKEY, enabled=True), ttl=0)
    try:
        assert (await b.get()).chat_on
        await a.set("kill_switch", True)
        assert not (await b.get()).chat_on, "another replica sees the kill switch without a restart"
    finally:
        await a.clear("kill_switch")
    assert (await b.get()).chat_on
    down = FlagStore(
        make_settings(valkey_url="redis://localhost:1/0", enabled=True, intake_sales=False), ttl=0
    )
    f = (
        await down.get()
    )  # the store is unreachable: configured defaults stand, nothing new is switched on
    assert f.chat_on and not f.intake_sales and not f.ai_answers


@needs_valkey
@pytest.mark.anyio
async def test_valkey_revocations_are_shared_and_an_outage_never_unrevokes() -> None:
    from zid_assistant.infrastructure.revocations import RevocationStore

    a = RevocationStore(make_settings(valkey_url=VALKEY), ttl=0)
    b = RevocationStore(make_settings(valkey_url=VALKEY), ttl=0)
    ident = "PUB-T" + uuid.uuid4().hex[:6].upper()
    try:
        await a.revoke("source", ident)
        assert ident in (await b.get()).sources, (
            "another replica sees the withdrawal with no deploy"
        )
        assert ident in (await a.get()).sources
        broken = RevocationStore(make_settings(valkey_url="redis://localhost:1/0"), ttl=0)
        broken._cache = (0.0, await a.get())
        assert ident in (await broken.get()).sources, "a store outage keeps the last known list"
    finally:
        await a.restore("source", ident)
    assert ident not in (await b.get()).sources

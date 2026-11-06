"""Software keys in PostgreSQL (CR-057): the real table, its append-only guard, least-privilege roles, races and the settings gate."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import RT_ROLE
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from zid_crypto.providers import KeyRef, KeyState
from zid_crypto.purposes import KeyPurpose
from zid_crypto.software_keys import MasterKeys, SoftwareKeyProvider, SoftwareSigners
from zid_platform import crypto_runtime
from zid_platform.db_keystore import PostgresKeyStore
from zid_platform.roles import Role
from zid_platform.settings import Settings

CTX = {"tenant": "t1", "object": "o1"}


def run_sql(url: str, *statements: str) -> None:
    async def go() -> None:
        eng = create_async_engine(url, isolation_level="AUTOCOMMIT")
        async with eng.connect() as c:
            for s in statements:
                await c.exec_driver_sql(s)
        await eng.dispose()

    asyncio.run(go())


def fetch(url: str, sql: str) -> list[tuple]:  # type: ignore[type-arg]
    async def go() -> list[tuple]:  # type: ignore[type-arg]
        eng = create_async_engine(url)
        async with eng.connect() as c:
            rows = [tuple(r) for r in (await c.execute(text(sql))).all()]
        await eng.dispose()
        return rows

    return asyncio.run(go())


@pytest.fixture(scope="module")
def keys_ready(owner_url: str) -> str:
    """The runtime role gets only what deploy/gcp/db/02_grants.sql gives it: SELECT, INSERT, UPDATE; never DELETE."""
    run_sql(
        owner_url,
        f"GRANT USAGE ON SCHEMA keys TO {RT_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA keys TO {RT_ROLE}",
    )
    return owner_url


@pytest.fixture()
def master(tmp_path: Path) -> tuple[Path, MasterKeys]:
    f = tmp_path / "master"
    f.write_text(MasterKeys.generate("m1") + "\n")
    return f, MasterKeys.from_file(f)


@pytest.fixture(autouse=True)
def clean(keys_ready: str) -> None:
    run_sql(
        keys_ready,
        "ALTER TABLE keys.key_version DISABLE TRIGGER key_version_guard",
        "DELETE FROM keys.key_version",
        "ALTER TABLE keys.key_version ENABLE TRIGGER key_version_guard",
    )
    crypto_runtime._software_parts.cache_clear()


def test_keys_round_trip_through_postgres_and_nothing_is_stored_in_the_clear(
    keys_ready: str, rt_url: str, master: tuple[Path, MasterKeys]
) -> None:
    _, masters = master
    first = SoftwareKeyProvider(store=PostgresKeyStore(rt_url, "keys"), masters=masters)
    first.ensure_key(KeyPurpose.DATA_KEK, "data_kek-eu-1")
    ref = KeyRef(KeyPurpose.DATA_KEK, "data_kek-eu-1")
    wrapped, exact = asyncio.run(first.wrap_data_key(ref, b"d" * 32, CTX))
    # a different process: new store object, same database and master
    second = SoftwareKeyProvider(store=PostgresKeyStore(rt_url, "keys"), masters=masters)
    assert bytes(asyncio.run(second.unwrap_data_key(exact, wrapped, CTX))) == b"d" * 32
    ((blob, jwk),) = fetch(keys_ready, "SELECT wrapped, public_jwk FROM keys.key_version")
    assert jwk is None and bytes(blob).startswith(b"ZK1")
    plain = first._keys["data_kek-eu-1"].current().material
    assert plain is not None and bytes(plain) not in bytes(blob)


def test_the_wrong_master_key_cannot_read_the_database(
    keys_ready: str, rt_url: str, master: tuple[Path, MasterKeys], tmp_path: Path
) -> None:
    _, masters = master
    SoftwareKeyProvider(store=PostgresKeyStore(rt_url, "keys"), masters=masters).ensure_key(
        KeyPurpose.DATA_KEK, "k"
    )
    other = tmp_path / "other"
    other.write_text(MasterKeys.generate("m1") + "\n")  # same id, different key
    with pytest.raises(Exception, match="does not unwrap"):
        SoftwareKeyProvider(
            store=PostgresKeyStore(rt_url, "keys"), masters=MasterKeys.from_file(other)
        )


def test_rotation_and_destruction_persist_and_survive_a_restart(
    keys_ready: str, rt_url: str, master: tuple[Path, MasterKeys]
) -> None:
    _, masters = master
    p = SoftwareKeyProvider(store=PostgresKeyStore(rt_url, "keys"), masters=masters)
    p.ensure_key(KeyPurpose.SUBJECT_ERASURE_KEY, "subject_erasure_key-eu-1")
    ref = KeyRef(KeyPurpose.SUBJECT_ERASURE_KEY, "subject_erasure_key-eu-1")
    p.rotate_key(ref)
    states = fetch(keys_ready, "SELECT version, state FROM keys.key_version ORDER BY version")
    assert states == [(1, KeyState.RETIRED.value), (2, KeyState.ACTIVE.value)]
    asyncio.run(p.destroy_key(ref))
    rows = fetch(
        keys_ready,
        "SELECT version, state, wrapped IS NULL, destroyed_at IS NOT NULL FROM keys.key_version ORDER BY version",
    )
    assert rows == [(1, "DESTROYED", True, True), (2, "DESTROYED", True, True)]
    fresh = SoftwareKeyProvider(store=PostgresKeyStore(rt_url, "keys"), masters=masters)
    with pytest.raises(Exception, match="KEY_DESTROYED|destroyed|CRYPTO"):
        asyncio.run(fresh.wrap_data_key(ref, b"x" * 32, CTX))


def test_the_table_is_append_only_and_the_runtime_role_cannot_delete(
    keys_ready: str, rt_url: str, master: tuple[Path, MasterKeys]
) -> None:
    _, masters = master
    SoftwareSigners(store=PostgresKeyStore(rt_url, "keys"), masters=masters).signer(
        "signing_token-eu-1", kid="token-eu-1"
    )
    for statement, why in (
        ("DELETE FROM keys.key_version", "never deleted"),
        ("UPDATE keys.key_version SET key_id = 'other'", "identity"),
        ("UPDATE keys.key_version SET algorithm = 'AES-256-GCM'", "identity"),
        ("UPDATE keys.key_version SET public_jwk = '{}'::jsonb", "identity"),
    ):
        with pytest.raises(DBAPIError, match=why):
            run_sql(keys_ready, statement)
    with pytest.raises(DBAPIError, match="permission denied"):
        run_sql(rt_url, "DELETE FROM keys.key_version")
    run_sql(keys_ready, "UPDATE keys.key_version SET state = 'RETIRED', retired_at = now()")
    with pytest.raises(DBAPIError, match="not reactivated"):
        run_sql(keys_ready, "UPDATE keys.key_version SET state = 'ACTIVE'")
    with pytest.raises(DBAPIError, match="check constraint|violates"):
        run_sql(
            keys_ready, "UPDATE keys.key_version SET state = 'DESTROYED'"
        )  # destroyed means the material is gone too


def test_processes_racing_to_create_a_signing_key_converge_on_one(
    keys_ready: str, rt_url: str, master: tuple[Path, MasterKeys]
) -> None:
    _, masters = master

    def make(_: int) -> dict[str, str]:
        return (
            SoftwareSigners(store=PostgresKeyStore(rt_url, "keys"), masters=masters)
            .signer("evidence-eu-1", kid="evidence-eu-1")
            .public_jwk()
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        keys = list(pool.map(make, range(6)))
    assert all(k == keys[0] for k in keys)
    assert fetch(
        keys_ready, "SELECT count(*) FROM keys.key_version WHERE key_id = 'evidence-eu-1'"
    ) == [(1,)]


def settings(url: str, master_file: Path, **kw: object) -> Settings:
    return Settings(
        environment=kw.pop("environment", "production"),  # type: ignore[arg-type]
        cell_id="eu-1",
        role=Role.CELL,
        key_provider="db",
        key_master_file=str(master_file),
        database_url=url,
        token_issuer="https://api.example.test",
        **kw,  # type: ignore[arg-type]
    )


def test_a_deployed_environment_needs_the_written_acceptance_of_software_keys(
    keys_ready: str, rt_url: str, master: tuple[Path, MasterKeys]
) -> None:
    f, _ = master
    with pytest.raises(RuntimeError, match="ZID_KEY_PROVIDER_DB_ACK=accept-software-keys"):
        crypto_runtime.software_keys(settings(rt_url, f))
    with pytest.raises(RuntimeError, match="ZID_KEY_PROVIDER_DB_ACK"):
        crypto_runtime.software_keys(settings(rt_url, f, key_provider_db_ack="yes"))
    crypto_runtime.software_keys(settings(rt_url, f, key_provider_db_ack="accept-software-keys"))
    crypto_runtime.software_keys(
        settings(rt_url, f, environment="local")
    )  # local/test never need it
    with pytest.raises(RuntimeError, match="ZID_KEY_MASTER_FILE"):
        crypto_runtime.software_keys(
            Settings(environment="local", key_provider="db", database_url=rt_url)
        )


def test_production_settings_build_every_key_the_platform_needs_from_the_database(
    keys_ready: str, rt_url: str, master: tuple[Path, MasterKeys]
) -> None:
    f, _ = master
    s = settings(rt_url, f, key_provider_db_ack="accept-software-keys")
    rings = crypto_runtime.kms_keyrings_from_settings(s)
    assert rings.token.sign({"sub": "x"}, typ="test+jwt")
    evidence = crypto_runtime.evidence_signer_from_settings(s)
    assert evidence.kid == "evidence-eu-1"
    provider = crypto_runtime.key_provider_from_settings(s)
    mac = crypto_runtime.handle_key_from_settings(s, provider)
    assert mac is not None
    assert (
        crypto_runtime.placement_signer(s, dev_purpose="set", kid="webhook-eu-1", kms_key_id=None)
        is not None
    )
    # a restart reads the same keys: same public keys, no new rows
    before = fetch(keys_ready, "SELECT count(*) FROM keys.key_version")
    crypto_runtime._software_parts.cache_clear()
    again = crypto_runtime.kms_keyrings_from_settings(s)
    assert again.token.jwks() == rings.token.jwks()
    assert crypto_runtime.evidence_signer_from_settings(s).public_jwk() == evidence.public_jwk()
    assert fetch(keys_ready, "SELECT count(*) FROM keys.key_version") == before
    purposes = {r[0] for r in fetch(keys_ready, "SELECT purpose FROM keys.key_version")}
    assert {
        "TOKEN_SIGNING",
        "EVIDENCE_SIGNING",
        "HMAC_INTERNAL",
        "DATA_KEK",
        "SUBJECT_ERASURE_KEY",
    } <= purposes


def test_the_global_plane_keeps_its_keys_in_its_own_table(
    owner_url: str, master: tuple[Path, MasterKeys]
) -> None:
    """locator_0002 creates global_directory.key_version; a global process uses it, never the cell's table."""
    from zid_platform.migrations import upgrade

    run_sql(owner_url, "CREATE SCHEMA IF NOT EXISTS global_directory")
    upgrade(owner_url, role="global")
    _, masters = master
    SoftwareSigners(store=PostgresKeyStore(owner_url, "global_directory"), masters=masters).signer(
        "discovery-global", kid="discovery-global"
    )
    assert fetch(owner_url, "SELECT key_id FROM global_directory.key_version") == [
        ("discovery-global",)
    ]
    assert fetch(
        owner_url, "SELECT count(*) FROM keys.key_version WHERE key_id = 'discovery-global'"
    ) == [(0,)]

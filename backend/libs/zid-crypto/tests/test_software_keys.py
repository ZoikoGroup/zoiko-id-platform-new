"""Database-held software keys (CR-060): wrapping under a master key, versions, rotation, erasure, races, signers."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from zid_crypto import CryptoError
from zid_crypto.fips import require_fips
from zid_crypto.providers import KeyDestroyedError, KeyRef, KeyState
from zid_crypto.purposes import KeyPurpose
from zid_crypto.software_keys import (
    MasterKeyError,
    MasterKeys,
    MemoryKeyStore,
    SoftwareKeyProvider,
    SoftwareSigners,
)

CTX = {"tenant": "t1", "object": "o1"}


def verifies(signer, other_signer) -> bool:
    """`signer` signs; the public key of `other_signer` (rebuilt from its JWK) must accept it."""
    jwk = other_signer.public_jwk()
    num = lambda v: int.from_bytes(base64.urlsafe_b64decode(v + "=" * (-len(v) % 4)), "big")
    key = ec.EllipticCurvePublicNumbers(num(jwk["x"]), num(jwk["y"]), ec.SECP256R1()).public_key()
    raw = signer.sign(b"a signed statement")
    key.verify(
        encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
        b"a signed statement",
        ec.ECDSA(hashes.SHA256()),
    )
    return True


def masters(*ids: str) -> MasterKeys:
    text = "\n".join(MasterKeys.generate(i) for i in ids)
    return MasterKeys.parse(text)


def provider(store: MemoryKeyStore, m: MasterKeys) -> SoftwareKeyProvider:
    return SoftwareKeyProvider(store=store, masters=m)


# ------------------------------------------------------------------------------------------------ master keys


def test_a_master_key_file_parses_and_the_first_key_is_current() -> None:
    m = masters("m2", "m1")
    assert m.current == "m2"
    blob = m.wrap(b"x" * 32, b"aad")
    assert m.master_id_of(blob) == "m2" and bytes(m.unwrap(blob, b"aad")) == b"x" * 32


@pytest.mark.parametrize(
    "text",
    ["", "# only a comment", "m1", "m1 !!!notbase64", "m1 " + base64.b64encode(b"short").decode()],
)
def test_a_malformed_master_key_file_is_refused(text: str) -> None:
    with pytest.raises(MasterKeyError):
        MasterKeys.parse(text)


def test_duplicate_master_ids_are_refused() -> None:
    line = MasterKeys.generate("m1")
    with pytest.raises(MasterKeyError, match="duplicate"):
        MasterKeys.parse(line + "\n" + line)


def test_a_wrapped_key_binds_to_its_associated_data_and_to_the_master() -> None:
    m = masters("m1")
    blob = m.wrap(b"y" * 32, b"row-a")
    with pytest.raises(MasterKeyError, match="does not unwrap"):
        m.unwrap(blob, b"row-b")  # moved to another row
    with pytest.raises(MasterKeyError, match="not available"):
        masters("other").unwrap(blob, b"row-a")  # a different master key set
    with pytest.raises(MasterKeyError):
        m.unwrap(b"garbage", b"row-a")


def test_a_master_file_can_be_read_from_disk(tmp_path) -> None:
    f = tmp_path / "master"
    f.write_text(MasterKeys.generate("m1") + "\n")
    assert MasterKeys.from_file(f).current == "m1"
    with pytest.raises(MasterKeyError, match="cannot be read"):
        MasterKeys.from_file(tmp_path / "missing")


# ------------------------------------------------------------------------------------------------ the provider


def test_keys_survive_a_restart_and_nothing_is_stored_in_the_clear() -> None:
    store, m = MemoryKeyStore(), masters("m1")
    first = provider(store, m)
    ref = KeyRef(KeyPurpose.DATA_KEK, "kek-1")
    first.ensure_key(KeyPurpose.DATA_KEK, "kek-1")
    wrapped, exact = asyncio.run(first.wrap_data_key(ref, b"d" * 32, CTX))
    # a new process over the same store and master reads the same key
    second = provider(store, m)
    assert bytes(asyncio.run(second.unwrap_data_key(exact, wrapped, CTX))) == b"d" * 32
    # the store never holds the key material itself
    plain = first._keys["kek-1"].current().material
    assert plain is not None
    assert all(bytes(plain) not in (r.wrapped or b"") for r in store.rows.values())


def test_the_wrong_master_key_cannot_open_the_store() -> None:
    store = MemoryKeyStore()
    provider(store, masters("m1")).ensure_key(KeyPurpose.DATA_KEK, "kek-1")
    with pytest.raises(MasterKeyError):
        provider(store, masters("m1"))  # same id, different bytes


def test_a_row_moved_to_another_key_id_does_not_load() -> None:
    store, m = MemoryKeyStore(), masters("m1")
    provider(store, m).ensure_key(KeyPurpose.DATA_KEK, "kek-1")
    row = store.rows[("kek-1", 1)]
    del store.rows[("kek-1", 1)]
    store.rows[("kek-2", 1)] = replace(row, key_id="kek-2")  # relabelled
    with pytest.raises(MasterKeyError, match="does not unwrap"):
        provider(store, m)


def test_two_processes_creating_the_same_key_converge_on_one() -> None:
    store, m = MemoryKeyStore(), masters("m1")
    a, b = provider(store, m), provider(store, m)  # both started before the key existed
    a.ensure_key(KeyPurpose.DATA_KEK, "kek-1")
    b.ensure_key(KeyPurpose.DATA_KEK, "kek-1")  # loses the insert race, adopts a's key
    ref = KeyRef(KeyPurpose.DATA_KEK, "kek-1")
    wrapped, exact = asyncio.run(a.wrap_data_key(ref, b"k" * 32, CTX))
    assert bytes(asyncio.run(b.unwrap_data_key(exact, wrapped, CTX))) == b"k" * 32
    assert len([r for r in store.rows.values() if r.key_id == "kek-1"]) == 1


def test_rotation_persists_and_old_data_still_opens() -> None:
    store, m = MemoryKeyStore(), masters("m1")
    p = provider(store, m)
    p.ensure_key(KeyPurpose.DATA_KEK, "kek-1")
    ref = KeyRef(KeyPurpose.DATA_KEK, "kek-1")
    old_wrapped, old = asyncio.run(p.wrap_data_key(ref, b"o" * 32, CTX))
    new = p.rotate_key(ref)
    assert new.version == 2
    assert store.rows[("kek-1", 1)].state == KeyState.RETIRED.value
    assert store.rows[("kek-1", 2)].state == KeyState.ACTIVE.value
    fresh = provider(store, m)  # after a restart
    assert bytes(asyncio.run(fresh.unwrap_data_key(old, old_wrapped, CTX))) == b"o" * 32
    _, exact_new = asyncio.run(fresh.wrap_data_key(ref, b"n" * 32, CTX))
    assert exact_new.version == 2


def test_destroying_a_key_is_persisted_and_unrecoverable_after_a_restart() -> None:
    store, m = MemoryKeyStore(), masters("m1")
    p = provider(store, m)
    p.ensure_key(KeyPurpose.SUBJECT_ERASURE_KEY, "subject-1")
    ref = KeyRef(KeyPurpose.SUBJECT_ERASURE_KEY, "subject-1")
    wrapped, exact = asyncio.run(p.wrap_data_key(ref, b"s" * 32, CTX))
    asyncio.run(p.destroy_key(ref))
    row = store.rows[("subject-1", 1)]
    assert (
        row.state == KeyState.DESTROYED.value
        and row.wrapped is None
        and row.destroyed_at is not None
    )
    fresh = provider(store, m)
    with pytest.raises(KeyDestroyedError):
        asyncio.run(fresh.unwrap_data_key(exact, wrapped, CTX))


def test_a_master_rotation_rewraps_every_row_without_losing_a_key() -> None:
    store = MemoryKeyStore()
    old = masters("m1")
    p = provider(store, old)
    p.ensure_key(KeyPurpose.DATA_KEK, "kek-1")
    p.ensure_key(KeyPurpose.HMAC_INTERNAL, "mac-1")
    ref = KeyRef(KeyPurpose.DATA_KEK, "kek-1")
    wrapped, exact = asyncio.run(p.wrap_data_key(ref, b"d" * 32, CTX))
    # a new master is added in front; the old one is still readable
    both = MasterKeys.parse(MasterKeys.generate("m2") + "\n" + _line_of(old, "m1"))
    rotating = provider(store, both)
    assert rotating.rewrap_all() == 2
    assert {both.master_id_of(r.wrapped) for r in store.rows.values() if r.wrapped} == {"m2"}
    # the old master can now be retired: only m2 is needed
    only_new = MasterKeys.parse(_line_of(both, "m2"))
    assert (
        bytes(asyncio.run(provider(store, only_new).unwrap_data_key(exact, wrapped, CTX)))
        == b"d" * 32
    )
    assert provider(store, only_new).rewrap_all() == 0


def _line_of(m: MasterKeys, mid: str) -> str:
    return f"{mid} {base64.b64encode(m._keys[mid]).decode()}"


def test_the_software_provider_is_not_fips_validated() -> None:
    p = provider(MemoryKeyStore(), masters("m1"))
    with pytest.raises(CryptoError, match="FIPS_MODULE_REQUIRED"):
        require_fips(p, KeyPurpose.DATA_KEK, "AES-256-GCM")


def test_purposes_never_share_a_key_id() -> None:
    p = provider(MemoryKeyStore(), masters("m1"))
    p.ensure_key(KeyPurpose.DATA_KEK, "k")
    with pytest.raises(CryptoError):
        p.ensure_key(KeyPurpose.SUBJECT_ERASURE_KEY, "k")


# ------------------------------------------------------------------------------------------------ signers


def test_a_signing_key_is_stable_across_restarts_and_signs_verifiably() -> None:
    store, m = MemoryKeyStore(), masters("m1")
    one = SoftwareSigners(store=store, masters=m).signer(
        "signing_token-eu-1", kid="token-eu-1", purpose=KeyPurpose.TOKEN_SIGNING
    )
    two = SoftwareSigners(store=store, masters=m).signer(
        "signing_token-eu-1", kid="token-eu-1", purpose=KeyPurpose.TOKEN_SIGNING
    )
    assert one.public_jwk() == two.public_jwk()
    assert verifies(one, two)
    assert len(store.rows) == 1


def test_the_stored_signing_key_is_wrapped_not_plain_pem() -> None:
    store = MemoryKeyStore()
    SoftwareSigners(store=store, masters=masters("m1")).signer("k", kid="k")
    row = next(iter(store.rows.values()))
    assert (
        row.wrapped is not None
        and b"PRIVATE KEY" not in row.wrapped
        and row.public_jwk
        and "d" not in row.public_jwk
    )


def test_two_processes_creating_a_signing_key_converge() -> None:
    store, m = MemoryKeyStore(), masters("m1")
    a = SoftwareSigners(store=store, masters=m).signer("k", kid="k")
    b = SoftwareSigners(store=store, masters=m).signer("k", kid="k")
    assert a.public_jwk() == b.public_jwk() and len(store.rows) == 1


def test_rotating_a_signing_key_keeps_the_old_public_key_for_verification() -> None:
    store, m = MemoryKeyStore(), masters("m1")
    s = SoftwareSigners(store=store, masters=m)
    before = s.signer("k", kid="k-eu-1")
    assert s.rotate("k") == 2
    after = SoftwareSigners(store=store, masters=m).signer("k", kid="k-eu-1")
    assert before.public_jwk()["x"] != after.public_jwk()["x"]
    retired = s.retired_public_jwks("k", "k-eu-1")
    assert [j["kid"] for j in retired] == ["k-eu-1-v1"] and retired[0]["x"] == before.public_jwk()[
        "x"
    ]
    assert store.rows[("k", 1)].state == KeyState.RETIRED.value


def test_a_signing_key_row_cannot_be_swapped_to_another_key_id() -> None:
    store, m = MemoryKeyStore(), masters("m1")
    SoftwareSigners(store=store, masters=m).signer("evidence", kid="e")
    store.rows[("webhook", 1)] = replace(store.rows[("evidence", 1)], key_id="webhook")
    with pytest.raises(MasterKeyError):
        SoftwareSigners(store=store, masters=m).signer("webhook", kid="w")


def test_a_signing_purpose_must_allow_the_algorithm() -> None:
    s = SoftwareSigners(store=MemoryKeyStore(), masters=masters("m1"))
    s.signer("t", kid="t", purpose=KeyPurpose.TOKEN_SIGNING)  # ES256 is allowed for tokens

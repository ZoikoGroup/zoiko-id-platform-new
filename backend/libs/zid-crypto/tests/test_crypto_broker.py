"""Crypto broker: registry, purposes, local provider, envelopes, handles (DEC-F5, DEC-F6)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from zid_crypto import ALLOWED_JWS_ALGS, EXTERNAL_VERIFY_ALGS, CryptoError, LocalSigner
from zid_crypto.envelope import Aad, Envelope, open_envelope, seal
from zid_crypto.handles import LocalHmacKey, handle, handle_key_version
from zid_crypto.policy import CryptoPolicyRegistry, default_registry
from zid_crypto.providers import KeyDestroyedError, KeyRef, KeyState, LocalKeyProvider
from zid_crypto.purposes import KeyPurpose

AAD = Aad("tenant-a", "principal", "prn_1", "email", "1")


def provider(**kw: Any) -> tuple[LocalKeyProvider, KeyRef]:
    p = LocalKeyProvider(environment="test", **kw)
    meta = p.create_key(KeyPurpose.DATA_KEK, "kek-1")
    return p, KeyRef(meta.purpose, meta.key_id)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --- Crypto Policy Registry -------------------------------------------------------------------


def test_registry_maps_every_purpose_and_allows_by_exact_name() -> None:
    reg = default_registry()
    assert set(reg.purposes()) == set(KeyPurpose)
    assert reg.allowed(KeyPurpose.DATA_KEK, "AES-256-GCM")
    assert reg.allowed(KeyPurpose.TOKEN_SIGNING, "ES256")
    assert reg.allowed(KeyPurpose.EVIDENCE_SIGNING, "ML-DSA-65")
    for bad in ("none", "HS256", "es256", "AES-128-GCM", "", None, ["ES256"], "UNKNOWN-ALG"):
        assert not reg.allowed(KeyPurpose.TOKEN_SIGNING, bad)
    assert not reg.allowed(KeyPurpose.TOKEN_SIGNING, "AES-256-GCM")  # wrong purpose family
    with pytest.raises(CryptoError) as e:
        reg.require(KeyPurpose.HMAC_INTERNAL, "HMAC-SHA-1")
    assert e.value.code == "CRYPTO_ALGORITHM_NOT_ALLOWED"


def test_current_jws_algorithms_stay_inside_the_registry() -> None:
    reg = default_registry()
    assert all(reg.allowed(KeyPurpose.TOKEN_SIGNING, a) for a in ALLOWED_JWS_ALGS)
    external = reg.profile_algorithms("external-verification.v1")
    assert set(EXTERNAL_VERIFY_ALGS) <= set(external)


def test_registry_rejects_incomplete_documents(tmp_path: Path) -> None:
    good = Path(__file__).resolve().parents[3] / "config" / "crypto-profiles"
    doc = CryptoPolicyRegistry.load(good / "crypto-policy-registry.yaml")
    assert doc.version
    with pytest.raises(CryptoError, match="key_purposes"):
        CryptoPolicyRegistry({"version": "x", "profiles": {"p": {"purpose": "X"}}})
    with pytest.raises(CryptoError, match="CRYPTO_POLICY_INVALID"):
        CryptoPolicyRegistry(
            {
                "version": "x",
                "profiles": {"p": {"purpose": "X", "allowed_algorithms": "ES256"}},
                "key_purposes": {},
            }
        )
    missing = tmp_path / "missing.yaml"
    with pytest.raises(CryptoError, match="CRYPTO_POLICY_UNAVAILABLE"):
        CryptoPolicyRegistry.load(missing)


def test_local_signer_bound_to_a_purpose_checks_the_registry() -> None:
    LocalSigner("k1", purpose=KeyPurpose.TOKEN_SIGNING)
    LocalSigner("k2", "Ed25519", purpose=KeyPurpose.TOKEN_SIGNING)
    with pytest.raises(CryptoError, match="CRYPTO_ALGORITHM_NOT_ALLOWED"):
        LocalSigner("k3", "Ed25519", purpose=KeyPurpose.FEDERATION_SIGNING)  # ES256 only
    LocalSigner("k4", "Ed25519")  # unbound signers keep today's behaviour


# --- Local provider ---------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["production", "sandbox", "dev", ""])
def test_local_provider_refuses_deployed_environments(environment: str) -> None:
    with pytest.raises(CryptoError) as e:
        LocalKeyProvider(environment=environment)
    assert e.value.code == "CRYPTO_LOCAL_PROVIDER_FORBIDDEN"


def test_wrong_purpose_is_refused() -> None:
    p, kek = provider()
    hmac_meta = p.create_key(KeyPurpose.HMAC_INTERNAL, "mac-1")
    # a ref that re-labels the KEK as another wrapping purpose
    relabelled = KeyRef(KeyPurpose.BACKUP_KEK, kek.key_id)
    with pytest.raises(CryptoError, match="CRYPTO_KEY_PURPOSE_MISMATCH"):
        run(seal(b"x", AAD, p, relabelled))
    # an HMAC key can never wrap data keys, and a KEK can never be an HMAC key
    with pytest.raises(CryptoError, match="CRYPTO_KEY_PURPOSE_MISMATCH"):
        run(seal(b"x", AAD, p, hmac_meta.ref))
    with pytest.raises(CryptoError, match="CRYPTO_KEY_PURPOSE_MISMATCH"):
        p.hmac_key(KeyRef(KeyPurpose.HMAC_INTERNAL, kek.key_id))
    with pytest.raises(CryptoError, match="CRYPTO_KEY_PURPOSE_MISMATCH"):
        p.ensure_key(KeyPurpose.HMAC_INTERNAL, kek.key_id)
    # signing purposes are not local symmetric keys
    with pytest.raises(CryptoError, match="CRYPTO_ALGORITHM_NOT_ALLOWED"):
        p.create_key(KeyPurpose.TOKEN_SIGNING, "tok-1")


def test_keystore_file_is_shared_between_instances(tmp_path: Path) -> None:
    store = tmp_path / "keys" / "dev-keystore.json"
    p1, kek = provider(keystore=store)
    envelope = run(seal(b"shared", AAD, p1, kek))
    p2 = LocalKeyProvider(environment="local", keystore=store)
    assert run(open_envelope(envelope, AAD, p2)) == b"shared"
    meta = run(p2.metadata(kek))
    assert meta.state is KeyState.ACTIVE and meta.pq_ready and meta.algorithm == "AES-256-GCM"


# --- Envelope encryption ----------------------------------------------------------------------


def test_gcm_round_trip_and_fresh_nonce_and_dek() -> None:
    p, kek = provider()
    e1 = run(seal(b"hello", AAD, p, kek))
    e2 = run(seal(b"hello", AAD, p, kek))
    assert run(open_envelope(e1, AAD, p)) == b"hello"
    assert e1.nonce != e2.nonce and e1.wrapped_dek != e2.wrapped_dek
    assert len(e1.nonce) == 12 and e1.alg == "AES-256-GCM" and e1.kek.version == 1
    assert b"hello" not in e1.ciphertext


@pytest.mark.parametrize(
    "other",
    [
        Aad("tenant-b", "principal", "prn_1", "email", "1"),
        Aad("tenant-a", "principal", "prn_2", "email", "1"),
        Aad("tenant-a", "principal", "prn_1", "phone", "1"),
        Aad("tenant-a", "principal", "prn_1", "email", "2"),
        Aad("tenant-a", "mandate", "prn_1", "email", "1"),
    ],
)
def test_aad_mismatch_fails(other: Aad) -> None:
    p, kek = provider()
    envelope = run(seal(b"secret", AAD, p, kek))
    with pytest.raises(CryptoError) as e:
        run(open_envelope(envelope, other, p))
    assert e.value.code == "CRYPTO_KEY_CONTEXT_MISMATCH"


def test_aad_encoding_is_unambiguous() -> None:
    a = Aad("t|a", "b", "c", "d", "1")
    b = Aad("t", "a|b", "c", "d", "1")
    assert a.encode() != b.encode()
    with pytest.raises(CryptoError):
        Aad("", "b", "c", "d", "1")


def test_tampered_ciphertext_fails_with_tag_error() -> None:
    p, kek = provider()
    envelope = run(seal(b"secret", AAD, p, kek))
    flipped = bytes([envelope.ciphertext[0] ^ 1]) + envelope.ciphertext[1:]
    bad = Envelope(envelope.kek, envelope.wrapped_dek, envelope.nonce, flipped)
    with pytest.raises(CryptoError, match="CRYPTO_AUTHENTICATION_TAG_INVALID"):
        run(open_envelope(bad, AAD, p))


def test_destroyed_key_raises_key_destroyed() -> None:
    p = LocalKeyProvider(environment="test")
    meta = p.create_key(KeyPurpose.SUBJECT_ERASURE_KEY, "subject-42")
    envelope = run(seal(b"pii", AAD, p, meta.ref))
    destroyed = run(p.destroy_key(KeyRef(KeyPurpose.SUBJECT_ERASURE_KEY, "subject-42")))
    assert destroyed.state is KeyState.DESTROYED and destroyed.destroyed_at is not None
    with pytest.raises(KeyDestroyedError) as e:
        run(open_envelope(envelope, AAD, p))
    assert e.value.code == "KEY_DESTROYED"
    with pytest.raises(KeyDestroyedError):
        run(seal(b"pii", AAD, p, meta.ref))


def test_rotation_keeps_old_envelopes_readable() -> None:
    p, kek = provider()
    old = run(seal(b"v1", AAD, p, kek))
    new_meta = p.rotate_key(kek)
    new = run(seal(b"v2", AAD, p, kek))
    assert new.kek.version == new_meta.version == 2 and old.kek.version == 1
    assert run(p.metadata(old.kek)).state is KeyState.RETIRED
    assert run(open_envelope(old, AAD, p)) == b"v1"
    assert run(open_envelope(new, AAD, p)) == b"v2"


def test_envelope_serialization_round_trip_and_version_check() -> None:
    p, kek = provider()
    envelope = run(seal(b"payload", AAD, p, kek))
    text = envelope.to_json()
    again = Envelope.from_json(text)
    assert again == envelope and Envelope.from_bytes(envelope.to_bytes()) == envelope
    assert run(open_envelope(again, AAD, p)) == b"payload"
    doc = json.loads(text)
    assert set(doc) == {"v", "alg", "kek", "wdek", "iv", "ct"}
    assert doc["kek"] == {"purpose": "DATA_KEK", "id": "kek-1", "version": 1}
    with pytest.raises(CryptoError, match="CRYPTO_ENVELOPE_VERSION_UNSUPPORTED"):
        Envelope.from_json(json.dumps({**doc, "v": 2}))
    with pytest.raises(CryptoError, match="CRYPTO_ENVELOPE_MALFORMED"):
        Envelope.from_json(json.dumps({**doc, "iv": "AA"}))
    with pytest.raises(CryptoError, match="CRYPTO_ENVELOPE_MALFORMED"):
        Envelope.from_json("[]")
    # a stored algorithm never selects behaviour: anything but AES-256-GCM is refused
    other_alg = Envelope.from_json(json.dumps({**doc, "alg": "ChaCha20-Poly1305"}))
    with pytest.raises(CryptoError, match="CRYPTO_ALGORITHM_NOT_ALLOWED"):
        run(open_envelope(other_alg, AAD, p))


# --- Pseudonymous handles ---------------------------------------------------------------------


def test_handles_are_deterministic_scoped_and_versioned() -> None:
    k1 = LocalHmacKey(1, b"k" * 32)
    h = handle(k1, "tenant-a", "authn", "alice@example.com")
    assert h == handle(k1, "tenant-a", "authn", "alice@example.com")
    assert h.startswith("ph1.1.") and handle_key_version(h) == 1
    assert "alice" not in h
    assert h != handle(k1, "tenant-b", "authn", "alice@example.com")
    assert h != handle(k1, "tenant-a", "mandate", "alice@example.com")
    assert h != handle(k1, "tenant-a", "authn", "bob@example.com")
    # boundaries between tenant/domain/value cannot be shifted
    assert handle(k1, "t", "ab", "c") != handle(k1, "ta", "b", "c")
    k2 = LocalHmacKey(2, b"q" * 32)
    h2 = handle(k2, "tenant-a", "authn", "alice@example.com")
    assert h2.startswith("ph1.2.") and h2 != h
    with pytest.raises(CryptoError, match="CRYPTO_KEY_STRENGTH"):
        LocalHmacKey(1, b"short")


def test_handle_needs_an_hmac_internal_key() -> None:
    wrong = LocalHmacKey(1, b"k" * 32, purpose=KeyPurpose.DATA_KEK)
    with pytest.raises(CryptoError, match="CRYPTO_KEY_PURPOSE_MISMATCH"):
        handle(wrong, "tenant-a", "authn", "x")


def test_provider_hmac_key_rotates_versions() -> None:
    p = LocalKeyProvider(environment="test")
    meta = p.create_key(KeyPurpose.HMAC_INTERNAL, "handles")
    ref = KeyRef(KeyPurpose.HMAC_INTERNAL, "handles")
    h1 = handle(p.hmac_key(ref), "t", "d", "v")
    p.rotate_key(ref)
    h2 = handle(p.hmac_key(ref), "t", "d", "v")
    assert handle_key_version(h1) == meta.version == 1 and handle_key_version(h2) == 2
    # the old version still recomputes old handles
    assert handle(p.hmac_key(ref.at(1)), "t", "d", "v") == h1

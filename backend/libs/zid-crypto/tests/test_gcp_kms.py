"""Google Cloud KMS adapter against a fake client (no network, no google-cloud-kms)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from zid_crypto import CryptoError
from zid_crypto.envelope import Aad, open_envelope, seal
from zid_crypto.gcp_kms import GcpKmsKeyProvider, GcpKmsMacKey, GcpKmsSigner
from zid_crypto.providers import KeyDestroyedError, KeyRef, KeyState
from zid_crypto.purposes import KeyPurpose

AAD = Aad("tenant-a", "evidence", "ev_1", "subject", "1")
RING = "projects/p/locations/europe-west2/keyRings/zid-eu-1/cryptoKeys"
KEK = f"{RING}/data_kek"
ERASE = f"{RING}/subject_erasure_key"
SIGN = f"{RING}/evidence_signing"
HMAC = f"{RING}/hmac_internal"


class ResourceExhausted(Exception):
    pass


class FailedPrecondition(Exception):
    pass


class InvalidArgument(Exception):
    pass


class PermissionDenied(Exception):
    pass


class FakeGcpKms:
    """Cloud KMS semantics that matter here: AAD-bound encryption, version states, call log."""

    def __init__(self) -> None:
        self.keys = {KEK: os.urandom(32), ERASE: os.urandom(32)}
        self.state = {KEK: "ENABLED", ERASE: "ENABLED"}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.signing = ec.generate_private_key(ec.SECP256R1())
        self.mac_key = os.urandom(32)
        self.sign_enabled = True

    def _key(self, name: str) -> bytes:
        if self.state[name] != "ENABLED":
            raise FailedPrecondition(name)
        return self.keys[name]

    def encrypt(self, request: dict[str, Any]) -> Any:
        self.calls.append(("encrypt", request))
        nonce = os.urandom(12)
        aad = request["additional_authenticated_data"]
        blob = nonce + AESGCM(self._key(request["name"])).encrypt(nonce, request["plaintext"], aad)
        return SimpleNamespace(ciphertext=blob)

    def decrypt(self, request: dict[str, Any]) -> Any:
        self.calls.append(("decrypt", request))
        blob = request["ciphertext"]
        try:
            plain = AESGCM(self._key(request["name"])).decrypt(
                blob[:12], blob[12:], request["additional_authenticated_data"]
            )
        except InvalidTag as e:
            raise InvalidArgument("Decryption failed") from e
        return SimpleNamespace(plaintext=plain)

    def get_crypto_key(self, request: dict[str, Any]) -> Any:
        self.calls.append(("get_crypto_key", request))
        name = request["name"]
        return SimpleNamespace(
            name=name,
            create_time=datetime(2026, 1, 1, tzinfo=UTC),
            primary=SimpleNamespace(
                name=f"{name}/cryptoKeyVersions/1", algorithm="GOOGLE_SYMMETRIC_ENCRYPTION"
            ),
        )

    def list_crypto_key_versions(self, request: dict[str, Any]) -> Any:
        self.calls.append(("list_crypto_key_versions", request))
        parent = request["parent"]
        return [SimpleNamespace(name=f"{parent}/cryptoKeyVersions/1", state=self.state[parent])]

    def destroy_crypto_key_version(self, request: dict[str, Any]) -> Any:
        self.calls.append(("destroy_crypto_key_version", request))
        self.state[request["name"].split("/cryptoKeyVersions/")[0]] = "DESTROY_SCHEDULED"
        return SimpleNamespace(state="DESTROY_SCHEDULED")

    def asymmetric_sign(self, request: dict[str, Any]) -> Any:
        self.calls.append(("asymmetric_sign", request))
        if not self.sign_enabled:
            raise FailedPrecondition("disabled")
        digest = request["digest"]["sha256"]
        return SimpleNamespace(
            signature=self.signing.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))
        )

    def get_public_key(self, request: dict[str, Any]) -> Any:
        pem = self.signing.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return SimpleNamespace(pem=pem.decode())

    def mac_sign(self, request: dict[str, Any]) -> Any:
        return SimpleNamespace(mac=hmac.new(self.mac_key, request["data"], hashlib.sha256).digest())


def kms() -> tuple[FakeGcpKms, GcpKmsKeyProvider]:
    fake = FakeGcpKms()
    return fake, GcpKmsKeyProvider(
        fake, {KeyPurpose.DATA_KEK: KEK, KeyPurpose.SUBJECT_ERASURE_KEY: ERASE}
    )


def test_envelope_round_trip_passes_aad_and_pins_the_key() -> None:
    fake, provider = kms()
    ref = KeyRef(KeyPurpose.DATA_KEK, KEK)
    envelope = asyncio.run(seal(b"evidence", AAD, provider, ref))
    assert asyncio.run(open_envelope(envelope, AAD, provider)) == b"evidence"
    enc, dec = fake.calls[0], fake.calls[1]
    assert enc[0] == "encrypt" and dec[0] == "decrypt"
    assert enc[1]["additional_authenticated_data"] == dec[1]["additional_authenticated_data"]
    assert dec[1]["name"] == KEK
    assert envelope.kek == ref.at(1)


def test_context_mismatch_and_purpose_checks() -> None:
    _, provider = kms()
    envelope = asyncio.run(seal(b"x", AAD, provider, KeyRef(KeyPurpose.DATA_KEK, KEK)))
    other = Aad("tenant-b", "evidence", "ev_1", "subject", "1")
    with pytest.raises(CryptoError, match="CRYPTO_KEY_CONTEXT_MISMATCH"):
        asyncio.run(open_envelope(envelope, other, provider))
    with pytest.raises(CryptoError, match="CRYPTO_KEY_PURPOSE_MISMATCH"):
        asyncio.run(seal(b"x", AAD, provider, KeyRef(KeyPurpose.SUBJECT_ERASURE_KEY, KEK)))
    with pytest.raises(CryptoError, match="CRYPTO_KEY_UNKNOWN"):
        asyncio.run(seal(b"x", AAD, provider, KeyRef(KeyPurpose.DATA_KEK, f"{RING}/other")))
    with pytest.raises(CryptoError, match="CRYPTO_KEY_PURPOSE_MISMATCH"):
        GcpKmsKeyProvider(FakeGcpKms(), {KeyPurpose.DATA_KEK: KEK, KeyPurpose.BACKUP_KEK: KEK})


def test_destroy_schedules_destruction_and_blocks_decrypt() -> None:
    fake, provider = kms()
    ref = KeyRef(KeyPurpose.SUBJECT_ERASURE_KEY, ERASE)
    envelope = asyncio.run(seal(b"pii", AAD, provider, ref))
    meta = asyncio.run(provider.destroy_key(ref))
    assert meta.state is KeyState.PENDING_DESTRUCTION
    assert any(c[0] == "destroy_crypto_key_version" for c in fake.calls)
    with pytest.raises(KeyDestroyedError):
        asyncio.run(open_envelope(envelope, AAD, provider))


def test_data_key_is_generated_locally_and_wrapped_by_kms() -> None:
    _, provider = kms()
    ref = KeyRef(KeyPurpose.DATA_KEK, KEK)
    key = asyncio.run(provider.generate_data_key(ref, AAD.context()))
    assert len(key.plaintext) == 32 and key.wrapped != bytes(key.plaintext)
    assert asyncio.run(provider.unwrap_data_key(ref, key.wrapped, AAD.context())) == key.plaintext


def test_throttled_kms_is_retried_briefly_then_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zid_crypto.gcp_kms as kms_module

    pauses: list[float] = []

    async def no_sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(kms_module.asyncio, "sleep", no_sleep)
    fake, provider = kms()
    real = fake.encrypt
    attempts = {"n": 0}

    def flaky(request: dict[str, Any]) -> Any:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise ResourceExhausted("quota")
        return real(request)

    fake.encrypt = flaky  # type: ignore[method-assign]
    env = asyncio.run(seal(b"payload", AAD, provider, KeyRef(KeyPurpose.DATA_KEK, KEK)))
    assert attempts["n"] == 3 and len(pauses) == 2
    assert asyncio.run(open_envelope(env, AAD, provider)) == b"payload"

    def always(request: dict[str, Any]) -> Any:
        attempts["n"] += 1
        raise ResourceExhausted("quota")

    fake.encrypt = always  # type: ignore[method-assign]
    attempts["n"] = 0
    with pytest.raises(CryptoError, match="CRYPTO_THROTTLED"):
        asyncio.run(seal(b"x", AAD, provider, KeyRef(KeyPurpose.DATA_KEK, KEK)))
    assert attempts["n"] == 3


def test_access_denied_fails_closed() -> None:
    fake, provider = kms()

    def denied(request: dict[str, Any]) -> Any:
        raise PermissionDenied("no")

    fake.encrypt = denied  # type: ignore[method-assign]
    with pytest.raises(CryptoError, match="CRYPTO_ACCESS_DENIED"):
        asyncio.run(seal(b"x", AAD, provider, KeyRef(KeyPurpose.DATA_KEK, KEK)))


def test_signer_produces_verifiable_es256_jws_from_the_primary_version() -> None:
    from zid_crypto import sign_jws, verify_jws

    fake = FakeGcpKms()
    signer = GcpKmsSigner(fake, SIGN, kid="ev-1", purpose=KeyPurpose.EVIDENCE_SIGNING)
    token = sign_jws(signer, {"a": 1}, typ="test+jws")
    jwk = signer.public_jwk()
    assert "d" not in jwk and jwk["kid"] == "ev-1" and jwk["alg"] == "ES256"
    _, payload = verify_jws(token, keys=[jwk], typ="test+jws")
    assert payload == {"a": 1}
    signed = next(c for c in fake.calls if c[0] == "asymmetric_sign")
    assert signed[1]["name"] == f"{SIGN}/cryptoKeyVersions/1"
    fake.sign_enabled = False  # a disabled key fails closed, never falls back to a local key
    with pytest.raises(CryptoError, match="CRYPTO_KEY_NOT_ACTIVE"):
        signer.sign(b"x")


def test_signer_accepts_an_explicit_key_version() -> None:
    fake = FakeGcpKms()
    version = f"{SIGN}/cryptoKeyVersions/7"
    GcpKmsSigner(fake, version, kid="ev-7", purpose=KeyPurpose.EVIDENCE_SIGNING).sign(b"x")
    assert fake.calls[-1][1]["name"] == version  # no primary-version lookup


def test_mac_key_makes_stable_handles() -> None:
    from zid_crypto.handles import handle

    key = GcpKmsMacKey(FakeGcpKms(), HMAC, version=2)
    h = handle(key, "tenant-a", "evidence.actor", "p1")
    assert h.startswith("ph1.2.") and h == handle(key, "tenant-a", "evidence.actor", "p1")
    assert h != handle(key, "tenant-b", "evidence.actor", "p1")


def test_throttled_signing_produces_no_signature_and_no_fallback() -> None:
    from zid_crypto import sign_jws

    fake = FakeGcpKms()
    signer = GcpKmsSigner(fake, SIGN, kid="ev-1", purpose=KeyPurpose.EVIDENCE_SIGNING)

    def throttled(request: dict[str, Any]) -> Any:
        raise ResourceExhausted("quota")

    fake.asymmetric_sign = throttled  # type: ignore[method-assign]
    with pytest.raises(CryptoError, match="CRYPTO_THROTTLED"):
        sign_jws(signer, {"a": 1}, typ="test+jws")

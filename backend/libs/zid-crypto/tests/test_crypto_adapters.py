"""AWS KMS and OpenBao adapters against fakes (no network, no boto3)."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from zid_crypto import CryptoError
from zid_crypto.aws_kms import AwsKmsKeyProvider
from zid_crypto.envelope import Aad, open_envelope, seal
from zid_crypto.providers import KeyDestroyedError, KeyRef, KeyState
from zid_crypto.purposes import KeyPurpose
from zid_crypto.secret_broker import EnvSecretBroker, OpenBaoSecretBroker, SecretError

AAD = Aad("tenant-a", "evidence", "ev_1", "subject", "1")
KEK = "arn:aws:kms:eu-west-2:111:key/data"
ERASE = "arn:aws:kms:eu-west-2:111:key/erase"


class ClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeKms:
    """KMS semantics that matter here: context-bound wrapping, key state, call log."""

    def __init__(self) -> None:
        self.keys = {KEK: os.urandom(32), ERASE: os.urandom(32)}
        self.state = {KEK: "Enabled", ERASE: "Enabled"}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _key(self, key_id: str) -> bytes:
        if self.state[key_id] != "Enabled":
            raise ClientError("KMSInvalidStateException")
        return self.keys[key_id]

    def _wrap(self, key_id: str, plaintext: bytes, ctx: dict[str, str]) -> bytes:
        nonce = os.urandom(12)
        aad = json.dumps(ctx, sort_keys=True).encode()
        return nonce + AESGCM(self._key(key_id)).encrypt(nonce, plaintext, aad)

    def generate_data_key(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("generate_data_key", kw))
        assert kw["KeySpec"] == "AES_256"
        dek = os.urandom(32)
        blob = self._wrap(kw["KeyId"], dek, kw["EncryptionContext"])
        return {"Plaintext": dek, "CiphertextBlob": blob, "KeyId": kw["KeyId"]}

    def encrypt(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("encrypt", kw))
        blob = self._wrap(kw["KeyId"], kw["Plaintext"], kw["EncryptionContext"])
        return {"CiphertextBlob": blob}

    def decrypt(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("decrypt", kw))
        blob, aad = kw["CiphertextBlob"], json.dumps(kw["EncryptionContext"], sort_keys=True)
        try:
            plain = AESGCM(self._key(kw["KeyId"])).decrypt(blob[:12], blob[12:], aad.encode())
        except InvalidTag as e:
            raise ClientError("InvalidCiphertextException") from e
        return {"Plaintext": plain, "KeyId": kw["KeyId"]}

    def schedule_key_deletion(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("schedule_key_deletion", kw))
        self.state[kw["KeyId"]] = "PendingDeletion"
        return {"KeyId": kw["KeyId"], "KeyState": "PendingDeletion"}

    def describe_key(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("describe_key", kw))
        return {
            "KeyMetadata": {
                "KeyId": kw["KeyId"],
                "KeyState": self.state[kw["KeyId"]],
                "KeySpec": "SYMMETRIC_DEFAULT",
                "CreationDate": datetime(2026, 1, 1, tzinfo=UTC),
            }
        }


def kms() -> tuple[FakeKms, AwsKmsKeyProvider]:
    fake = FakeKms()
    return fake, AwsKmsKeyProvider(
        fake, {KeyPurpose.DATA_KEK: KEK, KeyPurpose.SUBJECT_ERASURE_KEY: ERASE}
    )


def test_kms_envelope_round_trip_passes_aad_as_encryption_context() -> None:
    fake, provider = kms()
    ref = KeyRef(KeyPurpose.DATA_KEK, KEK)
    envelope = asyncio.run(seal(b"evidence", AAD, provider, ref))
    assert asyncio.run(open_envelope(envelope, AAD, provider)) == b"evidence"
    gen, dec = fake.calls[0], fake.calls[1]
    assert gen[0] == "generate_data_key" and dec[0] == "decrypt"
    expected = {
        "tenant": "tenant-a",
        "object_type": "evidence",
        "object_id": "ev_1",
        "field": "subject",
        "schema_version": "1",
    }
    assert gen[1]["EncryptionContext"] == dec[1]["EncryptionContext"] == expected == AAD.context()
    assert dec[1]["KeyId"] == KEK  # decrypt pins the key
    assert envelope.kek == ref.at(1)


def test_kms_context_mismatch_and_purpose_checks() -> None:
    """CRYP-15: reaching the KMS is not enough. A key opens only for its own purpose and the tenant/object context
    it was wrapped for, and a key bound to two purposes is refused at configuration. Which workload may call the
    key at all is the key policy's job in the deployed account (not exercised here)."""
    _, provider = kms()
    envelope = asyncio.run(seal(b"x", AAD, provider, KeyRef(KeyPurpose.DATA_KEK, KEK)))
    other = Aad("tenant-b", "evidence", "ev_1", "subject", "1")
    with pytest.raises(CryptoError, match="CRYPTO_KEY_CONTEXT_MISMATCH"):
        asyncio.run(open_envelope(envelope, other, provider))
    with pytest.raises(CryptoError, match="CRYPTO_KEY_PURPOSE_MISMATCH"):
        asyncio.run(seal(b"x", AAD, provider, KeyRef(KeyPurpose.SUBJECT_ERASURE_KEY, KEK)))
    with pytest.raises(CryptoError, match="CRYPTO_KEY_UNKNOWN"):
        asyncio.run(seal(b"x", AAD, provider, KeyRef(KeyPurpose.DATA_KEK, "alias/other")))
    with pytest.raises(CryptoError, match="CRYPTO_KEY_PURPOSE_MISMATCH"):
        AwsKmsKeyProvider(FakeKms(), {KeyPurpose.DATA_KEK: KEK, KeyPurpose.BACKUP_KEK: KEK})


def test_kms_destroy_schedules_deletion_and_blocks_decrypt() -> None:
    fake, provider = kms()
    ref = KeyRef(KeyPurpose.SUBJECT_ERASURE_KEY, ERASE)
    envelope = asyncio.run(seal(b"pii", AAD, provider, ref))
    meta = asyncio.run(provider.destroy_key(ref))
    assert meta.state is KeyState.PENDING_DESTRUCTION
    deletion = [c for c in fake.calls if c[0] == "schedule_key_deletion"]
    assert deletion == [("schedule_key_deletion", {"KeyId": ERASE, "PendingWindowInDays": 30})]
    with pytest.raises(KeyDestroyedError) as e:
        asyncio.run(open_envelope(envelope, AAD, provider))
    assert e.value.code == "KEY_DESTROYED"


def test_kms_outage_fails_closed() -> None:
    fake, provider = kms()

    def boom(**kw: Any) -> dict[str, Any]:
        raise ConnectionError("unreachable")

    fake.generate_data_key = boom  # type: ignore[method-assign]
    with pytest.raises(CryptoError, match="CRYPTO_MODULE_UNAVAILABLE"):
        asyncio.run(seal(b"x", AAD, provider, KeyRef(KeyPurpose.DATA_KEK, KEK)))


# --- secrets ------------------------------------------------------------------------------------


def test_env_secret_broker_convention_and_local_only() -> None:
    broker = EnvSecretBroker(environ={"ZID_SECRET__ACME_OIDC": "s3cret"})
    assert broker.get("acme-oidc") == "s3cret"
    with pytest.raises(SecretError, match="SECRET_UNAVAILABLE"):
        broker.get("missing")
    with pytest.raises(CryptoError, match="CRYPTO_LOCAL_PROVIDER_FORBIDDEN"):
        EnvSecretBroker(environment="production")


def openbao(handler: Any, **kw: Any) -> OpenBaoSecretBroker:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenBaoSecretBroker("https://bao.test", client=client, **kw)


def test_openbao_kv2_read_with_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/secret/data/federation/acme-oidc":
            data = {"value": "client-secret", "pem": "-----BEGIN-----"}
            return httpx.Response(200, json={"data": {"data": data, "metadata": {"version": 3}}})
        if request.url.path == "/v1/secret/data/denied":
            return httpx.Response(403, json={"errors": ["permission denied"]})
        return httpx.Response(404, json={"errors": []})

    broker = openbao(handler, token=lambda: "tok-1", namespace="cell-eu-1")
    assert broker.get("federation/acme-oidc") == "client-secret"
    assert broker.get("federation/acme-oidc#pem") == "-----BEGIN-----"
    assert broker.get("federation/acme-oidc") == "client-secret"  # cached
    assert len(seen) == 2
    assert seen[0].headers["X-Vault-Token"] == "tok-1"
    assert seen[0].headers["X-Vault-Namespace"] == "cell-eu-1"
    with pytest.raises(SecretError, match="SECRET_ACCESS_DENIED"):
        broker.get("denied")
    with pytest.raises(SecretError, match="SECRET_UNAVAILABLE"):
        broker.get("absent")
    for bad in ("../sys/raw", "/abs", "a b", ""):
        with pytest.raises(SecretError, match="SECRET_REF_INVALID"):
            broker.get(bad)


def test_openbao_jwt_svid_login_and_relogin() -> None:
    logins: list[dict[str, Any]] = []
    tokens = iter(["tok-a", "tok-b"])
    valid: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/auth/jwt/login":
            logins.append(json.loads(request.content))
            token = next(tokens)
            valid.add(token)
            return httpx.Response(
                200, json={"auth": {"client_token": token, "lease_duration": 600}}
            )
        if request.headers.get("X-Vault-Token") not in valid:
            return httpx.Response(403)
        return httpx.Response(200, json={"data": {"data": {"value": "db-pass"}}})

    broker = openbao(handler, jwt=lambda: "jwt-svid", jwt_role="zid-platform", cache_seconds=0)
    assert broker.get("db/zid") == "db-pass"
    assert logins == [{"role": "zid-platform", "jwt": "jwt-svid"}]
    valid.clear()  # token revoked early: one re-login, then success
    assert broker.get("db/zid") == "db-pass"
    assert len(logins) == 2
    with pytest.raises(ValueError):
        OpenBaoSecretBroker("https://bao.test", jwt=lambda: "x")  # no role
    with pytest.raises(ValueError):
        OpenBaoSecretBroker("https://bao.test")  # no auth


def test_openbao_unreachable_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(SecretError, match="SECRET_UNAVAILABLE"):
        openbao(handler, token=lambda: "t").get("x")


class FakeKmsAsymmetric:
    """KMS Sign/GetPublicKey (ECC_NIST_P256, DER signatures) and GenerateMac (HMAC_256)."""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import ec

        self.key = ec.generate_private_key(ec.SECP256R1())
        self.mac_key = os.urandom(32)
        self.enabled = True

    def sign(self, **kw: Any) -> dict[str, Any]:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        if not self.enabled:
            raise ClientError("DisabledException")
        assert kw["MessageType"] == "RAW" and kw["SigningAlgorithm"] == "ECDSA_SHA_256"
        return {"Signature": self.key.sign(kw["Message"], ec.ECDSA(hashes.SHA256()))}

    def get_public_key(self, **kw: Any) -> dict[str, Any]:
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        der = self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        return {"PublicKey": der, "KeySpec": "ECC_NIST_P256"}

    def generate_mac(self, **kw: Any) -> dict[str, Any]:
        import hashlib
        import hmac

        assert kw["MacAlgorithm"] == "HMAC_SHA_256"
        return {"Mac": hmac.new(self.mac_key, kw["Message"], hashlib.sha256).digest()}


def test_kms_signer_produces_verifiable_es256_jws() -> None:
    from zid_crypto import sign_jws, verify_jws
    from zid_crypto.aws_kms import KmsSigner

    fake = FakeKmsAsymmetric()
    signer = KmsSigner(
        fake, "arn:aws:kms:eu-west-2:111:key/ev", kid="ev-1", purpose=KeyPurpose.EVIDENCE_SIGNING
    )
    token = sign_jws(signer, {"a": 1}, typ="test+jws")
    jwk = signer.public_jwk()
    assert "d" not in jwk and jwk["kid"] == "ev-1" and jwk["alg"] == "ES256"
    _, payload = verify_jws(token, keys=[jwk], typ="test+jws")
    assert payload == {"a": 1}
    fake.enabled = False  # a disabled key fails closed, never falls back to a local key
    with pytest.raises(CryptoError, match="CRYPTO_KEY_NOT_ACTIVE"):
        signer.sign(b"x")


def test_kms_mac_key_makes_stable_handles() -> None:
    from zid_crypto.aws_kms import KmsMacKey
    from zid_crypto.handles import handle

    key = KmsMacKey(FakeKmsAsymmetric(), "arn:aws:kms:eu-west-2:111:key/hmac", version=2)
    h = handle(key, "tenant-a", "evidence.actor", "p1")
    assert h.startswith("ph1.2.") and h == handle(key, "tenant-a", "evidence.actor", "p1")
    assert h != handle(key, "tenant-b", "evidence.actor", "p1")


# --- OPS-17: a throttled or unreachable key service degrades safely --------------------------------


def test_throttled_kms_is_retried_briefly_then_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPS-17: a few throttles are absorbed with a short pause; persistent throttling is a retryable CRYPTO_THROTTLED
    and never an envelope sealed some other way."""
    import zid_crypto.aws_kms as kms_module

    pauses: list[float] = []

    async def no_sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(kms_module.asyncio, "sleep", no_sleep)
    fake, provider = kms()
    real = fake.generate_data_key
    attempts = {"n": 0}

    def flaky(**kw: Any) -> dict[str, Any]:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise ClientError("ThrottlingException")
        return real(**kw)

    fake.generate_data_key = flaky  # type: ignore[method-assign]
    env = asyncio.run(seal(b"payload", AAD, provider, KeyRef(KeyPurpose.DATA_KEK, KEK)))
    assert attempts["n"] == 3 and len(pauses) == 2  # two throttles absorbed, then it worked
    assert asyncio.run(open_envelope(env, AAD, provider)) == b"payload"

    def always(**kw: Any) -> dict[str, Any]:
        attempts["n"] += 1
        raise ClientError("LimitExceededException")

    fake.generate_data_key = always  # type: ignore[method-assign]
    attempts["n"] = 0
    with pytest.raises(CryptoError, match="CRYPTO_THROTTLED"):
        asyncio.run(seal(b"x", AAD, provider, KeyRef(KeyPurpose.DATA_KEK, KEK)))
    assert attempts["n"] == 3  # bounded: it does not hold the request open indefinitely


def test_throttled_signing_produces_no_signature_and_no_fallback() -> None:
    """OPS-17: with the signing key throttled, no token is produced at all: there is no unsigned or locally signed
    substitute."""
    from zid_crypto import sign_jws
    from zid_crypto.aws_kms import KmsSigner

    fake = FakeKmsAsymmetric()
    signer = KmsSigner(
        fake, "arn:aws:kms:eu-west-2:111:key/ev", kid="ev-1", purpose=KeyPurpose.EVIDENCE_SIGNING
    )

    def throttled(**kw: Any) -> dict[str, Any]:
        raise ClientError("ThrottlingException")

    fake.sign = throttled  # type: ignore[method-assign]
    with pytest.raises(CryptoError, match="CRYPTO_THROTTLED"):
        sign_jws(signer, {"a": 1}, typ="test+jws")

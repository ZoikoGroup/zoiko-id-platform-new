"""AWS KMS key provider (DEC-F5 §2: K2/K3 KEKs are KMS keys per purpose per cell).

Dependency-free: it talks to a `KmsClient` (the boto3 KMS client's method shapes). `from_boto3`
imports boto3 lazily, so only images that deploy against KMS need it installed. boto3 is blocking,
so calls run in a worker thread.

- GenerateDataKey (AES_256) / Encrypt / Decrypt always pass `EncryptionContext` = the envelope AAD
  fields, and Decrypt pins `KeyId`, so ciphertext cannot be steered to another key.
- Purpose separation: each purpose has its own configured KMS key ids; a `KeyRef` naming a key
  configured for another purpose is refused before any KMS call.
- `destroy_key` = ScheduleKeyDeletion (KMS enforces a 7-30 day pending window); the key is unusable
  from that moment, which is what crypto-erasure needs (DEC-F1). Errors fail closed (HYOK too).
- KMS rotates key material transparently, so the version recorded in envelopes is the configured
  label (`ref.version`, default 1), not KMS internal state.

`KmsSigner` (a `zid_crypto.Signer`) signs with a KMS asymmetric key (ECC_NIST_P256, ES256): the
private key never leaves KMS, the public JWK comes from GetPublicKey. `KmsMacKey` (a
`zid_crypto.handles.MacKey`) computes pseudonymous handles with a KMS HMAC_256 key. Both are
blocking calls: async callers run them in a worker thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from zid_crypto import CryptoError, b64url
from zid_crypto.providers import DataKey, KeyDestroyedError, KeyMetadata, KeyRef, KeyState
from zid_crypto.purposes import WRAPPING_PURPOSES, KeyPurpose, require_purpose

_STATES = {
    "Enabled": KeyState.ACTIVE,
    "Disabled": KeyState.RETIRED,
    "PendingDeletion": KeyState.PENDING_DESTRUCTION,
    "PendingReplicaDeletion": KeyState.PENDING_DESTRUCTION,
}


class KmsClient(Protocol):
    """The subset of boto3's KMS client used here (keyword arguments, dict responses)."""

    def generate_data_key(self, **kwargs: Any) -> dict[str, Any]: ...
    def encrypt(self, **kwargs: Any) -> dict[str, Any]: ...
    def decrypt(self, **kwargs: Any) -> dict[str, Any]: ...
    def schedule_key_deletion(self, **kwargs: Any) -> dict[str, Any]: ...
    def describe_key(self, **kwargs: Any) -> dict[str, Any]: ...


def _error_code(exc: Exception) -> str | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        return str(code) if code else None
    return None


class AwsKmsKeyProvider:
    def __init__(
        self,
        client: KmsClient,
        key_ids: Mapping[KeyPurpose, str | tuple[str, ...]],
        *,
        pending_window_days: int = 30,
    ) -> None:
        if not 7 <= pending_window_days <= 30:
            raise ValueError("KMS pending window is 7-30 days")
        self._client = client
        self._keys: dict[KeyPurpose, frozenset[str]] = {
            KeyPurpose(p): frozenset((ids,) if isinstance(ids, str) else ids)
            for p, ids in key_ids.items()
        }
        owners: dict[str, KeyPurpose] = {}
        for purpose, ids in self._keys.items():
            for key_id in ids:
                if owners.setdefault(key_id, purpose) is not purpose:
                    raise CryptoError("CRYPTO_KEY_PURPOSE_MISMATCH", f"{key_id} has two purposes")
        self._window = pending_window_days

    @classmethod
    def from_boto3(
        cls, region: str | None, key_ids: Mapping[KeyPurpose, str | tuple[str, ...]]
    ) -> AwsKmsKeyProvider:
        try:
            import boto3  # type: ignore[import-not-found]  # optional: deployed images only
        except ImportError as e:
            raise CryptoError("CRYPTO_MODULE_UNAVAILABLE", "boto3 is not installed") from e
        return cls(boto3.client("kms", region_name=region), key_ids)

    async def _call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        fn = getattr(self._client, method)
        try:
            result: dict[str, Any] = await asyncio.to_thread(fn, **kwargs)
        except CryptoError:
            raise
        except Exception as e:
            code = _error_code(e)
            if code == "InvalidCiphertextException":
                raise CryptoError("CRYPTO_KEY_CONTEXT_MISMATCH") from e
            if code == "KMSInvalidStateException":
                key_id = str(kwargs.get("KeyId"))
                if await self._gone(key_id):
                    raise KeyDestroyedError(key_id) from e
                raise CryptoError("CRYPTO_KEY_NOT_ACTIVE", key_id) from e
            if code == "DisabledException":
                raise CryptoError("CRYPTO_KEY_NOT_ACTIVE", str(kwargs.get("KeyId"))) from e
            if code == "NotFoundException":
                raise CryptoError("CRYPTO_KEY_UNKNOWN", str(kwargs.get("KeyId"))) from e
            if code == "AccessDeniedException":
                raise CryptoError("CRYPTO_ACCESS_DENIED", method) from e
            raise CryptoError(
                "CRYPTO_MODULE_UNAVAILABLE", f"{method}: {code or type(e).__name__}"
            ) from e
        return result

    async def _gone(self, key_id: str) -> bool:
        try:
            info = await self._call("describe_key", KeyId=key_id)
        except CryptoError:
            return False
        return _STATES.get(info["KeyMetadata"].get("KeyState")) is KeyState.PENDING_DESTRUCTION

    def _check(self, ref: KeyRef, *purposes: KeyPurpose) -> None:
        require_purpose(ref.purpose, *purposes)
        if ref.key_id not in self._keys.get(ref.purpose, frozenset()):
            owner = next((p for p, ids in self._keys.items() if ref.key_id in ids), None)
            if owner is not None:
                raise CryptoError("CRYPTO_KEY_PURPOSE_MISMATCH", f"{ref.key_id} is {owner}")
            raise CryptoError("CRYPTO_KEY_UNKNOWN", ref.key_id)

    async def generate_data_key(self, kek: KeyRef, context: Mapping[str, str]) -> DataKey:
        self._check(kek, *WRAPPING_PURPOSES)
        out = await self._call(
            "generate_data_key",
            KeyId=kek.key_id,
            KeySpec="AES_256",
            EncryptionContext=dict(context),
        )
        return DataKey(
            plaintext=bytearray(out["Plaintext"]),
            wrapped=bytes(out["CiphertextBlob"]),
            kek=kek.at(kek.version or 1),
        )

    async def wrap_data_key(
        self, kek: KeyRef, key: bytes | bytearray, context: Mapping[str, str]
    ) -> tuple[bytes, KeyRef]:
        self._check(kek, *WRAPPING_PURPOSES)
        out = await self._call(
            "encrypt", KeyId=kek.key_id, Plaintext=bytes(key), EncryptionContext=dict(context)
        )
        return bytes(out["CiphertextBlob"]), kek.at(kek.version or 1)

    async def unwrap_data_key(
        self, kek: KeyRef, wrapped: bytes, context: Mapping[str, str]
    ) -> bytearray:
        self._check(kek, *WRAPPING_PURPOSES)
        out = await self._call(
            "decrypt", KeyId=kek.key_id, CiphertextBlob=wrapped, EncryptionContext=dict(context)
        )
        return bytearray(out["Plaintext"])

    async def destroy_key(self, ref: KeyRef) -> KeyMetadata:
        self._check(ref, ref.purpose)
        await self._call(
            "schedule_key_deletion", KeyId=ref.key_id, PendingWindowInDays=self._window
        )
        return await self.metadata(ref)

    async def metadata(self, ref: KeyRef) -> KeyMetadata:
        self._check(ref, ref.purpose)
        info = (await self._call("describe_key", KeyId=ref.key_id))["KeyMetadata"]
        state = _STATES.get(info.get("KeyState"), KeyState.RETIRED)
        created = info.get("CreationDate") or datetime.now(UTC)
        spec = info.get("KeySpec", "SYMMETRIC_DEFAULT")
        return KeyMetadata(
            purpose=ref.purpose,
            key_id=ref.key_id,
            version=ref.version or 1,
            algorithm="AES-256-GCM" if spec == "SYMMETRIC_DEFAULT" else str(spec),
            state=state,
            created_at=created,
            activated_at=created if state is KeyState.ACTIVE else None,
            destroyed_at=info.get("DeletionDate"),
            pq_ready=spec in ("SYMMETRIC_DEFAULT", "HMAC_256"),
        )


def _sync_call(client: Any, method: str, **kwargs: Any) -> dict[str, Any]:
    """Blocking KMS call with the provider's error mapping (fail closed on anything unexpected)."""
    try:
        result: dict[str, Any] = getattr(client, method)(**kwargs)
    except CryptoError:
        raise
    except Exception as e:
        code = _error_code(e)
        key_id = str(kwargs.get("KeyId"))
        if code in ("KMSInvalidStateException", "DisabledException"):
            raise CryptoError("CRYPTO_KEY_NOT_ACTIVE", key_id) from e
        if code == "NotFoundException":
            raise CryptoError("CRYPTO_KEY_UNKNOWN", key_id) from e
        if code == "AccessDeniedException":
            raise CryptoError("CRYPTO_ACCESS_DENIED", method) from e
        raise CryptoError(
            "CRYPTO_MODULE_UNAVAILABLE", f"{method}: {code or type(e).__name__}"
        ) from e
    return result


class KmsSigner:
    """ES256 signer backed by a KMS ECC_NIST_P256 SIGN_VERIFY key (DEC-F5 §2: K4 signing keys).

    `kid` is the published key id (stable across the key's life); `key_id` the KMS key id/ARN."""

    def __init__(
        self, client: Any, key_id: str, *, kid: str, purpose: KeyPurpose | None = None
    ) -> None:
        from zid_crypto import _check_policy

        _check_policy(purpose, "ES256")
        self._client, self._key_id, self._kid = client, key_id, kid
        self._purpose = purpose
        self._jwk: dict[str, Any] | None = None

    @property
    def kid(self) -> str:
        return self._kid

    @property
    def alg(self) -> str:
        return "ES256"

    def sign(self, signing_input: bytes) -> bytes:
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        out = _sync_call(
            self._client,
            "sign",
            KeyId=self._key_id,
            Message=signing_input,
            MessageType="RAW",
            SigningAlgorithm="ECDSA_SHA_256",
        )
        r, s = decode_dss_signature(bytes(out["Signature"]))  # KMS returns DER; JWS needs R||S
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    def public_jwk(self) -> dict[str, Any]:
        if self._jwk is None:
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.serialization import load_der_public_key

            out = _sync_call(self._client, "get_public_key", KeyId=self._key_id)
            if out.get("KeySpec") not in (None, "ECC_NIST_P256"):
                raise CryptoError("CRYPTO_ALGORITHM_NOT_ALLOWED", str(out.get("KeySpec")))
            pub = load_der_public_key(bytes(out["PublicKey"]))
            if not isinstance(pub, ec.EllipticCurvePublicKey) or not isinstance(
                pub.curve, ec.SECP256R1
            ):
                raise CryptoError("CRYPTO_ALGORITHM_NOT_ALLOWED", "KMS key is not P-256")
            n = pub.public_numbers()
            self._jwk = {
                "kty": "EC",
                "crv": "P-256",
                "x": b64url(n.x.to_bytes(32, "big")),
                "y": b64url(n.y.to_bytes(32, "big")),
                "kid": self._kid,
                "alg": "ES256",
                "use": "sig",
            }
        return dict(self._jwk)


class KmsMacKey:
    """HMAC_INTERNAL key held in KMS (HMAC_256): `mac` is GenerateMac, the key never leaves KMS."""

    purpose = KeyPurpose.HMAC_INTERNAL

    def __init__(self, client: Any, key_id: str, *, version: int = 1) -> None:
        self._client, self._key_id, self._version = client, key_id, version

    @property
    def version(self) -> int:
        return self._version

    def mac(self, message: bytes) -> bytes:
        out = _sync_call(
            self._client,
            "generate_mac",
            KeyId=self._key_id,
            Message=message,
            MacAlgorithm="HMAC_SHA_256",
        )
        return bytes(out["Mac"])


__all__ = ["AwsKmsKeyProvider", "KmsClient", "KmsMacKey", "KmsSigner"]

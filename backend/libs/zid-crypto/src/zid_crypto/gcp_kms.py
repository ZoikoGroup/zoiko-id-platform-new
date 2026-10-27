"""Google Cloud KMS key provider (DEC-F5 §2: K2/K3 KEKs are KMS keys per purpose per cell).

Same contract as `aws_kms`: dependency-free, it talks to a `GcpKmsClient` (the `google-cloud-kms`
KeyManagementServiceClient method shapes: a `request` dict in, a response object out). `from_google`
imports the SDK lazily, so only images that deploy against Cloud KMS need it. The SDK is blocking, so
calls run in a worker thread.

Differences from AWS KMS that shape this module:
- Cloud KMS has no GenerateDataKey. The data key is generated here (`os.urandom`, 32 bytes) and wrapped
  with `encrypt`; the plaintext is returned to the caller for one use and zeroized by it.
- The envelope AAD fields are passed as `additional_authenticated_data` (canonical JSON) on Encrypt and
  Decrypt, so a wrapped key moved to another tenant/object fails to unwrap.
- Keys are named by the CryptoKey resource name (`projects/P/locations/L/keyRings/R/cryptoKeys/K`);
  Cloud KMS picks the right key version on Decrypt, and Encrypt uses the primary version.
- There is no ScheduleKeyDeletion on a key: `destroy_key` schedules destruction of every live version
  (the key's `destroy_scheduled_duration`, set when the key is created, is the pending window). A
  scheduled version cannot be used, which is what crypto-erasure needs (DEC-F1).
- Signing and MAC use a CryptoKeyVersion. Given a CryptoKey name the signer resolves its primary version.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from zid_crypto import CryptoError, b64url
from zid_crypto.providers import (
    DataKey,
    KeyDestroyedError,
    KeyMetadata,
    KeyRef,
    KeyState,
    canonical_context,
)
from zid_crypto.purposes import WRAPPING_PURPOSES, KeyPurpose, require_purpose

DEK_BYTES = 32
_VERSION_MARKER = "/cryptoKeyVersions/"

_STATES = {
    "ENABLED": KeyState.ACTIVE,
    "PENDING_GENERATION": KeyState.ACTIVE,
    "DISABLED": KeyState.RETIRED,
    "DESTROY_SCHEDULED": KeyState.PENDING_DESTRUCTION,
    "DESTROYED": KeyState.DESTROYED,
}
_DESTROYABLE = frozenset({"ENABLED", "DISABLED", "PENDING_GENERATION"})


class GcpKmsClient(Protocol):
    """The subset of google-cloud-kms's KeyManagementServiceClient used here."""

    def encrypt(self, request: dict[str, Any]) -> Any: ...
    def decrypt(self, request: dict[str, Any]) -> Any: ...
    def get_crypto_key(self, request: dict[str, Any]) -> Any: ...
    def list_crypto_key_versions(self, request: dict[str, Any]) -> Any: ...
    def destroy_crypto_key_version(self, request: dict[str, Any]) -> Any: ...


# Cloud KMS says "slow down" with ResourceExhausted (quota) and brief Unavailable/DeadlineExceeded. As with AWS, the
# provider retries a few times with a short growing pause, then fails closed with CRYPTO_THROTTLED (retryable) and
# never returns an unsigned or unencrypted result in its place (OPS-17).
THROTTLE_ERRORS = frozenset({"ResourceExhausted", "TooManyRequests"})
TRANSIENT_ERRORS = frozenset({"ServiceUnavailable", "DeadlineExceeded", "InternalServerError"})
THROTTLE_RETRY_DELAYS = (0.05, 0.15)  # three attempts in all


def _error_name(exc: Exception) -> str:
    """The SDK error class name (google.api_core.exceptions.*), matched by name so no SDK import is needed."""
    return type(exc).__name__


def _map_error(exc: Exception, method: str, key_id: str) -> CryptoError:
    name = _error_name(exc)
    if name in THROTTLE_ERRORS or name in TRANSIENT_ERRORS:
        return CryptoError("CRYPTO_THROTTLED", method)
    if name == "InvalidArgument" and method == "decrypt":
        return CryptoError(
            "CRYPTO_KEY_CONTEXT_MISMATCH"
        )  # wrong AAD or ciphertext not for this key
    if name == "FailedPrecondition":
        return CryptoError(
            "CRYPTO_KEY_NOT_ACTIVE", key_id
        )  # disabled, scheduled or destroyed version
    if name == "NotFound":
        return CryptoError("CRYPTO_KEY_UNKNOWN", key_id)
    if name in ("PermissionDenied", "Unauthenticated", "Forbidden"):
        return CryptoError("CRYPTO_ACCESS_DENIED", method)
    return CryptoError("CRYPTO_MODULE_UNAVAILABLE", f"{method}: {name}")


def _enum_name(value: Any) -> str:
    """Enum member name for both SDK enums (`.name`) and plain strings (fakes)."""
    return str(getattr(value, "name", value))


def _utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


class GcpKmsKeyProvider:
    def __init__(
        self,
        client: GcpKmsClient,
        key_ids: Mapping[KeyPurpose, str | tuple[str, ...]],
        *,
        fips_endpoint: bool = False,
    ) -> None:
        # Cloud KMS HSM-protected keys are FIPS 140-2 Level 3; software keys are Level 1. Set by the deployment
        # that created the keys (protection level HSM); the provider cannot see it from the client alone.
        self.fips_validated = fips_endpoint
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

    @classmethod
    def from_google(
        cls,
        key_ids: Mapping[KeyPurpose, str | tuple[str, ...]],
        *,
        fips_endpoint: bool = False,
    ) -> GcpKmsKeyProvider:
        return cls(google_kms_client(), key_ids, fips_endpoint=fips_endpoint)

    async def _call(self, method: str, key_id: str, request: dict[str, Any]) -> Any:
        fn = getattr(self._client, method)
        delays = iter(THROTTLE_RETRY_DELAYS)
        while True:
            try:
                return await asyncio.to_thread(fn, request=request)
            except CryptoError:
                raise
            except Exception as e:
                mapped = _map_error(e, method, key_id)
                if mapped.code == "CRYPTO_THROTTLED":
                    pause = next(delays, None)
                    if pause is not None:
                        await asyncio.sleep(pause)
                        continue
                if mapped.code == "CRYPTO_KEY_NOT_ACTIVE" and await self._gone(key_id):
                    raise KeyDestroyedError(key_id) from e
                raise mapped from e

    async def _gone(self, key_id: str) -> bool:
        try:
            states = await self._version_states(key_id)
        except CryptoError:
            return False
        return bool(states) and all(
            s in (KeyState.PENDING_DESTRUCTION, KeyState.DESTROYED) for s in states
        )

    async def _version_states(self, key_id: str) -> list[KeyState]:
        versions = await self._versions(key_id)
        return [_STATES.get(_enum_name(v.state), KeyState.RETIRED) for v in versions]

    async def _versions(self, key_id: str) -> list[Any]:
        out = await self._call("list_crypto_key_versions", key_id, {"parent": key_id})
        return list(out)

    def _check(self, ref: KeyRef, *purposes: KeyPurpose) -> None:
        require_purpose(ref.purpose, *purposes)
        if ref.key_id not in self._keys.get(ref.purpose, frozenset()):
            owner = next((p for p, ids in self._keys.items() if ref.key_id in ids), None)
            if owner is not None:
                raise CryptoError("CRYPTO_KEY_PURPOSE_MISMATCH", f"{ref.key_id} is {owner}")
            raise CryptoError("CRYPTO_KEY_UNKNOWN", ref.key_id)

    async def _encrypt(self, key_id: str, plaintext: bytes, context: Mapping[str, str]) -> bytes:
        out = await self._call(
            "encrypt",
            key_id,
            {
                "name": key_id,
                "plaintext": plaintext,
                "additional_authenticated_data": canonical_context(context),
            },
        )
        return bytes(out.ciphertext)

    async def generate_data_key(self, kek: KeyRef, context: Mapping[str, str]) -> DataKey:
        self._check(kek, *WRAPPING_PURPOSES)
        dek = bytearray(os.urandom(DEK_BYTES))
        try:
            wrapped = await self._encrypt(kek.key_id, bytes(dek), context)
        except BaseException:
            for i in range(len(dek)):
                dek[i] = 0
            raise
        return DataKey(plaintext=dek, wrapped=wrapped, kek=kek.at(kek.version or 1))

    async def wrap_data_key(
        self, kek: KeyRef, key: bytes | bytearray, context: Mapping[str, str]
    ) -> tuple[bytes, KeyRef]:
        self._check(kek, *WRAPPING_PURPOSES)
        return await self._encrypt(kek.key_id, bytes(key), context), kek.at(kek.version or 1)

    async def unwrap_data_key(
        self, kek: KeyRef, wrapped: bytes, context: Mapping[str, str]
    ) -> bytearray:
        self._check(kek, *WRAPPING_PURPOSES)
        out = await self._call(
            "decrypt",
            kek.key_id,
            {
                "name": kek.key_id,
                "ciphertext": wrapped,
                "additional_authenticated_data": canonical_context(context),
            },
        )
        return bytearray(out.plaintext)

    async def destroy_key(self, ref: KeyRef) -> KeyMetadata:
        self._check(ref, ref.purpose)
        for version in await self._versions(ref.key_id):
            if _enum_name(version.state) in _DESTROYABLE:
                await self._call("destroy_crypto_key_version", ref.key_id, {"name": version.name})
        return await self.metadata(ref)

    async def metadata(self, ref: KeyRef) -> KeyMetadata:
        self._check(ref, ref.purpose)
        key = await self._call("get_crypto_key", ref.key_id, {"name": ref.key_id})
        states = await self._version_states(ref.key_id)
        if not states:
            state = KeyState.RETIRED
        elif any(s is KeyState.ACTIVE for s in states):
            state = KeyState.ACTIVE
        elif any(s is KeyState.RETIRED for s in states):
            state = KeyState.RETIRED
        elif any(s is KeyState.PENDING_DESTRUCTION for s in states):
            state = KeyState.PENDING_DESTRUCTION
        else:
            state = KeyState.DESTROYED
        created = _utc(getattr(key, "create_time", None)) or datetime.now(UTC)
        algorithm = (
            _enum_name(getattr(getattr(key, "primary", None), "algorithm", "")) or "AES-256-GCM"
        )
        if algorithm in ("GOOGLE_SYMMETRIC_ENCRYPTION", "EXTERNAL_SYMMETRIC_ENCRYPTION"):
            algorithm = "AES-256-GCM"
        return KeyMetadata(
            purpose=ref.purpose,
            key_id=ref.key_id,
            version=ref.version or 1,
            algorithm=algorithm,
            state=state,
            created_at=created,
            activated_at=created if state is KeyState.ACTIVE else None,
            destroyed_at=datetime.now(UTC) if state is KeyState.DESTROYED else None,
            pq_ready=algorithm in ("AES-256-GCM", "HMAC_SHA256"),
        )


def google_kms_client() -> Any:
    """The google-cloud-kms client using Application Default Credentials (the VM's service account)."""
    try:
        from google.cloud import kms  # type: ignore[import-not-found]  # optional: deployed images only
    except ImportError as e:
        raise CryptoError("CRYPTO_MODULE_UNAVAILABLE", "google-cloud-kms is not installed") from e
    return kms.KeyManagementServiceClient()


def _sync_call(client: Any, method: str, key_id: str, request: dict[str, Any]) -> Any:
    """Blocking KMS call with the provider's error mapping (fail closed on anything unexpected)."""
    try:
        return getattr(client, method)(request=request)
    except CryptoError:
        raise
    except Exception as e:
        raise _map_error(e, method, key_id) from e


def _version_name(client: Any, key_id: str) -> str:
    """A CryptoKeyVersion name: as given, or the primary version of the CryptoKey."""
    if _VERSION_MARKER in key_id:
        return key_id
    key = _sync_call(client, "get_crypto_key", key_id, {"name": key_id})
    primary = getattr(key, "primary", None)
    name = getattr(primary, "name", None)
    if not name:
        raise CryptoError("CRYPTO_KEY_NOT_ACTIVE", key_id)
    return str(name)


class GcpKmsSigner:
    """ES256 signer backed by a Cloud KMS EC_SIGN_P256_SHA256 key (DEC-F5 §2: K4 signing keys).

    `kid` is the published key id (stable across the key's life); `key_id` the CryptoKey or CryptoKeyVersion
    name. Given a CryptoKey the primary version is used, resolved once. Rotation: add a version and promote it,
    then publish a new `kid` for the new public key."""

    def __init__(
        self, client: Any, key_id: str, *, kid: str, purpose: KeyPurpose | None = None
    ) -> None:
        from zid_crypto import _check_policy

        _check_policy(purpose, "ES256")
        self._client, self._key_id, self._kid = client, key_id, kid
        self._purpose = purpose
        self._version: str | None = None
        self._jwk: dict[str, Any] | None = None

    @property
    def kid(self) -> str:
        return self._kid

    @property
    def alg(self) -> str:
        return "ES256"

    def _name(self) -> str:
        if self._version is None:
            self._version = _version_name(self._client, self._key_id)
        return self._version

    def sign(self, signing_input: bytes) -> bytes:
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        out = _sync_call(
            self._client,
            "asymmetric_sign",
            self._key_id,
            {"name": self._name(), "digest": {"sha256": hashlib.sha256(signing_input).digest()}},
        )
        r, s = decode_dss_signature(bytes(out.signature))  # KMS returns DER; JWS needs R||S
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    def public_jwk(self) -> dict[str, Any]:
        if self._jwk is None:
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.serialization import load_pem_public_key

            out = _sync_call(self._client, "get_public_key", self._key_id, {"name": self._name()})
            pub = load_pem_public_key(str(out.pem).encode())
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


class GcpKmsMacKey:
    """HMAC_INTERNAL key held in Cloud KMS (HMAC_SHA256): `mac` is MacSign, the key never leaves KMS."""

    purpose = KeyPurpose.HMAC_INTERNAL

    def __init__(self, client: Any, key_id: str, *, version: int = 1) -> None:
        self._client, self._key_id, self._version = client, key_id, version
        self._name: str | None = None

    @property
    def version(self) -> int:
        return self._version

    def mac(self, message: bytes) -> bytes:
        if self._name is None:
            self._name = _version_name(self._client, self._key_id)
        out = _sync_call(
            self._client, "mac_sign", self._key_id, {"name": self._name, "data": message}
        )
        return bytes(out.mac)


__all__ = [
    "GcpKmsClient",
    "GcpKmsKeyProvider",
    "GcpKmsMacKey",
    "GcpKmsSigner",
    "google_kms_client",
]

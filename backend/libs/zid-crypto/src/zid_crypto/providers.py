"""Key providers: where KEKs live and how data keys are wrapped (DEC-F5 §1-3; CRYPTO-MI-001 §6-7).

`KeyProvider` is the only interface application code uses for key-encryption keys. A KEK is named by
a `KeyRef` (purpose, key id, version) and every wrap/unwrap is bound to an encryption context, so a
wrapped data key moved to another tenant/object fails to unwrap (§19).

- `LocalKeyProvider`: development and tests only. KEKs are random AES-256 keys held in memory and,
  optionally, in a local JSON keystore file so separate local processes share them. It refuses to
  start outside local/test, like the development signing keys.
- `zid_crypto.aws_kms.AwsKmsKeyProvider`: deployed cells (KMS GenerateDataKey/Encrypt/Decrypt).
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from zid_crypto import CryptoError
from zid_crypto.handles import LocalHmacKey
from zid_crypto.policy import CryptoPolicyRegistry, default_registry
from zid_crypto.purposes import WRAPPING_PURPOSES, KeyPurpose, require_purpose

LOCAL_ENVIRONMENTS = ("local", "test")
NONCE_BYTES = 12
DEK_BYTES = 32


class KeyState(StrEnum):
    ACTIVE = "ACTIVE"  # may create new artifacts
    RETIRED = "RETIRED"  # superseded; unwrap only (background rewrap, §8)
    PENDING_DESTRUCTION = "PENDING_DESTRUCTION"  # scheduled (KMS pending window); unusable
    DESTROYED = "DESTROYED"  # material gone; metadata kept as evidence


class KeyDestroyedError(CryptoError):
    """The key material is destroyed or scheduled for destruction (crypto-erasure, DEC-F1)."""

    def __init__(self, detail: str = "") -> None:
        super().__init__("KEY_DESTROYED", detail)


@dataclass(frozen=True, slots=True)
class KeyRef:
    """Names a key: one purpose, a provider key id and (optionally) a version.

    `version=None` means the current active version; envelopes always record the exact version."""

    purpose: KeyPurpose
    key_id: str
    version: int | None = None

    def at(self, version: int) -> KeyRef:
        return replace(self, version=version)


@dataclass(frozen=True, slots=True)
class KeyMetadata:
    purpose: KeyPurpose
    key_id: str
    version: int
    algorithm: str
    state: KeyState
    created_at: datetime
    activated_at: datetime | None = None
    retired_at: datetime | None = None
    destroyed_at: datetime | None = None
    pq_ready: bool = False  # 256-bit symmetric keys are; classical asymmetric keys are not

    @property
    def ref(self) -> KeyRef:
        return KeyRef(self.purpose, self.key_id, self.version)


@dataclass(slots=True)
class DataKey:
    """A fresh data key: plaintext for one use (zeroize after), wrapped copy for storage."""

    plaintext: bytearray
    wrapped: bytes
    kek: KeyRef  # exact version that wrapped it

    def zeroize(self) -> None:
        zeroize(self.plaintext)


def zeroize(buffer: bytearray) -> None:
    """Best-effort: overwrite key material held in a mutable buffer (CPython may keep copies)."""
    for i in range(len(buffer)):
        buffer[i] = 0


class KeyProvider(Protocol):
    async def generate_data_key(self, kek: KeyRef, context: Mapping[str, str]) -> DataKey: ...
    async def wrap_data_key(
        self, kek: KeyRef, key: bytes | bytearray, context: Mapping[str, str]
    ) -> tuple[bytes, KeyRef]: ...
    async def unwrap_data_key(
        self, kek: KeyRef, wrapped: bytes, context: Mapping[str, str]
    ) -> bytearray: ...
    async def destroy_key(self, ref: KeyRef) -> KeyMetadata: ...
    async def metadata(self, ref: KeyRef) -> KeyMetadata: ...


def canonical_context(context: Mapping[str, str]) -> bytes:
    """Deterministic encoding of an encryption context (sorted keys, no whitespace)."""
    if not context or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in context.items()
    ):
        raise CryptoError("CRYPTO_KEY_CONTEXT_MISMATCH", "a non-empty str->str context is required")
    return json.dumps(dict(context), sort_keys=True, separators=(",", ":")).encode()


def require_local_environment(environment: str, what: str) -> None:
    if environment not in LOCAL_ENVIRONMENTS:
        raise CryptoError(
            "CRYPTO_LOCAL_PROVIDER_FORBIDDEN",
            f"{what} is for local/test only; environment {environment!r} needs KMS/HSM (DEC-F5)",
        )


@dataclass
class _Version:
    meta: KeyMetadata
    material: bytearray | None


@dataclass
class _Key:
    purpose: KeyPurpose
    versions: dict[int, _Version] = field(default_factory=dict)

    def current(self) -> _Version:
        return self.versions[max(self.versions)]


class LocalKeyProvider:
    """Development/test KEKs (AES-256-GCM wrapping). Never used outside local/test."""

    fips_validated = False  # an in-process software key is not a validated cryptographic module

    def __init__(
        self,
        *,
        environment: str,
        keystore: Path | str | None = None,
        registry: CryptoPolicyRegistry | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        require_local_environment(environment, "LocalKeyProvider")
        self._registry = registry or default_registry()
        self._clock = clock
        self._path = Path(keystore) if keystore else None
        self._keys: dict[str, _Key] = {}
        if self._path and self._path.exists():
            self._load()

    # --- administration (local only; deployed keys are created by ceremony/IaC) ---------------

    def create_key(
        self, purpose: KeyPurpose, key_id: str, *, algorithm: str | None = None
    ) -> KeyMetadata:
        if key_id in self._keys:
            raise CryptoError("CRYPTO_KEY_EXISTS", key_id)
        self._keys[key_id] = _Key(KeyPurpose(purpose))
        return self._new_version(key_id, algorithm)

    def ensure_key(self, purpose: KeyPurpose, key_id: str) -> KeyMetadata:
        """Create `key_id` on first use; an existing key must have the same purpose."""
        if key_id in self._keys:
            require_purpose(self._keys[key_id].purpose, KeyPurpose(purpose))
            return self._keys[key_id].current().meta
        return self.create_key(purpose, key_id)

    def rotate_key(self, ref: KeyRef) -> KeyMetadata:
        """New ACTIVE version; the previous one is RETIRED (still unwraps until rewrapped)."""
        key = self._key(ref)
        current = key.current()
        if current.meta.state is KeyState.ACTIVE:
            current.meta = replace(current.meta, state=KeyState.RETIRED, retired_at=self._clock())
        return self._new_version(ref.key_id, current.meta.algorithm)

    def hmac_key(self, ref: KeyRef) -> LocalHmacKey:
        """An HMAC_INTERNAL key for `zid_crypto.handles.handle` (current version unless pinned)."""
        require_purpose(ref.purpose, KeyPurpose.HMAC_INTERNAL)
        version = self._version(ref, usable_for_new=ref.version is None)
        assert version.material is not None
        return LocalHmacKey(version.meta.version, bytes(version.material))

    # --- KeyProvider ----------------------------------------------------------------------------

    async def generate_data_key(self, kek: KeyRef, context: Mapping[str, str]) -> DataKey:
        dek = bytearray(secrets.token_bytes(DEK_BYTES))
        wrapped, exact = await self.wrap_data_key(kek, dek, context)
        return DataKey(plaintext=dek, wrapped=wrapped, kek=exact)

    async def wrap_data_key(
        self, kek: KeyRef, key: bytes | bytearray, context: Mapping[str, str]
    ) -> tuple[bytes, KeyRef]:
        require_purpose(kek.purpose, *WRAPPING_PURPOSES)
        version = self._version(kek, usable_for_new=True)
        assert version.material is not None
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = AESGCM(version.material).encrypt(nonce, bytes(key), canonical_context(context))
        return nonce + sealed, version.meta.ref

    async def unwrap_data_key(
        self, kek: KeyRef, wrapped: bytes, context: Mapping[str, str]
    ) -> bytearray:
        require_purpose(kek.purpose, *WRAPPING_PURPOSES)
        if kek.version is None:
            raise CryptoError("CRYPTO_KEY_UNKNOWN", "unwrap needs the exact key version")
        version = self._version(kek, usable_for_new=False)
        assert version.material is not None
        try:
            plain = AESGCM(version.material).decrypt(
                wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], canonical_context(context)
            )
        except InvalidTag as e:
            raise CryptoError("CRYPTO_KEY_CONTEXT_MISMATCH") from e
        return bytearray(plain)

    async def destroy_key(self, ref: KeyRef) -> KeyMetadata:
        """Destroy one version, or every version when `ref.version` is None (crypto-erasure)."""
        key = self._key(ref)
        require_purpose(key.purpose, ref.purpose)
        now = self._clock()
        targets = (
            [key.versions[ref.version]] if ref.version is not None else list(key.versions.values())
        )
        for v in targets:
            if v.material is not None:
                zeroize(v.material)
            v.material = None
            v.meta = replace(v.meta, state=KeyState.DESTROYED, destroyed_at=now)
        self._save()
        return targets[-1].meta

    async def metadata(self, ref: KeyRef) -> KeyMetadata:
        key = self._key(ref)
        require_purpose(key.purpose, ref.purpose)
        return (key.versions[ref.version] if ref.version is not None else key.current()).meta

    # --- internals ------------------------------------------------------------------------------

    def _key(self, ref: KeyRef) -> _Key:
        key = self._keys.get(ref.key_id)
        if key is None or (ref.version is not None and ref.version not in key.versions):
            raise CryptoError("CRYPTO_KEY_UNKNOWN", f"{ref.key_id} v{ref.version}")
        return key

    def _version(self, ref: KeyRef, *, usable_for_new: bool) -> _Version:
        key = self._key(ref)
        require_purpose(ref.purpose, key.purpose)  # a ref never re-labels a key's purpose
        version = key.versions[ref.version] if ref.version is not None else key.current()
        state = version.meta.state
        if state in (KeyState.DESTROYED, KeyState.PENDING_DESTRUCTION) or version.material is None:
            raise KeyDestroyedError(f"{ref.key_id} v{version.meta.version}")
        if usable_for_new and state is not KeyState.ACTIVE:
            raise CryptoError("CRYPTO_KEY_NOT_ACTIVE", f"{ref.key_id} v{version.meta.version}")
        return version

    def _new_version(self, key_id: str, algorithm: str | None) -> KeyMetadata:
        key = self._keys[key_id]
        alg = self._registry.require(
            key.purpose, algorithm or self._registry.algorithms(key.purpose)[0]
        )
        if alg not in ("AES-256-GCM", "HMAC-SHA-256"):
            raise CryptoError(
                "CRYPTO_ALGORITHM_NOT_ALLOWED", f"local keys are symmetric, not {alg}"
            )
        number = max(key.versions, default=0) + 1
        now = self._clock()
        meta = KeyMetadata(
            purpose=key.purpose,
            key_id=key_id,
            version=number,
            algorithm=alg,
            state=KeyState.ACTIVE,
            created_at=now,
            activated_at=now,
            pq_ready=True,  # 256-bit symmetric
        )
        key.versions[number] = _Version(meta, bytearray(secrets.token_bytes(32)))
        self._save()
        return meta

    def _save(self) -> None:
        if self._path is None:
            return
        doc: dict[str, Any] = {}
        for key_id, key in self._keys.items():
            doc[key_id] = {
                "purpose": key.purpose.value,
                "versions": [
                    {
                        **{
                            k: (v.isoformat() if isinstance(v, datetime) else v)
                            for k, v in _meta_fields(ver.meta).items()
                        },
                        "material": base64.b64encode(ver.material).decode()
                        if ver.material is not None
                        else None,
                    }
                    for ver in key.versions.values()
                ],
            }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        os.replace(tmp, self._path)

    def _load(self) -> None:
        assert self._path is not None
        doc = json.loads(self._path.read_text(encoding="utf-8"))
        for key_id, body in doc.items():
            key = _Key(KeyPurpose(body["purpose"]))
            for raw in body["versions"]:
                material = raw.pop("material")
                times = {
                    k: datetime.fromisoformat(raw[k]) if raw.get(k) else None
                    for k in ("created_at", "activated_at", "retired_at", "destroyed_at")
                }
                meta = KeyMetadata(
                    purpose=key.purpose,
                    key_id=key_id,
                    version=int(raw["version"]),
                    algorithm=str(raw["algorithm"]),
                    state=KeyState(raw["state"]),
                    created_at=times["created_at"] or self._clock(),
                    activated_at=times["activated_at"],
                    retired_at=times["retired_at"],
                    destroyed_at=times["destroyed_at"],
                    pq_ready=bool(raw.get("pq_ready")),
                )
                key.versions[meta.version] = _Version(
                    meta, bytearray(base64.b64decode(material)) if material else None
                )
            self._keys[key_id] = key


def _meta_fields(meta: KeyMetadata) -> dict[str, object]:
    return {
        "version": meta.version,
        "algorithm": meta.algorithm,
        "state": meta.state.value,
        "created_at": meta.created_at,
        "activated_at": meta.activated_at,
        "retired_at": meta.retired_at,
        "destroyed_at": meta.destroyed_at,
        "pq_ready": meta.pq_ready,
    }


__all__ = [
    "DataKey",
    "KeyDestroyedError",
    "KeyMetadata",
    "KeyProvider",
    "KeyRef",
    "KeyState",
    "LocalKeyProvider",
    "canonical_context",
    "require_local_environment",
    "zeroize",
]

"""Software key custody in a database: standard-assurance keys for deployments without a KMS/HSM (DEC-F5 amendment, CR-060).

Every key is a row in a key store (PostgreSQL in the platform), encrypted by a **master key held outside the database** (a
root-only file, or a secret delivered by the operator's secret service). A stolen database, backup or snapshot is useless
without the master key. What this does NOT protect against is anyone who controls a running process or the host that
holds the master key: unlike a KMS/HSM, the plaintext keys exist in process memory. It is therefore not FIPS-validated
(`fips_validated = False`, so FIPS-required operations refuse it) and it is allowed in a deployed environment only with an
explicit risk acknowledgement (see `zid_platform.crypto_runtime`).

- `MasterKeys`: the master key(s) and the envelope format that wraps one key's material. The first master key in the file
  is current; older ones stay readable so a master rotation can re-wrap rows without downtime.
- `KeyStore` / `MemoryKeyStore`: where wrapped rows live. Rows are bound to their identity by the AEAD associated data, so a
  row moved to another key id, version, purpose or algorithm does not unwrap.
- `SoftwareKeyProvider`: the same `KeyProvider` (wrap, unwrap, rotate, destroy, HMAC) as the local one, but persisted.
- `SoftwareSigners`: asymmetric signing keys (ES256) generated in process, stored wrapped, with rotation that keeps the
  retired public keys.

Destroying a key (crypto-erasure) overwrites its stored material and marks the row. Backups taken before that still hold the
wrapped key, so erasure also depends on retiring the master key those backups were wrapped under (see the runbook).
"""

from __future__ import annotations

import asyncio
import base64
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from zid_crypto import CryptoError, LocalSigner
from zid_crypto.policy import CryptoPolicyRegistry, default_registry
from zid_crypto.providers import (
    KeyMetadata,
    KeyRef,
    KeyState,
    LocalKeyProvider,
    _Key,
    _Version,
    zeroize,
)
from zid_crypto.purposes import KeyPurpose, require_purpose

MASTER_KEY_BYTES = 32
NONCE_BYTES = 12
BLOB_MAGIC = b"ZK1"
SYMMETRIC_ALGORITHMS = frozenset({"AES-256-GCM", "HMAC-SHA-256"})
SIGNING_ALGORITHM = "ES256"
GENERIC_SIGNING_PURPOSE = (
    "SIGNING"  # a signing key bound to no registry purpose (placement statements)
)


class MasterKeyError(CryptoError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("CRYPTO_MASTER_KEY", detail)


class MasterKeys:
    """The master key(s) that wrap every stored key. `current` wraps new rows; the others only unwrap."""

    def __init__(self, keys: Mapping[str, bytes], current: str) -> None:
        if current not in keys:
            raise MasterKeyError("the current master key id is not in the set")
        for mid, key in keys.items():
            if len(key) != MASTER_KEY_BYTES:
                raise MasterKeyError(f"master key {mid!r} must be {MASTER_KEY_BYTES} bytes")
            if not 0 < len(mid.encode()) < 256:
                raise MasterKeyError("master key ids are 1 to 255 bytes")
        self._keys = dict(keys)
        self.current = current

    @classmethod
    def parse(cls, text: str) -> MasterKeys:
        """One master key per line, `<id> <base64 of 32 bytes>`; blank lines and `#` comments ignored; the first is current."""
        keys: dict[str, bytes] = {}
        current: str | None = None
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2:
                raise MasterKeyError("a master key line is `<id> <base64 key>`")
            try:
                material = base64.b64decode(parts[1], validate=True)
            except ValueError as e:
                raise MasterKeyError("a master key is not valid base64") from e
            if parts[0] in keys:
                raise MasterKeyError(f"duplicate master key id {parts[0]!r}")
            keys[parts[0]] = material
            current = current or parts[0]
        if current is None:
            raise MasterKeyError("the master key file holds no key")
        return cls(keys, current)

    @classmethod
    def from_file(cls, path: str | Path) -> MasterKeys:
        try:
            return cls.parse(Path(path).read_text(encoding="utf-8"))
        except OSError as e:
            raise MasterKeyError(f"the master key file cannot be read: {type(e).__name__}") from e

    @staticmethod
    def generate(master_id: str) -> str:
        """A fresh master key line for a new file (`<id> <base64>`)."""
        return f"{master_id} {base64.b64encode(secrets.token_bytes(MASTER_KEY_BYTES)).decode()}"

    def wrap(self, material: bytes | bytearray, aad: bytes) -> bytes:
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = AESGCM(self._keys[self.current]).encrypt(nonce, bytes(material), aad)
        mid = self.current.encode()
        return BLOB_MAGIC + bytes([len(mid)]) + mid + nonce + sealed

    def master_id_of(self, blob: bytes) -> str:
        if not blob.startswith(BLOB_MAGIC) or len(blob) < len(BLOB_MAGIC) + 2:
            raise MasterKeyError("not a wrapped key")
        n = blob[len(BLOB_MAGIC)]
        return blob[len(BLOB_MAGIC) + 1 : len(BLOB_MAGIC) + 1 + n].decode()

    def unwrap(self, blob: bytes, aad: bytes) -> bytearray:
        mid = self.master_id_of(blob)
        key = self._keys.get(mid)
        if key is None:
            raise MasterKeyError(f"the master key {mid!r} that wrapped this key is not available")
        body = blob[len(BLOB_MAGIC) + 1 + len(mid.encode()) :]
        try:
            return bytearray(AESGCM(key).decrypt(body[:NONCE_BYTES], body[NONCE_BYTES:], aad))
        except InvalidTag as e:
            raise MasterKeyError(
                "a stored key does not unwrap (wrong master key, or the row was altered or moved)"
            ) from e


@dataclass(frozen=True, slots=True)
class StoredKey:
    """One version of one key as the store holds it."""

    key_id: str
    version: int
    purpose: str
    algorithm: str
    state: str
    wrapped: bytes | None  # None once destroyed
    public_jwk: dict[str, Any] | None
    created_at: datetime
    activated_at: datetime | None = None
    retired_at: datetime | None = None
    destroyed_at: datetime | None = None
    pq_ready: bool = False

    @property
    def aad(self) -> bytes:
        return f"zid-key-v1|{self.key_id}|{self.version}|{self.purpose}|{self.algorithm}".encode()


class KeyStore(Protocol):
    """Synchronous on purpose: keys are loaded while the application is built. The platform's PostgreSQL store runs its
    queries on a private event loop."""

    def load_all(self) -> list[StoredKey]: ...
    def insert(self, row: StoredKey) -> bool:
        """Add a version; False when (key_id, version) already exists, because another process created it first."""
        ...

    def update(self, row: StoredKey) -> None:
        """Change state and timestamps of an existing version, or destroy it (wrapped=None)."""
        ...


class MemoryKeyStore:
    """For tests and single-process tools."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, int], StoredKey] = {}

    def load_all(self) -> list[StoredKey]:
        return list(self.rows.values())

    def insert(self, row: StoredKey) -> bool:
        if (row.key_id, row.version) in self.rows:
            return False
        self.rows[(row.key_id, row.version)] = row
        return True

    def update(self, row: StoredKey) -> None:
        if (row.key_id, row.version) not in self.rows:
            raise CryptoError("CRYPTO_KEY_UNKNOWN", f"{row.key_id} v{row.version}")
        self.rows[(row.key_id, row.version)] = row


def _now() -> datetime:
    return datetime.now(UTC)


class SoftwareKeyProvider(LocalKeyProvider):
    """KEKs and HMAC keys persisted in a `KeyStore`, wrapped by `MasterKeys`. Same behaviour as the local provider."""

    fips_validated = False  # an in-process software key is not a validated cryptographic module

    def __init__(
        self,
        *,
        store: KeyStore,
        masters: MasterKeys,
        registry: CryptoPolicyRegistry | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        # deliberately not LocalKeyProvider.__init__: that one refuses to run outside local/test
        self._registry = registry or default_registry()
        self._clock = clock
        self._path = None
        self._keys: dict[str, _Key] = {}
        self._store = store
        self._masters = masters
        self._reload()

    # --- persistence --------------------------------------------------------------------------------------------

    def _reload(self) -> None:
        keys: dict[str, _Key] = {}
        for row in sorted(self._store.load_all(), key=lambda r: (r.key_id, r.version)):
            if row.algorithm not in SYMMETRIC_ALGORITHMS:
                continue  # a signing key: SoftwareSigners' business
            key = keys.setdefault(row.key_id, _Key(KeyPurpose(row.purpose)))
            material = (
                self._masters.unwrap(row.wrapped, row.aad) if row.wrapped is not None else None
            )
            meta = KeyMetadata(
                purpose=key.purpose,
                key_id=row.key_id,
                version=row.version,
                algorithm=row.algorithm,
                state=KeyState(row.state),
                created_at=row.created_at,
                activated_at=row.activated_at,
                retired_at=row.retired_at,
                destroyed_at=row.destroyed_at,
                pq_ready=row.pq_ready,
            )
            key.versions[row.version] = _Version(meta, material)
        self._keys = keys

    def _row(self, meta: KeyMetadata, material: bytes | bytearray | None) -> StoredKey:
        row = StoredKey(
            key_id=meta.key_id,
            version=meta.version,
            purpose=meta.purpose.value,
            algorithm=meta.algorithm,
            state=meta.state.value,
            wrapped=None,
            public_jwk=None,
            created_at=meta.created_at,
            activated_at=meta.activated_at,
            retired_at=meta.retired_at,
            destroyed_at=meta.destroyed_at,
            pq_ready=meta.pq_ready,
        )
        if material is None:
            return row
        return replace(row, wrapped=self._masters.wrap(material, row.aad))

    def _save(self) -> None:  # persistence is explicit and incremental here
        return None

    def _new_version(self, key_id: str, algorithm: str | None) -> KeyMetadata:
        key = self._keys[key_id]
        alg = self._registry.require(
            key.purpose, algorithm or self._registry.algorithms(key.purpose)[0]
        )
        if alg not in SYMMETRIC_ALGORITHMS:
            raise CryptoError(
                "CRYPTO_ALGORITHM_NOT_ALLOWED", f"software KEKs are symmetric, not {alg}"
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
            pq_ready=True,
        )
        material = bytearray(secrets.token_bytes(32))
        if not self._store.insert(self._row(meta, material)):
            # another process created this version first: adopt what is stored and use that
            zeroize(material)
            self._reload()
            if key_id not in self._keys:
                raise CryptoError("CRYPTO_KEY_UNKNOWN", key_id)
            return self._keys[key_id].current().meta
        key.versions[number] = _Version(meta, material)
        return meta

    def rotate_key(self, ref: KeyRef) -> KeyMetadata:
        """New ACTIVE version; the previous one is RETIRED (persisted) and still unwraps until rewrapped."""
        key = self._key(ref)
        current = key.current()
        retired = None
        if current.meta.state is KeyState.ACTIVE:
            retired = replace(current.meta, state=KeyState.RETIRED, retired_at=self._clock())
        meta = self._new_version(ref.key_id, current.meta.algorithm)
        if retired is not None and meta.version == current.meta.version + 1:
            current.meta = retired
            self._store.update(self._row(retired, current.material))
        return meta

    async def destroy_key(self, ref: KeyRef) -> KeyMetadata:
        """Destroy one version, or every version when `ref.version` is None (crypto-erasure). Persisted before it returns."""
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
            await asyncio.to_thread(self._store.update, self._row(v.meta, None))
        return targets[-1].meta

    def rewrap_all(self) -> int:
        """Re-wrap every row under the current master key (after a master rotation). Returns the rows changed."""
        changed = 0
        for row in self._store.load_all():
            if (
                row.wrapped is None
                or self._masters.master_id_of(row.wrapped) == self._masters.current
            ):
                continue
            plain = self._masters.unwrap(row.wrapped, row.aad)
            self._store.update(replace(row, wrapped=self._masters.wrap(plain, row.aad)))
            zeroize(plain)
            changed += 1
        return changed


class SoftwareSigners:
    """Asymmetric signing keys (ES256) kept in the same store. One key id per purpose and cell, with versions."""

    def __init__(
        self,
        *,
        store: KeyStore,
        masters: MasterKeys,
        clock: Callable[[], datetime] = _now,
        registry: CryptoPolicyRegistry | None = None,
    ) -> None:
        self._store = store
        self._masters = masters
        self._clock = clock
        self._registry = registry or default_registry()

    def _versions(self, key_id: str) -> list[StoredKey]:
        return sorted(
            (
                r
                for r in self._store.load_all()
                if r.key_id == key_id and r.algorithm == SIGNING_ALGORITHM
            ),
            key=lambda r: r.version,
        )

    def _create(self, key_id: str, purpose: KeyPurpose | None, version: int) -> bool:
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        if purpose is not None:
            self._registry.require(purpose, SIGNING_ALGORITHM)
        now = self._clock()
        public = LocalSigner.from_pem(key_id, pem.decode(), purpose=purpose).public_jwk()
        row = StoredKey(
            key_id=key_id,
            version=version,
            purpose=purpose.value if purpose is not None else GENERIC_SIGNING_PURPOSE,
            algorithm=SIGNING_ALGORITHM,
            state=KeyState.ACTIVE.value,
            wrapped=None,
            public_jwk={k: v for k, v in public.items() if k != "kid"},
            created_at=now,
            activated_at=now,
        )
        return self._store.insert(replace(row, wrapped=self._masters.wrap(pem, row.aad)))

    def signer(self, key_id: str, *, kid: str, purpose: KeyPurpose | None = None) -> LocalSigner:
        """The signer for the ACTIVE version of `key_id`, created on first use. Safe if several processes race to create it."""
        versions = self._versions(key_id)
        if not versions:
            self._create(
                key_id, purpose, 1
            )  # False means another process just did: read what it stored
            versions = self._versions(key_id)
        active = [r for r in versions if r.state == KeyState.ACTIVE.value and r.wrapped is not None]
        if not active:
            raise CryptoError("CRYPTO_KEY_NOT_ACTIVE", key_id)
        row = active[-1]
        assert row.wrapped is not None
        pem = bytes(self._masters.unwrap(row.wrapped, row.aad)).decode()
        return LocalSigner.from_pem(kid, pem, purpose=purpose)

    def rotate(self, key_id: str, *, purpose: KeyPurpose | None = None) -> int:
        """A new ACTIVE version; the previous one is RETIRED and its public key stays available for the verification window."""
        versions = self._versions(key_id)
        if not versions:
            raise CryptoError("CRYPTO_KEY_UNKNOWN", key_id)
        last = versions[-1]
        if not self._create(key_id, purpose, last.version + 1):
            raise CryptoError(
                "CRYPTO_KEY_EXISTS", f"{key_id} v{last.version + 1}: rotated by another process"
            )
        for row in versions:
            if row.state == KeyState.ACTIVE.value:
                self._store.update(
                    replace(row, state=KeyState.RETIRED.value, retired_at=self._clock())
                )
        return last.version + 1

    def retired_public_jwks(self, key_id: str, kid: str) -> list[dict[str, Any]]:
        """Public keys of every non-active version, for `ZID_SIGNING_RETIRING_KEYS` and the published key sets."""
        return [
            {**(r.public_jwk or {}), "kid": f"{kid}-v{r.version}"}
            for r in self._versions(key_id)
            if r.state != KeyState.ACTIVE.value and r.public_jwk
        ]


# The exact text an operator must set (ZID_KEY_PROVIDER_DB_ACK) to accept standard-assurance keys in a deployed environment.
RISK_ACKNOWLEDGEMENT = "accept-software-keys"


__all__ = [
    "RISK_ACKNOWLEDGEMENT",
    "KeyStore",
    "MasterKeyError",
    "MasterKeys",
    "MemoryKeyStore",
    "SoftwareKeyProvider",
    "SoftwareSigners",
    "StoredKey",
]

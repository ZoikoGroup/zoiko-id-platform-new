"""Crypto Policy Registry (ZID-CRYPTO-MI-001 §4, §4.1; DEC-F5, DEC-F6).

Loads `backend/config/crypto-profiles/crypto-policy-registry.yaml` (or ZID_CRYPTO_POLICY_REGISTRY)
and answers one question: may a key of this purpose use this algorithm? Algorithm names arriving in
messages (`alg` headers and the like) are only ever checked against the allowlist; they never pick a
code path.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_crypto import CryptoError
from zid_crypto.purposes import KeyPurpose

# backend/config/... relative to libs/zid-crypto/src/zid_crypto (source checkouts and tests); the
# runtime image sets ZID_CRYPTO_POLICY_REGISTRY.
DEFAULT_PATH = (
    Path(__file__).resolve().parents[4]
    / "config"
    / "crypto-profiles"
    / "crypto-policy-registry.yaml"
)
# Lists that permit creating new artifacts with a key of the profile's purpose.
ISSUANCE_FIELDS = (
    "allowed_algorithms",
    "allowed_algorithms_non_fips_ecosystems",
    "allowed_algorithms_by_credential_profile",
)
NON_FIPS_FIELD = "allowed_algorithms_non_fips_ecosystems"

# Every list that names an algorithm the platform knows about (inventory check, DEC-F6 PQC-0).
KNOWN_FIELDS = (
    *ISSUANCE_FIELDS,
    "compatibility_only",
    "verify_only_algorithms",
    "key_transport_algorithms",
    "content_encryption_algorithms",
    "hybrid_groups_preferred",
    "hybrid_groups_fips_oriented",
)


# Algorithms whose standard is not final (NIST HQC draft, FN-DSA/FIPS 206 draft). ZID-CRYPTO-001 §12:
# they may be inventoried and piloted in a lab, never activated in the production baseline.
NON_FINAL_ALGORITHMS = frozenset(
    {"HQC", "HQC-128", "HQC-192", "HQC-256", "FN-DSA", "FN-DSA-512", "FN-DSA-1024"}
    | {"FALCON-512", "FALCON-1024"}
)


def _names(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise CryptoError("CRYPTO_POLICY_INVALID", f"{where} must be a list of algorithm names")
    return tuple(value)


class CryptoPolicyRegistry:
    """A validated, immutable view of one registry release."""

    def __init__(self, document: Mapping[str, Any], *, allow_non_final: bool = False) -> None:
        """`allow_non_final` is for lab registries only; the production baseline never sets it."""
        version, profiles = document.get("version"), document.get("profiles")
        if not isinstance(version, str) or not version:
            raise CryptoError("CRYPTO_POLICY_INVALID", "version is required")
        if not isinstance(profiles, dict) or not profiles:
            raise CryptoError("CRYPTO_POLICY_INVALID", "profiles are required")
        self.version = version
        self._documents: dict[str, dict[str, Any]] = {
            str(k): dict(v) for k, v in profiles.items() if isinstance(v, dict)
        }
        self._profiles: dict[str, dict[str, tuple[str, ...]]] = {}
        for profile_id, body in profiles.items():
            if not isinstance(body, dict) or not isinstance(body.get("purpose"), str):
                raise CryptoError("CRYPTO_POLICY_INVALID", f"{profile_id}: purpose is required")
            lists = {f: _names(body[f], f"{profile_id}.{f}") for f in KNOWN_FIELDS if f in body}
            exceptions = body.get("permitted_exceptions", [])
            lists["permitted_exceptions"] = _names(
                [e.get("algorithm") for e in exceptions if isinstance(e, dict)],
                f"{profile_id}.permitted_exceptions",
            )
            if not allow_non_final:
                blocked = sorted(
                    {a for names in lists.values() for a in names} & NON_FINAL_ALGORITHMS
                )
                if blocked:
                    raise CryptoError(
                        "CRYPTO_ALGORITHM_NOT_FINAL",
                        f"{profile_id}: {blocked} cannot be activated in the production baseline",
                    )
            self._profiles[str(profile_id)] = lists
        mapping = document.get("key_purposes")
        if not isinstance(mapping, dict):
            raise CryptoError("CRYPTO_POLICY_INVALID", "key_purposes is required")
        unknown = set(mapping) - {p.value for p in KeyPurpose}
        missing = {p.value for p in KeyPurpose} - set(mapping)
        if unknown or missing:
            raise CryptoError(
                "CRYPTO_POLICY_INVALID",
                f"key_purposes unknown={sorted(unknown)} missing={sorted(missing)}",
            )
        self._purpose_profile: dict[KeyPurpose, str] = {}
        for purpose in KeyPurpose:
            profile_id = mapping[purpose.value]
            if profile_id not in self._profiles:
                raise CryptoError(
                    "CRYPTO_POLICY_INVALID", f"{purpose}: unknown profile {profile_id}"
                )
            if not self._issuance(profile_id):
                raise CryptoError("CRYPTO_POLICY_INVALID", f"{profile_id}: no allowed algorithms")
            self._purpose_profile[purpose] = profile_id

    @classmethod
    def load(
        cls, path: Path | str | None = None, *, allow_non_final: bool = False
    ) -> CryptoPolicyRegistry:
        file = Path(path or os.environ.get("ZID_CRYPTO_POLICY_REGISTRY") or DEFAULT_PATH)
        try:
            document = yaml.safe_load(file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            raise CryptoError("CRYPTO_POLICY_UNAVAILABLE", str(file)) from e
        if not isinstance(document, dict):
            raise CryptoError("CRYPTO_POLICY_INVALID", "the registry must be a mapping")
        return cls(document, allow_non_final=allow_non_final)

    def _issuance(self, profile_id: str) -> tuple[str, ...]:
        lists = self._profiles[profile_id]
        return tuple(a for f in ISSUANCE_FIELDS for a in lists.get(f, ()))

    def profile_setting(self, profile_id: str, key: str) -> Any:
        """A scalar/free-form setting of a profile (for example `tls.v1` `min_version_new_protocols`)."""
        try:
            return self._documents[profile_id][key]
        except KeyError as e:
            raise CryptoError("CRYPTO_POLICY_INVALID", f"{profile_id}.{key} is not set") from e

    def profile_for(self, purpose: KeyPurpose) -> str:
        return self._purpose_profile[KeyPurpose(purpose)]

    def algorithms(self, purpose: KeyPurpose, *, fips_required: bool = False) -> tuple[str, ...]:
        """Ordered allowlist for new artifacts with a key of `purpose` (first = default).

        `fips_required` drops the algorithms the registry lists only for non-FIPS ecosystems."""
        names = self._issuance(self.profile_for(purpose))
        if not fips_required:
            return names
        outside = set(self._profiles[self.profile_for(purpose)].get(NON_FIPS_FIELD, ()))
        return tuple(a for a in names if a not in outside)

    def profile_algorithms(self, profile_id: str, *fields: str) -> tuple[str, ...]:
        lists = self._profiles[profile_id]
        return tuple(a for f in (fields or KNOWN_FIELDS) for a in lists.get(f, ()))

    def allowed(
        self, purpose: KeyPurpose, algorithm: object, *, fips_required: bool = False
    ) -> bool:
        """Exact, case-sensitive allowlist match; anything that is not a known name is refused."""
        return isinstance(algorithm, str) and algorithm in self.algorithms(
            purpose, fips_required=fips_required
        )

    def require(
        self, purpose: KeyPurpose, algorithm: object, *, fips_required: bool = False
    ) -> str:
        if not self.allowed(purpose, algorithm, fips_required=fips_required):
            raise CryptoError("CRYPTO_ALGORITHM_NOT_ALLOWED", f"{algorithm!s} for {purpose}")
        return str(algorithm)

    def known_algorithms(self) -> frozenset[str]:
        return frozenset(
            a for lists in self._profiles.values() for names in lists.values() for a in names
        )

    def purposes(self) -> Iterable[KeyPurpose]:
        return iter(self._purpose_profile)


@lru_cache(maxsize=1)
def default_registry() -> CryptoPolicyRegistry:
    """The registry release this process runs with (loaded once)."""
    return CryptoPolicyRegistry.load()


__all__ = ["NON_FINAL_ALGORITHMS", "CryptoPolicyRegistry", "default_registry"]

"""FIPS-required operations (ZID-CRYPTO-001 §10; registry `allowed_algorithms_non_fips_ecosystems`).

An operation marked FIPS-required runs only when two things hold: the provider executing it is a validated
module (`fips_validated`), and the algorithm is one the registry allows outside the non-FIPS ecosystems.
Either failing refuses the operation; there is no fallback to a software key or to an ecosystem-only
algorithm. Whether a deployed KMS endpoint really is validated is the deployment's claim, recorded in the
`fips_endpoint` flag of its provider.
"""

from __future__ import annotations

from typing import Any

from zid_crypto import CryptoError
from zid_crypto.policy import CryptoPolicyRegistry, default_registry
from zid_crypto.purposes import KeyPurpose


def require_fips(
    provider: Any,
    purpose: KeyPurpose,
    algorithm: object,
    registry: CryptoPolicyRegistry | None = None,
) -> str:
    if getattr(provider, "fips_validated", False) is not True:
        raise CryptoError("CRYPTO_FIPS_MODULE_REQUIRED", type(provider).__name__)
    return (registry or default_registry()).require(purpose, algorithm, fips_required=True)


__all__ = ["require_fips"]

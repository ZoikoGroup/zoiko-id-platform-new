"""Hybrid post-quantum key exchange policy for TLS (ZID-CRYPTO-001 §11, §12.1; registry profile `tls.v1`).

The TLS handshake itself happens at the terminator (ingress, mesh sidecar, load balancer); Python's `ssl`
cannot offer ML-KEM groups. The terminator reports the key-exchange group it negotiated, and this module
decides what the platform does with that report:

- `HYBRID_REQUIRED`: only a hybrid group is accepted. A classical group, or no report at all, is refused,
  so a downgrade can never pass as success.
- `HYBRID_PREFERRED` (the registry default): a classical group is accepted but flagged as a fallback so the
  caller can count and attribute it (endpoint, client).
- `CLASSICAL`: no hybrid expectation.
- Pre-standard Kyber code points are refused in every mode (registry `tls.v1.disabled`).

The hybrid group names come from the registry (`hybrid_groups_preferred`, `hybrid_groups_fips_oriented`),
not from this file.
"""

from __future__ import annotations

from dataclasses import dataclass

from zid_crypto import CryptoError
from zid_crypto.policy import CryptoPolicyRegistry, default_registry

PROFILE_ID = "tls.v1"
PQ_MODES = ("CLASSICAL", "HYBRID_PREFERRED", "HYBRID_REQUIRED")
PRE_STANDARD_GROUPS = frozenset(
    {
        "x25519kyber768draft00",
        "secp256r1kyber768draft00",
        "x25519kyber512draft00",
        "kyber512",
        "kyber768",
        "kyber1024",
    }
)
_GROUP_FIELDS = ("hybrid_groups_preferred", "hybrid_groups_fips_oriented")


@dataclass(frozen=True, slots=True)
class GroupDecision:
    allowed: bool
    hybrid: bool  # the negotiated group is a registered hybrid group
    fallback: bool  # a classical group was accepted where hybrid was preferred
    code: str | None = None  # why it was refused


def hybrid_groups(registry: CryptoPolicyRegistry | None = None) -> frozenset[str]:
    reg = registry or default_registry()
    return frozenset(g.lower() for g in reg.profile_algorithms(PROFILE_ID, *_GROUP_FIELDS))


def default_mode(registry: CryptoPolicyRegistry | None = None) -> str:
    mode = str((registry or default_registry()).profile_setting(PROFILE_ID, "pq_mode"))
    if mode not in PQ_MODES:
        raise CryptoError("CRYPTO_POLICY_INVALID", f"{PROFILE_ID}: unknown pq_mode {mode!r}")
    return mode


def evaluate_group(
    negotiated: str | None, *, mode: str, registry: CryptoPolicyRegistry | None = None
) -> GroupDecision:
    """What to do with the key-exchange group the terminator says it negotiated (None = not reported)."""
    if mode not in PQ_MODES:
        raise CryptoError("CRYPTO_POLICY_INVALID", f"unknown pq_mode {mode!r}")
    group = (negotiated or "").strip().lower()
    if group in PRE_STANDARD_GROUPS:
        return GroupDecision(False, False, False, "CRYPTO_GROUP_PRE_STANDARD")
    if group and group in hybrid_groups(registry):
        return GroupDecision(True, True, False)
    if mode == "HYBRID_REQUIRED":
        return GroupDecision(False, False, False, "CRYPTO_HYBRID_REQUIRED")
    # Unknown (not reported) is not counted as a fallback: only a reported classical group is.
    return GroupDecision(True, False, fallback=bool(group) and mode == "HYBRID_PREFERRED")


__all__ = [
    "PQ_MODES",
    "PRE_STANDARD_GROUPS",
    "GroupDecision",
    "default_mode",
    "evaluate_group",
    "hybrid_groups",
]

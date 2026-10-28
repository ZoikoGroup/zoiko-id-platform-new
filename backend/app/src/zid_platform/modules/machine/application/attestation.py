"""Runtime attestation statements (ZID-MACHINE-001 §9; CR-042). No network, no database; lives with the application layer because it verifies signatures.

An attestor the platform trusts signs a compact JWS of type `attestation+jwt` saying: this workload (SPIFFE ID), in this
environment, running this artifact digest, was verified to this level until this time. The platform checks the signature
against keys it pinned for attestors (never a key sent with the statement), then the claims. Attestation is evidence about
a runtime; it never grants business authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from zid_context.spiffe import SpiffeError, SpiffeId, tenant_of
from zid_crypto import CryptoError, verify_external_jws

from zid_platform.modules.machine.domain.agents import MachineError

STATEMENT_TYP = "attestation+jwt"
ALGORITHMS = ("ES256", "Ed25519")
MAX_LIFETIME_SECONDS = 3600
CLOCK_SKEW_SECONDS = 60
ARTIFACT = "sha256:"
# What an attestor may vouch for unless its key says otherwise. Level 3 (hardware or TEE evidence) is only for an
# attestor whose key entry carries `zid_max_level: 3`, because only such an attestor checks a hardware quote.
DEFAULT_MAX_LEVEL = 2


@dataclass(frozen=True, slots=True)
class Statement:
    spiffe_id: str
    tenant_id: str
    environment_id: str
    artifact_digest: str
    level: int
    verifier: str
    issued_at: datetime
    valid_until: datetime
    commitment: str


def signature_checked(
    token: str, keys: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The statement's header and claims once an approved attestor's signature has verified; MachineError otherwise.
    Nothing in the claims is trusted before this returns."""
    try:
        return verify_external_jws(token, keys=keys, algorithms=ALGORITHMS, typ=STATEMENT_TYP)
    except CryptoError as e:
        raise MachineError("ATTESTATION_SIGNATURE_INVALID", e.code) from e


def read_claims(header: Mapping[str, Any], claims: Mapping[str, Any]) -> Statement:
    """The claims of a statement whose signature has verified, as a value. MachineError when they cannot be read;
    whether the statement is still in date is a separate question (`timing_problem`)."""
    try:
        sid = SpiffeId.parse(str(claims["sub"]))
        tenant = tenant_of(sid)
        environment = str(claims["environment_id"])
        digest = str(claims["artifact_digest"])
        level = int(claims["level"])
        issued = datetime.fromtimestamp(int(claims["iat"]), UTC)
        until = datetime.fromtimestamp(int(claims["exp"]), UTC)
    except (KeyError, ValueError, TypeError, SpiffeError) as e:
        raise MachineError("ATTESTATION_MALFORMED", type(e).__name__) from e
    if not 1 <= level <= 3:
        raise MachineError("ATTESTATION_MALFORMED", "level must be 1 to 3")
    if not digest.startswith(ARTIFACT) or len(digest) != len(ARTIFACT) + 64:
        raise MachineError("ATTESTATION_MALFORMED", "artifact digest must be sha256")
    commitment = hashlib.sha256(
        json.dumps(
            {
                "sub": str(sid),
                "environment_id": environment,
                "artifact_digest": digest,
                "level": level,
                "iat": int(claims["iat"]),
                "exp": int(claims["exp"]),
                "kid": header.get("kid"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return Statement(
        str(sid),
        tenant,
        environment,
        digest,
        level,
        str(header.get("kid")),
        issued,
        until,
        commitment,
    )


def level_problem(stmt: Statement, keys: Sequence[Mapping[str, Any]]) -> str | None:
    """Whether the attestor that signed this statement is allowed to vouch for its level. The ceiling is a property of
    the pinned key (`zid_max_level`), never of the statement."""
    key = next((k for k in keys if k.get("kid") == stmt.verifier), None)
    ceiling = int(key.get("zid_max_level", DEFAULT_MAX_LEVEL)) if key else 0
    return "ATTESTATION_LEVEL_NOT_PERMITTED" if stmt.level > ceiling else None


def timing_problem(stmt: Statement, now: datetime | None = None) -> str | None:
    """Why a readable statement cannot be accepted now: already past, longer-lived than an hour, or from the future."""
    now = now or datetime.now(UTC)
    if stmt.valid_until <= now:
        return "ATTESTATION_STALE"
    if (
        stmt.valid_until <= stmt.issued_at
        or (stmt.valid_until - stmt.issued_at).total_seconds() > MAX_LIFETIME_SECONDS
    ):
        return "ATTESTATION_LIFETIME_INVALID"
    if (stmt.issued_at - now).total_seconds() > CLOCK_SKEW_SECONDS:
        return "ATTESTATION_NOT_YET_VALID"
    return None

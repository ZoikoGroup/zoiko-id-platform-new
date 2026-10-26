"""Key ceremony record with an M-of-N custodian quorum (ZID-CRYPTO-001 §20, §20.1).

A root/CA/evidence key ceremony is a human procedure; this module is the part software can enforce
and anyone can check afterwards:

- `KeyCeremony` holds the `KeyCeremonyRecord` fields (purpose, key id, environment, participants
  and roles, quorum policy, procedure version, times, generated public key fingerprint, backup/share
  handling, verification steps, anomalies). Its canonical digest covers all of them.
- Each custodian attests by signing that digest with their own key (a JWS with an explicit `typ`).
  `finalize` refuses to seal the record until at least `required` distinct, enrolled custodians have
  attested, and never accepts fewer than two (no single person can complete a ceremony).
- `verify_ceremony_evidence` re-computes the digest and checks every attestation against the
  custodians' public keys, needing nothing but the evidence document and those keys.
It does not run or witness the ceremony, hold the HSM, or prove that the people followed the
procedure; the evidence receipt id comes from the Evidence ledger when the record is stored.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from zid_crypto import CryptoError, Signer, jwk_thumbprint, sign_jws, verify_jws

CEREMONY_TYP = "zid-key-ceremony-attestation+jws"
CEREMONY_SCHEMA = "key-ceremony-record.v1"
MIN_QUORUM = 2


@dataclass(frozen=True, slots=True)
class Custodian:
    custodian_id: str
    role: str  # for example SECURITY_OFFICER, ROOT_CUSTODIAN
    public_jwk: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class QuorumPolicy:
    required: int  # M
    custodian_ids: tuple[str, ...]  # the N enrolled custodians

    def __post_init__(self) -> None:
        if len(set(self.custodian_ids)) != len(self.custodian_ids):
            raise CryptoError("CEREMONY_QUORUM_INVALID", "duplicate custodians")
        if not MIN_QUORUM <= self.required <= len(self.custodian_ids):
            raise CryptoError(
                "CEREMONY_QUORUM_INVALID", f"need {MIN_QUORUM} <= M <= N, got M={self.required}"
            )


def _digest(record: Mapping[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class KeyCeremony:
    ceremony_id: str
    key_purpose: str
    key_id: str
    environment: str
    procedure_version: str
    custodians: tuple[Custodian, ...]
    quorum: QuorumPolicy
    started_at: str
    ended_at: str
    public_key_jwk: Mapping[str, Any]  # the generated key's public half; its thumbprint is recorded
    share_handling: str = ""
    verification_steps: tuple[str, ...] = ()
    anomalies: tuple[str, ...] = ()
    _attestations: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if {c.custodian_id for c in self.custodians} != set(self.quorum.custodian_ids):
            raise CryptoError(
                "CEREMONY_QUORUM_INVALID", "participants differ from the quorum policy"
            )

    def record(self) -> dict[str, Any]:
        return {
            "schema_version": CEREMONY_SCHEMA,
            "ceremony_id": self.ceremony_id,
            "key_purpose": self.key_purpose,
            "key_id": self.key_id,
            "environment": self.environment,
            "participants": sorted(
                ({"custodian_id": c.custodian_id, "role": c.role} for c in self.custodians),
                key=lambda p: p["custodian_id"],
            ),
            "quorum_policy": {
                "required": self.quorum.required,
                "of": sorted(self.quorum.custodian_ids),
            },
            "procedure_version": self.procedure_version,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "public_key_fingerprint": jwk_thumbprint(self.public_key_jwk),
            "share_handling": self.share_handling,
            "verification_steps": list(self.verification_steps),
            "anomalies": list(self.anomalies),
        }

    def digest(self) -> str:
        return _digest(self.record())

    def attest(self, custodian_id: str, signer: Signer) -> None:
        """A custodian signs the record digest with their own key."""
        if custodian_id not in self.quorum.custodian_ids:
            raise CryptoError("CEREMONY_NOT_A_CUSTODIAN", custodian_id)
        self._attestations[custodian_id] = sign_jws(
            signer,
            {"ceremony_id": self.ceremony_id, "record_digest": self.digest()},
            typ=CEREMONY_TYP,
        )

    def finalize(self) -> dict[str, Any]:
        """The sealed evidence document; refused until the quorum has attested."""
        if len(self._attestations) < self.quorum.required:
            raise CryptoError(
                "CEREMONY_QUORUM_NOT_MET",
                f"{len(self._attestations)} of {self.quorum.required} required attestations",
            )
        return {
            "record": self.record(),
            "record_digest": self.digest(),
            "attestations": dict(sorted(self._attestations.items())),
        }


def verify_ceremony_evidence(
    evidence: Mapping[str, Any], custodian_keys: Mapping[str, Mapping[str, Any]]
) -> tuple[str, ...]:
    """Check a sealed ceremony independently; returns the custodians whose attestation verified.

    Raises unless the digest matches the record and at least `quorum_policy.required` distinct
    enrolled custodians signed exactly that digest with the key we hold for them."""
    record, claimed = evidence.get("record"), evidence.get("record_digest")
    attestations = evidence.get("attestations")
    if not isinstance(record, dict) or not isinstance(attestations, dict):
        raise CryptoError("CEREMONY_EVIDENCE_MALFORMED")
    if _digest(record) != claimed:
        raise CryptoError("CEREMONY_DIGEST_MISMATCH")
    policy = record["quorum_policy"]
    enrolled = set(policy["of"])
    verified: list[str] = []
    for custodian_id, token in attestations.items():
        jwk = custodian_keys.get(custodian_id)
        if custodian_id not in enrolled or jwk is None:
            continue
        try:
            _, payload = verify_jws(token, keys=[jwk], typ=CEREMONY_TYP)
        except CryptoError:
            continue
        if payload.get("record_digest") == claimed and payload.get("ceremony_id") == record.get(
            "ceremony_id"
        ):
            verified.append(custodian_id)
    if len(verified) < max(int(policy["required"]), MIN_QUORUM):
        raise CryptoError(
            "CEREMONY_QUORUM_NOT_MET", f"{len(verified)} verified of {policy['required']} required"
        )
    return tuple(sorted(verified))


def enroll(custodians: Sequence[tuple[str, str, Signer]]) -> tuple[Custodian, ...]:
    """Convenience for tests and tooling: (id, role, signer) -> enrolled custodians."""
    return tuple(Custodian(i, r, s.public_jwk()) for i, r, s in custodians)


__all__ = [
    "Custodian",
    "KeyCeremony",
    "QuorumPolicy",
    "enroll",
    "verify_ceremony_evidence",
]

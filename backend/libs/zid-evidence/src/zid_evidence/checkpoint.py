"""Signed checkpoint manifests (EVIDENCE-001 §10-12; DEC-F2 §2-3, §5).

A checkpoint commits a bounded batch of consecutive ledger entries [first_sequence, last_sequence]
of one partition into a Merkle root (leaves are the entries' `entry_hash` strings, in sequence
order) and links to the previous checkpoint of the partition through `previous_checkpoint_hash`
= H(JCS(previous manifest)). The manifest is signed as a compact JWS (typ CHECKPOINT_TYP) with a
dedicated evidence key; verifying it needs only the public key (`zid_evidence.jws`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from zid_evidence.digest import Digest, hash_json
from zid_evidence.jcs import canonicalize
from zid_evidence.jws import SignatureError, verify_compact
from zid_evidence.merkle import leaf_hash, root

__all__ = [
    "CHECKPOINT_TYP",
    "CheckpointManifest",
    "batch_root",
    "entry_leaf",
    "verify_checkpoint_signature",
]

CHECKPOINT_TYP = "zid-evidence-checkpoint+jws"


def entry_leaf(entry_hash: Digest) -> bytes:
    """The Merkle leaf input for a ledger entry: its `<alg>:<hex>` entry hash, UTF-8."""
    return str(entry_hash).encode("ascii")


def batch_root(entry_hashes: Sequence[Digest], *, alg: str) -> Digest:
    return Digest(alg, root([entry_leaf(h) for h in entry_hashes], alg=alg))


def leaf_digest(entry_hash: Digest, *, alg: str) -> bytes:
    return leaf_hash(entry_leaf(entry_hash), alg=alg)


@dataclass(frozen=True, slots=True)
class CheckpointManifest:
    checkpoint_id: str
    partition_id: str
    first_sequence: int
    last_sequence: int
    merkle_root: Digest
    previous_checkpoint_hash: Digest | None
    created_at: str
    signer_key_id: str
    alg: str  # signature algorithm (JWS `alg`)

    @property
    def tree_size(self) -> int:
        return self.last_sequence - self.first_sequence + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "partition_id": self.partition_id,
            "first_sequence": self.first_sequence,
            "last_sequence": self.last_sequence,
            "tree_size": self.tree_size,
            "merkle_root": str(self.merkle_root),
            "previous_checkpoint_hash": (
                str(self.previous_checkpoint_hash) if self.previous_checkpoint_hash else None
            ),
            "created_at": self.created_at,
            "signer_key_id": self.signer_key_id,
            "alg": self.alg,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CheckpointManifest:
        prev = data.get("previous_checkpoint_hash")
        manifest = cls(
            checkpoint_id=str(data["checkpoint_id"]),
            partition_id=str(data["partition_id"]),
            first_sequence=int(data["first_sequence"]),
            last_sequence=int(data["last_sequence"]),
            merkle_root=Digest.parse(data["merkle_root"]),
            previous_checkpoint_hash=Digest.parse(prev) if prev is not None else None,
            created_at=str(data["created_at"]),
            signer_key_id=str(data["signer_key_id"]),
            alg=str(data["alg"]),
        )
        if "tree_size" in data and int(data["tree_size"]) != manifest.tree_size:
            raise ValueError("tree_size does not match the sequence range")
        return manifest

    def canonical(self) -> bytes:
        return canonicalize(self.to_dict())

    def hash(self, *, alg: str) -> Digest:
        """What the next checkpoint links to as `previous_checkpoint_hash`."""
        return hash_json(self.to_dict(), alg=alg)


def verify_checkpoint_signature(
    manifest: CheckpointManifest, signature: str, *, keys: Sequence[Mapping[str, Any]]
) -> None:
    """The JWS verifies under a trusted key, names the manifest's key id and algorithm, and its
    payload is exactly the manifest (compared canonically). Raises SignatureError."""
    header, payload = verify_compact(signature, keys=keys, typ=CHECKPOINT_TYP)
    if header.get("kid") != manifest.signer_key_id or header.get("alg") != manifest.alg:
        raise SignatureError("signature key id or algorithm differs from the manifest")
    if canonicalize(payload) != manifest.canonical():
        raise SignatureError("signed payload differs from the manifest")

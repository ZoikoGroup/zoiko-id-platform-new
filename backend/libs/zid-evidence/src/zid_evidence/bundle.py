"""Evidence export bundles and their offline verifier (EVIDENCE-001 §11.1, §17; DEC-F2 §6).

A bundle is one JSON document (BUNDLE_FORMAT) that lets anyone check, without access to Zoiko
production, that a record was committed to a partition's ledger and sealed by a signed checkpoint:

    {
      "format": "zid-evidence-bundle/1",
      "profile": {"canonicalization": "RFC8785-JCS", "hash_alg": "sha-256",
                  "merkle": "RFC9162", "signature": "JWS"},
      "record_type": "...", "record_id": "...",
      "record": {...} | null,            # canonical record (e.g. a receipt); null when withheld/erased
      "ledger_entry": {...LedgerEntry fields..., "entry_hash": "sha-256:..."},
      "checkpoint": {...CheckpointManifest fields..., "signature": "<compact JWS>",
                     "external_timestamp_ref": null},
      "inclusion_proof": {"leaf_index": 0, "tree_size": 1, "hashes": ["sha-256:..."]},
      "signatures": [{"type": "checkpoint", "kid": "...", "jws": "..."}],
      "keys": {"keys": [<public JWK>, ...]}   # every key the bundle's signatures use
    }

`verify_bundle` recomputes everything: the record digest (JCS), the entry hash, the Merkle path to
the checkpoint root, and the checkpoint signature. Keys come from the caller (`trusted_keys`, the
recommended pinned set) or, failing that, from the bundle itself, which the report flags.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from zid_evidence.chain import LedgerEntry, entry_hash
from zid_evidence.checkpoint import (
    CheckpointManifest,
    leaf_digest,
    verify_checkpoint_signature,
)
from zid_evidence.digest import HASH_ALGS, Digest, hash_json
from zid_evidence.jcs import CanonicalizationError
from zid_evidence.jws import SignatureError
from zid_evidence.merkle import verify_inclusion

__all__ = [
    "BUNDLE_FORMAT",
    "PROFILE",
    "Check",
    "VerificationReport",
    "build_bundle",
    "verify_bundle",
]

BUNDLE_FORMAT = "zid-evidence-bundle/1"
PROFILE = {"canonicalization": "RFC8785-JCS", "merkle": "RFC9162", "signature": "JWS"}


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class VerificationReport:
    checks: list[Check] = field(default_factory=list)
    keys_source: str = "trusted"  # "trusted" (caller-pinned) or "bundle" (self-asserted)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def structural_only(self) -> bool:
        """Verified without the record itself (withheld or lawfully erased)."""
        return any(c.name == "record_hash" and c.detail == "record not included" for c in self.checks)

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, ok, detail))
        return ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "structural_only": self.structural_only,
            "keys_source": self.keys_source,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }


def build_bundle(
    *,
    record_type: str,
    record_id: str,
    record: Mapping[str, Any] | None,
    entry: LedgerEntry,
    entry_digest: Digest,
    manifest: CheckpointManifest,
    checkpoint_signature: str,
    proof: Sequence[bytes],
    keys: Sequence[Mapping[str, Any]],
    external_timestamp_ref: str | None = None,
    entry_signature: str | None = None,
    signer_key_id: str | None = None,
) -> dict[str, Any]:
    alg = manifest.merkle_root.alg
    signatures: list[dict[str, Any]] = [
        {"type": "checkpoint", "kid": manifest.signer_key_id, "jws": checkpoint_signature}
    ]
    if entry_signature:
        signatures.append({"type": "entry", "kid": signer_key_id, "jws": entry_signature})
    return {
        "format": BUNDLE_FORMAT,
        "profile": {**PROFILE, "hash_alg": entry_digest.alg},
        "record_type": record_type,
        "record_id": record_id,
        "record": dict(record) if record is not None else None,
        "ledger_entry": {**entry.to_dict(), "entry_hash": str(entry_digest)},
        "checkpoint": {
            **manifest.to_dict(),
            "signature": checkpoint_signature,
            "external_timestamp_ref": external_timestamp_ref,
        },
        "inclusion_proof": {
            "leaf_index": entry.sequence_no - manifest.first_sequence,
            "tree_size": manifest.tree_size,
            "hashes": [str(Digest(alg, h)) for h in proof],
        },
        "signatures": signatures,
        "keys": {"keys": [dict(k) for k in keys]},
    }


def verify_bundle(
    bundle: Mapping[str, Any], *, trusted_keys: Sequence[Mapping[str, Any]] | None = None
) -> VerificationReport:
    """Verify a bundle offline. Never raises on bad input: every failure is a failed check."""
    report = VerificationReport(keys_source="trusted" if trusted_keys is not None else "bundle")
    try:
        _verify(bundle, trusted_keys, report)
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        report.add("structure", False, f"{type(e).__name__}: {e}")
    return report


def _verify(
    bundle: Mapping[str, Any],
    trusted_keys: Sequence[Mapping[str, Any]] | None,
    report: VerificationReport,
) -> None:
    if not report.add("format", bundle.get("format") == BUNDLE_FORMAT, str(bundle.get("format"))):
        return
    profile = bundle["profile"]
    alg = profile.get("hash_alg")
    profile_ok = (
        alg in HASH_ALGS
        and profile.get("canonicalization") == PROFILE["canonicalization"]
        and profile.get("merkle") == PROFILE["merkle"]
        and profile.get("signature") == PROFILE["signature"]
    )
    if not report.add("profile", profile_ok, str(alg)):
        return
    raw_entry = dict(bundle["ledger_entry"])
    stored_entry_hash = Digest.parse(raw_entry.pop("entry_hash"))
    entry = LedgerEntry.from_dict(raw_entry)
    report.add(
        "record_identity",
        entry.record_id == bundle["record_id"] and entry.record_type == bundle["record_type"],
    )

    record = bundle.get("record")
    if record is None:
        report.add("record_hash", True, "record not included")
    else:
        try:
            ok = hash_json(record, alg=entry.record_hash.alg).matches(entry.record_hash)
            report.add("record_hash", ok, "" if ok else "record digest differs from the ledger")
        except CanonicalizationError as e:
            report.add("record_hash", False, str(e))

    ok = entry_hash(entry, alg=stored_entry_hash.alg).matches(stored_entry_hash)
    report.add("entry_hash", ok, "" if ok else "ledger entry fields do not match entry_hash")

    raw_cp = dict(bundle["checkpoint"])
    signature = raw_cp.pop("signature")
    raw_cp.pop("external_timestamp_ref", None)
    manifest = CheckpointManifest.from_dict(raw_cp)
    in_range = (
        manifest.partition_id == entry.partition_id
        and manifest.first_sequence <= entry.sequence_no <= manifest.last_sequence
    )
    report.add("checkpoint_covers_entry", in_range)

    proof = bundle["inclusion_proof"]
    hashes = [Digest.parse(h) for h in proof["hashes"]]
    merkle_alg = manifest.merkle_root.alg
    included = (
        in_range
        and all(h.alg == merkle_alg for h in hashes)
        and int(proof["leaf_index"]) == entry.sequence_no - manifest.first_sequence
        and int(proof["tree_size"]) == manifest.tree_size
        and verify_inclusion(
            leaf_index=int(proof["leaf_index"]),
            tree_size=manifest.tree_size,
            leaf_digest=leaf_digest(stored_entry_hash, alg=merkle_alg),
            proof=[h.value for h in hashes],
            root_hash=manifest.merkle_root.value,
            alg=merkle_alg,
        )
    )
    report.add("inclusion_proof", included, "" if included else "entry not in checkpoint root")

    keys = trusted_keys if trusted_keys is not None else bundle.get("keys", {}).get("keys", [])
    try:
        verify_checkpoint_signature(manifest, signature, keys=keys)
        report.add("checkpoint_signature", True, manifest.signer_key_id)
    except SignatureError as e:
        report.add("checkpoint_signature", False, str(e))

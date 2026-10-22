"""Evidence integrity primitives for Zoiko iD (EVIDENCE-001 §5-6, §10-12; DEC-F2).

Pure and dependency-light (only `cryptography`, for signature verification), so an external
verifier can use it without any Zoiko service:
- `jcs`        RFC 8785 canonical JSON, strict (ambiguous input rejected)
- `digest`     digests that always carry their algorithm id (`sha-256:<hex>`)
- `chain`      hash-chained ledger entries
- `merkle`     RFC 9162 Merkle trees, inclusion and consistency proofs
- `checkpoint` signed checkpoint manifests
- `bundle`     export bundles and the offline verifier (`python -m zid_evidence verify`)
"""

from zid_evidence.bundle import (
    BUNDLE_FORMAT,
    Check,
    VerificationReport,
    build_bundle,
    verify_bundle,
)
from zid_evidence.chain import (
    EVIDENCE_CLASSES,
    ChainBreak,
    LedgerEntry,
    entry_hash,
    format_time,
    parse_time,
    verify_chain,
)
from zid_evidence.checkpoint import (
    CHECKPOINT_TYP,
    CheckpointManifest,
    batch_root,
    entry_leaf,
    leaf_digest,
    verify_checkpoint_signature,
)
from zid_evidence.digest import HASH_ALGS, REQUIRED_ALG, Digest, DigestError, hash_bytes, hash_json
from zid_evidence.jcs import CanonicalizationError, canonicalize, loads_strict
from zid_evidence.jws import SignatureError, verify_compact

__all__ = [
    "BUNDLE_FORMAT",
    "CHECKPOINT_TYP",
    "EVIDENCE_CLASSES",
    "HASH_ALGS",
    "REQUIRED_ALG",
    "CanonicalizationError",
    "ChainBreak",
    "Check",
    "CheckpointManifest",
    "Digest",
    "DigestError",
    "LedgerEntry",
    "SignatureError",
    "VerificationReport",
    "batch_root",
    "build_bundle",
    "canonicalize",
    "entry_hash",
    "entry_leaf",
    "format_time",
    "hash_bytes",
    "hash_json",
    "leaf_digest",
    "loads_strict",
    "parse_time",
    "verify_bundle",
    "verify_chain",
    "verify_checkpoint_signature",
    "verify_compact",
]

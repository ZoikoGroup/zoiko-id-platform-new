"""W3C Data Integrity proofs, suite eddsa-rdfc-2022, verified offline (ZID-GAP-001 v2.0 GID-051; CR-073 P2-2).

A JSON-LD credential secured by a DataIntegrityProof is verified by canonicalizing it with the RDF Dataset Canonicalization Algorithm (URDNA2015),
hashing, and checking an Ed25519 signature. The rules this module keeps, each of which is a test:

- NO NETWORK, EVER. JSON-LD contexts are served only from the bundle in `config/jsonld-contexts/`, each pinned by the SHA-256 of its exact bytes; a
  context that is not bundled is refused (`CONTEXT_NOT_BUNDLED`), because fetching one would let a remote server change what a signature means;
- URDNA2015 is requested explicitly (the library's default is the older, different algorithm);
- NOTHING MAY BE DROPPED. JSON-LD silently ignores a property its context does not define, which would let unsigned data sit inside a "verified"
  credential. The document is expanded and compacted back and compared with itself; any difference, and the credential is refused
  (`PROPERTIES_NOT_DEFINED_BY_CONTEXT`). A genuine credential that trips this fails closed, never open;
- exactly one proof, `proofPurpose` assertionMethod, `proofValue` a base58-btc multibase of 64 bytes, and the proof's own `@context`, if it has one,
  must equal the credential's;
- the verification key is supplied by the CALLER from the issuer trust table. This module never decides whom to trust: a valid signature from a key
  nobody vouched for proves nothing about the issuer.

It proves the credential is unchanged since the holder of that key signed it. It does not prove the claims, the issuer's authority, or that the
person presenting it is the person it describes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from pyld import jsonld

Loader = Callable[[str, Mapping[str, Any] | None], dict[str, Any]]
SUITE = "eddsa-rdfc-2022"  # the first suite; kept for callers that name it
ECDSA_SUITE = "ecdsa-rdfc-2019"  # P-256 with SHA-256
SUITES = (SUITE, ECDSA_SUITE)
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ED25519_MULTICODEC = b"\xed\x01"
P256_MULTICODEC = b"\x80\x24"


class DataIntegrityError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def base58_decode(text: str) -> bytes:
    n = 0
    for ch in text:
        i = _B58.find(ch)
        if i < 0:
            raise DataIntegrityError("BASE58_CHARACTER_INVALID")
        n = n * 58 + i
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    leading = len(text) - len(text.lstrip("1"))
    return b"\x00" * leading + body


def decode_multibase(value: str) -> bytes:
    """Only base58-btc (prefix z) is allowed, as the suite requires."""
    if not isinstance(value, str) or not value.startswith("z") or len(value) < 2:
        raise DataIntegrityError("MULTIBASE_NOT_BASE58BTC")
    return base58_decode(value[1:])


def ed25519_public_key(multikey: str) -> bytes:
    """The 32-byte key from a Multikey `publicKeyMultibase`. Any other key type, or a wrong length, is refused (never a private key)."""
    raw = decode_multibase(multikey)
    if not raw.startswith(ED25519_MULTICODEC) or len(raw) != 34:
        raise DataIntegrityError("KEY_NOT_ED25519_MULTIKEY")
    return raw[2:]


@dataclass(frozen=True, slots=True)
class VerificationKey:
    """A key a proof may be checked against. `ed25519`: the 32 raw bytes. `p256`: a SEC1 point (compressed or not) on the P-256 curve."""

    kind: str
    raw: bytes


def p256_public_key(multikey: str) -> VerificationKey:
    """The P-256 key from a Multikey `publicKeyMultibase` (prefix 0x8024, then the 33-byte compressed point). A point that is not on the curve,
    another curve, or any other key type is refused."""
    raw = decode_multibase(multikey)
    if not raw.startswith(P256_MULTICODEC) or len(raw) != 35:
        raise DataIntegrityError("KEY_NOT_P256_MULTIKEY")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw[2:])
    except ValueError as e:
        raise DataIntegrityError("KEY_NOT_ON_THE_P256_CURVE") from e
    return VerificationKey("p256", raw[2:])


# which key type each suite uses: a key of the wrong type is never tried
_SUITE_KEY = {SUITE: "ed25519", ECDSA_SUITE: "p256"}


class BundledContexts:
    """A JSON-LD document loader that serves only the bundled, hash-pinned contexts. Anything else raises."""

    def __init__(
        self, directory: Path | None, extra: Mapping[str, Mapping[str, Any]] | None = None
    ) -> None:
        self._docs: dict[str, dict[str, Any]] = {}
        if directory is not None:
            manifest = (
                yaml.safe_load((directory / "MANIFEST.yaml").read_text(encoding="utf-8")) or {}
            )
            for entry in manifest.get("contexts") or []:
                data = (directory / entry["file"]).read_bytes()
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise DataIntegrityError("CONTEXT_HASH_MISMATCH", entry["url"])
                self._docs[entry["url"]] = json.loads(data)
        for url, doc in (extra or {}).items():
            self._docs[url] = dict(doc)

    def __call__(self, url: str, _options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        doc = self._docs.get(url)
        if doc is None:
            raise jsonld.JsonLdError(
                "That context is not bundled and the verifier never fetches one.",
                "jsonld.LoadDocumentError",
                {"url": url},
                code="loading document failed",
            )
        return {
            "contentType": "application/ld+json",
            "contextUrl": None,
            "documentUrl": url,
            "document": doc,
        }

    @property
    def urls(self) -> frozenset[str]:
        return frozenset(self._docs)


def canonicalize(document: Mapping[str, Any], loader: Loader) -> str:
    """URDNA2015 as N-Quads. The algorithm is named on purpose: the library's default is a different one."""
    return jsonld.normalize(
        dict(document),
        {"algorithm": "URDNA2015", "format": "application/n-quads", "documentLoader": loader},
    )


def _norm(value: Any) -> Any:
    """A form of a JSON-LD value that ignores what compaction legitimately changes: key order, array order, and one-element arrays."""
    if isinstance(value, Mapping):
        return {k: _norm(v) for k, v in sorted(value.items()) if k != "@context"}
    if isinstance(value, list | tuple):
        items = sorted((_norm(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
        return items[0] if len(items) == 1 else items
    return value


def assert_nothing_dropped(document: Mapping[str, Any], loader: Loader) -> None:
    """Expand, compact back with the document's own context, and compare. A property the context does not define disappears in expansion, so the
    round trip differs from the original and the document is refused."""
    opts = {"documentLoader": loader}
    context = document.get("@context")
    expanded = jsonld.expand(dict(document), opts)
    again = jsonld.compact(expanded, context, opts)
    if _norm(dict(document)) != _norm(again):
        raise DataIntegrityError("PROPERTIES_NOT_DEFINED_BY_CONTEXT")


@dataclass(frozen=True, slots=True)
class Hashes:
    proof_options: bytes
    document: bytes

    @property
    def combined(self) -> bytes:
        return self.proof_options + self.document


def hashes_for(
    unsecured: Mapping[str, Any], proof_config: Mapping[str, Any], loader: BundledContexts
) -> Hashes:
    return Hashes(
        hashlib.sha256(canonicalize(proof_config, loader).encode("utf-8")).digest(),
        hashlib.sha256(canonicalize(unsecured, loader).encode("utf-8")).digest(),
    )


@dataclass(frozen=True, slots=True)
class Verdict:
    status: str  # VERIFIED | NOT_VERIFIED | INDETERMINATE
    reasons: tuple[str, ...]
    verification_method: str | None = None
    created: str | None = None


def split(vc: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the credential without its proof, the proof configuration), refusing anything the suite does not allow."""
    proof = vc.get("proof")
    if isinstance(proof, list):
        raise DataIntegrityError("MULTIPLE_PROOFS_NOT_SUPPORTED")
    if not isinstance(proof, Mapping):
        raise DataIntegrityError("PROOF_MISSING")
    if proof.get("type") != "DataIntegrityProof" or proof.get("cryptosuite") not in SUITES:
        raise DataIntegrityError("CRYPTOSUITE_NOT_SUPPORTED", str(proof.get("cryptosuite")))
    if proof.get("proofPurpose") != "assertionMethod":
        raise DataIntegrityError("PROOF_PURPOSE_NOT_ASSERTION_METHOD")
    if "@context" not in vc:
        raise DataIntegrityError("CONTEXT_MISSING")
    unsecured = {k: v for k, v in vc.items() if k != "proof"}
    if "@context" in proof and proof["@context"] != vc["@context"]:
        raise DataIntegrityError("PROOF_CONTEXT_DIFFERS")
    config = {k: v for k, v in proof.items() if k != "proofValue"}
    config["@context"] = vc["@context"]
    return unsecured, config


def _check(suite: str, key: VerificationKey, signature: bytes, data: bytes) -> bool:
    """True if `signature` over `data` verifies under `key` for this suite. A malformed key or signature is a failure, never an exception."""
    try:
        if suite == SUITE:
            Ed25519PublicKey.from_public_bytes(key.raw).verify(signature, data)
        else:  # ecdsa-rdfc-2019 over P-256: the signature is the fixed-width r||s form, over the combined hash with SHA-256
            public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), key.raw)
            der = encode_dss_signature(
                int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
            )
            public.verify(der, data, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        return False
    return True


def verify(
    vc: Mapping[str, Any], public_key: bytes | VerificationKey, contexts: BundledContexts
) -> Verdict:
    """The proof on `vc`, against the key the caller took from its issuer trust table. Raw bytes mean an Ed25519 key (the first suite); a
    `VerificationKey` names its type, and a key whose type does not belong to the proof's suite is refused, never tried."""
    key = (
        public_key
        if isinstance(public_key, VerificationKey)
        else VerificationKey("ed25519", public_key)
    )
    refused: list[str] = []  # per call, so concurrent verifications never share it

    def loader(url: str, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            return contexts(url, options)
        except jsonld.JsonLdError:
            refused.append(url)
            raise

    try:
        unsecured, config = split(vc)
        proof = vc["proof"]
        suite = str(proof["cryptosuite"])
        if _SUITE_KEY[suite] != key.kind:
            raise DataIntegrityError("KEY_DOES_NOT_MATCH_SUITE")
        signature = decode_multibase(str(proof.get("proofValue", "")))
        if len(signature) != 64:
            raise DataIntegrityError("PROOF_VALUE_LENGTH_INVALID")
        assert_nothing_dropped(unsecured, loader)
        assert_nothing_dropped(config, loader)
        data = hashes_for(unsecured, config, loader).combined
    except DataIntegrityError as e:
        return Verdict("INDETERMINATE", (e.code,))
    except jsonld.JsonLdError:
        # the library wraps a loader refusal in its own error, so what the loader refused is what says why
        return Verdict(
            "INDETERMINATE", ("CONTEXT_NOT_BUNDLED" if refused else "JSONLD_PROCESSING_FAILED",)
        )
    vm, created = proof.get("verificationMethod"), proof.get("created")
    if not _check(suite, key, signature, data):
        return Verdict("NOT_VERIFIED", ("SIGNATURE_INVALID",), vm, created)
    return Verdict("VERIFIED", ("SIGNATURE_VALID_FOR_SUPPLIED_KEY",), vm, created)

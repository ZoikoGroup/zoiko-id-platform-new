"""Holder binding for JSON-LD (Data Integrity) credentials: a did:key holder signs a presentation that wraps THIS credential for THIS verifier.

The presentations here are signed for real with `eddsa-jcs-2022` and `ecdsa-jcs-2019`, which sign canonical JSON and need no JSON-LD context, so every
rule below is exercised against a genuine signature and not a stub. What is proved is narrow: the key that signs the presentation is the DID the
credential was issued to, for this verifier, now. It is not proof of who the person is.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from zid_platform.modules.credential.application import data_integrity as di
from zid_platform.modules.credential.application import vc_verify

BUNDLE = Path(__file__).resolve().parents[3] / "config" / "jsonld-contexts"
AUD = "https://verifier.example"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
PROOF_FIELDS = ("created", "verificationMethod", "proofPurpose", "domain", "challenge")
VP_FIELDS = ("holder", "verifiableCredential", "type")


def b58(raw: bytes) -> str:
    n, out = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = ALPHABET[r] + out
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return "z" + "1" * pad + out


class Holder:
    """A holder with a key and the did:key it names, for one of the three supported key types."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        if kind == "ed25519":
            self.key = Ed25519PrivateKey.generate()
            pub = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            multikey = b58(b"\xed\x01" + pub)
        else:
            curve, prefix = (
                (ec.SECP256R1(), bytes([0x80, 0x24]))
                if kind == "p256"
                else (ec.SECP384R1(), bytes([0x81, 0x24]))
            )
            self.key = ec.generate_private_key(curve)
            pub = self.key.public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint)
            multikey = b58(prefix + pub)
        self.did = f"did:key:{multikey}"
        self.vm = f"{self.did}#{multikey}"

    def sign(self, data: bytes) -> bytes:
        if self.kind == "ed25519":
            return self.key.sign(data)
        hash_cls, width = (hashes.SHA256, 32) if self.kind == "p256" else (hashes.SHA384, 48)
        r, s = decode_dss_signature(self.key.sign(data, ec.ECDSA(hash_cls())))
        return r.to_bytes(width, "big") + s.to_bytes(width, "big")

    @property
    def suite(self) -> str:
        return "eddsa-jcs-2022" if self.kind == "ed25519" else "ecdsa-jcs-2019"

    @property
    def digest(self):
        return hashlib.sha384 if self.kind == "p384" else hashlib.sha256


def credential_for(subject: str) -> dict:
    return {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "type": ["VerifiableCredential"],
        "issuer": "https://issuer.example",
        "validFrom": "2026-01-01T00:00:00Z",
        "credentialSubject": {"id": subject},
    }


def presentation(h: Holder, vc: dict, **over) -> dict:
    """A VerifiablePresentation signed by `h` for AUD, with the proof fields overridable one at a time."""
    vp = {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "type": ["VerifiablePresentation"],
        "holder": h.did,
        "verifiableCredential": vc,
    }
    vp.update({k: v for k, v in over.items() if k in VP_FIELDS})
    options = {
        "type": "DataIntegrityProof",
        "cryptosuite": h.suite,
        "created": NOW.isoformat().replace("+00:00", "Z"),
        "verificationMethod": h.vm,
        "proofPurpose": "authentication",
        "domain": AUD,
        "challenge": "n-0S6_WzA2Mj",
        "@context": vp["@context"],
    }
    options.update({k: v for k, v in over.items() if k in PROOF_FIELDS})
    data = di.hashes_for(vp, options, None, h.digest, json_canonical=True).combined  # type: ignore[arg-type]
    return {**vp, "proof": {**options, "proofValue": b58(h.sign(data))}}


@pytest.fixture(scope="module")
def contexts() -> di.BundledContexts:
    return di.BundledContexts(BUNDLE)


def bound(vc: dict, vp: dict, contexts, now: datetime = NOW, audience: str = AUD) -> bool:
    return vc_verify.holder_bound_di(vc, vp, audience, contexts, now)


@pytest.mark.parametrize("kind", ["ed25519", "p256", "p384"])
def test_the_holder_the_credential_was_issued_to_is_bound_whatever_the_key_type(
    kind, contexts
) -> None:
    h = Holder(kind)
    vc = credential_for(h.did)
    assert bound(vc, presentation(h, vc), contexts) is True


def test_a_copied_credential_presented_by_someone_else_is_not_bound(contexts) -> None:
    owner, thief = Holder("ed25519"), Holder("ed25519")
    vc = credential_for(owner.did)
    assert bound(vc, presentation(thief, vc), contexts) is False


def test_a_presentation_signed_by_the_owner_for_another_credential_is_not_bound(contexts) -> None:
    h = Holder("ed25519")
    mine, other = credential_for(h.did), credential_for(h.did)
    other["validFrom"] = "2020-01-01T00:00:00Z"
    assert bound(mine, presentation(h, other), contexts) is False


def test_a_credential_issued_to_a_different_subject_is_not_bound_even_to_its_own_signer(
    contexts,
) -> None:
    h = Holder("ed25519")
    vc = credential_for("did:example:somebody-else")
    assert bound(vc, presentation(h, vc), contexts) is False


def test_a_presentation_made_for_another_verifier_is_not_reusable_here(contexts) -> None:
    h = Holder("ed25519")
    vc = credential_for(h.did)
    assert bound(vc, presentation(h, vc, domain="https://other.example"), contexts) is False
    assert bound(vc, presentation(h, vc), contexts, audience="https://other.example") is False


def test_a_stale_or_future_presentation_is_not_binding(contexts) -> None:
    h = Holder("ed25519")
    vc = credential_for(h.did)
    vp = presentation(h, vc)
    assert bound(vc, vp, contexts, NOW + timedelta(minutes=4)) is True
    assert bound(vc, vp, contexts, NOW + timedelta(minutes=6)) is False
    assert bound(vc, vp, contexts, NOW - timedelta(minutes=6)) is False


def test_a_presentation_without_a_challenge_is_not_binding(contexts) -> None:
    h = Holder("ed25519")
    vc = credential_for(h.did)
    assert bound(vc, presentation(h, vc, challenge=""), contexts) is False


def test_a_proof_for_the_wrong_purpose_is_not_binding(contexts) -> None:
    h = Holder("ed25519")
    vc = credential_for(h.did)
    assert bound(vc, presentation(h, vc, proofPurpose="assertionMethod"), contexts) is False


def test_any_change_after_signing_breaks_the_binding(contexts) -> None:
    h = Holder("ed25519")
    vc = credential_for(h.did)
    vp = presentation(h, vc)
    assert bound(vc, vp, contexts) is True
    for mutate in (
        lambda d: d["proof"].__setitem__("domain", "https://verifier.example/"),
        lambda d: d["proof"].__setitem__("challenge", "another"),
        lambda d: d.__setitem__("extra", "added"),
    ):
        tampered = copy.deepcopy(vp)
        mutate(tampered)
        assert bound(vc, tampered, contexts) is False


def test_a_verification_method_that_is_not_the_holder_is_not_binding(contexts) -> None:
    h, other = Holder("ed25519"), Holder("ed25519")
    vc = credential_for(h.did)
    assert bound(vc, presentation(h, vc, verificationMethod=other.vm), contexts) is False


def test_only_did_key_holders_are_accepted_nothing_is_resolved_over_the_network(contexts) -> None:
    h = Holder("ed25519")
    vc = credential_for("did:web:person.example")
    vp = presentation(h, vc, holder="did:web:person.example")
    assert bound(vc, vp, contexts) is False


def test_a_presentation_that_is_not_a_presentation_is_not_binding(contexts) -> None:
    h = Holder("ed25519")
    vc = credential_for(h.did)
    assert bound(vc, presentation(h, vc, type=["VerifiableCredential"]), contexts) is False
    assert bound(vc, {"proof": {}}, contexts) is False


def test_did_key_identifiers_are_decoded_for_the_three_key_types_and_nothing_else() -> None:
    for kind in ("ed25519", "p256", "p384"):
        h = Holder(kind)
        assert di.did_key_verification_key(h.vm).kind == kind
    with pytest.raises(di.DataIntegrityError):
        di.did_key_verification_key("did:web:example.com")
    with pytest.raises(di.DataIntegrityError):
        di.did_key_verification_key(Holder("ed25519").did + "#z6MkSomethingElse")

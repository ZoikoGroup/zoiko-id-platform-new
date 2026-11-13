"""GID-051 / CR-073 P2-2: Data Integrity proofs (eddsa-rdfc-2022), verified offline against the W3C test vector.

The vector is from the W3C specification itself (Appendix B.2): its canonical form, every hash, the combined hash and the signature are
reproduced exactly before the signed credential is verified, so a pass means interoperability, not just self-consistency.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from zid_platform.modules.credential.application import data_integrity as di

HERE = Path(__file__).resolve()
FIXTURES = HERE.parents[1] / "fixtures" / "data-integrity"
BUNDLE = HERE.parents[3] / "config" / "jsonld-contexts"
VECTOR = json.loads((FIXTURES / "eddsa-rdfc-2022-vector.json").read_text(encoding="utf-8"))
CITIZENSHIP = json.loads((FIXTURES / "citizenship-v4rc1.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def loader() -> di.BundledContexts:
    return di.BundledContexts(BUNDLE, {"https://w3id.org/citizenship/v4rc1": CITIZENSHIP})


@pytest.fixture(scope="module")
def key() -> bytes:
    return di.ed25519_public_key(VECTOR["public_key_multibase"])


def signed() -> dict:
    return copy.deepcopy(VECTOR["signed"])


# ------------------------------------------------------------------------------------------- the W3C vector, step by step


def test_the_canonical_form_of_the_credential_matches_the_specification(loader) -> None:
    assert (
        di.canonicalize(VECTOR["unsigned"], loader).strip() == VECTOR["canonical_unsigned"].strip()
    )


def test_the_canonical_form_of_the_proof_options_matches_the_specification(loader) -> None:
    assert (
        di.canonicalize(VECTOR["proof_options"], loader).strip()
        == VECTOR["canonical_proof_options"].strip()
    )


def test_every_hash_and_the_combined_hash_match_the_specification(loader) -> None:
    unsecured, config = di.split(VECTOR["signed"])
    h = di.hashes_for(unsecured, config, loader)
    assert h.document.hex() == VECTOR["unsigned_hash"]
    assert h.proof_options.hex() == VECTOR["proof_options_hash"]
    assert h.combined.hex() == VECTOR["combined_hash"]


def test_the_proof_value_decodes_to_the_specifications_signature() -> None:
    assert (
        di.decode_multibase(VECTOR["signed"]["proof"]["proofValue"]).hex()
        == VECTOR["signature_hex"]
    )


def test_the_signed_credential_verifies_against_the_specifications_key(loader, key) -> None:
    v = di.verify(VECTOR["signed"], key, loader)
    assert v.status == "VERIFIED" and v.reasons == ("SIGNATURE_VALID_FOR_SUPPLIED_KEY",)
    assert v.created == "2023-02-24T23:36:38Z" and v.verification_method.startswith("did:key:z6Mk")


# ------------------------------------------------------------------------------------------------------- one break at a time


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["credentialSubject"].__setitem__("givenName", "JANE"),
        lambda d: d["credentialSubject"]["employmentAuthorizationDocument"].__setitem__(
            "lprNumber", "000-000-000"
        ),
        lambda d: d.__setitem__("validUntil", "2099-12-03T00:00:00Z"),
        lambda d: d["proof"].__setitem__("created", "2024-01-01T00:00:00Z"),
        lambda d: d["proof"].__setitem__("verificationMethod", "did:key:z6MkOther#z6MkOther"),
        lambda d: d["issuer"].__setitem__("id", "did:key:zSomeoneElse"),
    ],
)
def test_changing_the_credential_or_the_proof_options_breaks_the_signature(
    loader, key, mutate
) -> None:
    doc = signed()
    mutate(doc)
    assert di.verify(doc, key, loader).status == "NOT_VERIFIED"


def test_a_different_key_does_not_verify(loader) -> None:
    other = bytes(32)
    assert di.verify(VECTOR["signed"], other, loader).status == "NOT_VERIFIED"
    assert di.verify(VECTOR["signed"], b"short", loader).status == "NOT_VERIFIED", (
        "a malformed key is a failure, not an exception"
    )


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda d: d.__setitem__("proof", [d["proof"], d["proof"]]),
            "MULTIPLE_PROOFS_NOT_SUPPORTED",
        ),
        (lambda d: d.pop("proof"), "PROOF_MISSING"),
        (
            lambda d: d["proof"].__setitem__("cryptosuite", "bbs-2023"),
            "CRYPTOSUITE_NOT_SUPPORTED",
        ),
        (
            lambda d: d["proof"].__setitem__("type", "Ed25519Signature2020"),
            "CRYPTOSUITE_NOT_SUPPORTED",
        ),
        (
            lambda d: d["proof"].__setitem__("proofPurpose", "authentication"),
            "PROOF_PURPOSE_NOT_ASSERTION_METHOD",
        ),
        (
            lambda d: d["proof"].__setitem__("@context", ["https://example.org/other"]),
            "PROOF_CONTEXT_DIFFERS",
        ),
        (
            lambda d: d["proof"].__setitem__("proofValue", "uNotBase58Btc"),
            "MULTIBASE_NOT_BASE58BTC",
        ),
        (
            lambda d: d["proof"].__setitem__("proofValue", "z2NEpo7TZRRrLZSi2U"),
            "PROOF_VALUE_LENGTH_INVALID",
        ),
        (lambda d: d["proof"].__setitem__("proofValue", "z0OIl"), "BASE58_CHARACTER_INVALID"),
        (lambda d: d.pop("@context"), "CONTEXT_MISSING"),
    ],
)
def test_a_proof_the_suite_does_not_allow_is_refused_by_name(
    loader, key, mutate, code: str
) -> None:
    doc = signed()
    mutate(doc)
    v = di.verify(doc, key, loader)
    assert v.status == "INDETERMINATE" and v.reasons == (code,)


# ------------------------------------------------------------------------------------------------ offline, pinned, and lossless


def test_a_context_that_is_not_bundled_is_refused_and_nothing_is_fetched(key) -> None:
    only_core = di.BundledContexts(BUNDLE)  # the production bundle: no citizenship context
    v = di.verify(VECTOR["signed"], key, only_core)
    assert v.status == "INDETERMINATE" and v.reasons == ("CONTEXT_NOT_BUNDLED",)
    remote = signed()
    remote["@context"] = ["https://www.w3.org/ns/credentials/v2", "https://attacker.example/ctx"]
    assert di.verify(remote, key, only_core).reasons == ("CONTEXT_NOT_BUNDLED",)


def test_a_bundled_file_that_was_changed_is_refused_at_load(tmp_path) -> None:
    data = (BUNDLE / "credentials-v2.json").read_bytes()
    (tmp_path / "c.json").write_bytes(data + b" ")
    (tmp_path / "MANIFEST.yaml").write_text(
        'contexts:\n  - url: "https://www.w3.org/ns/credentials/v2"\n    file: c.json\n    sha256: '
        + hashlib.sha256(data).hexdigest()
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(di.DataIntegrityError) as e:
        di.BundledContexts(tmp_path)
    assert e.value.code == "CONTEXT_HASH_MISMATCH"


def test_the_production_bundle_loads_and_lists_what_it_serves() -> None:
    urls = di.BundledContexts(BUNDLE).urls
    assert {
        "https://www.w3.org/ns/credentials/v2",
        "https://purl.imsglobal.org/spec/ob/v3p0/context-3.0.3.json",
        "https://purl.imsglobal.org/spec/clr/v2p0/context-2.0.1.json",
    } <= urls


def test_a_property_the_context_does_not_define_is_never_silently_dropped(key) -> None:
    """JSON-LD ignores an undefined property: it would sit in the credential unsigned. The round trip catches it."""
    tiny = {"@context": {"name": "https://schema.org/name"}}
    loader = di.BundledContexts(None, {"urn:test:ctx": tiny})
    di.assert_nothing_dropped(
        {"@context": "urn:test:ctx", "name": "ok"}, loader
    )  # fully defined: fine
    with pytest.raises(di.DataIntegrityError) as e:
        di.assert_nothing_dropped(
            {"@context": "urn:test:ctx", "name": "ok", "secret": "unsigned"}, loader
        )
    assert e.value.code == "PROPERTIES_NOT_DEFINED_BY_CONTEXT"


def test_the_lossless_check_accepts_the_genuine_vector(loader) -> None:
    unsecured, config = di.split(VECTOR["signed"])
    di.assert_nothing_dropped(unsecured, loader)
    di.assert_nothing_dropped(config, loader)


def test_urdna2015_is_requested_not_the_library_default(loader) -> None:
    """The library's default algorithm is a different one; the specification's hashes only match URDNA2015."""
    out = di.canonicalize(VECTOR["unsigned"], loader)
    assert out.strip() == VECTOR["canonical_unsigned"].strip()


# ------------------------------------------------------------------------------------------------------ encodings


def test_base58_matches_known_values() -> None:
    assert di.base58_decode("2NEpo7TZRRrLZSi2U") == b"Hello World!"
    assert di.base58_decode("111") == b"\x00\x00\x00"
    assert di.base58_decode("") == b""


def test_only_an_ed25519_multikey_is_accepted_as_a_key() -> None:
    assert len(di.ed25519_public_key(VECTOR["public_key_multibase"])) == 32
    for bad in (
        "",
        "z",
        "uAAAA",
        "z2NEpo7TZRRrLZSi2U",
        "zDnaegE6RR3atJtHKwTRTWHsJ3kNHqFwv7n9YjTgmU7TyfU76",
    ):
        with pytest.raises(di.DataIntegrityError):
            di.ed25519_public_key(bad)  # the last is a P-256 did:key: another suite, another module


# ------------------------------------------------------------------------------------- the second suite: ecdsa-rdfc-2019 (P-256)

ECDSA = json.loads((FIXTURES / "ecdsa-rdfc-2019-vector.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def p256() -> di.VerificationKey:
    return di.p256_public_key(ECDSA["public_key_multibase"])


def test_the_ecdsa_vectors_canonical_forms_and_hashes_match_the_specification(loader) -> None:
    assert di.canonicalize(ECDSA["unsigned"], loader).strip() == ECDSA["canonical_unsigned"].strip()
    assert (
        di.canonicalize(ECDSA["proof_options"], loader).strip()
        == ECDSA["canonical_proof_options"].strip()
    )
    unsecured, config = di.split(ECDSA["signed"])
    h = di.hashes_for(unsecured, config, loader)
    assert (
        h.document.hex() == ECDSA["unsigned_hash"]
        and h.proof_options.hex() == ECDSA["proof_options_hash"]
    )
    assert h.combined.hex() == ECDSA["combined_hash"]
    assert (
        di.decode_multibase(ECDSA["signed"]["proof"]["proofValue"]).hex() == ECDSA["signature_hex"]
    )


def test_the_ecdsa_signed_credential_verifies_against_the_specifications_p256_key(
    loader, p256
) -> None:
    v = di.verify(ECDSA["signed"], p256, loader)
    assert v.status == "VERIFIED" and v.reasons == ("SIGNATURE_VALID_FOR_SUPPLIED_KEY",)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["credentialSubject"].__setitem__("givenName", "JANE"),
        lambda d: d["proof"].__setitem__("created", "2024-01-01T00:00:00Z"),
        lambda d: d.__setitem__("validUntil", "2099-12-03T00:00:00Z"),
    ],
)
def test_changing_an_ecdsa_secured_credential_breaks_the_signature(loader, p256, mutate) -> None:
    doc = copy.deepcopy(ECDSA["signed"])
    mutate(doc)
    assert di.verify(doc, p256, loader).status == "NOT_VERIFIED"


def test_a_flipped_ecdsa_signature_does_not_verify(loader, p256) -> None:
    doc = copy.deepcopy(ECDSA["signed"])
    raw = bytearray(di.decode_multibase(doc["proof"]["proofValue"]))
    raw[10] ^= 0x01
    # re-encode the changed signature as base58-btc
    n, out = int.from_bytes(bytes(raw), "big"), ""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    while n:
        n, r = divmod(n, 58)
        out = alphabet[r] + out
    doc["proof"]["proofValue"] = "z" + out
    assert di.verify(doc, p256, loader).status == "NOT_VERIFIED"


def test_a_key_of_the_wrong_type_for_the_suite_is_refused_not_tried(loader, p256, key) -> None:
    ed = di.VerificationKey("ed25519", key)
    assert di.verify(ECDSA["signed"], ed, loader).reasons == ("KEY_DOES_NOT_MATCH_SUITE",)
    assert di.verify(VECTOR["signed"], p256, loader).reasons == ("KEY_DOES_NOT_MATCH_SUITE",)
    assert di.verify(VECTOR["signed"], key, loader).status == "VERIFIED", (
        "raw bytes still mean the first suite"
    )


def test_only_a_p256_multikey_that_is_on_the_curve_is_accepted() -> None:
    assert len(di.p256_public_key(ECDSA["public_key_multibase"]).raw) == 33
    for bad in ("", "z", "uAAAA", VECTOR["public_key_multibase"]):  # the last is an Ed25519 key
        with pytest.raises(di.DataIntegrityError):
            di.p256_public_key(bad)
    # find an x that is not the x of any P-256 point (about half of all values are not), rather than assume one
    from cryptography.hazmat.primitives.asymmetric import ec

    for x in range(1, 200):
        point = b"\x02" + x.to_bytes(32, "big")
        try:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        except ValueError:
            off_curve = "z" + _b58(b"\x80\x24" + point)
            break
    else:
        pytest.fail("no off-curve x found")
    with pytest.raises(di.DataIntegrityError) as e:
        di.p256_public_key(off_curve)
    assert e.value.code == "KEY_NOT_ON_THE_P256_CURVE"


def test_a_cryptosuite_that_is_neither_is_still_refused_by_name(loader, p256) -> None:
    doc = copy.deepcopy(ECDSA["signed"])
    doc["proof"]["cryptosuite"] = "bbs-2023"
    assert di.verify(doc, p256, loader).reasons == ("CRYPTOSUITE_NOT_SUPPORTED",)


def _b58(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n, out = int.from_bytes(data, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = alphabet[r] + out
    return "1" * (len(data) - len(data.lstrip(b"\x00"))) + out


# ------------------------------------------------------------------------- ecdsa-rdfc-2019 with curve P-384 (SHA-384)

P384 = json.loads((FIXTURES / "ecdsa-rdfc-2019-p384-vector.json").read_text(encoding="utf-8"))
_ZB58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _zb58(raw: bytes) -> str:
    n, out = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = _ZB58[r] + out
    return "z" + out


@pytest.fixture(scope="module")
def p384() -> di.VerificationKey:
    return di.p384_public_key(P384["public_key_multibase"])


def test_the_p384_vectors_canonical_forms_hashes_and_signature_match_the_specification(
    loader,
) -> None:
    assert (
        di.canonicalize(P384["unsigned"], loader, "sha384").strip()
        == P384["canonical_unsigned"].strip()
    )
    assert (
        di.canonicalize(P384["proof_options"], loader, "sha384").strip()
        == P384["canonical_proof_options"].strip()
    )
    unsecured, config = di.split(P384["signed"])
    h = di.hashes_for(unsecured, config, loader, hashlib.sha384)
    assert h.document.hex() == P384["unsigned_hash"]
    assert h.proof_options.hex() == P384["proof_options_hash"]
    assert h.combined.hex() == P384["combined_hash"]
    assert di.decode_multibase(P384["signed"]["proof"]["proofValue"]).hex() == P384["signature_hex"]


def test_the_p384_signed_credential_verifies_against_the_specifications_key(loader, p384) -> None:
    v = di.verify(P384["signed"], p384, loader)
    assert v.status == "VERIFIED" and v.reasons == ("SIGNATURE_VALID_FOR_SUPPLIED_KEY",)


def test_a_changed_p384_credential_does_not_verify(loader, p384) -> None:
    doc = copy.deepcopy(P384["signed"])
    doc["credentialSubject"]["givenName"] = "JANE"
    assert di.verify(doc, p384, loader).status == "NOT_VERIFIED"


def test_a_flipped_p384_signature_does_not_verify(loader, p384) -> None:
    doc = copy.deepcopy(P384["signed"])
    raw = bytearray(di.decode_multibase(doc["proof"]["proofValue"]))
    raw[10] ^= 0x01
    doc["proof"]["proofValue"] = _zb58(bytes(raw))
    assert di.verify(doc, p384, loader).status == "NOT_VERIFIED"


def test_a_p256_key_cannot_check_a_p384_signature_and_the_reverse(loader, p384, p256) -> None:
    # the signature width differs, so the mismatch is refused before any curve arithmetic
    assert di.verify(P384["signed"], p256, loader).reasons == ("PROOF_VALUE_LENGTH_INVALID",)
    assert di.verify(ECDSA["signed"], p384, loader).reasons == ("PROOF_VALUE_LENGTH_INVALID",)


def test_a_multikey_of_another_curve_or_off_the_p384_curve_is_refused() -> None:
    with pytest.raises(di.DataIntegrityError):
        di.p384_public_key(ECDSA["public_key_multibase"])  # a P-256 multikey
    bad = bytearray(di.decode_multibase(P384["public_key_multibase"]))
    bad[2] = 0x05  # not a valid compressed-point prefix
    with pytest.raises(di.DataIntegrityError):
        di.p384_public_key(_zb58(bytes(bad)))


def test_the_issuer_key_lookup_returns_a_pinned_p384_key_and_it_verifies(loader, p384) -> None:
    import base64

    from cryptography.hazmat.primitives.asymmetric import ec
    from zid_platform.modules.credential.application import vc_verify

    point = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP384R1(), p384.raw).public_numbers()

    def b64(n: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(48, "big")).rstrip(b"=").decode()

    vm = P384["signed"]["proof"]["verificationMethod"]
    jwks = {
        "keys": [{"kty": "EC", "crv": "P-384", "kid": vm, "x": b64(point.x), "y": b64(point.y)}]
    }
    key = vc_verify._proof_key(jwks, vm, "ecdsa-rdfc-2019")
    assert key is not None and key.kind == "p384"
    assert di.verify(P384["signed"], key, loader).status == "VERIFIED"
    # a P-384 JWK is never offered to the EdDSA suite
    assert vc_verify._proof_key(jwks, vm, "eddsa-rdfc-2022") is None


# ------------------------------------------------------------------------------ the JCS suites (RFC 8785 canonical JSON)

JCS_VECTORS = {
    name: json.loads((FIXTURES / f"{name}-vector.json").read_text(encoding="utf-8"))
    for name in ("ecdsa-jcs-2019-p256", "ecdsa-jcs-2019-p384", "eddsa-jcs-2022")
}


def _jcs_key(v: dict) -> di.VerificationKey:
    mk = v["public_key_multibase"]
    if v["curve"] == "P-256":
        return di.p256_public_key(mk)
    if v["curve"] == "P-384":
        return di.p384_public_key(mk)
    return di.VerificationKey("ed25519", di.ed25519_public_key(mk))


@pytest.mark.parametrize("name", sorted(JCS_VECTORS))
def test_the_jcs_vectors_canonical_forms_hashes_and_signature_match_the_specification(
    name, loader
) -> None:
    v = JCS_VECTORS[name]
    digest = hashlib.sha384 if v["curve"] == "P-384" else hashlib.sha256
    assert di.jcs(v["unsigned"]) == v["canonical_unsigned"]
    assert di.jcs(v["proof_options"]) == v["canonical_proof_options"]
    unsecured, config = di.split(v["signed"])
    h = di.hashes_for(unsecured, config, loader, digest, json_canonical=True)
    assert h.document.hex() == v["unsigned_hash"]
    assert h.proof_options.hex() == v["proof_options_hash"]
    assert h.combined.hex() == v["combined_hash"]
    assert di.decode_multibase(v["signed"]["proof"]["proofValue"]).hex() == v["signature_hex"]


@pytest.mark.parametrize("name", sorted(JCS_VECTORS))
def test_the_jcs_signed_credentials_verify_against_the_specifications_keys(name, loader) -> None:
    v = JCS_VECTORS[name]
    got = di.verify(v["signed"], _jcs_key(v), loader)
    assert got.status == "VERIFIED" and got.reasons == ("SIGNATURE_VALID_FOR_SUPPLIED_KEY",)


@pytest.mark.parametrize("name", sorted(JCS_VECTORS))
def test_a_changed_or_extended_jcs_credential_does_not_verify(name, loader) -> None:
    v = JCS_VECTORS[name]
    key = _jcs_key(v)
    changed = copy.deepcopy(v["signed"])
    changed["credentialSubject"]["id"] = "did:example:someoneelse"
    assert di.verify(changed, key, loader).status == "NOT_VERIFIED"
    # JCS signs the JSON text, so a member no context defines is covered too: adding one must break it
    extended = copy.deepcopy(v["signed"])
    extended["unexpected"] = "added after signing"
    assert di.verify(extended, key, loader).status == "NOT_VERIFIED"


@pytest.mark.parametrize("name", sorted(JCS_VECTORS))
def test_a_flipped_jcs_signature_does_not_verify(name, loader) -> None:
    v = JCS_VECTORS[name]
    doc = copy.deepcopy(v["signed"])
    raw = bytearray(di.decode_multibase(doc["proof"]["proofValue"]))
    raw[5] ^= 0x01
    doc["proof"]["proofValue"] = _zb58(bytes(raw))
    assert di.verify(doc, _jcs_key(v), loader).status == "NOT_VERIFIED"


def test_a_jcs_proof_is_never_checked_with_a_key_of_the_other_family(loader) -> None:
    ed = _jcs_key(JCS_VECTORS["eddsa-jcs-2022"])
    ec_proof = JCS_VECTORS["ecdsa-jcs-2019-p256"]["signed"]
    assert di.verify(ec_proof, ed, loader).reasons == ("KEY_DOES_NOT_MATCH_SUITE",)
    assert di.verify(
        JCS_VECTORS["eddsa-jcs-2022"]["signed"],
        _jcs_key(JCS_VECTORS["ecdsa-jcs-2019-p256"]),
        loader,
    ).reasons == ("KEY_DOES_NOT_MATCH_SUITE",)


def test_a_jcs_suite_reads_no_context_so_an_unbundled_one_does_not_matter(loader) -> None:
    v = JCS_VECTORS["eddsa-jcs-2022"]
    doc = copy.deepcopy(v["signed"])
    # the vector uses bundled contexts, but nothing is fetched or expanded for this suite: a rewritten context list simply changes the signed text
    doc["@context"] = [
        "https://www.w3.org/ns/credentials/v2",
        "https://example.invalid/never-fetched",
    ]
    doc["proof"]["@context"] = doc["@context"]
    assert di.verify(doc, _jcs_key(v), loader).status == "NOT_VERIFIED"


def test_an_unsupported_cryptosuite_is_still_refused_by_name(loader) -> None:
    doc = copy.deepcopy(JCS_VECTORS["eddsa-jcs-2022"]["signed"])
    doc["proof"]["cryptosuite"] = "bbs-2023"
    assert di.verify(doc, _jcs_key(JCS_VECTORS["eddsa-jcs-2022"]), loader).reasons == (
        "CRYPTOSUITE_NOT_SUPPORTED",
    )


# ------------------------------------------------------------------------------------- the Europass EDC contexts (bundled 2026-11-13)

EDC_DOC = {
    "@context": [
        "https://www.w3.org/2018/credentials/v1",
        "https://data.europa.eu/snb/model/context/edc-ap",
    ],
    "id": "urn:credential:1",
    "type": ["VerifiableCredential", "EuropeanDigitalCredential"],
    "issuer": {"id": "did:web:uni.example", "type": "Agent"},
    "validFrom": "2026-01-01T00:00:00Z",
    "credentialSubject": {"id": "urn:person:1", "type": "Person", "givenName": {"en": "Ana"}},
}


def test_the_bundle_serves_the_europass_edc_contexts_pinned_by_hash() -> None:
    urls = di.BundledContexts(BUNDLE).urls
    assert {
        "https://www.w3.org/2018/credentials/v1",
        "https://data.europa.eu/snb/model/context/edc-ap",
    } <= urls


def test_an_edc_shaped_credential_canonicalizes_to_the_europass_vocabulary() -> None:
    b = di.BundledContexts(BUNDLE)
    nquads = di.canonicalize(EDC_DOC, b)
    assert "<http://data.europa.eu/snb/model/elm/EuropeanDigitalCredential>" in nquads
    di.assert_nothing_dropped(EDC_DOC, b)


def test_a_property_the_edc_context_does_not_define_is_caught_not_silently_dropped() -> None:
    b = di.BundledContexts(BUNDLE)
    doc = {**EDC_DOC, "madeUpClaim": "nobody defined this"}
    with pytest.raises(di.DataIntegrityError):
        di.assert_nothing_dropped(doc, b)


def test_a_tampered_edc_context_file_is_refused_at_load(tmp_path) -> None:
    import shutil

    for name in (
        "MANIFEST.yaml",
        "credentials-v1.json",
        "europass-edc-ap.json",
        "credentials-v2.json",
        "ob-v3p0.json",
        "clr-v2p0.json",
    ):
        shutil.copy(BUNDLE / name, tmp_path / name)
    (tmp_path / "europass-edc-ap.json").write_text("{}", encoding="utf-8")
    with pytest.raises(di.DataIntegrityError):
        di.BundledContexts(tmp_path)

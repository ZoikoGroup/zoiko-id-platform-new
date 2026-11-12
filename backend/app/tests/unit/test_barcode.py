"""Signed 2D barcodes (CR-073 P2-4): base45, zlib, COSE_Sign1, verified only against keys the operator configured."""

from __future__ import annotations

import zlib
from datetime import timedelta

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from pdf_signing import NOW, ec_key, make_cert, pki, rsa_key
from zid_platform.modules.evidence.application import barcode

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"


def b45(data: bytes) -> str:
    out = ""
    for i in range(0, len(data), 2):
        pair = data[i : i + 2]
        if len(pair) == 2:
            n = int.from_bytes(pair, "big")
            out += ALPHABET[n % 45] + ALPHABET[(n // 45) % 45] + ALPHABET[n // 2025]
        else:
            n = pair[0]
            out += ALPHABET[n % 45] + ALPHABET[n // 45]
    return out


PAYLOAD = {1: "UT", 4: 1893456000, 6: 1735689600, -260: {1: {"v": [{"tg": "840539006", "dn": 1}]}}}


def cose(
    cert_party, *, alg: int = barcode.ALG_ES256, payload=None, kid: bytes | None = None, key=None
) -> bytes:
    protected = cbor2.dumps(
        {1: alg, 4: kid if kid is not None else barcode.key_id_of(cert_party.cert)}
    )
    body = cbor2.dumps(PAYLOAD if payload is None else payload)
    data = cbor2.dumps(["Signature1", protected, b"", body])
    k = key or cert_party.key
    if alg == barcode.ALG_ES256:
        r, s = decode_dss_signature(k.sign(data, ec.ECDSA(hashes.SHA256())))
        sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    elif alg == barcode.ALG_PS256:
        sig = k.sign(
            data, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256()
        )
    else:
        sig = (
            b"\0" * 64
        )  # an algorithm this module does not support: the signature is never reached
    return cbor2.dumps(cbor2.CBORTag(18, [protected, {}, body, sig]))


def qr(raw: bytes) -> str:
    return "HC1:" + b45(zlib.compress(raw))


def signer(*, key=None, **dates) -> object:
    return make_cert("Document Signer", key or ec_key(), pki().root, **dates)


# ------------------------------------------------------------------------------------------------ base45 (RFC 9285)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("BB8", b"AB"),
        ("%69 VD92EX0", b"Hello!!"),
        ("UJCLQE7W581", b"base-45"),
        ("QED8WEX0", b"ietf!"),
    ],
)
def test_base45_decodes_the_rfc_9285_vectors(text: str, expected: bytes) -> None:
    assert barcode.base45_decode(text) == expected
    assert b45(expected) == text, "the test encoder agrees with the RFC too"


@pytest.mark.parametrize("bad", ["A", "ABCD", "lowercase", "GGW", ":::"])
def test_base45_refuses_what_is_not_base45(bad: str) -> None:
    with pytest.raises(barcode.BarcodeError):
        barcode.base45_decode(bad)


def test_a_decompression_bomb_and_junk_are_refused() -> None:
    bomb = "HC1:" + b45(zlib.compress(b"\x00" * (barcode.MAX_INFLATED + 10)))
    with pytest.raises(barcode.BarcodeError) as e:
        barcode.decode_text(bomb)
    assert e.value.code in {"PAYLOAD_TOO_LARGE"}
    for text, code in (
        ("NOTHC1:ABC", "PREFIX_NOT_RECOGNISED"),
        ("HC1:" + b45(b"not zlib"), "PAYLOAD_NOT_COMPRESSED_DATA"),
    ):
        with pytest.raises(barcode.BarcodeError) as e2:
            barcode.decode_text(text)
        assert e2.value.code == code


def test_cose_that_is_not_a_sign1_is_refused() -> None:
    for raw, code in (
        (cbor2.dumps([1, 2]), "COSE_SIGN1_SHAPE_INVALID"),
        (cbor2.dumps(cbor2.CBORTag(99, [b"", {}, b"", b""])), "COSE_TAG_UNEXPECTED"),
        (b"\xff\xff", "COSE_NOT_READABLE"),
    ):
        with pytest.raises(barcode.BarcodeError) as e:
            barcode.read_cose(raw)
        assert e.value.code == code


# ---------------------------------------------------------------------------------------------------------- verification


def test_a_payload_signed_by_a_configured_key_is_verified_es256() -> None:
    s = signer()
    msg = barcode.decode_text(qr(cose(s)))
    v = barcode.verify(msg, [s.cert])
    assert v.status == "VERIFIED" and v.signer == "CN=Document Signer"
    assert barcode.claims_of(msg)["payload"]["1"] == "UT"


def test_ps256_is_verified_too() -> None:
    s = signer(key=rsa_key())
    msg = barcode.decode_text(qr(cose(s, alg=barcode.ALG_PS256)))
    assert barcode.verify(msg, [s.cert]).status == "VERIFIED"


def test_a_changed_payload_fails_the_signature() -> None:
    s = signer()
    good = barcode.read_cose(cose(s))
    forged = barcode.CoseSign1(
        good.protected_raw, good.protected, good.unprotected, cbor2.dumps({1: "XX"}), good.signature
    )
    assert barcode.verify(forged, [s.cert]).status == "NOT_VERIFIED"


def test_a_key_nobody_configured_is_indeterminate_not_verified() -> None:
    s, other = signer(), signer()
    v = barcode.verify(barcode.read_cose(cose(s)), [other.cert])
    assert v.status == "INDETERMINATE" and v.reasons == ("KEY_NOT_CONFIGURED",)
    assert barcode.verify(barcode.read_cose(cose(s)), []).status == "INDETERMINATE"


def test_a_signature_made_with_a_different_key_than_the_one_named_fails() -> None:
    s, impostor = signer(), signer()
    forged = cose(s, key=impostor.key)  # names s's key id, signed by someone else
    assert barcode.verify(barcode.read_cose(forged), [s.cert]).status == "NOT_VERIFIED"


def test_an_unsupported_algorithm_a_missing_key_id_and_an_expired_key_are_never_verified() -> None:
    s = signer()
    assert barcode.verify(barcode.read_cose(cose(s, alg=-8)), [s.cert]).reasons == (
        "ALGORITHM_NOT_SUPPORTED",
    )
    protected = cbor2.dumps({1: barcode.ALG_ES256})
    nokid = cbor2.dumps(cbor2.CBORTag(18, [protected, {}, cbor2.dumps({}), b"\x00" * 64]))
    assert barcode.verify(barcode.read_cose(nokid), [s.cert]).reasons == ("KEY_ID_MISSING",)
    old = signer(not_before=NOW - timedelta(days=400), not_after=NOW - timedelta(days=30))
    v = barcode.verify(barcode.read_cose(cose(old)), [old.cert])
    assert v.status == "INDETERMINATE" and v.reasons == ("KEY_CERTIFICATE_NOT_VALID_NOW",)


def test_an_es256_signature_of_the_wrong_length_or_curve_is_not_verified() -> None:
    s = signer()
    good = barcode.read_cose(cose(s))
    short = barcode.CoseSign1(
        good.protected_raw, good.protected, good.unprotected, good.payload, good.signature[:60]
    )
    assert barcode.verify(short, [s.cert]).reasons == ("SIGNATURE_SHAPE_INVALID",)
    rsa_signer = signer(key=rsa_key())
    mismatched = barcode.read_cose(cose(rsa_signer, alg=barcode.ALG_PS256))
    # the same message claimed as ES256 against an RSA key: the algorithm and key do not match
    claimed = barcode.CoseSign1(
        cbor2.dumps({1: barcode.ALG_ES256, 4: barcode.key_id_of(rsa_signer.cert)}),
        {1: barcode.ALG_ES256, 4: barcode.key_id_of(rsa_signer.cert)},
        {},
        mismatched.payload,
        b"\x00" * 64,
    )
    assert barcode.verify(claimed, [rsa_signer.cert]).reasons == ("KEY_DOES_NOT_MATCH_ALGORITHM",)


def test_a_payload_that_is_not_cbor_is_returned_as_hex_not_dropped() -> None:
    s = signer()
    raw = barcode.read_cose(cose(s, payload=b"\x00"))
    claims = barcode.claims_of(
        barcode.CoseSign1(
            raw.protected_raw, raw.protected, raw.unprotected, b"\xff\xfe", raw.signature
        )
    )
    assert claims == {"payload_hex": "fffe"}


def test_the_barcode_keys_folder_is_read_by_the_trust_loader(tmp_path) -> None:
    from cryptography.hazmat.primitives.serialization import Encoding
    from zid_platform.modules.evidence.application import trust as tr

    s = signer()
    (tmp_path / "barcode-keys").mkdir()
    (tmp_path / "barcode-keys" / "dsc.pem").write_bytes(s.cert.public_bytes(Encoding.PEM))
    (tmp_path / "barcode-keys" / "junk.pem").write_bytes(b"not a certificate")
    cfg = tr.load_trust(tmp_path)
    assert len(cfg.barcode_keys) == 1 and tr.load_trust(tmp_path / "nothing").barcode_keys == ()

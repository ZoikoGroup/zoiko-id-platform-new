"""App Attest verification against a synthetic chain built here (CR-110). No Apple device or Apple certificate is involved: this proves the checks, not that a real device's report verifies."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cbor2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from zid_platform.modules.verification.infrastructure import device_attestation as da

NOW = datetime.now(UTC)
APP = "TEAM123456.com.example.app"
CHALLENGE = b"nonce-1:" + b"a" * 64


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _cert(
    subject: str,
    key: ec.EllipticCurvePrivateKey,
    issuer: str,
    issuer_key: ec.EllipticCurvePrivateKey,
    *,
    extra: x509.ExtensionType | None = None,
    days: int = 30,
) -> x509.Certificate:
    b = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(_name(issuer))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=days))
    )
    if extra is not None:
        b = b.add_extension(extra, critical=False)
    return b.sign(issuer_key, hashes.SHA256())


def build(
    *,
    app_id: str = APP,
    counter: int = 0,
    challenge: bytes = CHALLENGE,
    key_id_override: bytes | None = None,
    aaguid: bytes = da.AAGUID["production"],
    nonce_override: bytes | None = None,
    root_days: int = 30,
):
    root_key, leaf_key = (
        ec.generate_private_key(ec.SECP256R1()),
        ec.generate_private_key(ec.SECP256R1()),
    )
    point = leaf_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    key_id = hashlib.sha256(point).digest()
    auth = (
        hashlib.sha256(app_id.encode()).digest()
        + b"\x41"
        + counter.to_bytes(4, "big")
        + aaguid
        + len(key_id).to_bytes(2, "big")
        + key_id
    )
    nonce = nonce_override or hashlib.sha256(auth + hashlib.sha256(challenge).digest()).digest()
    der = bytes([0x30, 0x24, 0xA1, 0x22, 0x04, 0x20]) + nonce
    root = _cert(
        "Test App Attestation Root", root_key, "Test App Attestation Root", root_key, days=root_days
    )
    leaf = _cert(
        "leaf",
        leaf_key,
        "Test App Attestation Root",
        root_key,
        extra=x509.UnrecognizedExtension(da.NONCE_OID, der),
    )
    obj = {
        "fmt": "apple-appattest",
        "attStmt": {"x5c": [leaf.public_bytes(serialization.Encoding.DER)], "receipt": b""},
        "authData": auth,
    }
    pem = root.public_bytes(serialization.Encoding.PEM)
    return cbor2.dumps(obj), (key_id_override or key_id), da.AppAttestConfig(APP, pem)


def test_a_well_formed_attestation_verifies() -> None:
    att, key_id, cfg = build()
    assert da.verify_app_attest(att, key_id=key_id, challenge=CHALLENGE, cfg=cfg) is True


def test_every_check_can_fail_on_its_own() -> None:
    att, key_id, cfg = build()
    assert not da.verify_app_attest(
        att, key_id=key_id, challenge=b"another challenge", cfg=cfg
    )  # not bound to this challenge
    assert not da.verify_app_attest(
        att, key_id=b"\x00" * 32, challenge=CHALLENGE, cfg=cfg
    )  # key id does not match the leaf
    assert not da.verify_app_attest(
        att, key_id=key_id, challenge=CHALLENGE, cfg=da.AppAttestConfig("OTHER.app", cfg.root_pem)
    )  # another app
    assert not da.verify_app_attest(
        att,
        key_id=key_id,
        challenge=CHALLENGE,
        cfg=da.AppAttestConfig(APP, cfg.root_pem, "development"),
    )  # wrong environment
    _, _, other_cfg = build()
    assert not da.verify_app_attest(
        att, key_id=key_id, challenge=CHALLENGE, cfg=other_cfg
    )  # a different root
    a2, k2, c2 = build(counter=1)
    assert not da.verify_app_attest(
        a2, key_id=k2, challenge=CHALLENGE, cfg=c2
    )  # a first attestation has counter zero
    a3, k3, c3 = build(nonce_override=b"\x01" * 32)
    assert not da.verify_app_attest(a3, key_id=k3, challenge=CHALLENGE, cfg=c3)
    a4, k4, c4 = build(root_days=-0)  # not_valid_after = now
    assert (
        da.verify_app_attest(
            a4, key_id=k4, challenge=CHALLENGE, cfg=c4, now=NOW + timedelta(days=400)
        )
        is False
    )  # outside the validity window
    assert not da.verify_app_attest(b"not cbor", key_id=key_id, challenge=CHALLENGE, cfg=cfg)
    assert not da.verify_app_attest(
        cbor2.dumps({"fmt": "other"}), key_id=key_id, challenge=CHALLENGE, cfg=cfg
    )


def test_no_verifier_means_not_available_and_never_false_or_true(tmp_path: Path) -> None:
    att, key_id, cfg = build()
    report = {
        "attestation": base64.b64encode(att).decode(),
        "key_id": base64.b64encode(key_id).decode(),
    }
    assert (
        da.verify("ANDROID_PLAY_INTEGRITY", report, challenge=CHALLENGE, cfg=cfg) is None
    )  # no verifier for it
    assert da.verify("IOS_APP_ATTEST", None, challenge=CHALLENGE, cfg=cfg) is None
    assert da.verify("IOS_APP_ATTEST", report, challenge=CHALLENGE, cfg=cfg) is True
    assert (
        da.verify("IOS_APP_ATTEST", {"attestation": "!!"}, challenge=CHALLENGE, cfg=cfg) is False
    )  # a report that cannot be read is a failure
    assert (
        da.load_config({}) is None and da.load_config({"ios": {"app_id": APP}}) is None
    )  # half-filled is not configured
    pem = tmp_path / "root.pem"
    pem.write_bytes(cfg.root_pem)
    got = da.load_config(
        {"ios": {"app_id": APP, "root_pem_path": str(pem), "environment": "production"}}
    )
    assert got is not None and got.app_id == APP
    assert da.configured() is None  # the shipped file configures nothing

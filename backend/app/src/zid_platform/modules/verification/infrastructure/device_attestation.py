"""Device attestation for capture envelopes (ZID-DTV-WF-03 E08; CR-110).

Supported: Apple App Attest, verified as Apple documents it. The attestation object (CBOR) carries a certificate chain, the authenticator data and a receipt; this checks that
1. the chain leads from the leaf to the Apple App Attestation root the operators recorded (`root_pem_path`), with every certificate in its validity window;
2. the leaf certificate's nonce extension (1.2.840.113635.100.8.2) equals SHA-256(authenticator data || SHA-256(challenge)), which ties the attestation to THIS challenge;
3. the key id given equals SHA-256 of the leaf's public key point, and is the credential id in the authenticator data;
4. the relying-party hash is SHA-256 of the configured app id (team id + bundle id), the counter is zero, and the AAGUID is the App Attest one for the configured environment.
The answer is True (all hold), False (something checked and failed) or None (not available: the platform has no verifier configured, or the report is for a platform with none). Never True without every check.

NOT supported, on purpose: Android Play Integrity. Its verdicts are decoded by Google's service with a service account that nobody has configured here; building a decoder with no credentials to test it
against would produce an unverified "pass" path. An Android capture therefore reports None (not available) and the market's `attestation_required` decides whether that blocks approval.
The configuration (`config/idv/device-attestation.yaml`) ships empty: nothing is attested until people record the app id and the root."""

from __future__ import annotations

import hashlib
import itertools
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cbor2
import yaml
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

NONCE_OID = x509.ObjectIdentifier("1.2.840.113635.100.8.2")
AAGUID = {"production": b"appattest" + b"\x00" * 7, "development": b"appattestdevelop"}
FILE = Path(
    os.environ.get("ZID_IDV_ATTESTATION")
    or Path(__file__).resolve().parents[6] / "config" / "idv" / "device-attestation.yaml"
)


@dataclass(frozen=True, slots=True)
class AppAttestConfig:
    app_id: str
    root_pem: bytes
    environment: str = "production"


def load_config(doc: dict[str, Any] | None, *, base: Path | None = None) -> AppAttestConfig | None:
    """None when iOS attestation is not configured. A half-filled entry is also None: never a partial check."""
    ios = ((doc or {}).get("ios") or {}) if isinstance(doc, dict) else {}
    app_id, path, env = (
        str(ios.get("app_id") or "").strip(),
        str(ios.get("root_pem_path") or "").strip(),
        str(ios.get("environment") or "production"),
    )
    if not app_id or not path or env not in AAGUID:
        return None
    p = Path(path) if Path(path).is_absolute() else (base or FILE.parent) / path
    try:
        return AppAttestConfig(app_id, p.read_bytes(), env)
    except OSError:
        return None


def configured() -> AppAttestConfig | None:
    try:
        doc = yaml.safe_load(FILE.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError):
        doc = {}
    return load_config(doc)


def _chain_ok(chain: list[x509.Certificate], root: x509.Certificate, now: datetime) -> bool:
    """Each certificate is signed by the next, the last by the root, all within their validity windows."""
    certs = [*chain, root]
    for c in certs:
        if not c.not_valid_before_utc <= now <= c.not_valid_after_utc:
            return False
    for child, parent in itertools.pairwise(certs):
        pub = parent.public_key()
        try:
            if (
                not isinstance(pub, ec.EllipticCurvePublicKey)
                or child.signature_hash_algorithm is None
            ):
                return False
            pub.verify(
                child.signature,
                child.tbs_certificate_bytes,
                ec.ECDSA(child.signature_hash_algorithm),
            )
        except InvalidSignature:
            return False
    return True


def verify_app_attest(
    attestation: bytes,
    *,
    key_id: bytes,
    challenge: bytes,
    cfg: AppAttestConfig,
    now: datetime | None = None,
) -> bool:
    when = now or datetime.now(UTC)
    try:
        obj = cbor2.loads(attestation)
        if obj.get("fmt") != "apple-appattest":
            return False
        x5c = obj["attStmt"]["x5c"]
        auth: bytes = obj["authData"]
        chain = [x509.load_der_x509_certificate(bytes(c)) for c in x5c]
        root = x509.load_pem_x509_certificate(cfg.root_pem)
    except (ValueError, KeyError, TypeError, cbor2.CBORDecodeError, AttributeError):
        return False
    if len(chain) < 1 or not _chain_ok(chain, root, when):
        return False
    leaf = chain[0]
    # 2: the nonce binds the attestation to this challenge
    nonce = hashlib.sha256(auth + hashlib.sha256(challenge).digest()).digest()
    try:
        ext = leaf.extensions.get_extension_for_oid(NONCE_OID).value
        raw = ext.value  # type: ignore[attr-defined]
    except (x509.ExtensionNotFound, AttributeError):
        return False
    if (
        not raw.endswith(nonce) or len(raw) != len(nonce) + 6
    ):  # SEQUENCE { [1] { OCTET STRING nonce } }: six bytes of DER framing
        return False
    # 3: the key id
    pub = leaf.public_key()
    if not isinstance(pub, ec.EllipticCurvePublicKey):
        return False
    point = pub.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    if hashlib.sha256(point).digest() != key_id:
        return False
    # 4: the authenticator data
    if len(auth) < 55:
        return False
    rp_hash, counter, aaguid = auth[:32], int.from_bytes(auth[33:37], "big"), auth[37:53]
    cred_len = int.from_bytes(auth[53:55], "big")
    cred_id = auth[55 : 55 + cred_len]
    return (
        rp_hash == hashlib.sha256(cfg.app_id.encode()).digest()
        and counter == 0
        and aaguid == AAGUID[cfg.environment]
        and cred_id == key_id
    )


def verify(
    platform: str | None,
    report: dict[str, Any] | None,
    *,
    challenge: bytes,
    cfg: AppAttestConfig | None = None,
    now: datetime | None = None,
) -> bool | None:
    """True, False or None (not available). A report for a platform with no verifier is None, never False: nothing was checked."""
    if platform != "IOS_APP_ATTEST" or report is None:
        return None
    cfg = cfg or configured()
    if cfg is None:
        return None
    try:
        import base64

        return verify_app_attest(
            base64.b64decode(report["attestation"], validate=True),
            key_id=base64.b64decode(report["key_id"], validate=True),
            challenge=challenge,
            cfg=cfg,
            now=now,
        )
    except (KeyError, ValueError, TypeError):
        return False

"""Device attestation for capture envelopes (ZID-DTV-WF-03 E08; CR-110).

Supported: Apple App Attest, verified as Apple documents it. The attestation object (CBOR) carries a certificate chain, the authenticator data and a receipt; this checks that
1. the chain leads from the leaf to the Apple App Attestation root the operators recorded (`root_pem_path`), with every certificate in its validity window;
2. the leaf certificate's nonce extension (1.2.840.113635.100.8.2) equals SHA-256(authenticator data || SHA-256(challenge)), which ties the attestation to THIS challenge;
3. the key id given equals SHA-256 of the leaf's public key point, and is the credential id in the authenticator data;
4. the relying-party hash is SHA-256 of the configured app id (team id + bundle id), the counter is zero, and the AAGUID is the App Attest one for the configured environment.
The answer is True (all hold), False (something checked and failed) or None (not available: the platform has no verifier configured, or the report is for a platform with none). Never True without every check.

Android Play Integrity (standard requests), verified as Google documents it: the integrity token is decoded by Google's `decodeIntegrityToken` with the operators' service account, and the decoded
verdict must name the configured package, carry the request hash base64url(SHA-256(challenge)) the app was asked to bind, be recognized by Play with one of the configured signing-certificate digests,
meet the configured device-integrity verdicts, and be fresh. Google unreachable or not configured is None (not available), never a pass (WF-01 Q18).
The configuration (`config/idv/device-attestation.yaml`) ships empty: nothing is attested until people record the app ids, the root, the certificate digests and the service account."""

from __future__ import annotations

import base64
import hashlib
import hmac
import itertools
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import cbor2
import httpx
import yaml
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from joserfc import jwt
from joserfc.jwk import RSAKey

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


# ---------------------------------------------------------------------------------------------------------------------- Android Play Integrity

PLAY_SCOPE = "https://www.googleapis.com/auth/playintegrity"
PLAY_DECODE = "https://playintegrity.googleapis.com/v1/{package}:decodeIntegrityToken"


@dataclass(frozen=True, slots=True)
class PlayIntegrityConfig:
    package_name: str
    certificate_digests: frozenset[str]  # base64url SHA-256 of the app signing certificate(s), as Play reports them
    client_email: str
    private_key_pem: str
    token_uri: str = "https://oauth2.googleapis.com/token"
    device_verdicts: frozenset[str] = frozenset({"MEETS_DEVICE_INTEGRITY"})  # at least one must be present
    max_age_seconds: int = 600


def load_play_config(doc: dict[str, Any] | None, *, base: Path | None = None) -> PlayIntegrityConfig | None:
    """None unless every value is recorded and the service-account key file reads: never a partial check."""
    a = ((doc or {}).get("android") or {}) if isinstance(doc, dict) else {}
    package, digests, key_path = str(a.get("package_name") or "").strip(), [str(d).strip() for d in a.get("certificate_sha256") or [] if str(d).strip()], str(a.get("service_account_path") or "").strip()
    if not package or not digests or not key_path:
        return None
    p = Path(key_path) if Path(key_path).is_absolute() else (base or FILE.parent) / key_path
    try:
        sa = json.loads(p.read_text(encoding="utf-8"))
        email, key = str(sa["client_email"]), str(sa["private_key"])
    except (OSError, ValueError, KeyError):
        return None
    verdicts = frozenset(str(v) for v in a.get("device_verdicts") or ["MEETS_DEVICE_INTEGRITY"])
    return PlayIntegrityConfig(package, frozenset(digests), email, key, str(sa.get("token_uri") or "https://oauth2.googleapis.com/token"), verdicts, int(a.get("max_age_seconds") or 600))


def configured_play() -> PlayIntegrityConfig | None:
    try:
        doc = yaml.safe_load(FILE.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError):
        doc = {}
    return load_play_config(doc)


def request_hash(challenge: bytes) -> str:
    """What the app passes as `requestHash` when it asks Play for a token for this capture."""
    return base64.urlsafe_b64encode(hashlib.sha256(challenge).digest()).rstrip(b"=").decode()


def check_play_verdict(payload: dict[str, Any], *, cfg: PlayIntegrityConfig, challenge: bytes, now: datetime | None = None) -> bool:
    """The decoded verdict, checked in full. Anything missing or different is False."""
    when = now or datetime.now(UTC)
    try:
        req, app, device = payload["requestDetails"], payload["appIntegrity"], payload["deviceIntegrity"]
        issued = datetime.fromtimestamp(int(req["timestampMillis"]) / 1000, UTC)
        return (
            req.get("requestPackageName") == cfg.package_name
            and hmac.compare_digest(str(req.get("requestHash") or ""), request_hash(challenge))
            and timedelta(seconds=-120) <= when - issued <= timedelta(seconds=cfg.max_age_seconds)
            and app.get("appRecognitionVerdict") == "PLAY_RECOGNIZED"
            and app.get("packageName") == cfg.package_name
            and bool(set(app.get("certificateSha256Digest") or []) & cfg.certificate_digests)
            and bool(set(device.get("deviceRecognitionVerdict") or []) & cfg.device_verdicts)
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


_access: dict[str, tuple[str, float]] = {}


async def _access_token(cfg: PlayIntegrityConfig, client: httpx.AsyncClient) -> str:
    """An OAuth access token for the service account (JWT bearer grant, RFC 7523), kept until a minute before it expires."""
    held = _access.get(cfg.client_email)
    if held and held[1] > time.time() + 60:
        return held[0]
    now = int(time.time())
    assertion = jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {"iss": cfg.client_email, "scope": PLAY_SCOPE, "aud": cfg.token_uri, "iat": now, "exp": now + 3600},
        RSAKey.import_key(cfg.private_key_pem),
    )
    got = await client.post(cfg.token_uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    got.raise_for_status()
    body = got.json()
    _access[cfg.client_email] = (str(body["access_token"]), now + int(body.get("expires_in", 3600)))
    return str(body["access_token"])


async def verify_play_integrity(
    report: dict[str, Any] | None, *, challenge: bytes, cfg: PlayIntegrityConfig | None = None, transport: httpx.AsyncBaseTransport | None = None,
    now: datetime | None = None,
) -> bool | None:  # fmt: skip
    """True, False or None. None when not configured or Google cannot be asked: nothing was checked, and that is never a pass."""
    cfg = cfg or configured_play()
    if cfg is None or report is None:
        return None
    token = str(report.get("integrity_token") or "")
    if not token:
        return False
    try:
        async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
            access = await _access_token(cfg, client)
            got = await client.post(
                PLAY_DECODE.format(package=cfg.package_name), json={"integrity_token": token}, headers={"Authorization": f"Bearer {access}"}
            )
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    if got.status_code == 400:
        return False  # Google refused the token itself: malformed, for another app, or forged
    if got.status_code != 200:
        return None
    try:
        payload = got.json()["tokenPayloadExternal"]
    except (ValueError, KeyError):
        return False
    return check_play_verdict(payload, cfg=cfg, challenge=challenge, now=now)

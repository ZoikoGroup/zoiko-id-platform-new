"""Software credential issuers and holders for tests: real SD-JWT VC and real ISO mdoc structures.

SD-JWT VC: issuer-signed JWT with `_sd` digests of salted disclosures, `cnf.jwk` holder binding,
Token Status List reference; the holder discloses a subset and adds a KB-JWT over sd_hash.
mdoc: IACA root -> document signer certificate -> COSE_Sign1 MobileSecurityObject with value
digests; the device signs DeviceAuthentication over the OID4VP session transcript.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any

import cbor2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID
from zid_crypto import LocalSigner, sign_jws


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SdJwtIssuer:
    def __init__(self, iss: str, signer: LocalSigner | None = None) -> None:
        self.iss = iss
        self.signer = signer or LocalSigner(f"{iss}-key")
        self.statuses = bytearray(1024)  # 2 bits per entry

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.signer.public_jwk()]}

    def issue(
        self,
        credential_type: str,
        claims: dict[str, Any],
        holder: LocalSigner,
        *,
        idx: int = 0,
        **extra: Any,
    ) -> tuple[str, dict[str, str]]:
        disclosures: dict[str, str] = {}
        digests = []
        for name, value in claims.items():
            d = b64(json.dumps([b64(os.urandom(16)), name, value]).encode())
            disclosures[name] = d
            digests.append(b64(hashlib.sha256(d.encode()).digest()))
        now = int(time.time())
        payload = {
            "iss": self.iss,
            "vct": credential_type,
            "iat": now,
            "exp": now + 86400,
            "cnf": {"jwk": holder.public_jwk()},
            "_sd": sorted(digests),
            "_sd_alg": "sha-256",
            "status": {"status_list": {"idx": idx, "uri": f"{self.iss}/status/1"}},
            **extra,
        }
        return sign_jws(self.signer, payload, typ="dc+sd-jwt"), disclosures

    def status_token(self) -> str:
        now = int(time.time())
        return sign_jws(
            self.signer,
            {
                "sub": f"{self.iss}/status/1",
                "iat": now,
                "exp": now + 3600,
                "status_list": {"bits": 2, "lst": b64(zlib.compress(bytes(self.statuses)))},
            },
            typ="statuslist+jwt",
        )

    def set(self, idx: int, value: int) -> None:
        byte, slot = divmod(idx, 4)
        self.statuses[byte] = (self.statuses[byte] & ~(3 << (slot * 2))) | (value << (slot * 2))


def present(
    issuer_jwt: str,
    disclosures: list[str],
    holder: LocalSigner,
    *,
    aud: str,
    nonce: str,
    iat: int | None = None,
) -> str:
    body = issuer_jwt + "~" + "".join(d + "~" for d in disclosures)
    kb = sign_jws(
        holder,
        {
            "aud": aud,
            "nonce": nonce,
            "iat": iat or int(time.time()),
            "sd_hash": b64(hashlib.sha256(body.encode()).digest()),
        },
        typ="kb+jwt",
    )
    return body + kb


def _cert(
    subject: str,
    key: ec.EllipticCurvePrivateKey,
    issuer: x509.Name | None = None,
    signing: ec.EllipticCurvePrivateKey | None = None,
    ca: bool = False,
) -> x509.Certificate:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer or name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
    )
    if ca:
        builder = builder.add_extension(
            x509.BasicConstraints(ca=True, path_length=0), critical=True
        )
    return builder.sign(signing or key, hashes.SHA256())


def _raw_sig(key: ec.EllipticCurvePrivateKey, data: bytes) -> bytes:
    r, s = decode_dss_signature(key.sign(data, ec.ECDSA(hashes.SHA256())))
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


class Mdoc:
    def __init__(self, doc_type: str = "org.iso.18013.5.1.mDL") -> None:
        self.doc_type, self.ns = doc_type, "org.iso.18013.5.1"
        self.iaca_key = ec.generate_private_key(ec.SECP256R1())
        self.iaca = _cert("Test IACA", self.iaca_key, ca=True)
        self.ds_key = ec.generate_private_key(ec.SECP256R1())
        self.ds = _cert("Test DS", self.ds_key, issuer=self.iaca.subject, signing=self.iaca_key)
        self.device_key = ec.generate_private_key(ec.SECP256R1())

    def iaca_pem(self) -> str:
        return self.iaca.public_bytes(serialization.Encoding.PEM).decode()

    def device_response(
        self,
        elements: dict[str, Any],
        *,
        client_id: str,
        nonce: str,
        response_uri: str,
        tamper: str | None = None,
    ) -> str:
        items, digests = [], {}
        for i, (name, value) in enumerate(elements.items()):
            item = cbor2.dumps(
                {
                    "digestID": i,
                    "random": os.urandom(16),
                    "elementIdentifier": name,
                    "elementValue": value,
                }
            )
            digests[i] = hashlib.sha256(cbor2.dumps(cbor2.CBORTag(24, item))).digest()
            if tamper == name:
                item = cbor2.dumps(
                    {
                        "digestID": i,
                        "random": os.urandom(16),
                        "elementIdentifier": name,
                        "elementValue": "tampered",
                    }
                )
            items.append(cbor2.CBORTag(24, item))
        n = self.device_key.public_key().public_numbers()
        now = datetime.now(UTC)
        mso = {
            "version": "1.0",
            "digestAlgorithm": "SHA-256",
            "docType": self.doc_type,
            "valueDigests": {self.ns: digests},
            "deviceKeyInfo": {
                "deviceKey": {1: 2, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
            },
            "validityInfo": {
                "signed": cbor2.CBORTag(0, now.isoformat()),
                "validFrom": cbor2.CBORTag(0, (now - timedelta(days=1)).isoformat()),
                "validUntil": cbor2.CBORTag(0, (now + timedelta(days=30)).isoformat()),
            },
        }
        protected = cbor2.dumps({1: -7})
        payload = cbor2.dumps(cbor2.CBORTag(24, cbor2.dumps(mso)))
        sig = _raw_sig(self.ds_key, cbor2.dumps(["Signature1", protected, b"", payload]))
        issuer_auth = [
            protected,
            {33: self.ds.public_bytes(serialization.Encoding.DER)},
            payload,
            sig,
        ]
        info = [client_id, nonce, None, response_uri]
        transcript = [None, None, ["OpenID4VPHandover", hashlib.sha256(cbor2.dumps(info)).digest()]]
        ns_bytes = cbor2.CBORTag(24, cbor2.dumps({}))
        device_auth = cbor2.dumps(
            cbor2.CBORTag(
                24, cbor2.dumps(["DeviceAuthentication", transcript, self.doc_type, ns_bytes])
            )
        )
        d_protected = cbor2.dumps({1: -7})
        d_sig = _raw_sig(
            self.device_key, cbor2.dumps(["Signature1", d_protected, b"", device_auth])
        )
        response = {
            "version": "1.0",
            "status": 0,
            "documents": [
                {
                    "docType": self.doc_type,
                    "issuerSigned": {"nameSpaces": {self.ns: items}, "issuerAuth": issuer_auth},
                    "deviceSigned": {
                        "nameSpaces": ns_bytes,
                        "deviceAuth": {"deviceSignature": [d_protected, {}, None, d_sig]},
                    },
                }
            ],
        }
        return b64(cbor2.dumps(response))

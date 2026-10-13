"""A software WebAuthn authenticator for tests: real CBOR attestation, real COSE keys, real ECDSA.

Produces the JSON a browser hands to the RP (PublicKeyCredential.toJSON()), so the server's
verification runs unmodified. `fmt=none` attestation, ES256 (P-256) keys.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

UP, UV, BE, BS, AT = 0x01, 0x04, 0x08, 0x10, 0x40


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftAuthenticator:
    def __init__(
        self,
        rp_id: str,
        origin: str,
        *,
        user_verified: bool = True,
        synced: bool = True,
        counter_step: int = 1,
    ) -> None:
        self.rp_id, self.origin = rp_id, origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.user_verified, self.synced, self.counter_step = user_verified, synced, counter_step
        self.sign_count = 0
        self.user_handle: bytes | None = None

    def _flags(self, extra: int = 0) -> bytes:
        f = UP | (UV if self.user_verified else 0) | ((BE | BS) if self.synced else 0) | extra
        return bytes([f])

    def _cose(self) -> bytes:
        n = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
        )

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": origin or self.origin,
                "crossOrigin": False,
            }
        ).encode()

    def create(self, options: dict[str, Any], *, origin: str | None = None) -> dict[str, Any]:
        self.user_handle = unb64(options["user"]["id"])
        rp_hash = hashlib.sha256(options["rp"]["id"].encode()).digest()
        attested = (
            bytes(16)
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose()
        )
        auth_data = rp_hash + self._flags(AT) + self.sign_count.to_bytes(4, "big") + attested
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64(client_data),
                "attestationObject": b64(att),
                "transports": ["internal", "hybrid"],
            },
            "clientExtensionResults": {},
        }

    def get(
        self, options: dict[str, Any], *, origin: str | None = None, rp_id: str | None = None
    ) -> dict[str, Any]:
        self.sign_count += self.counter_step
        rp_hash = hashlib.sha256((rp_id or options.get("rpId") or self.rp_id).encode()).digest()
        auth_data = rp_hash + self._flags() + self.sign_count.to_bytes(4, "big")
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64(client_data),
                "authenticatorData": b64(auth_data),
                "signature": b64(signature),
                "userHandle": b64(self.user_handle or b""),
            },
            "clientExtensionResults": {},
        }

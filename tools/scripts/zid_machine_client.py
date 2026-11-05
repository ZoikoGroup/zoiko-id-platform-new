"""Sign in to Zoiko iD as a machine client (OAuth client credentials with a signed key) and keep a usable access token.

Shared by the tools that call the cell as an application: the proof of who it is is a short-lived assertion signed with the client's PRIVATE key (a JWK
file, as the "Add your application" form offered to save). The key is read here, used to sign, and never sent anywhere or printed."""

from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
RENEW_BEFORE_S = 30


class MachineClientError(Exception):
    """Could not sign in: `code` says why (never the key, never a token)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def signed_assertion(client_id: str, audience: str, key_file: Path) -> str:
    jwk = json.loads(key_file.read_text(encoding="utf-8"))
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256" or "d" not in jwk or "kid" not in jwk:
        raise MachineClientError("KEY_FILE_INVALID", "expected the private EC P-256 key (a JWK with kid and d)")
    key = ec.derive_private_key(int.from_bytes(_unb64(jwk["d"]), "big"), ec.SECP256R1())
    now = int(time.time())
    head = {"alg": "ES256", "typ": "JWT", "kid": jwk["kid"]}
    claims = {"iss": client_id, "sub": client_id, "aud": audience, "iat": now, "exp": now + 60, "jti": uuid.uuid4().hex}
    signing_input = f"{_b64(json.dumps(head, separators=(',', ':')).encode())}.{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    return f"{signing_input}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


class MachineClient:
    """An access token that renews itself shortly before it expires."""

    def __init__(self, api_base: str, client_id: str, key_file: Path, scope: str, http: httpx.Client | None = None) -> None:
        self.base, self.client_id, self.key_file, self.scope = api_base.rstrip("/"), client_id, key_file, scope
        self.http = http or httpx.Client(timeout=30)
        self._token: str | None = None
        self._until = 0.0

    def renew(self) -> None:
        url = f"{self.base}/oauth2/token"
        res = self.http.post(
            url,
            data={
                "grant_type": "client_credentials",
                "scope": self.scope,
                "client_id": self.client_id,
                "client_assertion_type": ASSERTION_TYPE,
                "client_assertion": signed_assertion(self.client_id, url, self.key_file),
            },
        )
        if res.status_code != 200:
            raise MachineClientError("TOKEN_REFUSED", f"HTTP {res.status_code} {res.text[:200]}")
        body = res.json()
        self._token, self._until = body["access_token"], time.time() + float(body.get("expires_in", 300))

    def headers(self) -> dict[str, str]:
        if self._token is None or time.time() > self._until - RENEW_BEFORE_S:
            self.renew()
        return {"authorization": f"Bearer {self._token}"}

    def forget(self) -> None:
        self._token = None

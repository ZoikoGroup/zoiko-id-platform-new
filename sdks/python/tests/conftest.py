from __future__ import annotations

import base64
import json
import sys
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

AUD, TENANT = "tool-server-principal", "tenant-1"
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Keys:
    def __init__(self, kid: str = "k1") -> None:
        self.kid = kid
        self.key = ec.generate_private_key(ec.SECP256R1())

    def jwk(self) -> dict[str, Any]:
        n = self.key.public_key().public_numbers()
        return {
            "kty": "EC", "crv": "P-256", "kid": self.kid, "alg": "ES256", "use": "sig",
            "x": b64(n.x.to_bytes(32, "big")), "y": b64(n.y.to_bytes(32, "big")),
        }  # fmt: skip

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.jwk()]}

    def sign(self, claims: dict[str, Any], *, typ: str = "action-grant+jwt", alg: str = "ES256",
             kid: str | None = None) -> str:  # fmt: skip
        header = {"alg": alg, "kid": kid or self.kid, "typ": typ}
        signing = b64(json.dumps(header).encode()) + "." + b64(json.dumps(claims).encode())
        if alg == "none":
            return signing + "."
        r, s = decode_dss_signature(self.key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
        return signing + "." + b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def claims_for(digest: str, **over: Any) -> dict[str, Any]:
    t = int(NOW.timestamp())
    base: dict[str, Any] = {
        "iss": "zid", "aud": AUD, "sub": "agent-1", "tenant_id": TENANT, "jti": "nonce-1",
        "iat": t, "nbf": t, "exp": t + 120, "tool_id": "invoice.send", "action": "invoice:send",
        "resource": "invoice/42", "args_sha256": digest, "agent_chain": ["agent-1"],
        "chain_commitment": "sha256:" + "0" * 64, "decision_id": "dec-1", "on_behalf_of": "person-1",
    }  # fmt: skip
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


@pytest.fixture
def keys() -> Keys:
    return Keys()


@pytest.fixture
def stub() -> Iterator[Callable[[int, dict[str, Any]], str]]:
    """Start a stub platform that answers redeem with a fixed status/body; returns a configure function -> base_url."""
    state: dict[str, Any] = {"status": 200, "body": {}, "seen": []}

    class H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            state["seen"].append((self.path, self.headers.get("Authorization"), json.loads(raw)))
            data = json.dumps(state["body"]).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def configure(status: int, body: dict[str, Any]) -> str:
        state["status"], state["body"] = status, body
        return f"http://127.0.0.1:{server.server_port}"

    configure.state = state  # type: ignore[attr-defined]
    yield configure
    server.shutdown()
    server.server_close()

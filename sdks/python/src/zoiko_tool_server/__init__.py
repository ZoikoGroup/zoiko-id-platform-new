"""Accept a Zoiko iD action grant in a tool server: verify offline, redeem once, then act.

`verify_grant` alone does not make a grant single-use. Only `redeem` (the platform spends the nonce) does, which is
why `ToolServer.accept` always does both and raises `GrantError` unless both succeed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

__all__ = [
    "AUTH_SCHEME",
    "GRANT_TYP",
    "GrantError",
    "ToolServer",
    "args_digest",
    "fetch_jwks",
    "redeem",
    "verify_grant",
    "verify_signed",
]

GRANT_TYP = "action-grant+jwt"
ALLOWED_ALGS = ("ES256", "Ed25519")
AUTH_SCHEME = "ActionGrant"  # Authorization: ActionGrant <jwt>
REDEEM_PATH = "/v1/machine/tool-grants:redeem"


class GrantError(Exception):
    """A grant was refused. `.code` is stable (TOOL_GRANT_*); `.codes` lists every reason when there are several."""

    def __init__(self, code: str, detail: str = "", codes: Sequence[str] | None = None) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.codes = list(codes) if codes else [code]


def args_digest(arguments: Mapping[str, Any]) -> str:
    """Canonical JSON (sorted keys, no spaces, UTF-8), SHA-256, as 'sha256:<hex>'. Same as the platform's."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _invalid(detail: str) -> GrantError:
    return GrantError("TOOL_GRANT_INVALID", detail)


def _check_signature(jwk: Mapping[str, Any], alg: str, data: bytes, sig: bytes) -> None:
    if any(k in jwk for k in ("d", "p", "q", "k")):
        raise _invalid("key carries private members")
    if jwk.get("alg") not in (None, alg):
        raise _invalid("key algorithm mismatch")
    try:
        if alg == "ES256":
            if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256" or len(sig) != 64:
                raise _invalid("key does not fit ES256")
            numbers = ec.EllipticCurvePublicNumbers(
                int.from_bytes(_b64d(jwk["x"]), "big"),
                int.from_bytes(_b64d(jwk["y"]), "big"),
                ec.SECP256R1(),
            )
            numbers.public_key().verify(
                encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                data,
                ec.ECDSA(hashes.SHA256()),
            )
        else:
            if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
                raise _invalid("key does not fit Ed25519")
            ed25519.Ed25519PublicKey.from_public_bytes(_b64d(jwk["x"])).verify(sig, data)
    except InvalidSignature as e:
        raise _invalid("signature does not verify") from e
    except (KeyError, ValueError, TypeError, binascii.Error) as e:
        raise _invalid("unusable key") from e


def _decode(token: str, jwks: Mapping[str, Any], typ: str = GRANT_TYP) -> dict[str, Any]:
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        raise _invalid("not a compact JWS")
    try:
        header = json.loads(_b64d(parts[0]))
        payload = json.loads(_b64d(parts[1]))
        sig = _b64d(parts[2])
    except (ValueError, binascii.Error) as e:
        raise _invalid("malformed token") from e
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise _invalid("malformed token")
    alg = header.get("alg")
    if alg not in ALLOWED_ALGS:
        raise _invalid(f"algorithm not allowed: {alg}")
    if header.get("typ") != typ:
        raise _invalid("wrong token type")
    if "jku" in header or "x5u" in header or "x5c" in header or "jwk" in header:
        raise _invalid("remote or embedded keys are never followed")
    kid = header.get("kid")
    keys = jwks.get("keys") if isinstance(jwks, Mapping) else None
    matches = (
        [k for k in keys if isinstance(k, Mapping) and k.get("kid") == kid] if isinstance(keys, list) and kid else []
    )
    if len(matches) != 1:
        raise _invalid("unknown key id")
    _check_signature(matches[0], str(alg), f"{parts[0]}.{parts[1]}".encode("ascii"), sig)
    return payload


def verify_signed(token: str, jwks: Mapping[str, Any], *, typ: str) -> dict[str, Any]:
    """Verify any compact JWS offline against pinned keys, for one named `typ` (ES256 or Ed25519 only, no remote keys).
    Raises `GrantError`. Used by other Zoiko SDKs that receive signed decisions; it checks the signature, not the claims."""
    return _decode(token, jwks, typ)


def verify_grant(
    token: str,
    jwks: Mapping[str, Any],
    *,
    audience: str,
    tenant_id: str,
    arguments_digest: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify the signature offline, then apply the platform's grant checks. Returns the claims or raises GrantError.

    `audience` is this tool server's own principal id; `arguments_digest` is the digest of the arguments it will run."""
    claims = _decode(token, jwks)
    stamp = int((now or datetime.now(UTC)).timestamp())
    problems: list[str] = []
    if claims.get("aud") != audience:
        problems.append("TOOL_GRANT_AUDIENCE")
    if claims.get("tenant_id") != tenant_id:
        problems.append("TOOL_GRANT_TENANT")
    if claims.get("args_sha256") != arguments_digest:
        problems.append("TOOL_GRANT_ARGUMENTS")
    exp, nbf = claims.get("exp"), claims.get("nbf")
    if not isinstance(exp, int) or isinstance(exp, bool) or exp <= stamp:
        problems.append("TOOL_GRANT_EXPIRED")
    if not isinstance(nbf, int) or isinstance(nbf, bool) or nbf > stamp:
        problems.append("TOOL_GRANT_NOT_YET_VALID")
    if not claims.get("jti") or not isinstance(claims.get("agent_chain"), list):
        problems.append("TOOL_GRANT_MALFORMED")
    if problems:
        raise GrantError(problems[0], codes=problems)
    return claims


def redeem(base_url: str, access_token: str, grant: str, arguments_digest: str, timeout: float = 10) -> dict[str, Any]:
    """Spend the grant at the platform (the single-use authority). 200 -> call details; 409 -> TOOL_GRANT_USED;
    anything else -> GrantError with the server's code. A platform that cannot be reached is a refusal too."""
    request = urllib.request.Request(
        base_url.rstrip("/") + REDEEM_PATH,
        data=json.dumps({"grant": grant, "arguments_digest": arguments_digest}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read())
        if not isinstance(body, dict):
            raise GrantError("TOOL_GRANT_REDEEM_FAILED", "unexpected response")
        return body
    except urllib.error.HTTPError as e:
        code = "TOOL_GRANT_REFUSED"
        try:
            code = str(json.loads(e.read()).get("error", {}).get("code") or code)
        except (ValueError, AttributeError):
            pass
        if e.code == 409:
            code = "TOOL_GRANT_USED"
        raise GrantError(code, f"HTTP {e.code}", codes=[code]) from e
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        raise GrantError("TOOL_GRANT_REDEEM_FAILED", "platform unreachable or unreadable") from e


def fetch_jwks(url: str, timeout: float = 10) -> dict[str, Any]:
    """Fetch a JWKS once. No caching or rotation: call it again (for example on an unknown kid) as you see fit."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        jwks = json.loads(response.read())
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        raise TypeError("not a JWKS")
    return jwks


class ToolServer:
    """One tool server's grant acceptance: verify offline, then redeem. Run the tool only if `accept` returns."""

    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        jwks: Mapping[str, Any],
        audience: str,
        tenant_id: str,
        timeout: float = 10,
    ) -> None:
        self.base_url, self.access_token, self.jwks = base_url, access_token, jwks
        self.audience, self.tenant_id, self.timeout = audience, tenant_id, timeout

    def accept(self, grant_header: str | None, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """`grant_header` is the Authorization value, `ActionGrant <jwt>`. `arguments` is what will be executed."""
        scheme, _, token = (grant_header or "").partition(" ")
        if scheme != AUTH_SCHEME or not token.strip():
            raise GrantError("TOOL_GRANT_MISSING", f"expected '{AUTH_SCHEME} <grant>'")
        token = token.strip()
        digest = args_digest(arguments)
        claims = verify_grant(
            token, self.jwks, audience=self.audience, tenant_id=self.tenant_id, arguments_digest=digest
        )
        redeem(self.base_url, self.access_token, token, digest, self.timeout)
        return claims

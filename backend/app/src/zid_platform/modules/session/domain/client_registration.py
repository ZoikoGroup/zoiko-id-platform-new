"""Registering an OAuth client from the admin console (ZID-AUTH-001 §6.1, §7; DEC: no client secrets). Pure domain.

A company registers the applications that sign people in or call the API on its behalf. There is no client secret to
copy and lose: a browser or mobile app is a PUBLIC client (authorization code with PKCE), and a server-side app is a
CONFIDENTIAL client that proves itself with a key pair it keeps, so only its public key is registered here. A private
key pasted into the form is refused, never stored. Self-service clients can only ask for a narrow set of scopes; the
platform's own administrative scopes are not available to them.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

SELF_SERVICE_SCOPES = frozenset(
    {"openid", "verification:read", "verification:execute", "presentation:request"}
)
CLIENT_TYPES = frozenset({"PUBLIC", "CONFIDENTIAL"})
PRIVATE_JWK_MEMBERS = frozenset({"d", "p", "q", "dp", "dq", "qi", "k", "oth"})
_LOOPBACK = (
    "127.0.0.1",
    "::1",
)  # the platform stores exactly these; "localhost" is not a loopback literal
MAX_REDIRECTS = 10


class ClientRegistrationError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def new_client_id(name: str) -> str:
    """A readable id the developer can recognise (`acme-web-3f9a21c4`); the random tail keeps ids unguessable."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "app"
    return f"{slug}-{secrets.token_hex(4)}"


def check_redirect(uri: str) -> str:
    """Exact https URLs; plain http only for a loopback literal (a native app on the same machine, RFC 8252 §7.3)."""
    parts = urlsplit(uri)
    if "*" in uri or parts.fragment or not parts.netloc:
        raise ClientRegistrationError(
            "REDIRECT_URI_INVALID",
            f"{uri}: must be one exact absolute URL, with no wildcard and no # fragment",
        )
    if parts.scheme == "https" or (parts.scheme == "http" and (parts.hostname or "") in _LOOPBACK):
        return uri
    raise ClientRegistrationError(
        "REDIRECT_URI_INVALID", f"{uri}: use https (plain http only for 127.0.0.1 or [::1])"
    )


def check_public_jwks(jwks: Mapping[str, Any] | None) -> dict[str, Any]:
    keys = (jwks or {}).get("keys")
    if not isinstance(keys, list) or not keys or len(keys) > 5:
        raise ClientRegistrationError(
            "JWKS_INVALID", 'paste a JWK Set: {"keys": [ ... ]} with 1 to 5 public keys'
        )
    for k in keys:
        if not isinstance(k, Mapping) or k.get("kty") not in ("RSA", "EC", "OKP"):
            raise ClientRegistrationError("JWKS_INVALID", "each key needs kty RSA, EC or OKP")
        if PRIVATE_JWK_MEMBERS & set(k):
            raise ClientRegistrationError(
                "JWKS_PRIVATE_KEY",
                "this contains a private key; register the public key only and keep the private key",
            )
        if not k.get("kid"):
            raise ClientRegistrationError(
                "JWKS_INVALID", "each key needs a kid, so signatures can name which key they used"
            )
    return {"keys": [dict(k) for k in keys]}


def check_scopes(scopes: Sequence[str]) -> frozenset[str]:
    asked = frozenset(scopes)
    over = asked - SELF_SERVICE_SCOPES
    if over:
        raise ClientRegistrationError(
            "SCOPE_NOT_AVAILABLE", f"not available to self-service clients: {min(over)}"
        )
    return asked or frozenset({"openid"})


def check_registration(
    client_type: str,
    redirect_uris: Sequence[str],
    jwks: Mapping[str, Any] | None,
    scopes: Sequence[str],
) -> tuple[tuple[str, ...], dict[str, Any] | None, frozenset[str], str]:
    """(redirect URIs, public JWKS or None, scopes, token auth method), or why this registration is refused."""
    if client_type not in CLIENT_TYPES:
        raise ClientRegistrationError(
            "CLIENT_TYPE_INVALID", "choose PUBLIC (browser or mobile) or CONFIDENTIAL (server)"
        )
    if not redirect_uris or len(redirect_uris) > MAX_REDIRECTS:
        raise ClientRegistrationError(
            "REDIRECT_URI_INVALID", f"give 1 to {MAX_REDIRECTS} redirect URLs"
        )
    redirects = tuple(dict.fromkeys(check_redirect(u) for u in redirect_uris))
    if client_type == "PUBLIC":
        if jwks:
            raise ClientRegistrationError(
                "JWKS_NOT_ALLOWED", "a public client has no keys; it uses PKCE"
            )
        return redirects, None, check_scopes(scopes), "none"
    return redirects, check_public_jwks(jwks), check_scopes(scopes), "private_key_jwt"

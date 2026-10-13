"""OAuth client authentication (ZID-AUTH-001 §7; RFC 7523 private_key_jwt, RFC 8705 tls_client_auth).

- private_key_jwt: the assertion is signed with a key the client registered (allowlisted algs
  only), names the client as `iss` and `sub`, is addressed to this issuer, is short-lived and its
  `jti` is accepted once (stored like a DPoP jti, under a `client_assertion:` key space).
- tls_client_auth: the TLS terminator validated the chain and forwards the leaf certificate; the
  subject DN must equal the registration. The certificate thumbprint binds issued tokens.
- `none` only for public clients, and only with no assertion presented.
The client certificate is read only from the header the deployment configures; the edge must strip
that header from inbound requests (documented in the Phase E settings).
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

from cryptography import x509
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import CryptoError, peek_header, verify_jws

from zid_platform.modules.session.domain.oauth import (
    CLIENT_ASSERTION_MAX_LIFETIME,
    CLIENT_ASSERTION_TYPE,
    CLIENT_ASSERTION_TYPS,
    OAuthClient,
    OAuthError,
)
from zid_platform.modules.session.domain.tokens import LEEWAY
from zid_platform.modules.session.infrastructure import session_store


@dataclass(frozen=True, slots=True)
class ClientAuthenticated:
    method: str
    cert_thumbprint: str | None = None  # x5t#S256 of the mTLS client certificate, when presented


def parse_client_cert(header_value: str | None) -> x509.Certificate | None:
    """URL-encoded PEM as forwarded by the TLS terminator (nginx `$ssl_client_escaped_cert`)."""
    if not header_value:
        return None
    try:
        return x509.load_pem_x509_certificate(unquote(header_value).encode("ascii"))
    except ValueError as e:
        raise OAuthError("invalid_client", "client certificate unreadable", status=401) from e


def cert_thumbprint(cert: x509.Certificate) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding

    digest = hashlib.sha256(cert.public_bytes(Encoding.DER)).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _invalid(detail: str) -> OAuthError:
    return OAuthError("invalid_client", detail, status=401)


async def authenticate_client(
    conn: AsyncConnection,
    client: OAuthClient,
    form: dict[str, str],
    *,
    issuer: str,
    token_endpoint: str,
    cert: x509.Certificate | None,
    now: datetime,
) -> ClientAuthenticated:
    method = client.token_auth_method
    has_assertion = "client_assertion" in form or "client_assertion_type" in form
    thumb = cert_thumbprint(cert) if cert is not None else None
    if method == "none":
        if client.client_type != "PUBLIC" or has_assertion:
            raise _invalid("client authentication does not match the registration")
        return ClientAuthenticated("none", thumb)
    if method == "tls_client_auth":
        if cert is None:
            raise _invalid("a client certificate is required")
        try:  # compare parsed names, so RDN order and spacing in the registration do not matter
            registered = x509.Name.from_rfc4514_string(client.tls_subject_dn or "")
        except ValueError as e:
            raise _invalid("registered subject DN unreadable") from e
        if cert.subject != registered:
            raise _invalid("client certificate subject does not match the registration")
        if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
            raise _invalid("client certificate outside its validity period")
        return ClientAuthenticated("tls_client_auth", thumb)
    if method == "private_key_jwt":
        await _verify_assertion(
            conn, client, form, issuer=issuer, token_endpoint=token_endpoint, now=now
        )
        return ClientAuthenticated("private_key_jwt", thumb)
    raise _invalid("client authentication method not supported")


async def _verify_assertion(
    conn: AsyncConnection,
    client: OAuthClient,
    form: dict[str, str],
    *,
    issuer: str,
    token_endpoint: str,
    now: datetime,
) -> None:
    if form.get("client_assertion_type") != CLIENT_ASSERTION_TYPE or not form.get(
        "client_assertion"
    ):
        raise _invalid("private_key_jwt assertion required")
    assertion = form["client_assertion"]
    keys: Any = (client.jwks or {}).get("keys")
    if not isinstance(keys, list) or not keys:
        raise _invalid("client has no registered keys")
    try:
        typ = peek_header(assertion).get("typ")
        if typ not in CLIENT_ASSERTION_TYPS:
            raise _invalid("client assertion typ not accepted")
        _, claims = verify_jws(assertion, keys=keys, typ=str(typ))
    except CryptoError as e:
        raise _invalid(f"client assertion invalid ({e.code})") from e
    if claims.get("iss") != client.client_id or claims.get("sub") != client.client_id:
        raise _invalid("client assertion iss/sub must be the client id")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if not set(auds) & {issuer, token_endpoint}:
        raise _invalid("client assertion not addressed to this issuer")
    exp, iat, jti = claims.get("exp"), claims.get("iat", claims.get("nbf")), claims.get("jti")
    if not isinstance(exp, int | float) or isinstance(exp, bool):
        raise _invalid("client assertion exp missing")
    expires = datetime.fromtimestamp(exp, UTC)
    if now >= expires + LEEWAY:
        raise _invalid("client assertion expired")
    issued = datetime.fromtimestamp(iat, UTC) if isinstance(iat, int | float) else None
    if issued is not None and issued > now + LEEWAY:
        raise _invalid("client assertion not yet valid")
    if expires - (issued or now) > CLIENT_ASSERTION_MAX_LIFETIME:
        raise _invalid("client assertion lifetime too long")
    if not isinstance(jti, str) or not 8 <= len(jti) <= 128:
        raise _invalid("client assertion jti missing")
    if not await session_store.record_dpop_proof(
        conn,
        tenant_id=client.tenant_id,
        jkt=f"client_assertion:{client.client_id}",
        jti=jti,
        issued_at=now,
        window=max(expires - now, timedelta(0)) + LEEWAY,
    ):
        raise _invalid("client assertion replayed")

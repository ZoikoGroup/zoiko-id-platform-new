"""OAuth 2.0 authorization server rules (ZID-AUTH-001 §6.1-6.2, §6.4, §8; RFC 6749, 7636, 8252, 9700).

Pure domain, no I/O. Key rules:
- only the authorization code grant (PKCE S256, always) and rotating refresh tokens; implicit and
  password grants do not exist here (§6.1, §28);
- redirect URIs match the registration exactly; the only latitude is the port of a loopback
  redirect for native apps (RFC 8252 §7.3), never path, query or host;
- requested scope and resource can only narrow what the client is registered for (§6.2);
- codes and refresh tokens are opaque random values; only their SHA-256 is ever stored (§8);
- confidential clients authenticate asymmetrically (private_key_jwt or tls_client_auth); client
  secrets are not supported (§7 "documented exception" not granted); FAPI 2.0 clients must use PAR,
  sender-constrained tokens and asymmetric authentication (§13);
- client_credentials never implies user delegation; token exchange only narrows (§6.1).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

AUTHORIZATION_CODE_TTL = timedelta(seconds=60)
ID_TOKEN_TTL = timedelta(minutes=5)
DPOP_NONCE_TTL = timedelta(minutes=5)
ID_TOKEN_TYP = "JWT"
GRANT_AUTHORIZATION_CODE = "authorization_code"
GRANT_REFRESH_TOKEN = "refresh_token"
GRANT_CLIENT_CREDENTIALS = "client_credentials"
# The scope of a build pipeline's machine client (CR-066): what its token is for, proposing a build. Only a client that holds it can use the signed-key
# exemption for `machine.build.register`; the integration module issues clients with it (its own copy, `PIPELINE_SCOPE`, is checked equal by a test).
BUILD_PIPELINE_SCOPE = "machine.build:register"
GRANT_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
TOKEN_TYPE_ACCESS = "urn:ietf:params:oauth:token-type:access_token"
SUPPORTED_GRANTS = frozenset(
    {GRANT_AUTHORIZATION_CODE, GRANT_REFRESH_TOKEN, GRANT_CLIENT_CREDENTIALS, GRANT_TOKEN_EXCHANGE}
)
SUPPORTED_CLIENT_AUTH = frozenset({"none", "private_key_jwt", "tls_client_auth"})
CONFIDENTIAL_AUTH = frozenset({"private_key_jwt", "tls_client_auth"})
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
CLIENT_ASSERTION_TYPS = frozenset({"JWT", "client-authentication+jwt"})
CLIENT_ASSERTION_MAX_LIFETIME = timedelta(minutes=5)
PAR_TTL = timedelta(seconds=60)
PAR_URI_PREFIX = "urn:ietf:params:oauth:request_uri:"
INTROSPECT_SCOPE = "zid:introspect"
_VERIFIER = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_STATE = re.compile(r"^[\x20-\x7e]{1,512}$")
_LOOPBACK = ("127.0.0.1", "::1")


class OAuthError(ValueError):
    """An RFC 6749 §5.2 / §4.1.2.1 error. `redirectable` is False until the redirect URI is trusted."""

    def __init__(
        self, error: str, description: str = "", *, status: int = 400, redirectable: bool = True
    ) -> None:
        super().__init__(f"{error}: {description}" if description else error)
        self.error = error
        self.description = description
        self.status = status
        self.redirectable = redirectable


@dataclass(frozen=True, slots=True)
class OAuthClient:
    client_id: str
    tenant_id: str
    client_type: str  # PUBLIC | CONFIDENTIAL | SERVICE
    profile: str  # STANDARD | FAPI2
    redirect_uris: tuple[str, ...]
    grant_types: frozenset[str]
    scopes: frozenset[str]
    audiences: tuple[str, ...]
    token_auth_method: str
    dpop_bound: bool
    status: str
    first_party: bool = False
    require_dpop_nonce: bool = False
    refresh_ttl: timedelta = timedelta(days=14)
    jwks: Mapping[str, object] | None = None  # public keys for private_key_jwt
    tls_subject_dn: str | None = None  # tls_client_auth: RFC 4514 subject DN
    service_principal_id: str | None = None  # subject of client_credentials tokens
    require_par: bool = False

    @property
    def confidential(self) -> bool:
        return self.token_auth_method in CONFIDENTIAL_AUTH

    @property
    def must_push(self) -> bool:
        return self.require_par or self.profile == "FAPI2"


def opaque_token() -> str:
    """256 bits from the OS CSPRNG, URL-safe."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_pkce(verifier: str | None, challenge: str) -> None:
    """RFC 7636 §4.6 with S256 only; a missing or malformed verifier fails like a wrong one."""
    if not verifier or not _VERIFIER.fullmatch(verifier):
        raise OAuthError("invalid_grant", "PKCE verifier invalid")
    if not hmac.compare_digest(pkce_challenge(verifier), challenge):
        raise OAuthError("invalid_grant", "PKCE verifier mismatch")


def redirect_matches(registered: str, requested: str) -> bool:
    """Exact string match; loopback http redirects may differ only in port (RFC 8252 §7.3)."""
    if registered == requested:
        return True
    r, q = urlsplit(registered), urlsplit(requested)
    return (
        r.scheme == q.scheme == "http"
        and r.hostname in _LOOPBACK
        and r.hostname == q.hostname
        and (r.path, r.query, r.fragment) == (q.path, q.query, q.fragment)
        and r.username is None
        and q.username is None
    )


def client_usable(client: OAuthClient | None, client_id: str | None) -> OAuthClient:
    if client is None or client.client_id != client_id or client.status != "ACTIVE":
        raise OAuthError("invalid_client", "unknown or inactive client", status=401)
    if client.token_auth_method not in SUPPORTED_CLIENT_AUTH:
        raise OAuthError("invalid_client", "client authentication method not available", status=401)
    return client


def fapi2_token_rules(client: OAuthClient, *, sender_constrained: bool) -> None:
    """FAPI 2.0 §5.3: every token for a FAPI 2.0 client is sender-constrained."""
    if client.profile == "FAPI2" and not sender_constrained:
        raise OAuthError("invalid_request", "FAPI 2.0 clients need DPoP or mTLS-bound tokens")


def resolve_audience(client: OAuthClient, resource: str | None, default: str) -> str:
    """RFC 8707: a requested resource must be one the client is registered for (§6.2)."""
    allowed = client.audiences or (default,)
    if resource is None:
        return default if default in allowed else allowed[0]
    if resource not in allowed:
        raise OAuthError("invalid_target", "resource not registered for this client")
    return resource


def narrow_scope(requested: str | None, allowed: frozenset[str]) -> frozenset[str]:
    if requested is None:
        return allowed
    scope = frozenset(requested.split())
    if not scope <= allowed:
        raise OAuthError("invalid_scope", "scope exceeds what was registered or granted")
    return scope


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    client: OAuthClient
    redirect_uri: str
    state: str
    scope: frozenset[str]
    audience: str
    code_challenge: str
    nonce: str | None
    dpop_jkt: str | None
    prompt: frozenset[str]
    max_age: int | None


def validate_authorization_request(
    client: OAuthClient | None,
    params: Mapping[str, str],
    *,
    default_audience: str,
    pushed: bool = False,
) -> AuthorizationRequest:
    """RFC 6749 §4.1.1 + RFC 7636 + §6.2. Errors before the redirect URI is trusted never redirect."""
    try:
        client = client_usable(client, params.get("client_id"))
    except OAuthError as e:
        raise OAuthError(e.error, e.description, status=400, redirectable=False) from e
    redirect_uri = params.get("redirect_uri")
    if not redirect_uri or not any(redirect_matches(r, redirect_uri) for r in client.redirect_uris):
        raise OAuthError(
            "invalid_request", "redirect_uri not registered for this client", redirectable=False
        )
    if params.get("request"):
        raise OAuthError("request_not_supported", "request objects are not supported; use PAR")
    if client.must_push and not pushed:
        raise OAuthError("invalid_request", "this client must use pushed authorization requests")
    if params.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "only the authorization code flow")
    if GRANT_AUTHORIZATION_CODE not in client.grant_types:
        raise OAuthError("unauthorized_client", "client may not use the authorization code grant")
    if params.get("response_mode", "query") != "query":
        raise OAuthError("invalid_request", "only response_mode=query")
    state = params.get("state")
    if not state or not _STATE.fullmatch(state):
        raise OAuthError("invalid_request", "state is required")
    challenge = params.get("code_challenge")
    if params.get("code_challenge_method") != "S256" or not challenge:
        raise OAuthError("invalid_request", "PKCE with code_challenge_method=S256 is required")
    if not _CHALLENGE.fullmatch(challenge):
        raise OAuthError("invalid_request", "code_challenge malformed")
    scope = narrow_scope(params.get("scope", ""), client.scopes)
    nonce = params.get("nonce")
    if "openid" in scope and not nonce:
        raise OAuthError("invalid_request", "nonce is required when an ID token is returned")
    if nonce is not None and not _STATE.fullmatch(nonce):
        raise OAuthError("invalid_request", "nonce malformed")
    max_age = params.get("max_age")
    if max_age is not None and not (max_age.isdigit() and len(max_age) <= 9):
        raise OAuthError("invalid_request", "max_age malformed")
    prompt = frozenset(params.get("prompt", "").split())
    if not prompt <= {"none", "login", "consent"} or ("none" in prompt and len(prompt) > 1):
        raise OAuthError("invalid_request", "prompt unsupported")
    jkt = params.get("dpop_jkt")
    if jkt is not None and not _CHALLENGE.fullmatch(jkt):
        raise OAuthError("invalid_request", "dpop_jkt malformed")
    return AuthorizationRequest(
        client=client,
        redirect_uri=redirect_uri,
        state=state,
        scope=scope,
        audience=resolve_audience(client, params.get("resource"), default_audience),
        code_challenge=challenge,
        nonce=nonce,
        dpop_jkt=jkt,
        prompt=prompt,
        max_age=int(max_age) if max_age is not None else None,
    )

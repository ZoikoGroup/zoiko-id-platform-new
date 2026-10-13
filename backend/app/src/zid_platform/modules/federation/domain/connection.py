"""Federation connections and upstream assertion rules (ZID-AUTH-001 §9, §9.1, §10, §20).

Pure domain, no I/O.
- Issuer, audience/azp, nonce and time are checked exactly; the issuer is never inferred from an
  email domain. issuer + subject is the identity key; email is just an attribute (never read here).
- Upstream acr/amr are returned as asserted; the application maps them to AAL-Z only through the
  connection's approved mapping profile (<= Z2).
- A connection whose pinned trust material has expired, or that is quarantined, authenticates
  nobody: no fail-open and no silent fallback to a weaker login (§20, AUTH-15, AUTH-16).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

CLOCK_SKEW = timedelta(seconds=60)
MAX_ID_TOKEN_AGE = timedelta(minutes=10)
LOGIN_TTL = timedelta(minutes=10)


class FederationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Connection:
    connection_id: str
    tenant_id: str
    protocol: str  # OIDC | SAML
    display_name: str
    issuer: str
    status: str
    config_revision: int
    metadata_expires_at: datetime
    acr_levels: Mapping[str, int]
    trust_amr: bool
    link_policy: str  # EXISTING_ONLY | JIT
    client_id: str | None = None
    client_auth: str | None = None
    client_secret_ref: str | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    jwks: Mapping[str, Any] | None = None
    algorithms: tuple[str, ...] = ("ES256", "RS256")
    scopes: tuple[str, ...] = ("openid",)
    sso_url: str | None = None
    idp_certificates: tuple[str, ...] = ()
    sp_entity_id: str | None = None
    allow_idp_initiated: bool = False  # only through a separately approved R3 change
    idp_initiated_targets: tuple[str, ...] = ()  # exact https landing URLs for unsolicited logins
    sp_decryption_key_ref: str | None = None  # brokered SP RSA key for EncryptedAssertion
    require_encrypted_assertions: bool = False
    trust_anchor_id: str | None = None  # OpenID Federation: endpoints/keys come from a trust chain
    federation_entity_id: str | None = None


def usable(c: Connection | None, now: datetime) -> Connection:
    if c is None:
        raise FederationError("FEDERATION_CONNECTION_NOT_FOUND")
    if c.status != "ACTIVE":
        raise FederationError("FEDERATION_CONNECTION_QUARANTINED")
    if now >= c.metadata_expires_at:
        raise FederationError("FEDERATION_METADATA_EXPIRED")  # pinned trust past its validity
    return c


@dataclass(frozen=True, slots=True)
class UpstreamIdentity:
    issuer: str
    subject: str
    acr: str | None
    amr: tuple[str, ...]
    auth_time: datetime | None


def _ts(claims: Mapping[str, Any], name: str) -> datetime | None:
    v = claims.get(name)
    if v is None:
        return None
    if not isinstance(v, int | float) or isinstance(v, bool):
        raise FederationError("ID_TOKEN_INVALID", name)
    return datetime.fromtimestamp(v, UTC)


def validate_id_token(
    claims: Mapping[str, Any], c: Connection, *, nonce: str | None, now: datetime
) -> UpstreamIdentity:
    """OpenID Connect Core §3.1.3.7 on a signature-verified ID token (AUTH-08 mix-up defences)."""
    if claims.get("iss") != c.issuer:
        raise FederationError("ID_TOKEN_ISSUER_MISMATCH")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if c.client_id not in auds:
        raise FederationError("ID_TOKEN_AUDIENCE_MISMATCH")
    if len(auds) > 1 and claims.get("azp") != c.client_id:
        raise FederationError("ID_TOKEN_AZP_MISMATCH")
    if nonce is None or claims.get("nonce") != nonce:
        raise FederationError("ID_TOKEN_NONCE_MISMATCH")
    exp, iat = _ts(claims, "exp"), _ts(claims, "iat")
    if exp is None or iat is None:
        raise FederationError("ID_TOKEN_INVALID", "exp/iat")
    if now >= exp + CLOCK_SKEW:
        raise FederationError("ID_TOKEN_EXPIRED")
    if iat > now + CLOCK_SKEW or now - iat > MAX_ID_TOKEN_AGE:
        raise FederationError("ID_TOKEN_STALE")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not 1 <= len(sub) <= 255:
        raise FederationError("ID_TOKEN_INVALID", "sub")
    acr = claims.get("acr")
    amr = claims.get("amr") or []
    if not isinstance(amr, list) or not all(isinstance(a, str) for a in amr):
        raise FederationError("ID_TOKEN_INVALID", "amr")
    return UpstreamIdentity(
        c.issuer, sub, acr if isinstance(acr, str) else None, tuple(amr), _ts(claims, "auth_time")
    )

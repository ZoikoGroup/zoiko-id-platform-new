"""Stronger credentials for a partner's server: an IP allowlist and private-key JWT (ZID-EXT-IDV-001 WF-05, SEC-02; CR-158). Pure rules, no I/O.

- IP allowlist: the networks an application's server may call from. Empty means no restriction. An entry that would allow the whole internet is refused: that is no allowlist, and saying so silently would be worse.
- Private-key JWT (RFC 7523): the company registers a PUBLIC key (ES256 or Ed25519). To get a token its server signs a short assertion naming this client, this token endpoint and a one-time id. Zoiko iD holds nothing
  secret for such a credential, so there is nothing to leak. An assertion is accepted once, for a few minutes, and only for the audience it names.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from datetime import datetime, timedelta
from typing import Any

from zid_platform.modules.verify_api.domain.model import PartnerError

MAX_ALLOWLIST = 20
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
ASSERTION_ALGS = ("ES256", "EdDSA")
MAX_ASSERTION_LIFETIME = timedelta(minutes=5)
CLOCK_SKEW = timedelta(seconds=60)
JTI_MIN, JTI_MAX = 8, 128
_PRIVATE_MEMBERS = frozenset({"d", "p", "q", "dp", "dq", "qi", "k", "oth"})
_PUBLIC_MEMBERS = frozenset({"kty", "crv", "x", "y", "kid", "use", "alg"})


# ------------------------------------------------------------------------------- IP allowlist


def check_allowlist(entries: list[str] | None) -> list[str]:
    """The cleaned list: each entry an address or a network, written in one normal form, no duplicates. An address on its own means exactly that address."""
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise PartnerError("VALIDATION_FAILED", "The allowlist is a list of addresses or networks.")
    out: list[str] = []
    for raw in entries:
        if not isinstance(raw, str):
            raise PartnerError("VALIDATION_FAILED", "Each allowlist entry is text.")
        try:
            net = ipaddress.ip_network(raw.strip(), strict=False)
        except ValueError as e:
            raise PartnerError(
                "VALIDATION_FAILED",
                f"{raw.strip()!r} is not an IP address or network such as 203.0.113.7 or 203.0.113.0/24.",
            ) from e
        if net.prefixlen == 0:
            raise PartnerError(
                "VALIDATION_FAILED",
                "An entry that allows every address is not an allowlist. Leave the list empty for no restriction.",
            )
        text = str(net)
        if text not in out:
            out.append(text)
    if len(out) > MAX_ALLOWLIST:
        raise PartnerError("VALIDATION_FAILED", f"At most {MAX_ALLOWLIST} entries.")
    return out


def ip_allowed(allowlist: list[str] | tuple[str, ...], address: str | None) -> bool:
    """True when there is no allowlist, or the caller's address is inside one entry. An unknown or unreadable address never passes a non-empty allowlist."""
    if not allowlist:
        return True
    if not address:
        return False
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in ipaddress.ip_network(entry, strict=False) for entry in allowlist)


# ------------------------------------------------------------------------------- the public key


def check_public_jwk(jwk: Any) -> dict[str, str]:
    """A public key the company's server will sign with: EC P-256 or Ed25519, public members only. Anything that looks like private key material is refused outright."""
    if not isinstance(jwk, dict):
        raise PartnerError("VALIDATION_FAILED", "The public key is a JSON Web Key object.")
    if _PRIVATE_MEMBERS & set(jwk):
        raise PartnerError(
            "VALIDATION_FAILED",
            "That key contains private key material. Send only the public key, and treat that private key as exposed.",
        )
    unknown = sorted(set(jwk) - _PUBLIC_MEMBERS)
    if unknown:
        raise PartnerError("VALIDATION_FAILED", f"Unexpected key member: {unknown[0]}.")
    if not all(isinstance(v, str) for v in jwk.values()):
        raise PartnerError("VALIDATION_FAILED", "Every member of the key is text.")
    kty, crv = jwk.get("kty"), jwk.get("crv")
    if (kty, crv) == ("EC", "P-256"):
        needed, alg = ("x", "y"), "ES256"
    elif (kty, crv) == ("OKP", "Ed25519"):
        needed, alg = ("x",), "EdDSA"  # type: ignore[assignment]
    else:
        raise PartnerError("VALIDATION_FAILED", "The key must be EC P-256 or Ed25519.")
    if any(not jwk.get(n) for n in needed):
        raise PartnerError("VALIDATION_FAILED", "The key is missing its public coordinates.")
    if jwk.get("alg") not in (None, alg):
        raise PartnerError("VALIDATION_FAILED", f"This key is for {alg}.")
    if jwk.get("use") not in (None, "sig"):
        raise PartnerError("VALIDATION_FAILED", "The key must be for signatures.")
    clean = {k: str(v) for k, v in jwk.items()}
    clean.setdefault("alg", alg)
    clean.setdefault("use", "sig")
    clean.setdefault("kid", thumbprint(clean))
    if len(clean["kid"]) > 64:
        raise PartnerError("VALIDATION_FAILED", "The key id is at most 64 characters.")
    return clean


def thumbprint(jwk: dict[str, str]) -> str:
    """A short stable name for a key (RFC 7638 form), so a person can tell two keys apart."""
    members = ("crv", "kty", "x", "y") if jwk.get("kty") == "EC" else ("crv", "kty", "x")
    canonical = json.dumps({k: jwk[k] for k in members}, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


# ------------------------------------------------------------------------------- the assertion


def check_assertion_claims(
    claims: dict[str, Any], *, client_id: str, audiences: list[str], now: datetime
) -> tuple[str, datetime]:
    """The assertion's own claims after its signature has been checked: issued by and for this client, to this token endpoint, short-lived, with an id. Returns (jti, expiry)."""
    bad = PartnerError("AUTH_INVALID", "The client assertion is not valid.")
    if claims.get("iss") != client_id or claims.get("sub") != client_id:
        raise bad
    aud = claims.get("aud")
    got = [aud] if isinstance(aud, str) else aud if isinstance(aud, list) else []
    if not any(isinstance(a, str) and a in audiences for a in got):
        raise bad
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise bad
    expires = datetime.fromtimestamp(exp, tz=now.tzinfo)
    if expires <= now or expires > now + MAX_ASSERTION_LIFETIME + CLOCK_SKEW:
        raise bad
    for claim in ("iat", "nbf"):
        value = claims.get(claim)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise bad
            if datetime.fromtimestamp(value, tz=now.tzinfo) > now + CLOCK_SKEW:
                raise bad
    jti = claims.get("jti")
    if not isinstance(jti, str) or not JTI_MIN <= len(jti) <= JTI_MAX:
        raise bad
    return jti, expires

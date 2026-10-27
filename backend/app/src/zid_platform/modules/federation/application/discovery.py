"""Find an identity provider's settings from its address (ZID-AUTH-001 §9; CR-038).

A person adding an enterprise sign-in should not have to copy endpoints, keys and certificates by hand. Given an OpenID
Connect issuer, or a SAML metadata address, this reads the provider's published settings once, at set-up time, and hands
them back to be pinned like anything typed in: nothing is fetched at sign-in (the pinned trust material is unchanged).

Every request goes through `egress`: https only, a DNS host, a standard port, every resolved address public, no redirects,
a short timeout and a size limit, so an admin cannot point the platform at an internal address. The OpenID document must
name the very issuer that was asked about (OIDC Discovery §4.3), or it is refused.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any
from urllib.parse import urlsplit

import httpx
from defusedxml import ElementTree

from zid_platform import egress

MAX_BYTES = 512_000
TIMEOUT_S = 6.0
MAX_KEYS = 20
MAX_CERTS = 5


class DiscoveryError(Exception):
    """`code` is stable and safe to show; `detail` says what to do next."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False, transport=transport)


async def _get(http: httpx.AsyncClient, url: str, *, resolve: bool) -> bytes:
    try:
        safe = await egress.guard(url, resolve=resolve)
    except egress.EgressError as e:
        raise DiscoveryError("DISCOVERY_ADDRESS_REFUSED", _why(e)) from e
    try:
        async with http.stream(
            "GET",
            safe,
            headers={"accept": "application/json, application/samlmetadata+xml, text/xml"},
        ) as res:
            if res.status_code != 200:
                raise DiscoveryError(
                    "DISCOVERY_NOT_FOUND",
                    f"{urlsplit(safe).netloc} answered {res.status_code} for that address.",
                )
            body = bytearray()
            async for chunk in res.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise DiscoveryError("DISCOVERY_TOO_LARGE", "The page is larger than expected.")
            return bytes(body)
    except httpx.HTTPError as e:
        raise DiscoveryError(
            "DISCOVERY_UNREACHABLE", f"Could not reach {urlsplit(safe).netloc}."
        ) from e


def _why(e: egress.EgressError) -> str:
    if e.code == "EGRESS_DNS_FAILED":
        return "That address could not be found. Check the spelling."
    if e.code == "EGRESS_DESTINATION_BLOCKED":
        return "That address points inside a private network, which is not allowed."
    return "Use an https:// address with a normal host name."


def _same(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


async def discover_oidc(
    issuer: str, http: httpx.AsyncClient, *, resolve: bool = True
) -> dict[str, Any]:
    """The issuer's endpoints and signing keys, as published at `<issuer>/.well-known/openid-configuration`."""
    issuer = issuer.strip().rstrip("/")
    raw = await _get(http, f"{issuer}/.well-known/openid-configuration", resolve=resolve)
    try:
        doc = _json(raw)
    except ValueError as e:
        raise DiscoveryError(
            "DISCOVERY_NOT_OIDC", "That address does not publish OpenID Connect settings."
        ) from e
    if not _same(str(doc.get("issuer", "")), issuer):
        raise DiscoveryError(
            "DISCOVERY_ISSUER_MISMATCH",
            f"The provider says its address is {doc.get('issuer')!r}, not the one you entered. Use the one it reports.",
        )
    authorization, token, jwks_uri = (
        str(doc.get(k) or "") for k in ("authorization_endpoint", "token_endpoint", "jwks_uri")
    )
    if not all(u.startswith("https://") for u in (authorization, token, jwks_uri)):
        raise DiscoveryError(
            "DISCOVERY_INCOMPLETE",
            "The provider's settings are missing an https sign-in, token or key address.",
        )
    try:
        keys = _json(await _get(http, jwks_uri, resolve=resolve)).get("keys")
    except ValueError as e:
        raise DiscoveryError(
            "DISCOVERY_KEYS_INVALID", "The provider's key list could not be read."
        ) from e
    if (
        not isinstance(keys, list)
        or not keys
        or len(keys) > MAX_KEYS
        or not all(isinstance(k, dict) for k in keys)
    ):
        raise DiscoveryError(
            "DISCOVERY_KEYS_INVALID",
            "The provider's key list is empty or not in the expected form.",
        )
    return {
        "issuer": str(doc["issuer"]),
        "authorization_endpoint": authorization,
        "token_endpoint": token,
        "jwks": {"keys": keys},
    }


def _json(raw: bytes) -> dict[str, Any]:
    import json

    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("not an object")
    return value


_MD = "{urn:oasis:names:tc:SAML:2.0:metadata}"
_DS = "{http://www.w3.org/2000/09/xmldsig#}"
_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"


def _pem(b64: str) -> str:
    cleaned = "".join(b64.split())
    try:
        base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError) as e:
        raise DiscoveryError(
            "DISCOVERY_CERTIFICATE_INVALID", "A certificate in the metadata could not be read."
        ) from e
    lines = [cleaned[i : i + 64] for i in range(0, len(cleaned), 64)]
    return "-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----"


async def discover_saml(
    metadata_url: str, http: httpx.AsyncClient, *, resolve: bool = True
) -> dict[str, Any]:
    """The provider's entity id, single sign-on address and signing certificates from its SAML metadata."""
    raw = await _get(http, metadata_url.strip(), resolve=resolve)
    try:
        root = ElementTree.fromstring(raw)
    except Exception as e:
        raise DiscoveryError(
            "DISCOVERY_NOT_SAML", "That address does not publish SAML metadata."
        ) from e
    entity = (
        root if root.tag == f"{_MD}EntityDescriptor" else root.find(f".//{_MD}EntityDescriptor")
    )
    if entity is None or not entity.get("entityID"):
        raise DiscoveryError("DISCOVERY_NOT_SAML", "That address does not publish SAML metadata.")
    idp = entity.find(f"{_MD}IDPSSODescriptor")
    if idp is None:
        raise DiscoveryError(
            "DISCOVERY_NOT_SAML", "The metadata does not describe a sign-in provider."
        )
    services = {
        s.get("Binding"): s.get("Location") for s in idp.findall(f"{_MD}SingleSignOnService")
    }
    sso = services.get(_REDIRECT) or services.get(_POST)
    if not sso or not sso.startswith("https://"):
        raise DiscoveryError(
            "DISCOVERY_INCOMPLETE", "The metadata has no https single sign-on address."
        )
    certs: list[str] = []
    for key in idp.findall(f"{_MD}KeyDescriptor"):
        if key.get("use") in (None, "signing"):
            for cert in key.findall(f".//{_DS}X509Certificate"):
                if cert.text and len(certs) < MAX_CERTS:
                    certs.append(_pem(cert.text))
    if not certs:
        raise DiscoveryError("DISCOVERY_INCOMPLETE", "The metadata has no signing certificate.")
    return {"entity_id": entity.get("entityID"), "sso_url": sso, "certificates": certs}

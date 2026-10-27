"""Finding an identity provider's settings from its address (CR-038). No network: httpx's mock transport plays the provider."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx
import pytest
from zid_platform.modules.federation.application import discovery
from zid_platform.modules.federation.application.discovery import DiscoveryError

ISSUER = "https://login.acme.example"
KEY = {"kty": "EC", "crv": "P-256", "kid": "k1", "x": "x", "y": "y"}
CERT_B64 = base64.b64encode(b"not-a-real-certificate-but-valid-base64").decode()


def provider(routes: dict[str, Any]) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        body = routes.get(str(req.url))
        if body is None:
            return httpx.Response(404)
        if isinstance(body, bytes):
            return httpx.Response(200, content=body)
        return httpx.Response(200, json=body)

    return discovery.client(httpx.MockTransport(handler))


def oidc_doc(**over: Any) -> dict[str, Any]:
    doc = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/keys",
    }
    return {**doc, **over}


def run_oidc(routes: dict[str, Any], issuer: str = ISSUER) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        async with provider(routes) as http:
            return await discovery.discover_oidc(issuer, http, resolve=False)

    return asyncio.run(go())


def refused(routes: dict[str, Any], code: str, issuer: str = ISSUER) -> None:
    with pytest.raises(DiscoveryError) as e:
        run_oidc(routes, issuer)
    assert e.value.code == code


def test_an_issuer_gives_its_endpoints_and_keys() -> None:
    out = run_oidc(
        {
            f"{ISSUER}/.well-known/openid-configuration": oidc_doc(),
            f"{ISSUER}/keys": {"keys": [KEY]},
        }
    )
    assert (
        out["authorization_endpoint"] == f"{ISSUER}/authorize"
        and out["token_endpoint"] == f"{ISSUER}/token"
    )
    assert out["jwks"] == {"keys": [KEY]} and out["issuer"] == ISSUER


def test_a_trailing_slash_on_the_issuer_still_finds_the_settings() -> None:
    routes = {
        f"{ISSUER}/.well-known/openid-configuration": oidc_doc(),
        f"{ISSUER}/keys": {"keys": [KEY]},
    }
    assert run_oidc(routes, issuer=f"{ISSUER}/")["issuer"] == ISSUER


def test_a_provider_that_names_another_issuer_is_refused() -> None:
    routes = {
        f"{ISSUER}/.well-known/openid-configuration": oidc_doc(issuer="https://evil.example"),
        f"{ISSUER}/keys": {"keys": [KEY]},
    }
    refused(routes, "DISCOVERY_ISSUER_MISMATCH")


def test_unfindable_incomplete_or_keyless_providers_are_refused_in_plain_codes() -> None:
    refused({}, "DISCOVERY_NOT_FOUND")
    refused(
        {
            f"{ISSUER}/.well-known/openid-configuration": oidc_doc(
                token_endpoint="http://login.acme.example/token"
            )
        },
        "DISCOVERY_INCOMPLETE",
    )
    base = {f"{ISSUER}/.well-known/openid-configuration": oidc_doc()}
    refused({**base, f"{ISSUER}/keys": {"keys": []}}, "DISCOVERY_KEYS_INVALID")
    refused({**base, f"{ISSUER}/keys": {"nothing": 1}}, "DISCOVERY_KEYS_INVALID")
    refused(
        {f"{ISSUER}/.well-known/openid-configuration": b"<html>not json</html>"},
        "DISCOVERY_NOT_OIDC",
    )


def test_addresses_that_could_reach_inside_a_network_are_refused_before_any_request() -> None:
    for bad in (
        "https://127.0.0.1",
        "https://10.0.0.5",
        "http://login.acme.example",
        "https://login.acme.example:8080",
        "https://metadata.internal",
    ):
        refused({}, "DISCOVERY_ADDRESS_REFUSED", issuer=bad)


def metadata(**over: Any) -> bytes:
    sso = over.get(
        "sso",
        '<SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://login.acme.example/saml/sso"/>',
    )
    cert = over.get(
        "cert",
        f'<KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>{CERT_B64}</ds:X509Certificate></ds:X509Data></ds:KeyInfo></KeyDescriptor>',
    )
    return (
        '<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" '
        f'entityID="https://login.acme.example/saml"><IDPSSODescriptor>{cert}{sso}</IDPSSODescriptor></EntityDescriptor>'
    ).encode()


def run_saml(body: bytes) -> dict[str, Any]:
    url = "https://login.acme.example/saml/metadata"

    async def go() -> dict[str, Any]:
        async with provider({url: body}) as http:
            return await discovery.discover_saml(url, http, resolve=False)

    return asyncio.run(go())


def test_saml_metadata_gives_the_entity_id_sign_in_address_and_certificate() -> None:
    out = run_saml(metadata())
    assert (
        out["entity_id"] == "https://login.acme.example/saml"
        and out["sso_url"] == "https://login.acme.example/saml/sso"
    )
    assert len(out["certificates"]) == 1 and out["certificates"][0].startswith(
        "-----BEGIN CERTIFICATE-----"
    )


def test_saml_metadata_without_an_https_sign_in_address_or_a_certificate_is_refused() -> None:
    with pytest.raises(DiscoveryError) as e:
        run_saml(
            metadata(
                sso='<SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="http://login.acme.example/sso"/>'
            )
        )
    assert e.value.code == "DISCOVERY_INCOMPLETE"
    with pytest.raises(DiscoveryError) as e2:
        run_saml(metadata(cert=""))
    assert e2.value.code == "DISCOVERY_INCOMPLETE"
    with pytest.raises(DiscoveryError) as e3:
        run_saml(b"this is not xml")
    assert e3.value.code == "DISCOVERY_NOT_SAML"


def test_xml_entity_tricks_are_refused() -> None:
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="&a;"/>'
    with pytest.raises(DiscoveryError) as e:
        run_saml(evil)
    assert e.value.code == "DISCOVERY_NOT_SAML"
    assert json.dumps(e.value.detail)  # a plain message, never the file

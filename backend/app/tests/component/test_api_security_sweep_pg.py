"""Every route, attacked without credentials (ZID-VERIFY-MASTER-001 §17, MPR-03/04/08; ZID-API-001 §3).

For each operation the cell publishes, send it with no credentials, with garbage in the Authorization header,
and with forged tokens (`alg: none`, a valid structure signed with an unknown key). A route that is not
explicitly public must answer 401 or 403 (or 4xx for a malformed request), never succeed, and nothing may
answer 5xx. The public routes are listed below by name: adding a new unauthenticated endpoint makes this test
fail until someone decides it is meant to be public.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from conftest import CELL
from fastapi.testclient import TestClient
from zid_crypto import KeyRing, LocalSigner
from zid_platform.main import create_app
from zid_platform.settings import Settings

PARAM = re.compile(r"\{[^}]+\}")
SOME_ID = "00000000-0000-4000-8000-000000000001"

# Routes that are meant to answer without credentials, and why.
PUBLIC_PATHS: dict[str, str] = {
    # discovery and metadata
    "/.well-known/openid-configuration": "OIDC discovery",
    "/.well-known/oauth-authorization-server": "OAuth metadata",
    "/.well-known/jwks.json": "token keys",
    "/oauth2/jwks": "token keys",
    "/.well-known/ssf-configuration": "SSF metadata",
    "/ssf/jwks": "SET keys",
    "/.well-known/openid-federation": "federation entity configuration",
    "/.well-known/openid-credential-issuer": "OID4VCI issuer metadata",
    "/.well-known/jwt-vc-issuer": "VC issuer keys",
    "/.well-known/oauth-protected-resource": "resource metadata",
    "/v1/catalog": "published plans, released capabilities and APPROVED prices; no tenant data (CR-024)",
    "/v1/catalog/families": "the sixteen proposed service families, their units and wording, never a price or an availability claim (ZID-COM-WF-2026-11 section 02)",
    "/v1/commercial/capability-dependencies": "which service families need which, which need a provider and which are regulated: structure only, no tenant data (WF-C02)",
    "/health/live": "liveness",
    "/health/ready": "readiness",
    "/health/startup": "startup",
    "/metrics": "metrics (network-restricted at the edge)",
    "/v1/machine/trust-domain": "the cell's trust domain",
    "/v1/webhooks/signing-keys": "public keys receivers verify with",
    "/v1/machine/attestations:verify": "runtime attestation, authenticated by an attestor signature only (CR-042)",
    "/v1/provider-events/stripe": "payment processor events, authenticated by Stripe signature only (CR-040)",
    "/v1/signup/options": "sign-up form options",
    "/v1/workspaces/{name}": "workspace sign-in lookup",
    "/v1/cells": "published cell catalogue",
    "/v1/countries": "country availability for the sign-up selector: public, names no provider or approval",
    "/v1/residency-offers": "the residency offers and where Regional is available: public, names no gate reference",
    "/v1/tenant/discovery": "signed discovery",
    "/": "service banner (minimal outside local/test)",
    "/v1/evidence/keys": "public keys anyone verifying a receipt needs",
    "/v1/privacy/jurisdiction-packs": "jurisdiction pack names and deployability: public transparency data",
    # REQUIREMENT: never routed at the edge. The production edge config must answer 404 for /internal/*
    # (deploy/gcp/Caddyfile); Prometheus scrapes it over the private network only.
    "/internal/metrics": "Prometheus scrape; internal network only",
}
PUBLIC_PREFIXES = (
    "/health/",
    "/.well-known/",
    "/v1/signup/",  # registry lookups, rate limited, public data only
    "/authn/",  # sign-in, enrollment and recovery ceremonies authenticate with their own tickets
    "/oauth2/",  # protocol endpoints that authenticate the client themselves
    "/ssf/events/push/",  # authenticated by the stream credential
    "/scim/v2/",  # authenticated by the provisioning source credential
    "/federation/",  # relying-party callbacks and metadata
    "/presentations/",  # wallet-facing OID4VP endpoints
    "/credential",  # OID4VCI protocol endpoints (token-bound)
    "/v1/privacy/public",
)


# Answering 503 is the correct, honest response when a dependency is not configured in this environment.
UNCONFIGURED_OK = frozenset({"/health/ready", "/.well-known/webauthn"})


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def b64(obj: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def forged_tokens() -> list[str]:
    payload = {
        "iss": "https://id.zoiko.test",
        "aud": "https://api.zoiko.test",
        "sub": SOME_ID,
        "tenant_id": SOME_ID,
        "exp": 4102444800,
    }
    return [
        f"{b64({'alg': 'none', 'typ': 'at+jwt'})}.{b64(payload)}.",  # alg=none
        f"{b64({'alg': 'ES256', 'typ': 'at+jwt', 'kid': 'x'})}.{b64(payload)}.{b64({'sig': 'x'})}",  # unknown key
        "not-a-token",
        "A" * 4000,  # oversized
    ]


def test_every_route_refuses_unauthenticated_and_forged_callers(rt_url: str) -> None:
    """SEC-03: being on the private network is not authorization; every route refuses a caller without a valid identity."""
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer="https://id.zoiko.test",
            token_audience="https://api.zoiko.test",
            public_base_url="https://api.zoiko.test",
            environment="test",
            cell_uuid=CELL,
        ),
        keyring=KeyRing(LocalSigner("sweep")),
    )
    spec = app.openapi()
    operations = [
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method in ("get", "post", "put", "patch", "delete")
    ]
    assert len(operations) > 250, "the sweep should cover the whole published API"

    succeeded: list[str] = []
    crashed: list[str] = []
    protected = 0
    with TestClient(app, base_url="https://api.zoiko.test", raise_server_exceptions=False) as http:
        for method, path in operations:
            url = PARAM.sub(SOME_ID, path)
            attempts: list[dict[str, str]] = [{}, {"Authorization": "Bearer garbage"}]
            if not is_public(path):
                attempts += [{"Authorization": f"Bearer {t}"} for t in forged_tokens()]
                attempts += [{"Authorization": f"DPoP {forged_tokens()[0]}", "DPoP": "x"}]
            for headers in attempts:
                kwargs: dict[str, Any] = {"headers": headers}
                if method != "GET":
                    kwargs["json"] = {}
                r = http.request(method, url, **kwargs)
                if r.status_code >= 500 and not (r.status_code == 503 and path in UNCONFIGURED_OK):
                    crashed.append(f"{r.status_code} {method} {path} headers={list(headers)}")
                if not is_public(path) and r.status_code < 400:
                    succeeded.append(f"{r.status_code} {method} {path} headers={list(headers)}")
            if not is_public(path):
                protected += 1

    assert protected > 150, f"only {protected} protected operations were exercised"
    assert not crashed, "routes that crash on hostile input:\n" + "\n".join(crashed[:30])
    assert not succeeded, (
        "routes that answered without valid credentials but are not on the public list "
        "(decide: protect it, or add it to PUBLIC_PATHS with a reason):\n"
        + "\n".join(succeeded[:40])
    )

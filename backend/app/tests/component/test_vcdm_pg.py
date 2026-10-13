"""W3C VCDM 2.0 presentations through OID4VP (ZID-VC-001 §9, §13; VC-JOSE-COSE; Bitstring Status).

A holder-signed vp+jwt envelopes an issuer-signed vc+jwt; the verifier checks typed issuer trust,
the issuer signature, pinned contexts, holder binding to this transaction (aud/nonce), and the
Bitstring Status List (revocation and suspension purposes) fetched from the issuer's own domain.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from softcred import VcJwtIssuer, present_vp
from zid_crypto import KeyRing, LocalSigner, sign_jws, verify_jws
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
CLIENT_ID = f"openid_federation:{ISS}"
TYPE = "ExampleAgeCredential"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def uid() -> str:
    return str(uuid.uuid4())


def test_w3c_vc_jwt_presentations(rt_url: str) -> None:
    tenant, admin = uid(), uid()
    ring = KeyRing(LocalSigner("verifier-1"))
    uni = VcJwtIssuer("https://registry.uni.example")
    holder = LocalSigner("holder")
    routes: dict[str, Any] = {}

    def network(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for purpose in ("revocation", "suspension"):
            if url == uni.status_url(purpose):
                return routes.get(url) or httpx.Response(200, text=uni.status_list(purpose))
        return httpx.Response(404)

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await make_admin(
                c,
                tenant,
                admin,
                ["credential.trust.manage"],
                ["credential_configuration:*", "credential_issuer:*", "rp_registration:*"],
            )
            sid, now = uid(), datetime.now(UTC)
            pr, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    admin,
                    now - timedelta(seconds=5),
                    "native:webauthn",
                    2,
                    pr.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    now + timedelta(hours=1),
                    now,
                ),
            )
            return sid

    admin_sid = asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
        ),
        keyring=ring,
    )
    app.state.credential_transport = httpx.MockTransport(network)

    def bearer(client: str, scope: str = "", sid: str | None = None) -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=admin,
            tenant_id=tenant,
            client_id=client,
            now=datetime.now(UTC),
            session_id=sid,
            scope=frozenset(scope.split()),
        )
        return {"Authorization": f"Bearer {tok}"}

    adm, rp = bearer("console", sid=admin_sid), bearer("rp-shop", "presentation:request")
    until = (datetime.now(UTC) + timedelta(days=30)).isoformat()

    with TestClient(app, base_url=ISS) as http:

        def admin_post(path: str, body: dict[str, Any], expect: int = 201) -> Any:
            r = http.post(path, headers=adm, json=body)
            assert r.status_code in (expect, 200), r.text
            return r.json()

        # a VC_JWT configuration takes a Bitstring Status List, nothing else
        admin_post(
            "/credential-configurations",
            {
                "credential_type": TYPE,
                "format": "VC_JWT",
                "required_claims": ["age_over_18"],
                "status_method": "TOKEN_STATUS_LIST",
            },
            expect=422,
        )
        out = admin_post(
            "/credential-configurations",
            {
                "credential_type": TYPE,
                "format": "VC_JWT",
                "required_claims": ["age_over_18"],
                "optional_claims": ["degree"],
                "status_method": "BITSTRING_STATUS_LIST",
            },
        )
        assert out["adapter_version"] == "w3c-vcdm-2.0+vc-jose-cose"
        cfg = out["configuration_id"]
        admin_post(f"/credential-configurations/{cfg}:activate", {}, expect=200)
        admin_post(
            "/credential-issuer-trust",
            {
                "issuer_identifier": uni.iss,
                "credential_types": [TYPE],
                "trust_source": "PINNED",
                "jwks": uni.jwks(),
                "valid_to": until,
            },
        )
        admin_post(
            "/relying-party-registrations",
            {
                "client_id": "rp-shop",
                "purpose": "age",
                "allowed_attributes": ["age_over_18"],
                "valid_to": until,
            },
        )

        def request() -> tuple[str, dict[str, Any]]:
            r = http.post(
                "/presentations",
                headers=rp,
                json={
                    "configuration_id": cfg,
                    "purpose": "age",
                    "claims": ["age_over_18"],
                    "jurisdiction": "GB",
                },
            )
            assert r.status_code == 201, r.text
            ro = http.get(urlsplit(r.json()["request_uri"]).path)
            _, req = verify_jws(ro.text, keys=ring.jwks()["keys"], typ="oauth-authz-req+jwt")
            return r.json()["presentation_id"], req

        def respond(req: dict[str, Any], vp: str) -> Any:
            return http.post(
                urlsplit(req["response_uri"]).path,
                headers=FORM,
                data={"state": req["state"], "vp_token": json.dumps({"cred1": [vp]})},
            )

        def vp_for(req: dict[str, Any], idx: int = 0, **kw: Any) -> str:
            vc = kw.pop("vc", None) or uni.issue(
                TYPE, {"age_over_18": True, "degree": "BSc"}, holder, idx=idx, **kw
            )
            return present_vp(
                vc,
                kw.pop("signer", holder),
                aud=kw.pop("aud", CLIENT_ID),
                nonce=kw.pop("nonce", req["nonce"]),
            )

        # DCQL asks for jwt_vc_json by type; only the minimized claim
        pid, req = request()
        dcql = req["dcql_query"]["credentials"][0]
        assert dcql["format"] == "jwt_vc_json"
        assert dcql["meta"]["type_values"] == [["VerifiableCredential", TYPE]]
        assert dcql["claims"] == [{"path": ["credentialSubject", "age_over_18"]}]
        r = respond(req, vp_for(req))
        assert r.status_code == 200, r.text
        got = http.get(f"/presentations/{pid}", headers=rp).json()
        assert got["status"] == "VERIFIED" and got["claims"] == {"age_over_18": True}

        def rejected(vp_builder: Any) -> str:
            _, req = request()
            r = respond(req, vp_builder(req))
            assert r.status_code == 400, r.text
            return str(r.json()["error_description"])

        rogue = VcJwtIssuer(uni.iss, LocalSigner("rogue"))
        assert rejected(
            lambda q: vp_for(q, vc=rogue.issue(TYPE, {"age_over_18": True}, holder))
        ) == ("CREDENTIAL_PROOF_INVALID")
        assert rejected(lambda q: vp_for(q, nonce="other")) == "HOLDER_BINDING_INVALID"
        assert rejected(lambda q: vp_for(q, aud="x509_hash:someone-else")) == (
            "HOLDER_BINDING_INVALID"
        )
        assert rejected(lambda q: vp_for(q, signer=LocalSigner("thief"))) == (
            "HOLDER_BINDING_INVALID"
        )
        assert (
            rejected(lambda q: vp_for(q, **{"@context": [VcJwtIssuer.BASE, "https://evil/ctx"]}))
            == "VC_CONTEXT_NOT_ALLOWED"
        )
        unknown = VcJwtIssuer("https://unknown.example")
        assert (
            rejected(lambda q: vp_for(q, vc=unknown.issue(TYPE, {"age_over_18": True}, holder)))
            == "ISSUER_UNTRUSTED"
        )
        # an SD-JWT-typed token where a vc+jwt belongs
        assert (
            rejected(
                lambda q: vp_for(q, vc=sign_jws(uni.signer, {"issuer": uni.iss}, typ="dc+sd-jwt"))
            )
            == "VC_MALFORMED"
        )

        # Bitstring Status List: revocation and suspension, per credential index
        uni.set("suspension", 5)
        assert rejected(lambda q: vp_for(q, idx=5)) == "CREDENTIAL_SUSPENDED"
        uni.set("suspension", 5, 0)
        uni.set("revocation", 5)
        assert rejected(lambda q: vp_for(q, idx=5)) == "CREDENTIAL_REVOKED"
        _, req = request()
        assert respond(req, vp_for(req, idx=6)).status_code == 200
        # a status list we cannot verify is UNKNOWN, and mandatory status fails closed
        routes[uni.status_url("revocation")] = httpx.Response(
            200, text=rogue.status_list("revocation")
        )
        assert rejected(lambda q: vp_for(q, idx=6)) == "CREDENTIAL_STATUS_UNAVAILABLE"
        routes[uni.status_url("revocation")] = httpx.Response(503)
        assert rejected(lambda q: vp_for(q, idx=6)) == "CREDENTIAL_STATUS_UNAVAILABLE"

"""Verifiable credentials end to end (ZID-VC-001 §4-15, §18; OID4VP 1.0; OID4VCI 1.0; FR-VC-001..008).

Verifier: purpose-registered, minimized requests; signed single-use request objects; SD-JWT VC
(disclosures, KB-JWT bound to nonce/audience/sd_hash, typed issuer trust, Token Status List) and
ISO mdoc (IACA chain, value digests, device signature over the OID4VP transcript); outcomes become
verification assertions; claims are handed to the RP once. Issuer: OID4VCI with a key proof,
idempotent per proof, revocable through Zoiko's own status list - checked by Zoiko's own verifier.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from softcred import Mdoc, SdJwtIssuer, present
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner, sign_jws, verify_jws
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.credential.domain.reliance import RELIANCE
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
from zid_platform.modules.verification.infrastructure import verification_store as vstore
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
PID = "urn:eu.europa.ec.eudi:pid:1"
MDL = "org.iso.18013.5.1.mDL"
CLIENT_ID = f"openid_federation:{ISS}"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def uid() -> str:
    return str(uuid.uuid4())


def test_verifier_and_issuer(rt_url: str) -> None:
    """VC-08 audience binding, VC-11 expiry / not-yet-valid, VC-18 and VC-21 (a verified credential is not authority)."""
    tenant, admin, alice = uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-1"))
    gov = SdJwtIssuer("https://issuer.gov.example")
    rogue = SdJwtIssuer("https://issuer.gov.example", LocalSigner("rogue"))
    unknown = SdJwtIssuer("https://unknown.example")
    mdl = Mdoc()

    async def grants() -> int:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return int(
                (await c.execute(text("SELECT count(*) FROM authz.authority_grant"))).scalar_one()
            )

    holder = LocalSigner("wallet-holder")
    routes: dict[str, httpx.Response] = {}

    def network(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{gov.iss}/status/1":
            return httpx.Response(200, text=gov.status_token())
        return routes.get(url, httpx.Response(503))

    async def seed() -> tuple[str, str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (admin, alice):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await make_admin(
                c,
                tenant,
                admin,
                ["credential.trust.manage", "credential.issue", "credential.status.change"],
                [
                    "credential_configuration:*",
                    "credential_issuer:*",
                    "rp_registration:*",
                    "credential:*",
                ],
            )
            profile, _ = await vstore.create_profile(
                c,
                tenant_id=tenant,
                name="pid-reliance",
                level="IAL-Z2",
                requirements={
                    "required_claims": ["given_name", "family_name"],
                    "accepted_evidence": ["DIGITAL_CREDENTIAL"],
                    "binding_required": True,
                    "jurisdictions": ["GB", "FR"],
                },
            )
            await vstore.activate_profile(
                c, await vstore.load_profile(c, profile), datetime.now(UTC)
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
            return profile, sid

    profile_id, admin_sid = asyncio.run(seed())
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

    def bearer(sub: str, client: str, scope: str = "", sid: str | None = None) -> dict[str, str]:
        return {
            "Authorization": "Bearer "
            + issue_access_token(
                ring,
                issuer=ISS,
                audience=AUD,
                subject=sub,
                tenant_id=tenant,
                client_id=client,
                now=datetime.now(UTC),
                session_id=sid,
                scope=frozenset(scope.split()),
            )
        }

    adm = bearer(admin, "console", sid=admin_sid)
    rp = bearer(admin, "rp-bank", "presentation:request")
    until = (datetime.now(UTC) + timedelta(days=30)).isoformat()

    with TestClient(app, base_url=ISS) as http:

        def admin_post(path: str, body: dict[str, Any]) -> Any:
            r = http.post(path, headers=adm, json=body)
            assert r.status_code in (200, 201), r.text
            return r.json()

        pid_cfg = admin_post(
            "/credential-configurations",
            {
                "credential_type": PID,
                "format": "SD_JWT_VC",
                "required_claims": ["given_name", "family_name"],
                "optional_claims": ["birthdate", "age_over_18"],
                "status_method": "TOKEN_STATUS_LIST",
                "assurance_profile_id": profile_id,
            },
        )["configuration_id"]
        admin_post(f"/credential-configurations/{pid_cfg}:activate", {})
        mdl_cfg = admin_post(
            "/credential-configurations",
            {
                "credential_type": MDL,
                "format": "MDOC",
                "required_claims": ["age_over_18"],
                "optional_claims": ["family_name"],
                "status_method": "NONE",
                "status_mandatory": False,
            },
        )["configuration_id"]
        admin_post(f"/credential-configurations/{mdl_cfg}:activate", {})
        admin_post(
            "/credential-issuer-trust",
            {
                "issuer_identifier": gov.iss,
                "credential_types": [PID],
                "jurisdictions": ["GB"],
                "trust_source": "PINNED",
                "jwks": gov.jwks(),
                "valid_to": until,
            },
        )
        admin_post(
            "/credential-issuer-trust",
            {
                "issuer_identifier": "https://mdl.dvla.example",
                "credential_types": [MDL],
                "trust_source": "PINNED",
                "iaca_certificates": [mdl.iaca_pem()],
                "valid_to": until,
            },
        )
        admin_post(
            "/relying-party-registrations",
            {
                "client_id": "rp-bank",
                "purpose": "kyc",
                "allowed_attributes": ["given_name", "family_name", "age_over_18"],
                "valid_to": until,
            },
        )
        assert (
            http.post(
                "/credential-configurations",
                headers=rp,
                json={
                    "credential_type": "x",
                    "format": "SD_JWT_VC",
                    "required_claims": ["a"],
                    "status_method": "NONE",
                    "status_mandatory": False,
                },
            ).status_code
            == 403
        )

        def request(
            cfg: str = pid_cfg, claims: list[str] | None = None, purpose: str = "kyc"
        ) -> dict[str, Any]:
            r = http.post(
                "/presentations",
                headers=rp,
                json={
                    "configuration_id": cfg,
                    "purpose": purpose,
                    "claims": claims or ["given_name", "family_name"],
                    "jurisdiction": "GB",
                    "subject_principal_id": alice,
                },
            )
            assert r.status_code == 201, r.text
            out = r.json()
            ro = http.get(urlsplit(out["request_uri"]).path)
            assert ro.status_code == 200 and ro.headers["content-type"].startswith(
                "application/oauth-authz-req+jwt"
            )
            _, req = verify_jws(ro.text, keys=ring.jwks()["keys"], typ="oauth-authz-req+jwt")
            return {**out, "req": req}

        def respond(req: dict[str, Any], presentation: str) -> Any:
            return http.post(
                urlsplit(req["response_uri"]).path,
                headers=FORM,
                data={"state": req["state"], "vp_token": json.dumps({"cred1": [presentation]})},
            )

        def sd_presentation(
            req: dict[str, Any],
            issuer: SdJwtIssuer = gov,
            *,
            idx: int = 0,
            nonce: str | None = None,
            aud: str = CLIENT_ID,
            disclose: tuple[str, ...] = ("given_name", "family_name"),
            **extra: Any,
        ) -> str:
            jwt, ds = issuer.issue(
                PID,
                {"given_name": "Erika", "family_name": "Mustermann", "birthdate": "1984-01-26"},
                holder,
                idx=idx,
                **extra,
            )
            return present(
                jwt, [ds[n] for n in disclose], holder, aud=aud, nonce=nonce or req["nonce"]
            )

        grants_before = asyncio.run(grants())

        # minimization and registration (FR-VC-007)
        r = http.post(
            "/presentations",
            headers=rp,
            json={
                "configuration_id": pid_cfg,
                "purpose": "kyc",
                "claims": ["birthdate"],
                "jurisdiction": "GB",
            },
        )
        assert (
            r.status_code == 422 and r.json()["error"]["code"] == "CLAIM_NOT_PERMITTED_FOR_PURPOSE"
        )
        r = http.post(
            "/presentations",
            headers=rp,
            json={
                "configuration_id": pid_cfg,
                "purpose": "marketing",
                "claims": ["given_name"],
                "jurisdiction": "GB",
            },
        )
        assert r.status_code == 403

        # happy path: request object, single-use request_uri, verified presentation, claims once
        first = request()
        req = first["req"]
        assert req["client_id"] == CLIENT_ID and req["response_mode"] == "direct_post"
        assert [c["path"] for c in req["dcql_query"]["credentials"][0]["claims"]] == [
            ["given_name"],
            ["family_name"],
        ]
        assert (
            http.get(urlsplit(first["request_uri"]).path).status_code == 409
        )  # request_uri is single-use
        r = respond(req, sd_presentation(req))
        assert r.status_code == 200, r.text
        r = http.get(f"/presentations/{first['presentation_id']}", headers=rp).json()
        assert r["status"] == "VERIFIED" and r["claims"] == {
            "given_name": "Erika",
            "family_name": "Mustermann",
        }
        assert "ASSURANCE:VERIFIED" in r["reason_codes"]
        assert (
            http.get(f"/presentations/{first['presentation_id']}", headers=rp).json()["claims"]
            is None
        )
        assert (
            http.get(
                f"/presentations/{first['presentation_id']}",
                headers=bearer(admin, "other-rp", "presentation:request"),
            ).status_code
            == 404
        )
        assert respond(req, sd_presentation(req)).status_code == 400  # state already answered

        def rejected(build: Any, code: str) -> None:
            out = request()
            r = respond(out["req"], build(out["req"]))
            assert r.status_code == 400 and r.json()["error_description"] == code, (code, r.text)
            assert (
                http.get(f"/presentations/{out['presentation_id']}", headers=rp).json()["status"]
                == "REJECTED"
            )

        rejected(lambda q: sd_presentation(q, nonce="replayed-nonce"), "HOLDER_BINDING_INVALID")
        # VC-08: a presentation made for another relying party is refused here
        rejected(
            lambda q: sd_presentation(q, aud="https://other-rp.example/verifier"),
            "HOLDER_BINDING_INVALID",
        )
        # VC-11: an expired credential and one that is not valid yet are refused
        rejected(lambda q: sd_presentation(q, exp=int(time.time()) - 86400), "CREDENTIAL_EXPIRED")
        rejected(
            lambda q: sd_presentation(q, nbf=int(time.time()) + 86400), "CREDENTIAL_NOT_YET_VALID"
        )
        rejected(lambda q: sd_presentation(q, rogue), "CREDENTIAL_PROOF_INVALID")
        rejected(lambda q: sd_presentation(q, unknown), "ISSUER_UNTRUSTED")
        rejected(lambda q: sd_presentation(q, disclose=("given_name",)), "CLAIMS_MISSING")
        rejected(
            lambda q: sd_presentation(q, disclose=("given_name", "family_name", "given_name")),
            "SD_JWT_DUPLICATE_DISCLOSURE",
        )
        rejected(lambda q: sd_presentation(q, vct="urn:other"), "CREDENTIAL_TYPE_MISMATCH")
        gov.set(5, 1)
        RELIANCE.invalidate_containing(
            "/"
        )  # an external list is trusted until the reliance SLO; let it pass
        rejected(lambda q: sd_presentation(q, idx=5), "CREDENTIAL_REVOKED")
        gov.set(6, 2)
        RELIANCE.invalidate_containing("/")
        rejected(lambda q: sd_presentation(q, idx=6), "CREDENTIAL_SUSPENDED")
        rejected(
            lambda q: sd_presentation(
                q, status={"status_list": {"idx": 0, "uri": "https://issuer.gov.example/down"}}
            ),
            "CREDENTIAL_STATUS_UNAVAILABLE",
        )  # UNKNOWN never becomes VALID
        # an undisclosed claim stays invisible even though it is in the credential
        out = request()
        assert respond(out["req"], sd_presentation(out["req"])).status_code == 200
        assert (
            "birthdate"
            not in http.get(f"/presentations/{out['presentation_id']}", headers=rp).json()["claims"]
        )

        # ISO mdoc: IACA chain, value digests, device signature over this transaction's transcript
        def mdoc_req() -> dict[str, Any]:
            return request(mdl_cfg, ["age_over_18"])

        def mdoc_response(q: dict[str, Any], doc: Mdoc = mdl, **kw: Any) -> str:
            return doc.device_response(
                {"age_over_18": True, "family_name": "Mustermann"},
                client_id=CLIENT_ID,
                nonce=kw.pop("nonce", q["nonce"]),
                response_uri=q["response_uri"],
                **kw,
            )

        out = mdoc_req()
        assert respond(out["req"], mdoc_response(out["req"])).status_code == 200
        assert http.get(f"/presentations/{out['presentation_id']}", headers=rp).json()[
            "claims"
        ] == {"age_over_18": True}
        for build, code in (
            (lambda q: mdoc_response(q, tamper="age_over_18"), "MDOC_DIGEST_MISMATCH"),
            (lambda q: mdoc_response(q, nonce="other"), "MDOC_SIGNATURE_INVALID"),
            (lambda q: mdoc_response(q, Mdoc()), "MDOC_ISSUER_UNTRUSTED"),
        ):
            out = mdoc_req()
            r = respond(out["req"], build(out["req"]))
            assert r.status_code == 400 and r.json()["error_description"] == code, (code, r.text)

        # mdoc status: a Token Status List CWT signed under the same pinned IACA (mandatory here)
        photo = Mdoc("org.iso.23220.photoid.1")
        status_uri = "https://photoid.example/status/7"
        admin_post(
            "/credential-issuer-trust",
            {
                "issuer_identifier": "https://photoid.example",
                "credential_types": [photo.doc_type],
                "trust_source": "PINNED",
                "iaca_certificates": [photo.iaca_pem()],
                "valid_to": until,
            },
        )
        photo_cfg = admin_post(
            "/credential-configurations",
            {
                "credential_type": photo.doc_type,
                "format": "MDOC",
                "required_claims": ["age_over_18"],
                "status_method": "TOKEN_STATUS_LIST",
                "status_mandatory": True,
            },
        )["configuration_id"]
        admin_post(f"/credential-configurations/{photo_cfg}:activate", {})

        def photo_present(idx: int = 3, **cwt: Any) -> Any:
            routes[status_uri] = httpx.Response(200, content=photo.status_cwt(status_uri, **cwt))
            out = request(photo_cfg, ["age_over_18"])
            body = photo.device_response(
                {"age_over_18": True},
                client_id=CLIENT_ID,
                nonce=out["req"]["nonce"],
                response_uri=out["req"]["response_uri"],
                status=(idx, status_uri),
            )
            return respond(out["req"], body)

        r0 = photo_present()
        assert r0.status_code == 200, r0.text
        photo.set(3, 1)
        RELIANCE.invalidate_containing("/")
        r = photo_present()
        assert r.status_code == 400 and r.json()["error_description"] == "CREDENTIAL_REVOKED"
        assert photo_present(idx=4).status_code == 200  # only that credential
        rogue = Mdoc()
        for cwt in (
            {"signer": (rogue.ds_key, rogue.ds)},  # not under the pinned IACA
            {"typ": "application/statuslist+jwt"},
            {"sub": "https://photoid.example/status/8"},
            {"ttl": timedelta(seconds=-5)},
        ):
            r = photo_present(idx=4, **cwt)
            assert r.status_code == 400, (cwt, r.text)
            assert r.json()["error_description"] == "CREDENTIAL_STATUS_UNAVAILABLE", (cwt, r.text)
        routes[status_uri] = httpx.Response(503)
        out = request(photo_cfg, ["age_over_18"])
        body = photo.device_response(
            {"age_over_18": True},
            client_id=CLIENT_ID,
            nonce=out["req"]["nonce"],
            response_uri=out["req"]["response_uri"],
            status=(4, status_uri),
        )
        assert respond(out["req"], body).json()["error_description"] == (
            "CREDENTIAL_STATUS_UNAVAILABLE"
        )

        # the verified PID raised alice's identity assurance through the verification module
        facts = http.get(f"/principals/{alice}/assurance", headers=bearer(alice, "wallet")).json()
        assert facts["identity_proofing"]["level"] == "IAL-Z2"

        # ---- issuer: an assurance attestation, holder-bound, idempotent, revocable
        att_cfg = admin_post(
            "/credential-configurations",
            {
                "credential_type": "urn:zoiko:assurance:1",
                "format": "SD_JWT_VC",
                "required_claims": ["ial", "verified_claims"],
                "status_method": "TOKEN_STATUS_LIST",
                "issuance_enabled": True,
            },
        )["configuration_id"]
        admin_post(f"/credential-configurations/{att_cfg}:activate", {})
        meta = http.get("/.well-known/jwt-vc-issuer").json()
        assert meta["issuer"] == ISS
        c_nonce = http.post("/credentials/nonce").json()["c_nonce"]
        wallet = LocalSigner("alice-wallet")
        proof = sign_jws(
            wallet,
            {"aud": ISS, "nonce": c_nonce, "iat": int(time.time())},
            typ="openid4vci-proof+jwt",
            extra_header={"jwk": wallet.public_jwk()},
        )
        token = bearer(alice, "alice-wallet", f"credential:{att_cfg}")
        r = http.post(
            "/credentials",
            headers=token,
            json={"credential_configuration_id": att_cfg, "proofs": {"jwt": [proof]}},
        )
        assert r.status_code == 200, r.text
        credential, credential_id = (
            r.json()["credentials"][0]["credential"],
            r.json()["credential_id"],
        )
        r = http.post(
            "/credentials",
            headers=token,
            json={"credential_configuration_id": att_cfg, "proofs": {"jwt": [proof]}},
        )
        assert (
            r.status_code == 400 and r.json()["error_description"] == "ISSUANCE_ALREADY_COMPLETED"
        )
        bad = sign_jws(
            wallet,
            {"aud": ISS, "nonce": "forged", "iat": int(time.time())},
            typ="openid4vci-proof+jwt",
            extra_header={"jwk": wallet.public_jwk()},
        )
        r = http.post(
            "/credentials",
            headers=token,
            json={"credential_configuration_id": att_cfg, "proofs": {"jwt": [bad]}},
        )
        assert r.json()["error"] == "invalid_nonce"
        assert (
            http.post(
                "/credentials",
                headers=bearer(alice, "w", "credential:other"),
                json={"credential_configuration_id": att_cfg, "proofs": {"jwt": [proof]}},
            ).status_code
            == 403
        )

        # Zoiko's own verifier accepts it (trusted, pinned), until it is revoked
        admin_post(
            "/credential-issuer-trust",
            {
                "issuer_identifier": ISS,
                "credential_types": ["urn:zoiko:assurance:1"],
                "trust_source": "PINNED",
                "jwks": ring.jwks(),
                "valid_to": until,
            },
        )
        att_rp = admin_post(
            "/credential-configurations",
            {
                "credential_type": "urn:zoiko:assurance:1",
                "format": "SD_JWT_VC",
                "required_claims": ["ial"],
                "optional_claims": ["verified_claims"],
                "status_method": "TOKEN_STATUS_LIST",
            },
        )["configuration_id"]
        admin_post(f"/credential-configurations/{att_rp}:activate", {})
        admin_post(
            "/relying-party-registrations",
            {
                "client_id": "rp-bank",
                "purpose": "assurance",
                "allowed_attributes": ["ial"],
                "valid_to": until,
            },
        )
        status_uri = None

        def verify_own() -> Any:
            nonlocal status_uri
            body, *_ = credential.split("~", 1)
            _, payload = verify_jws(body, keys=ring.jwks()["keys"], typ="dc+sd-jwt")
            status_uri = payload["status"]["status_list"]["uri"]
            routes[status_uri] = httpx.Response(200, text=http.get(urlsplit(status_uri).path).text)
            out = request(att_rp, ["ial"], purpose="assurance")
            parts = credential.split("~")
            ial = next(
                d for d in parts[1:-1] if json.loads(base64.urlsafe_b64decode(d + "=="))[1] == "ial"
            )
            return respond(
                out["req"],
                present(parts[0], [ial], wallet, aud=CLIENT_ID, nonce=out["req"]["nonce"]),
            )

        assert verify_own().status_code == 200
        assert http.post(f"/credentials/{credential_id}:revoke", headers=rp).status_code == 403
        assert http.post(f"/credentials/{credential_id}:revoke", headers=adm).status_code == 200
        r = verify_own()
        assert r.status_code == 400 and r.json()["error_description"] == "CREDENTIAL_REVOKED"
        assert (
            http.post(f"/credentials/{credential_id}:reinstate", headers=adm).status_code == 409
        )  # terminal
        assert parse_qs(urlsplit(first["authorization_request"]).query)["client_id"] == [CLIENT_ID]

    # billing (CR-025): each credential issued and each presentation checked is exactly one billable event
    async def billed() -> tuple[int, int, int, int]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:

            async def n(sql: str) -> int:
                return int((await c.execute(text(sql))).scalar_one())

            return (
                await n(
                    "SELECT count(*) FROM commercial.billable_event WHERE meter_id = 'VC_ISSUED'"
                ),
                await n("SELECT count(*) FROM cred.credential_record"),
                await n(
                    "SELECT count(*) FROM commercial.billable_event WHERE meter_id = 'VC_PRESENTED'"
                ),
                await n(
                    "SELECT count(*) FROM cred.presentation_record WHERE status IN ('VERIFIED', 'REJECTED')"
                ),
            )

    issued, issued_rows, presented, presented_rows = asyncio.run(billed())
    assert issued >= 1 and issued == issued_rows
    assert presented >= 10 and presented == presented_rows

    # VC-18 / VC-21: verifying a PID, an attestation and an employee-style credential raised assurance and
    # nothing else; the tenant holds exactly the grants it held before any credential flow ran
    assert asyncio.run(grants()) == grants_before

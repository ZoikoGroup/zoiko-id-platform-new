"""POST /v2/identity/credential-verify through the real app and Postgres: the PERSON_IDENTITY dimension from a wallet or eID credential.

The credentials are signed for real by an issuer pinned in the typed trust table, with a real status list behind a fake network, and the holder's proof is
signed for real by the key the credential is bound to. Each case then changes exactly one property.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from conftest import ctx, engine
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_v2_facade_pg import world
from test_vc_verify_pg import World, _trust_ed
from zid_crypto import LocalSigner
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import data_integrity as di

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_holder_binding_di import Holder, b58, presentation

TYPE = "PersonIdentificationData"
URL = "/v2/identity/credential-verify"
CLAIMS = {"family_name": "Eriksson", "given_name": "Anna Maria", "birth_date": "1974-08-12"}


def seed(rt_url: str, w: World, tenant: str, types: list[str]) -> None:
    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await w.trust(c, tenant, types)

    asyncio.run(go())


def test_identity_is_established_only_for_a_trusted_issuer_a_bound_holder_and_a_confirmed_status(
    rt_url: str,
) -> None:
    w = World("ident")
    with world(rt_url) as (http, mine, tenant, _admin):
        http.app.state.credential_transport = httpx.MockTransport(w._network)
        vc = w.issuer.issue(TYPE, {**CLAIMS, "document_number": "L898902C3"}, w.holder)

        def ask(**body) -> dict:
            r = http.post(URL, headers=mine, json=body)
            assert r.status_code == 200, r.text
            return r.json()

        assert http.post(URL, json={"vc_jwt": vc}).status_code == 401

        # nobody is trusted until the owners pin an issuer for this exact credential type
        nobody = ask(vc_jwt=vc, holder_proof=w.proof(vc))
        assert nobody["status"] == "INDETERMINATE"
        assert nobody["reasons"] == ["ISSUER_NOT_TRUSTED_FOR_IDENTITY"]
        assert nobody["person_identity_established"] is False

        seed(rt_url, w, tenant, [TYPE])
        out = ask(vc_jwt=vc, holder_proof=w.proof(vc))
        assert out["status"] == "VERIFIED_LIMITED" and out["person_identity_established"] is True
        assert out["dimension"] == "PERSON_IDENTITY" and out["holder_bound"] is True
        assert out["primary_source_verified"] is False
        assert "LIVE_PERSON_NOT_ASSESSED" in out["reasons"]
        assert out["identity"] == CLAIMS and out["assurance_ceiling"] == "ISSUER_AUTHENTICATED"
        assert "L898902C3" not in str(out), "only the name and birth date leave the platform"

        # a copied credential presented by someone else, and one with no proof of possession at all
        copied = ask(vc_jwt=vc, holder_proof=w.proof(vc, LocalSigner("thief")))
        assert copied["status"] == "INDETERMINATE" and copied["reasons"] == ["HOLDER_NOT_BOUND"]
        assert ask(vc_jwt=vc)["person_identity_established"] is False

        # revoked after issue: a good signature does not rescue it
        revoked = w.issuer.issue(TYPE, CLAIMS, w.holder, idx=7)
        w.issuer.set("revocation", 7)
        gone = ask(vc_jwt=revoked, holder_proof=w.proof(revoked))
        assert gone["status"] == "NOT_VERIFIED" and gone["reasons"] == ["CREDENTIAL_REVOKED"]

        # a type the owners never accepted as evidence of identity
        degree = w.issuer.issue("UniversityDegree", CLAIMS, w.holder)
        refused = ask(vc_jwt=degree, holder_proof=w.proof(degree))
        assert refused["person_identity_established"] is False


def test_the_expected_person_is_checked_and_a_different_one_is_reported_not_accepted(
    rt_url: str,
) -> None:
    w = World("expect")
    with world(rt_url) as (http, mine, tenant, _admin):
        http.app.state.credential_transport = httpx.MockTransport(w._network)
        seed(rt_url, w, tenant, [TYPE])
        vc = w.issuer.issue(TYPE, CLAIMS, w.holder)
        base = {"vc_jwt": vc, "holder_proof": w.proof(vc)}

        def ask(expected: dict) -> dict:
            return http.post(URL, headers=mine, json={**base, "expected": expected}).json()

        same = ask(
            {"family_name": "ERIKSSON", "given_name": "anna maria", "birth_date": "1974-08-12"}
        )
        assert (
            same["status"] == "VERIFIED_LIMITED" and same["expected_person"]["match"] == "MATCHED"
        )

        other = ask({"family_name": "Tanaka", "given_name": "Kenji"})
        assert other["status"] == "NOT_VERIFIED" and other["person_identity_established"] is False
        assert other["expected_person"]["match"] == "NOT_MATCHED"
        assert "EXPECTED_PERSON_NOT_MATCHED" in other["reasons"]

        later = ask({"family_name": "Eriksson", "given_name": "Anna", "birth_date": "1975-08-12"})
        assert later["person_identity_established"] is False
        assert later["expected_person"]["reason"] == "BIRTH_DATE_DIFFERS"


def test_a_json_ld_credential_with_no_status_mechanism_is_never_accepted_as_identity(
    rt_url: str,
) -> None:
    """Holder binding works for a did:key presentation, but an identity credential whose status cannot be confirmed is not acceptance."""
    issuer_key, holder = Ed25519PrivateKey.generate(), Holder("ed25519")
    issuer = "https://eid.registry.example"
    context = ["https://www.w3.org/ns/credentials/v2"]
    doc = {
        "@context": context,
        "type": ["VerifiableCredential", TYPE],
        "issuer": issuer,
        "validFrom": "2026-01-01T00:00:00Z",
        "credentialSubject": {"id": holder.did, **CLAIMS},
    }
    proof = {
        "type": "DataIntegrityProof", "cryptosuite": "eddsa-jcs-2022", "created": "2026-11-12T00:00:00Z",
        "verificationMethod": issuer + "#key-1", "proofPurpose": "assertionMethod",
    }  # fmt: skip
    h = di.hashes_for(
        doc, {**proof, "@context": context}, None, hashlib.sha256, json_canonical=True
    )  # type: ignore[arg-type]
    vc = {
        **doc,
        "proof": {**proof, "@context": context, "proofValue": b58(issuer_key.sign(h.combined))},
    }
    with world(rt_url) as (http, mine, tenant, _admin):

        async def go() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await _trust_ed(c, tenant, issuer, issuer_key, "key-1", [TYPE])

        asyncio.run(go())
        audience = f"openid_federation:{http.app.state.oauth_settings.issuer}"
        created = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        pres = presentation(holder, vc, domain=audience, created=created)
        out = http.post(
            URL, headers=mine, json={"credential": vc, "holder_presentation": pres}
        ).json()
        assert out["holder_bound"] is True, "the did:key presentation did bind the holder"
        assert out["status"] == "INDETERMINATE"
        assert out["reasons"] == ["CREDENTIAL_STATUS_NOT_CONFIRMED"]
        assert out["person_identity_established"] is False


def test_malformed_requests_are_refused(rt_url: str) -> None:
    with world(rt_url) as (http, mine, _tenant, _admin):

        def post(payload: dict):
            return http.post(URL, headers=mine, json=payload)

        assert post({}).status_code == 422
        assert post({"vc_jwt": "x" * 30, "credential": {"@context": []}}).status_code == 422
        assert post({"credential": {"a": 1}, "holder_proof": "x" * 30}).status_code == 422
        assert post({"credential": {"a": 1}, "unknown": True}).status_code == 422

"""Standalone verification of signed credentials as evidence (GID-050, CR-069).

GLB-06  a copied valid credential presented without the holder's key is not bound to the presenter
GLB-07  a good signature from an issuer not trusted for this credential type: integrity yes, authenticity no
GLB-23  a revoked credential with a good signature is rejected
GLB-36  a signed credential is never a primary-source confirmation or a license; an EDC seal is refused
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from conftest import ctx, engine
from softcred import VcJwtIssuer, present_vp
from zid_crypto import KeyRing, LocalSigner, sign_jws
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import vc_verify
from zid_platform.modules.credential.application.verifier import VerifierSettings
from zid_platform.modules.credential.domain.credentials import CredentialError
from zid_platform.modules.credential.domain.reliance import RELIANCE
from zid_platform.modules.credential.infrastructure import credential_store as store

ISS = "https://id.zoiko.test"
BADGE = "OpenBadgeCredential"


def uid() -> str:
    return str(uuid.uuid4())


class World:
    """One issuer, one holder, a verifier, and a status-list network, per test."""

    def __init__(self, name: str) -> None:
        self.issuer = VcJwtIssuer(f"https://{name}.registry.example")
        self.holder = LocalSigner(f"holder-{name}")
        self.ring = KeyRing(LocalSigner("verifier-1"))
        self.settings = VerifierSettings(ISS, self.ring, httpx.MockTransport(self._network))

    def _network(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for purpose in ("revocation", "suspension"):
            if url == self.issuer.status_url(purpose):
                return httpx.Response(200, text=self.issuer.status_list(purpose))
        return httpx.Response(404)

    def credential(self, ctype: str = BADGE) -> str:
        return self.issuer.issue(ctype, {"achievement": "BSc"}, self.holder)

    def proof(self, vc_jwt: str, holder: LocalSigner | None = None) -> str:
        return present_vp(vc_jwt, holder or self.holder, aud=self.settings.client_id, nonce="n-1")

    async def trust(self, conn, tenant: str, types: list[str] | None = None) -> None:
        await store.create_trust(
            conn,
            tenant,
            {
                "issuer_identifier": self.issuer.iss,
                "credential_types": types or [BADGE],
                "trust_source": "PINNED",
                "jwks": self.issuer.jwks(),
                "valid_to": datetime.now(UTC) + timedelta(days=30),
            },
        )


async def verify(w: World, conn, vc: str, proof: str | None) -> dict[str, Any]:
    out = await vc_verify.verify_vc(
        conn, w.settings, vc_jwt=vc, holder_proof=proof, jurisdiction=None, now=datetime.now(UTC)
    )
    return out.to_contract()


def run(coro):
    RELIANCE._entries.clear()
    return asyncio.run(coro)


def test_a_trusted_bound_active_badge_is_accepted_as_a_claim_and_nothing_more(rt_url: str) -> None:
    w, tenant = World("good"), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await w.trust(c, tenant)
            vc = w.credential()
            r = await verify(w, c, vc, w.proof(vc))
            assert r["accepted_as_claim"] and r["reason_codes"] == []
            assert r["kind"] == "OPEN_BADGE" and r["credential_status"] == "ACTIVE"
            assert r["assurance_tier"] == "ISSUER_AUTHENTICATED"  # provenance, not truth
            assert r["primary_source_verified"] is False and r["statutory_license"] is False
            assert "does not prove" in " ".join(r["explanation"]["limitations"]).lower()

    run(go())


def test_glb_06_without_the_holders_key_the_credential_is_not_bound_to_the_presenter(
    rt_url: str,
) -> None:
    w, tenant = World("copied"), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await w.trust(c, tenant)
            vc = w.credential()
            none = await verify(w, c, vc, None)
            assert not none["accepted_as_claim"] and "HOLDER_BINDING_FAILED" in none["reason_codes"]
            thief = await verify(
                w, c, vc, w.proof(vc, LocalSigner("mallory"))
            )  # signed with the wrong key
            assert not thief["accepted_as_claim"] and thief["holder_bound"] is False
            other = w.credential()
            swapped = await verify(
                w, c, vc, w.proof(other)
            )  # a proof enveloping a different credential
            assert not swapped["holder_bound"]

    run(go())


def test_glb_07_an_issuer_not_trusted_for_this_type_gives_integrity_only(rt_url: str) -> None:
    w, tenant = World("wrongtype"), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await w.trust(c, tenant, types=["SomethingElseCredential"])
            vc = w.credential()
            r = await verify(w, c, vc, w.proof(vc))
            assert r["integrity"] and not r["accepted_as_claim"]
            assert (
                r["issuer_authenticity"] == "NOT_CONFIRMED"
                and r["assurance_tier"] == "DOCUMENT_INTEGRITY"
            )
            assert "ISSUER_NOT_IN_REGISTRY" in r["reason_codes"]

    run(go())


def test_an_unknown_issuer_or_a_forged_signature_proves_nothing(rt_url: str) -> None:
    w, tenant = World("unknown"), uid()
    rogue = VcJwtIssuer(w.issuer.iss, LocalSigner("rogue"))  # same name, different key

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            vc = w.credential()
            nothing_on_file = await verify(w, c, vc, w.proof(vc))
            assert (
                not nothing_on_file["integrity"]
                and nothing_on_file["assurance_tier"] == "SELF_DECLARED"
            )
            await w.trust(c, tenant)
            forged = rogue.issue(BADGE, {"achievement": "PhD"}, w.holder)
            r = await verify(w, c, forged, w.proof(forged))
            assert (
                not r["integrity"]
                and not r["accepted_as_claim"]
                and "PROOF_INVALID" in r["reason_codes"]
            )

    run(go())


def test_glb_23_a_revoked_credential_with_a_good_signature_is_rejected(rt_url: str) -> None:
    w, tenant = World("revoked"), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await w.trust(c, tenant)
            vc = w.issuer.issue(BADGE, {"achievement": "BSc"}, w.holder, idx=7)
            w.issuer.set("revocation", 7)
            r = await verify(w, c, vc, w.proof(vc))
            assert (
                r["integrity"]
                and r["credential_status"] == "REVOKED"
                and not r["accepted_as_claim"]
            )
            assert "CREDENTIAL_REVOKED" in r["reason_codes"]

    run(go())


def test_an_unreadable_status_list_is_not_acceptance(rt_url: str) -> None:
    w, tenant = World("nostatus"), uid()
    w.settings = VerifierSettings(ISS, w.ring, httpx.MockTransport(lambda r: httpx.Response(500)))

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await w.trust(c, tenant)
            vc = w.credential()
            r = await verify(w, c, vc, w.proof(vc))
            assert r["credential_status"] == "UNKNOWN" and not r["accepted_as_claim"]
            assert "STATUS_UNKNOWN" in r["reason_codes"]

    run(go())


def test_glb_36_a_jws_secured_eu_digital_credential_is_a_claim_never_a_qualified_seal_or_a_licence(
    rt_url: str,
) -> None:
    w, tenant = World("edc"), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await w.trust(c, tenant, types=["EuropeanDigitalCredential"])
            edc = w.credential("EuropeanDigitalCredential")
            r = await verify(w, c, edc, w.proof(edc))
            assert r["kind"] == "EDC" and r["integrity"] is True, r["reason_codes"]
            assert r["assurance_tier"] == "ISSUER_AUTHENTICATED"
            assert r["qualified_seal"] == "NOT_ASSESSED", "a JWS is not a qualified e-seal"
            assert r["primary_source_verified"] is False and r["statutory_license"] is False
            # a claim worth accepting: signed by a trusted issuer, bound to its holder, status read from the issuer. A rescinded
            # degree is the status endpoint's job (GLB-36), and the primary source is a separate check
            assert r["credential_status"] == "ACTIVE" and r["accepted_as_claim"] is True
            assert r["reason_codes"] == []

            # an issuer that is not trusted for this credential type: genuine signature, integrity only (GLB-07)
            other = World("edc2")
            await other.trust(c, tenant, types=["SomethingElse"])
            wrong = other.credential("EuropeanDigitalCredential")
            r2 = await verify(other, c, wrong, other.proof(wrong))
            assert "ISSUER_NOT_IN_REGISTRY" in r2["reason_codes"] and not r2["accepted_as_claim"]

            for junk in (
                "not-a-jwt-at-all-but-long-enough",
                sign_jws(w.holder, {"a": 1}, typ="JWT"),
            ):
                with pytest.raises(CredentialError) as e:
                    await verify(w, c, junk, None)
                assert e.value.code == "VC_MALFORMED"

    run(go())


# ---------------------------------------------------------------- Data Integrity proofs (eddsa-rdfc-2022), offline

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(data) - len(data.lstrip(b"\x00"))) + out


def _contexts():
    from pathlib import Path

    from zid_platform.modules.credential.application import data_integrity as di

    # plus one TEST-ONLY context that defines the EDC type, standing in for the real Europass context, which is not bundled
    edc_test_context = {
        "@context": {
            "@version": 1.1,
            "EuropeanDigitalCredential": "https://data.europa.eu/snb/model/context#EuropeanDigitalCredential",
        }
    }
    return di.BundledContexts(
        Path(__file__).resolve().parents[3] / "config" / "jsonld-contexts",
        {EDC_TEST_CONTEXT: edc_test_context},
    )


EDC_TEST_CONTEXT = "https://example.test/contexts/edc"


def _di_credential(issuer: str, ctype: str = BADGE) -> dict[str, Any]:
    return {
        "@context": [
            "https://www.w3.org/ns/credentials/v2",
            "https://purl.imsglobal.org/spec/ob/v3p0/context-3.0.3.json",
        ],
        "id": "urn:uuid:" + uid(),
        "type": ["VerifiableCredential", ctype],
        "issuer": {"id": issuer, "type": ["Profile"], "name": "Example University"},
        "validFrom": "2026-01-01T00:00:00Z",
        "name": "BSc Chemistry",
        "credentialSubject": {
            "type": ["AchievementSubject"],
            "achievement": {
                "id": "urn:uuid:" + uid(),
                "type": ["Achievement"],
                "name": "BSc Chemistry",
                "description": "A degree",
                "criteria": {"narrative": "Complete the programme"},
            },
        },
    }


def _sign_di(doc: dict[str, Any], key, vm: str) -> dict[str, Any]:
    from zid_platform.modules.credential.application import data_integrity as di

    proof = {
        "type": "DataIntegrityProof",
        "cryptosuite": "eddsa-rdfc-2022",
        "created": "2026-11-12T00:00:00Z",
        "verificationMethod": vm,
        "proofPurpose": "assertionMethod",
    }
    h = di.hashes_for(doc, {**proof, "@context": doc["@context"]}, _contexts())
    return {**doc, "proof": {**proof, "proofValue": "z" + _b58(key.sign(h.combined))}}


def _ed_jwks(key, kid: str) -> dict[str, Any]:
    import base64

    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": x, "kid": kid, "use": "sig"}]}


async def _trust_ed(conn, tenant: str, issuer: str, key, kid: str, types: list[str]) -> None:
    await store.create_trust(
        conn,
        tenant,
        {
            "issuer_identifier": issuer,
            "credential_types": types,
            "trust_source": "PINNED",
            "jwks": _ed_jwks(key, kid),
            "valid_to": datetime.now(UTC) + timedelta(days=30),
        },
    )


async def _verify_di(w: World, conn, vc: dict[str, Any]) -> dict[str, Any]:
    out = await vc_verify.verify_vc_di(
        conn, w.settings, _contexts(), vc=vc, jurisdiction=None, now=datetime.now(UTC)
    )
    return out.to_contract()


def test_a_data_integrity_credential_from_a_trusted_issuer_is_integrity_and_authenticity_but_not_a_claim_without_a_holder(
    rt_url: str,
) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    w, tenant = World("di"), uid()
    key = Ed25519PrivateKey.generate()
    issuer = "https://di.registry.example"
    vm = issuer + "#key-1"

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await _trust_ed(c, tenant, issuer, key, "key-1", [BADGE])
            good = _sign_di(_di_credential(issuer), key, vm)
            r = await _verify_di(w, c, good)
            assert r["kind"] == "OPEN_BADGE" and r["integrity"] is True
            assert (
                r["issuer_authenticity"] == "CONFIRMED"
                and r["assurance_tier"] == "ISSUER_AUTHENTICATED"
            )
            assert r["primary_source_verified"] is False and r["statutory_license"] is False
            # no holder presentation: it is never accepted as a claim on its own
            assert r["holder_bound"] is False and r["accepted_as_claim"] is False
            assert "HOLDER_BINDING_FAILED" in r["reason_codes"]

            # a changed credential, a different key, and a key the issuer never pinned all fail by name
            forged = {**good, "name": "MD Medicine"}
            f = await _verify_di(w, c, forged)
            assert f["integrity"] is False and "SIGNATURE_INVALID" in f["reason_codes"]
            other = _sign_di(_di_credential(issuer), Ed25519PrivateKey.generate(), vm)
            assert (await _verify_di(w, c, other))["integrity"] is False
            unpinned = _sign_di(_di_credential(issuer), key, issuer + "#key-unknown")
            assert (await _verify_di(w, c, unpinned))["integrity"] is False

    run(go())


def test_a_data_integrity_credential_from_an_issuer_not_trusted_for_its_type_is_integrity_only(
    rt_url: str,
) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    w, tenant = World("di2"), uid()
    key, issuer = Ed25519PrivateKey.generate(), "https://di2.registry.example"

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await _trust_ed(c, tenant, issuer, key, "k", ["SomethingElse"])
            r = await _verify_di(w, c, _sign_di(_di_credential(issuer), key, issuer + "#k"))
            assert r["integrity"] is True and r["issuer_authenticity"] == "NOT_CONFIRMED"
            assert "ISSUER_NOT_IN_REGISTRY" in r["reason_codes"] and not r["accepted_as_claim"]
            # an issuer nobody registered at all
            nobody = _sign_di(
                _di_credential("https://nobody.example"), key, "https://nobody.example#k"
            )
            assert (await _verify_di(w, c, nobody))["integrity"] is False

    run(go())


def test_a_context_that_is_not_bundled_is_refused_and_nothing_is_fetched(rt_url: str) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    w, tenant = World("di3"), uid()
    key, issuer = Ed25519PrivateKey.generate(), "https://di3.registry.example"

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await _trust_ed(c, tenant, issuer, key, "k", [BADGE])
            doc = _sign_di(_di_credential(issuer), key, issuer + "#k")
            doc["@context"] = [*doc["@context"], "https://attacker.example/ctx"]
            r = await _verify_di(w, c, doc)
            assert r["integrity"] is False and "CONTEXT_NOT_BUNDLED" in r["reason_codes"]

    run(go())


def test_a_data_integrity_edc_is_sealed_by_its_proof_and_never_a_qualified_seal(
    rt_url: str,
) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    w, tenant = World("di4"), uid()
    key, issuer = Ed25519PrivateKey.generate(), "https://di4.registry.example"

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await _trust_ed(c, tenant, issuer, key, "k", ["EuropeanDigitalCredential"])
            doc = _di_credential(issuer, "EuropeanDigitalCredential")
            doc["@context"] = [
                *doc["@context"],
                EDC_TEST_CONTEXT,
            ]  # an EDC must carry a context that defines its own type
            r = await _verify_di(w, c, _sign_di(doc, key, issuer + "#k"))
            assert r["kind"] == "EDC" and r["integrity"] is True, r["reason_codes"]
            assert r["qualified_seal"] == "NOT_ASSESSED"

            # without that context its type is undefined, so nothing about the credential is signed in any meaningful sense: refused
            bare = _di_credential(issuer, "EuropeanDigitalCredential")
            refused = await _verify_di(w, c, _sign_di(bare, key, issuer + "#k"))
            assert refused["integrity"] is False
            assert "PROPERTIES_NOT_DEFINED_BY_CONTEXT" in refused["reason_codes"]
            assert r["primary_source_verified"] is False and r["statutory_license"] is False

    run(go())


# ---------------------------------------------------------------- the second suite: ecdsa-rdfc-2019 (P-256), against a pinned EC key


def _sign_di_ecdsa(doc: dict[str, Any], key, vm: str) -> dict[str, Any]:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from zid_platform.modules.credential.application import data_integrity as di

    proof = {
        "type": "DataIntegrityProof",
        "cryptosuite": "ecdsa-rdfc-2019",
        "created": "2026-11-12T00:00:00Z",
        "verificationMethod": vm,
        "proofPurpose": "assertionMethod",
    }
    h = di.hashes_for(doc, {**proof, "@context": doc["@context"]}, _contexts())
    r, s = decode_dss_signature(key.sign(h.combined, ec.ECDSA(hashes.SHA256())))
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return {**doc, "proof": {**proof, "proofValue": "z" + _b58(raw)}}


def _ec_jwks(key, kid: str) -> dict[str, Any]:
    import base64

    nums = key.public_key().public_numbers()

    def b64(n: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()

    return {
        "keys": [
            {
                "kty": "EC",
                "crv": "P-256",
                "x": b64(nums.x),
                "y": b64(nums.y),
                "kid": kid,
                "use": "sig",
            }
        ]
    }


def test_a_p256_data_integrity_credential_verifies_against_the_issuers_pinned_ec_key(
    rt_url: str,
) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    w, tenant = World("di5"), uid()
    key = ec.generate_private_key(ec.SECP256R1())
    issuer = "https://di5.registry.example"
    vm = issuer + "#ec-1"

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await store.create_trust(
                c,
                tenant,
                {
                    "issuer_identifier": issuer,
                    "credential_types": [BADGE],
                    "trust_source": "PINNED",
                    "jwks": _ec_jwks(key, "ec-1"),
                    "valid_to": datetime.now(UTC) + timedelta(days=30),
                },
            )
            good = _sign_di_ecdsa(_di_credential(issuer), key, vm)
            r = await _verify_di(w, c, good)
            assert r["integrity"] is True and r["issuer_authenticity"] == "CONFIRMED", r[
                "reason_codes"
            ]
            assert (
                r["assurance_tier"] == "ISSUER_AUTHENTICATED"
                and r["primary_source_verified"] is False
            )

            assert (await _verify_di(w, c, {**good, "name": "MD Medicine"}))["integrity"] is False
            other = _sign_di_ecdsa(
                _di_credential(issuer), ec.generate_private_key(ec.SECP256R1()), vm
            )
            assert (await _verify_di(w, c, other))["integrity"] is False

            # an Ed25519 key pinned under the same name is never tried for an ECDSA proof
            tenant2 = uid()
            async with tenant_transaction(eng, ctx(tenant2)) as c2:
                ed = Ed25519PrivateKey.generate()
                await _trust_ed(c2, tenant2, issuer, ed, "ec-1", [BADGE])
                assert (await _verify_di(w, c2, good))["integrity"] is False

    run(go())


def test_a_data_integrity_credential_is_bound_only_by_a_presentation_from_the_key_it_was_issued_to(
    rt_url: str,
) -> None:
    import sys
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
    from test_holder_binding_di import Holder, presentation

    w, tenant = World("dibind"), uid()
    key = Ed25519PrivateKey.generate()
    issuer = "https://bind.registry.example"
    vm = issuer + "#key-1"
    holder, thief = Holder("ed25519"), Holder("ed25519")

    def issued_to(subject: str) -> dict[str, Any]:
        doc = _di_credential(issuer)
        doc["credentialSubject"] = {**doc["credentialSubject"], "id": subject}
        return _sign_di(doc, key, vm)

    def present(who, vc: dict[str, Any], **over: Any) -> dict[str, Any]:
        stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return presentation(who, vc, domain=w.settings.client_id, created=stamp, **over)

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await _trust_ed(c, tenant, issuer, key, "key-1", [BADGE])
            vc = issued_to(holder.did)

            async def verify(presented: dict[str, Any] | None, credential: dict[str, Any]):
                out = await vc_verify.verify_vc_di(
                    c,
                    w.settings,
                    _contexts(),
                    vc=credential,
                    jurisdiction=None,
                    now=datetime.now(UTC),
                    holder_presentation=presented,
                )
                return out.to_contract()

            bound = await verify(present(holder, vc), vc)
            assert bound["integrity"] is True and bound["holder_bound"] is True
            assert bound["primary_source_verified"] is False and bound["statutory_license"] is False

            # no presentation, someone else's key, and a presentation for another credential are never binding
            assert (await verify(None, vc))["holder_bound"] is False
            assert (await verify(present(thief, vc), vc))["holder_bound"] is False
            other = issued_to(holder.did)
            assert (await verify(present(holder, vc), other))["holder_bound"] is False

            # binding never rescues a credential whose own proof fails
            forged = {**vc, "name": "MD Medicine"}
            r = await verify(present(holder, forged), forged)
            assert r["integrity"] is False and r["holder_bound"] is False

    run(go())

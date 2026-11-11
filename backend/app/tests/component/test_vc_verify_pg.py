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


def test_glb_36_an_eu_digital_credential_seal_is_refused_not_guessed_and_junk_is_malformed(
    rt_url: str,
) -> None:
    w, tenant = World("edc"), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await w.trust(c, tenant, types=["EuropeanDigitalCredential"])
            edc = w.credential("EuropeanDigitalCredential")
            with pytest.raises(CredentialError) as e:
                await verify(w, c, edc, w.proof(edc))
            assert e.value.code == "EDC_SEAL_VALIDATION_NOT_AVAILABLE"
            for junk in (
                "not-a-jwt-at-all-but-long-enough",
                sign_jws(w.holder, {"a": 1}, typ="JWT"),
            ):
                with pytest.raises(CredentialError) as e:
                    await verify(w, c, junk, None)
                assert e.value.code == "VC_MALFORMED"

    run(go())

"""EUDI Wallet requests through the real OID4VP verifier (ZID-RCG-003 RCG-G11, P1 "interoperability conformance"; CR-080).

The verifier's own `create` builds and signs the request object; these tests check that object against the interoperability profile, and that a
request to an EUDI Wallet needs Zoiko's registration as a wallet-relying party in that Member State. A non-EUDI request is untouched. The class of a
presented attestation comes from official-register attestations at the issuance date.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from pack_test_support import captured
from zid_crypto import KeyRing, LocalSigner, b64url
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import eudi_gate
from zid_platform.modules.credential.application import verifier as vf
from zid_platform.modules.credential.domain import eudi
from zid_platform.modules.credential.domain.credentials import CredentialError
from zid_platform.modules.credential.infrastructure import credential_store as store
from zid_platform.modules.verification.domain.attestation import Scope, Snapshot
from zid_platform.modules.verification.domain.certification import CertStatus
from zid_platform.modules.verification.infrastructure import attestation_store

ISS = "https://id.zoiko.test"
PID = "eu.europa.ec.eudi.pid.1"
MDL = "org.iso.18013.5.1.mDL"
LEAF = (
    b"zoiko-wallet-relying-party-access-certificate"  # stands for the DER of the access certificate
)
LEAF_HASH = b64url(hashlib.sha256(LEAF).digest())


def uid() -> str:
    return str(uuid.uuid4())


def registration(**over: Any) -> eudi.Registration:
    base = eudi.Registration(
        member_state="DE",
        registration_id="DE-RP-1",
        status=eudi.RegistrationStatus.ACTIVE,
        valid_from=datetime.now(UTC) - timedelta(days=1),
        valid_until=None,
        access_certificate_sha256=LEAF_HASH,
        registration_certificate_ref="DE-RC-1",
        intended_uses={
            "KYC_ONBOARDING": {PID: frozenset({"family_name", "given_name", "birth_date"})}
        },
    )
    return replace(base, **over)


@pytest.fixture
def eudi_mode() -> Iterator[Any]:
    """Set the switch and the registrations for one test, and put them back after it."""
    mp = pytest.MonkeyPatch()
    regs: list[eudi.Registration] = []
    mp.setattr(eudi_gate, "eudi_registrations", lambda: tuple(regs))
    before = eudi_gate.mode()

    def set_(mode: str, *rs: eudi.Registration) -> None:
        eudi_gate.configure(mode)
        regs[:] = list(rs)

    yield set_
    eudi_gate.configure(before)
    mp.undo()


async def _seed(c: Any, tenant: str, doc_type: str, claims: list[str]) -> str:
    cfg = await store.create_configuration(
        c,
        tenant,
        {
            "credential_type": doc_type,
            "format": "MDOC",
            "adapter_version": vf.PINNED_ADAPTERS["MDOC"],
            "required_claims": claims[:1],
            "optional_claims": claims[1:],
            "status_method": "NONE",
            "status_mandatory": False,
        },
    )
    await store.activate_configuration(c, cfg)
    await store.create_registration(
        c,
        tenant,
        {
            "client_id": "rp-eudi",
            "purpose": "kyc onboarding",
            "allowed_attributes": claims,
            "valid_to": datetime.now(UTC) + timedelta(days=30),
        },
    )
    return cfg


def _create(
    rt_url: str,
    *,
    doc_type: str = PID,
    claims: list[str] | None = None,
    x5c: bool = True,
    mode: str = "direct_post.jwt",
    ask: list[str] | None = None,
    member_state: str = "DE",
) -> dict[str, Any]:
    tenant = uid()
    claims = claims or ["family_name", "given_name", "birth_date", "nationality"]
    settings = vf.VerifierSettings(
        ISS,
        KeyRing(LocalSigner("verifier-1")),
        x5c=(base64.b64encode(LEAF).decode(),) if x5c else (),
    )

    async def go() -> dict[str, Any]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            cfg = await _seed(c, tenant, doc_type, claims)
            out = await vf.create(
                c,
                settings,
                tenant_id=tenant,
                client_id="rp-eudi",
                configuration_id=cfg,
                purpose="kyc onboarding",
                claims=ask or ["family_name", "birth_date"],
                subject_principal_id=None,
                jurisdiction=member_state,
                now=datetime.now(UTC),
                response_mode=mode,
            )
            jwt = await vf.request_object(c, out["presentation_id"], datetime.now(UTC))
            return out | {"request_jwt": jwt}

    return asyncio.run(go())


def _parts(jwt: str) -> tuple[dict[str, Any], dict[str, Any]]:
    h, p, _ = jwt.split(".")
    return eudi_gate._part(h), eudi_gate._part(p)


def test_p1_the_verifier_s_own_signed_request_object_conforms_to_the_profile(
    rt_url: str, eudi_mode: Any
) -> None:
    eudi_mode("enforce", registration())
    out = _create(rt_url)
    header, payload = _parts(out["request_jwt"])
    assert eudi.conformance(header, payload) == []
    assert (
        payload["client_id"] == f"x509_hash:{LEAF_HASH}"
    )  # the registered access certificate signs it


def test_a_plaintext_response_or_no_certificate_is_not_conformant(
    rt_url: str, eudi_mode: Any
) -> None:
    eudi_mode("enforce", registration())
    with pytest.raises(CredentialError) as e:
        _create(rt_url, mode="direct_post")
    assert e.value.args[0].startswith("WALLET_REQUEST_NOT_CONFORMANT")
    eudi_mode("enforce", registration(access_certificate_sha256="x"))
    with pytest.raises(CredentialError) as e:
        _create(rt_url, x5c=False)  # no certificate: the client id is not x509-based
    assert e.value.args[0].startswith("ACCESS_CERTIFICATE_MISSING")


def test_no_registration_in_the_member_state_refuses_in_enforce_and_reports_in_shadow(
    rt_url: str, eudi_mode: Any
) -> None:
    eudi_mode("enforce")
    with pytest.raises(CredentialError) as e:
        _create(rt_url)
    assert e.value.args[0].startswith("WALLET_RP_NOT_REGISTERED")
    eudi_mode("shadow")
    with captured("zid.regulatory") as logs:
        assert _create(rt_url)["presentation_id"]
    assert any(
        "eudi wallet request would refuse (shadow)" in m and "WALLET_RP_NOT_REGISTERED" in m
        for m in logs
    )


def test_asking_for_an_attribute_outside_the_registration_is_refused(
    rt_url: str, eudi_mode: Any
) -> None:
    eudi_mode("enforce", registration())
    with pytest.raises(CredentialError) as e:
        _create(
            rt_url, ask=["family_name", "nationality"]
        )  # the tenant allows it; Zoiko's wallet registration does not
    assert e.value.args[0].startswith("ATTRIBUTE_NOT_REGISTERED")
    with pytest.raises(CredentialError) as e:
        _create(rt_url, member_state="FR")  # registered in DE only
    assert e.value.args[0].startswith("WALLET_RP_NOT_REGISTERED")


def test_a_request_for_a_non_eudi_credential_is_untouched(rt_url: str, eudi_mode: Any) -> None:
    eudi_mode("enforce")  # no registration at all
    out = _create(
        rt_url,
        doc_type=MDL,
        claims=["age_over_18"],
        ask=["age_over_18"],
        x5c=False,
        mode="direct_post",
    )
    assert out["presentation_id"]


def test_qeaa_needs_qualified_status_at_issuance_from_an_official_register(rt_url: str) -> None:
    issuer = f"QTSP-{uid()[:8]}"
    now = datetime.now(UTC)
    snap = Snapshot(
        snapshot_id=f"s-{uid()[:10]}",
        scope=Scope(issuer, eudi_gate.QEAA_SCHEME, "DE", "QEAA"),
        scheme_version="eIDAS2",
        certificate_ref="TL-1",
        component_only=False,
        status=CertStatus.ACTIVE,
        valid_from=date(2025, 1, 1),
        valid_until=None,
        register_url="https://eidas.ec.europa.eu/efda/tl-browser/#/screen/tl/DE",
        register_entry_id="DE-TSP-7",
        cab=None,
        fetched_at=now - timedelta(days=2),
        proof_hash="f" * 64,
        evidence_locator="vault:tl/de",
        recorded_by="ops",
        recorded_at=now - timedelta(days=2),
    )

    async def go() -> tuple[Any, Any, Any]:
        async with engine(rt_url) as eng, eng.begin() as c:
            before = await eudi_gate.classify_presented(
                c,
                attestation_type="eu.europa.ec.eudi.diploma.1",
                issuer_entity=issuer,
                issuer_country="DE",
                issued_at=now - timedelta(days=1),
            )
            await attestation_store.record(c, snap, now=now)
            after = await eudi_gate.classify_presented(
                c,
                attestation_type="eu.europa.ec.eudi.diploma.1",
                issuer_entity=issuer,
                issuer_country="DE",
                issued_at=now - timedelta(days=1),
            )
            long_ago = await eudi_gate.classify_presented(
                c,
                attestation_type="eu.europa.ec.eudi.diploma.1",
                issuer_entity=issuer,
                issuer_country="DE",
                issued_at=now - timedelta(days=40),
            )
            return before, after, long_ago

    before, after, long_ago = asyncio.run(go())
    assert before == (eudi.AttestationClass.EAA, ("QEAA_STATUS_NOT_ESTABLISHED",))
    assert after == (eudi.AttestationClass.QEAA, ())
    assert (
        long_ago[0] is eudi.AttestationClass.EAA
    )  # nobody had read the register then: not qualified at that issuance date


def test_a_presentation_records_its_class_judged_at_the_credential_s_issuance_time(
    rt_url: str,
) -> None:
    """The verifier records the class with the decision basis (stored, not in the public response): QEAA only when the issuer was
    qualified on the date the credential was issued; with no issuance time the class cannot be qualified."""
    issuer = f"QTSP-{uid()[:8]}"
    now = datetime.now(UTC)
    snap = Snapshot(
        snapshot_id=f"s-{uid()[:10]}",
        scope=Scope(issuer, eudi_gate.QEAA_SCHEME, "DE", "QEAA"),
        scheme_version="eIDAS2",
        certificate_ref="TL-1",
        component_only=False,
        status=CertStatus.ACTIVE,
        valid_from=date(2025, 1, 1),
        valid_until=None,
        register_url="https://eidas.ec.europa.eu/efda/tl-browser/#/screen/tl/DE",
        register_entry_id="DE-TSP-7",
        cab=None,
        fetched_at=now - timedelta(days=2),
        proof_hash="e" * 64,
        evidence_locator="vault:tl/de",
        recorded_by="ops",
        recorded_at=now - timedelta(days=2),
    )
    diploma = "eu.europa.ec.eudi.diploma.1"

    def verified(issued: Any) -> vf.Verified:
        basis = {"credential_type": diploma, "issued_at": vf._issued_at(issued)}
        return vf.Verified(issuer, {}, "VALID", True, basis)

    async def go() -> tuple[Any, Any, Any]:
        async with engine(rt_url) as eng, eng.begin() as c:
            await attestation_store.record(c, snap, now=now)
            fresh = await vf._with_eudi_class(
                c, verified(int((now - timedelta(days=1)).timestamp())), diploma, "DE"
            )
            old = await vf._with_eudi_class(
                c, verified((now - timedelta(days=40)).isoformat()), diploma, "DE"
            )
            unknown = await vf._with_eudi_class(c, verified(None), diploma, "DE")
            return fresh, old, unknown

    fresh, old, unknown = asyncio.run(go())
    assert (fresh.basis["eudi_class"], fresh.basis["eudi_class_reasons"]) == ("QEAA", [])
    assert (
        old.basis["eudi_class"] == "EAA"
        and "QEAA_STATUS_NOT_ESTABLISHED" in old.basis["eudi_class_reasons"]
    )
    assert (
        unknown.basis["eudi_class"] == "EAA"
        and "ISSUANCE_TIME_UNKNOWN" in unknown.basis["eudi_class_reasons"]
    )
    assert fresh.basis["credential_type"] == diploma  # the rest of the basis is kept as it was


def test_issuance_times_are_read_from_each_format() -> None:
    assert vf._issued_at(0) == "1970-01-01T00:00:00+00:00"
    assert vf._issued_at("2027-01-02T03:04:05Z") == "2027-01-02T03:04:05+00:00"
    assert (
        vf._issued_at("not a date") is None
        and vf._issued_at(None) is None
        and vf._issued_at(True) is None
    )

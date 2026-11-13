"""DTV-07 certified provider registry, DTV-14 / DTV-15 no self-asserted designation, GLB-28 scope-aware blocking.

GLB-28  a provider of a UK certified check loses its status: that pack is blocked, unrelated packs are not
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from zid_platform.modules.verification.domain import certification as cert
from zid_platform.modules.verification.domain.service_packs import (
    FeatureState,
    PackError,
    PackRegistry,
    ServicePack,
    resolve,
    sealed,
)
from zid_platform.modules.verification.infrastructure.certification_registry import (
    certification_registry,
)

D = date(2026, 11, 11)
NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)


def _rec(**over) -> cert.Certification:
    base = {
        "record_id": "r1",
        "entity": "partner-a",
        "scheme": "UK_DVS",
        "scheme_version": "1.0",
        "country": "GB",
        "service_code": "idv.certified",
        "certificate_ref": "CERT-1",
        "valid_from": date(2026, 1, 1),
        "valid_until": date(2027, 1, 1),
    }
    base.update(over)
    return cert.Certification(**base)


def _reg(*records: cert.Certification) -> cert.CertRegistry:
    return cert.CertRegistry("1", records)


def _held(reg, **over):
    args = {
        "entity": "partner-a",
        "scheme": "UK_DVS",
        "country": "GB",
        "service_code": "idv.certified",
        "on": D,
    }
    args.update(over)
    return cert.holds(reg, **args)


def test_the_shipped_registry_lists_no_certification_so_zoiko_claims_none():
    reg = certification_registry()
    assert reg.records == ()
    for scheme, service in (("UK_DVS", "idv.certified"), ("EU_QTSP", "sign.qualified")):
        with pytest.raises(cert.CertificationError) as e:
            claim = cert.CertClaim(cert.ZOIKO_ENTITY, scheme, "GB", service)
            cert.refuse_unbacked_claim(reg, claim, on=D)
        assert e.value.code == "CERTIFICATION_CLAIM_UNBACKED"


def test_a_record_in_force_backs_exactly_its_own_entity_scheme_place_and_service():
    reg = _reg(_rec())
    assert _held(reg).record_id == "r1"
    for over in (
        {"entity": cert.ZOIKO_ENTITY},  # the partner's certification is the partner's
        {"scheme": "EU_QTSP"},
        {"country": "DE"},
        {"service_code": "sign.qualified"},
    ):
        assert _held(reg, **over) is None, over


@pytest.mark.parametrize(
    "over",
    [
        {"status": cert.CertStatus.SUSPENDED},
        {"status": cert.CertStatus.WITHDRAWN},
        {"status": cert.CertStatus.EXPIRED},
        {"valid_until": date(2026, 11, 10)},
        {"valid_from": date(2026, 11, 12)},
    ],
)
def test_a_record_not_in_force_is_nothing(over):
    assert _held(_reg(_rec(**over))) is None


def test_the_last_day_counts_and_the_next_does_not():
    reg = _reg(_rec(valid_until=D))
    assert _held(reg) is not None
    assert _held(reg, on=date(2026, 11, 12)) is None


def test_a_component_certification_never_supports_a_full_service_claim():
    reg = _reg(_rec(component_only=True))
    assert _held(reg, full_service=True) is None
    assert _held(reg, full_service=False) is not None
    full = cert.CertClaim("partner-a", "UK_DVS", "GB", "idv.certified", full_service=True)
    part = cert.CertClaim("partner-a", "UK_DVS", "GB", "idv.certified", full_service=False)
    with pytest.raises(cert.CertificationError):
        cert.refuse_unbacked_claim(reg, full, on=D)
    assert cert.refuse_unbacked_claim(reg, part, on=D)


def test_zoiko_can_claim_only_with_a_record_in_its_own_name():
    claim = cert.CertClaim(cert.ZOIKO_ENTITY, "UK_DVS", "GB", "idv.certified")
    own = _reg(_rec(entity=cert.ZOIKO_ENTITY, record_id="z1"))
    assert cert.refuse_unbacked_claim(own, claim, on=D).record_id == "z1"
    with pytest.raises(cert.CertificationError):  # a partner's record does not let Zoiko say it
        cert.refuse_unbacked_claim(_reg(_rec()), claim, on=D)


def test_scheme_satisfied_and_blocked_scopes():
    suspended = _rec(
        record_id="r2",
        country="DE",
        service_code="sign.qualified",
        scheme="EU_QTSP",
        status=cert.CertStatus.SUSPENDED,
    )
    reg = _reg(_rec(), suspended)
    kw = {"country": "GB", "service_code": "idv.certified", "on": D}
    assert cert.scheme_satisfied(reg, scheme=None, entities=[], **{**kw, "service_code": "x"})
    assert cert.scheme_satisfied(reg, scheme="UK_DVS", entities={"partner-a"}, **kw)
    assert not cert.scheme_satisfied(reg, scheme="UK_DVS", entities=set(), **kw)
    needs = {("GB", "idv.certified"): "UK_DVS", ("DE", "sign.qualified"): "EU_QTSP"}
    assert cert.blocked_scopes(reg, needs, on=D) == {("DE", "sign.qualified")}


# --- GLB-28: a lost certification blocks that pack only -----------------------------------------------------------------


def _pack(pack_id, country, service, scheme):
    return sealed(
        ServicePack(
            pack_id=pack_id,
            version=1,
            country_iso2=country,
            service_code=service,
            feature_state=FeatureState.LIVE_PROVEN,
            data_residency_region="uk",
            allowed_processing_regions=("uk",),
            legal_basis_ref="l",
            source_registry_ids=("s",),
            retention_policy_id="r",
            human_review_rule_id="h",
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            legal_owner="l",
            product_owner="p",
            qa_gate_id="q",
            applicable_regulatory_scheme=scheme,
        )
    )


def test_glb_28_losing_a_certification_blocks_that_pack_and_not_the_unrelated_ones():
    uk = _pack("uk-certified", "GB", "idv.certified", "UK_DVS")
    plain = _pack("global-doc", "GB", "doc.check", None)
    packs = PackRegistry("1", (uk, plain))

    def pick(service, certs):
        return resolve(
            packs,
            country="GB",
            service_code=service,
            now=NOW,
            environment="production",
            certifications=certs,
        ).pack_id

    assert pick("idv.certified", _reg(_rec())) == "uk-certified"
    lost = _reg(_rec(status=cert.CertStatus.SUSPENDED))
    with pytest.raises(PackError) as e:
        pick("idv.certified", lost)
    assert e.value.code == "PACK_UNAVAILABLE" and "certification not in force" in str(e.value)
    assert pick("doc.check", lost) == "global-doc"  # an unrelated pack is untouched
    with pytest.raises(PackError):
        pick("idv.certified", _reg())  # no record at all
    # without a registry the scheme is simply not checked
    unchecked = resolve(
        packs, country="GB", service_code="idv.certified", now=NOW, environment="production"
    )
    assert unchecked.pack_id == "uk-certified"


def test_a_pack_with_no_scheme_needs_no_certification():
    p = _pack("plain", "GB", "doc.check", None)
    got = resolve(
        PackRegistry("1", (p,)),
        country="GB",
        service_code="doc.check",
        now=NOW,
        environment="production",
        certifications=_reg(),
    )
    assert got.pack_id == "plain" and replace(p, applicable_regulatory_scheme=None) == p


def test_the_loader_refuses_malformed_rows():
    ok = {
        "record_id": "r",
        "entity": "e",
        "scheme": "S",
        "scheme_version": "1",
        "country": "gb",
        "service_code": "x",
        "certificate_ref": "c",
        "valid_from": "2026-01-01",
    }
    assert cert.parse({"certifications": [ok]}).records[0].country == "GB"
    for bad in (
        {"certifications": [{**ok, "status": "GREAT"}]},
        {"certifications": [{"record_id": "r"}]},
        {"certifications": [ok, ok]},
        {"certifications": [{**ok, "valid_until": "2025-01-01"}]},
    ):
        with pytest.raises(cert.CertificationError) as e:
            cert.parse(bad)
        assert e.value.code == "CERTIFICATION_REGISTRY_INVALID"


# ------------------------------------------------------------------------- register reconciliation and the suspension proof (RCG-G02)


def _entry(rec: cert.Certification, **over) -> cert.RegisterEntry:
    base = {
        "entity": rec.entity,
        "scheme": rec.scheme,
        "country": rec.country,
        "service_code": rec.service_code,
        "certificate_ref": rec.certificate_ref,
        "status": "REGISTERED",
        "valid_until": rec.valid_until,
    }
    base.update(over)
    return cert.RegisterEntry(**base)


def _rec_run(reg, entries):
    return cert.reconcile(
        reg,
        entries,
        source_id="uk-dvs-register",
        snapshot_sha256="ab" * 32,
        checked_at="2026-11-11T12:00:00Z",
    )


def test_a_register_that_agrees_changes_nothing_and_proves_nothing() -> None:
    r = _rec()
    out, proofs = _rec_run(_reg(r), [_entry(r)])
    assert out.records == (r,) and proofs == ()


def test_a_suspension_on_the_register_stops_the_claim_at_once_with_a_proof() -> None:
    r = _rec()
    out, proofs = _rec_run(_reg(r), [_entry(r, status="SUSPENDED")])
    assert cert.holds(
        _reg(r),
        entity="partner-a",
        scheme="UK_DVS",
        country="GB",
        service_code="idv.certified",
        on=D,
    )
    assert (
        cert.holds(
            out,
            entity="partner-a",
            scheme="UK_DVS",
            country="GB",
            service_code="idv.certified",
            on=D,
        )
        is None
    )
    (p,) = proofs
    assert (p.previous_status, p.effective_status, p.reason) == (
        "ACTIVE",
        "SUSPENDED",
        "REGISTER_SUSPENDED",
    )
    assert p.source_id == "uk-dvs-register" and p.snapshot_sha256 == "ab" * 32 and p.checked_at


def test_a_withdrawal_on_the_register_withdraws_the_record() -> None:
    r = _rec()
    out, proofs = _rec_run(_reg(r), [_entry(r, status="WITHDRAWN")])
    assert (
        out.records[0].status is cert.CertStatus.WITHDRAWN
        and proofs[0].reason == "REGISTER_WITHDRAWN"
    )


def test_a_record_the_register_does_not_list_is_not_relied_on() -> None:
    r = _rec()
    out, proofs = _rec_run(
        _reg(r), []
    )  # an empty or unrelated snapshot is silence, not confirmation
    assert (
        out.records[0].status is cert.CertStatus.SUSPENDED and proofs[0].reason == "NOT_IN_REGISTER"
    )
    other = _entry(r, certificate_ref="SOMEONE-ELSES")
    assert _rec_run(_reg(r), [other])[0].records[0].status is cert.CertStatus.SUSPENDED


def test_an_earlier_end_date_on_the_register_shortens_the_record() -> None:
    r = _rec()
    out, proofs = _rec_run(_reg(r), [_entry(r, valid_until=date(2026, 12, 1))])
    assert (
        out.records[0].valid_until == date(2026, 12, 1)
        and proofs[0].reason == "REGISTER_EXPIRES_EARLIER"
    )
    assert cert.in_force(out.records[0], date(2026, 11, 30)) and not cert.in_force(
        out.records[0], date(2026, 12, 2)
    )


def test_a_later_end_date_on_the_register_never_lengthens_the_record() -> None:
    r = _rec()
    out, proofs = _rec_run(_reg(r), [_entry(r, valid_until=date(2030, 1, 1))])
    assert out.records == (r,) and proofs == ()


def test_an_unknown_register_status_is_not_certified() -> None:
    r = _rec()
    out, proofs = _rec_run(_reg(r), [_entry(r, status="PENDING_REVIEW")])
    assert (
        out.records[0].status is cert.CertStatus.SUSPENDED
        and proofs[0].reason == "REGISTER_STATUS_UNRECOGNIZED"
    )


def test_a_fetch_never_reinstates_a_record_a_person_suspended() -> None:
    r = _rec(status=cert.CertStatus.SUSPENDED)
    out, proofs = _rec_run(_reg(r), [_entry(r)])  # the register says registered
    assert out.records == (r,) and proofs == ()


def test_register_entries_for_providers_we_hold_no_record_for_are_ignored() -> None:
    r = _rec()
    stranger = _entry(r, entity="partner-z", certificate_ref="Z-1")
    out, proofs = _rec_run(_reg(r), [_entry(r), stranger])
    assert out.records == (r,) and proofs == ()


def test_the_downgrade_blocks_exactly_the_scope_whose_certification_was_lost() -> None:
    uk = _rec()
    eu = _rec(
        record_id="r2",
        scheme="EU_QTSP",
        country="DE",
        service_code="sig.qualified",
        certificate_ref="C-2",
    )
    out, _ = _rec_run(_reg(uk, eu), [_entry(uk, status="SUSPENDED"), _entry(eu)])
    required = {("GB", "idv.certified"): "UK_DVS", ("DE", "sig.qualified"): "EU_QTSP"}
    assert cert.blocked_scopes(out, required, on=D) == {("GB", "idv.certified")}

"""WF06 eligibility (CR-113): default off, signed policy, region, lawful basis, a current identity claim, entitlement and a usable source (QA-036, ENG-01, ENG-02)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from zid_platform.modules.screening.domain import eligibility as e
from zid_platform.modules.screening.domain.coverage import Klass, Tier

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
S, P, M = Klass.SANCTIONS, Klass.PEP_RCA, Klass.ADVERSE_MEDIA


def policy(**kw) -> e.MarketPolicy:
    base = {
        "market": "GB",
        "version": "gb-1",
        "purposes": frozenset({"KYB"}),
        "regions": frozenset({"eu-west"}),
        "subject_types": frozenset({e.SubjectType.BUSINESS, e.SubjectType.UBO}),
        "classes": {S: Tier.OFFICIAL, P: Tier.LICENSED},
        "legal_basis": "Money Laundering Regulations 2017 reg. 27",
        "signed_by": ("legal-1", "aml-1"),
        "valid_from": NOW - timedelta(days=30),
        "valid_to": None,
    }
    base.update(kw)
    return e.MarketPolicy(**base)


def request(**kw) -> e.Request:
    base = {
        "tenant_id": "t1",
        "market": "GB",
        "purpose": "KYB",
        "region": "eu-west",
        "subject_type": e.SubjectType.BUSINESS,
        "identity": e.IdentityClaim("COMPANY_REGISTRY_MATCHED", NOW - timedelta(days=5), "t1"),
        "requested": frozenset({S, P}),
    }
    base.update(kw)
    return e.Request(**base)


def check(req=None, pol="default", entitled=(S, P), sources=(S, P)):
    return e.check(
        req or request(),
        policy() if pol == "default" else pol,
        entitled=entitled,
        usable_sources=sources,
        now=NOW,
    )


def test_a_signed_policy_a_current_identity_and_entitled_classes_make_every_class_available() -> (
    None
):
    r = check()
    assert r.eligible and r.blocked == () and r.available() == {S, P}
    tiers = {c.klass: c.tier for c in r.classes}
    assert tiers == {S: Tier.OFFICIAL, P: Tier.LICENSED} and r.policy_version == "gb-1"


def test_no_policy_for_the_market_is_a_refusal_never_a_default() -> None:
    for pol in (None, policy(market="US")):
        r = check(pol=pol)
        assert (
            not r.eligible
            and r.blocked == ("NO_POLICY_FOR_MARKET",)
            and r.available() == frozenset()
        )


def test_an_unsigned_not_yet_effective_or_expired_policy_is_not_in_force() -> None:
    for pol in (
        policy(signed_by=("legal-1",)),
        policy(signed_by=("legal-1", "legal-1")),
        policy(valid_from=NOW + timedelta(days=1)),
        policy(valid_to=NOW - timedelta(seconds=1)),
    ):
        r = check(pol=pol)
        assert (
            "POLICY_NOT_IN_FORCE" in r.blocked and not r.eligible and r.available() == frozenset()
        )


def test_purpose_region_subject_type_and_legal_basis_each_block_the_whole_request() -> None:
    cases = [
        (request(purpose="MARKETING"), policy(), "PURPOSE_NOT_APPROVED"),
        (request(region="us-east"), policy(), "REGION_NOT_APPROVED"),
        (request(subject_type=e.SubjectType.INDIVIDUAL), policy(), "SUBJECT_TYPE_NOT_APPROVED"),
        (request(), policy(legal_basis=None), "NO_LEGAL_BASIS"),
        (request(), policy(legal_basis="  "), "NO_LEGAL_BASIS"),
    ]
    for req, pol, code in cases:
        r = check(req, pol)
        assert code in r.blocked and not r.eligible and r.available() == frozenset(), code


def test_a_subject_without_a_current_identity_is_pending_never_a_pass() -> None:
    r = check(request(identity=None))
    assert r.pending_identity and "PENDING_IDENTITY" in r.blocked and not r.eligible
    stale = check(request(identity=e.IdentityClaim("X", NOW - timedelta(days=400), "t1")))
    assert "IDENTITY_CLAIM_STALE" in stale.blocked and stale.pending_identity
    other = check(request(identity=e.IdentityClaim("X", NOW, "t2")))
    assert (
        "IDENTITY_CLAIM_NOT_THIS_TENANTS" in other.blocked
    )  # QA-036: another tenant's claim never carries over


def test_a_class_outside_the_policy_the_entitlement_or_without_a_source_is_unavailable_with_its_reason() -> (
    None
):
    r = check(request(requested=frozenset({S, P, M})), entitled=(S,), sources=(S, P))
    by = {c.klass: c for c in r.classes}
    assert by[S].available and r.eligible  # one class is enough to proceed
    assert by[P].reasons == ("CLASS_NOT_ENTITLED",)
    assert by[M].reasons == ("CLASS_NOT_IN_POLICY", "CLASS_NOT_ENTITLED", "NO_USABLE_SOURCE")
    assert by[P].tier is None and by[M].tier is None
    none = check(entitled=(), sources=())
    assert not none.eligible and none.available() == frozenset()


def test_a_blocked_request_makes_every_class_unavailable_with_the_blocking_reason_as_well() -> None:
    r = check(request(region="us-east"))
    assert all("REGION_NOT_APPROVED" in c.reasons and not c.available for c in r.classes)


def test_every_reason_has_words_and_an_unknown_code_reads_as_itself() -> None:
    codes = {
        "NO_POLICY_FOR_MARKET", "POLICY_NOT_IN_FORCE", "PURPOSE_NOT_APPROVED", "REGION_NOT_APPROVED", "SUBJECT_TYPE_NOT_APPROVED",
        "NO_LEGAL_BASIS", "PENDING_IDENTITY", "IDENTITY_CLAIM_NOT_THIS_TENANTS", "IDENTITY_CLAIM_STALE", "CLASS_NOT_IN_POLICY",
        "CLASS_NOT_ENTITLED", "NO_USABLE_SOURCE",
    }  # fmt: skip
    for c in codes:
        assert e.explain(c) != c and e.explain(c).endswith(".")
    assert e.explain("SOMETHING_NEW") == "SOMETHING_NEW"
    assert replace(policy(), version="x").version == "x"

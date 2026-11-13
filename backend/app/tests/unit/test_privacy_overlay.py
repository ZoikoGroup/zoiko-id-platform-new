"""Country privacy, cross-border and biometric overlay (ZID-RCG-003 RCG-G09; T31, T32; CR-079)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from zid_platform.modules.verification.domain import privacy_overlay as po


def overlay(**over: Any) -> po.Overlay:
    o = po.Overlay(
        country="GB",
        version=1,
        home_regions=frozenset({"UK"}),
        transfer_regions={"EU": "UK_ADEQUACY_REGULATIONS"},
        provider_regions={"local-idv": "UK", "eu-idv": "EU", "us-idv": "US"},
        biometrics=po.Biometrics.CONSENT_REQUIRED,
        dpia_required=True,
        dpia_ref="DPIA-2027-01",
        lawful_basis="contract",
    )
    o = replace(o, **over)
    if "approvals" not in over:
        digest = po.overlay_hash(o)
        o = replace(o, approvals={r: po.Approval(f"REF-{r}", digest) for r in po.APPROVER_ROLES})
    return o


def route(
    o: po.Overlay | None,
    provider: str = "local-idv",
    biometric: bool = False,
    consent: bool | None = None,
) -> po.RouteDecision:
    return po.route(o, provider=provider, biometric=biometric, consent=consent)


def test_a_home_region_provider_without_biometrics_is_allowed() -> None:
    d = route(overlay())
    assert d.allowed and (d.region, d.mechanism) == ("UK", None)


def test_another_region_is_allowed_only_with_its_lawful_transfer_mechanism() -> None:
    d = route(overlay(), provider="eu-idv")
    assert d.allowed and (d.region, d.mechanism) == ("EU", "UK_ADEQUACY_REGULATIONS")


def test_t32_a_region_without_a_lawful_transfer_is_denied() -> None:
    d = route(overlay(), provider="us-idv")
    assert not d.allowed and d.reasons == ("CROSS_BORDER_ROUTE_DENIED",)


def test_a_provider_whose_region_is_not_recorded_is_refused_never_assumed() -> None:
    assert route(overlay(), provider="unknown-idv").reasons == ("PROVIDER_REGION_UNKNOWN",)
    assert route(overlay(), provider=None).reasons == ("PROVIDER_REGION_UNKNOWN",)  # type: ignore[arg-type]


def test_no_overlay_is_its_own_refusal_never_a_pass() -> None:
    assert route(None).reasons == ("PRIVACY_OVERLAY_MISSING",)


@pytest.mark.parametrize(
    ("biometrics", "consent", "expect"),
    [
        (po.Biometrics.ALLOWED, None, ()),
        (po.Biometrics.CONSENT_REQUIRED, True, ()),
        (po.Biometrics.CONSENT_REQUIRED, False, ("BIOMETRIC_CONSENT_REQUIRED",)),
        (
            po.Biometrics.CONSENT_REQUIRED,
            None,
            ("BIOMETRIC_CONSENT_REQUIRED",),
        ),  # not looked up counts as not given
        (
            po.Biometrics.PROHIBITED,
            True,
            ("BIOMETRIC_NOT_PERMITTED_IN_COUNTRY",),
        ),  # consent cannot override the law
    ],
)
def test_the_country_decides_biometrics_and_a_profile_cannot_widen_it(
    biometrics: po.Biometrics, consent: bool | None, expect: tuple[str, ...]
) -> None:
    assert route(overlay(biometrics=biometrics), biometric=True, consent=consent).reasons == expect


def test_without_biometrics_the_biometric_rule_does_not_matter() -> None:
    assert route(overlay(biometrics=po.Biometrics.PROHIBITED), biometric=False).allowed


def test_the_overlay_needs_both_approvals_of_its_exact_hash_and_its_dpia() -> None:
    o = overlay()
    one = replace(o, approvals={"dpo": o.approvals["dpo"]})
    assert "approval_missing:legal" in route(one).reasons
    edited = replace(o, biometrics=po.Biometrics.ALLOWED)  # loosened after both signed
    assert {"approval_hash_mismatch:dpo", "approval_hash_mismatch:legal"} <= set(
        route(edited).reasons
    )
    assert "DPIA_MISSING" in route(overlay(dpia_ref=None)).reasons
    assert route(overlay(dpia_required=False, dpia_ref=None)).allowed


def test_every_reason_is_listed_at_once() -> None:
    o = overlay(biometrics=po.Biometrics.PROHIBITED, dpia_ref=None)
    assert set(route(o, provider="us-idv", biometric=True).reasons) == {
        "DPIA_MISSING",
        "CROSS_BORDER_ROUTE_DENIED",
        "BIOMETRIC_NOT_PERMITTED_IN_COUNTRY",
    }


PROFILES = [
    {
        "profile_id": "bio",
        "status": "ACTIVE",
        "jurisdictions": ["GB"],
        "biometric_jurisdictions": ["GB"],
    },
    {
        "profile_id": "doc-only",
        "status": "ACTIVE",
        "jurisdictions": ["GB", "IE"],
        "biometric_jurisdictions": [],
    },
    {
        "profile_id": "draft-doc",
        "status": "DRAFT",
        "jurisdictions": ["GB"],
        "biometric_jurisdictions": [],
    },
    {
        "profile_id": "ie-only",
        "status": "ACTIVE",
        "jurisdictions": ["IE"],
        "biometric_jurisdictions": [],
    },
]


def test_t31_a_refused_biometric_route_names_the_compliant_alternates() -> None:
    d = route(overlay(biometrics=po.Biometrics.PROHIBITED), biometric=True)
    assert [p["profile_id"] for p in po.alternates(d, PROFILES, country="GB")] == ["doc-only"]


def test_no_alternate_is_offered_when_something_other_than_biometrics_blocks() -> None:
    d = route(overlay(biometrics=po.Biometrics.PROHIBITED), provider="us-idv", biometric=True)
    assert (
        po.alternates(d, PROFILES, country="GB") == []
    )  # another profile would not fix the region
    assert (
        po.alternates(route(overlay()), PROFILES, country="GB") == []
    )  # allowed: nothing to offer


DOC = {
    "overlays": [
        {
            "country": "gb",
            "version": 2,
            "home_regions": ["uk"],
            "transfer_regions": {"eu": "ADEQUACY"},
            "provider_regions": {"local-idv": "uk"},
            "biometrics": "consent_required",
            "dpia": {"required": True, "reference": "DPIA-1"},
        },
        {"country": "GB", "version": 1, "home_regions": ["UK"], "biometrics": "ALLOWED"},
    ]
}


def test_parse_keeps_the_highest_version_and_normalises_codes() -> None:
    o = po.parse(DOC)["GB"]
    assert (
        o.version == 2
        and o.home_regions == frozenset({"UK"})
        and o.transfer_regions == {"EU": "ADEQUACY"}
    )
    assert (
        o.provider_regions == {"local-idv": "UK"}
        and o.biometrics is po.Biometrics.CONSENT_REQUIRED
        and o.dpia_ref == "DPIA-1"
    )


@pytest.mark.parametrize(
    "row",
    [
        {"country": "Great Britain", "version": 1, "home_regions": ["UK"], "biometrics": "ALLOWED"},
        {"country": "GB", "version": 1, "home_regions": ["UK"], "biometrics": "SOMETIMES"},
        {
            "country": "GB",
            "version": 1,
            "home_regions": ["UK"],
            "transfer_regions": {"UK": "X"},
            "biometrics": "ALLOWED",
        },
        {"country": "GB", "version": 1, "home_regions": ["u k"], "biometrics": "ALLOWED"},
        {"country": "GB", "home_regions": ["UK"], "biometrics": "ALLOWED"},
    ],
)
def test_a_malformed_file_is_refused_whole(row: dict[str, Any]) -> None:
    with pytest.raises(po.OverlayError) as e:
        po.parse({"overlays": [row]})
    assert e.value.code == "PRIVACY_OVERLAYS_INVALID"


def test_the_shipped_file_lists_no_country() -> None:
    from zid_platform.modules.verification.infrastructure.privacy_overlays import privacy_overlays

    assert privacy_overlays() == {}

"""Capability bands C1-C5 (ZID-ENG-DEC-008 P0 tests, policy level): the matrix is the record's, the protected baseline cannot be paywalled,
a plan is never a role or a hold-clearing, a downgrade deletes nothing, and the Commercial Estate and this record are reconciled in the open."""

from __future__ import annotations

import pytest
from zid_platform.modules.commercial.domain.bands import (
    BANDS,
    BASELINE_IDS,
    PRESERVED,
    BandError,
    Cell,
    EstateView,
    Feature,
    build,
    decide_feature,
    downgrade_impact,
    lowest_band,
    reconcile,
    state_of,
)
from zid_platform.modules.commercial.infrastructure.bands import (
    band_matrix,
    meter_register,
    parse,
    parse_meters,
)

M = band_matrix()
PLANS = M.plan_of_band
ENFORCED = parse({"version": M.version, "bands": PLANS, "band_matrix_enforcement": "ENFORCE", "features": [
    {"id": f.feature_id, "section": f.section, "feature": f.name, "bands": {b: c.value for b, c in f.cells.items()}, "note": f.note, "catalogue": f.catalogue}
    for f in M.features.values()
]})  # fmt: skip
OK = EstateView(True, None)


def row(fid: str) -> Feature:
    return M.features[fid]


def cells(fid: str) -> str:
    return "".join(row(fid).cells[b].value for b in BANDS)


# ---- the matrix is the record's ----------------------------------------------------------------------------------------


def test_the_matrix_has_the_records_forty_features_in_three_groups() -> None:
    sections = [f.section for f in M.features.values()]
    assert (
        len(M.features) == 40
        and sections.count("baseline") == 8
        and sections.count("standard") == 12
        and sections.count("advanced") == 20
    )
    assert tuple(M.plan_of_band) == BANDS and M.enforcement == "REPORT_ONLY"
    assert set(BASELINE_IDS) == {i for i, f in M.features.items() if f.section == "baseline"}


@pytest.mark.parametrize(
    ("fid", "bands"),
    [
        ("baseline.mfa-passkeys-session-protection", "MMMMM"),
        ("standard.hosted-authentication-oidc-core", "IIIII"),
        ("standard.custom-domain-hosted-brand-theme", "-IIII"),
        ("standard.service-identities-m2m-management", "-IIII"),
        ("advanced.custom-organization-roles-permission-models", "--III"),
        ("advanced.enterprise-sso-saml-oidc-federation", "--III"),
        ("advanced.scim-automated-provisioning", "---II"),
        ("advanced.bulk-identity-company-verification", "---II"),
        ("advanced.advanced-agent-governance-attestations", "---II"),
        ("advanced.dedicated-isolation-private-deployment", "----A"),
        ("advanced.sovereign-service-configuration", "----A"),
        ("advanced.byok-hyok-and-specialized-key-custody", "----A"),
    ],
)
def test_rows_read_exactly_as_the_record_prints_them(fid: str, bands: str) -> None:
    assert cells(fid) == bands


def test_a_matrix_that_paywalls_the_baseline_or_breaks_inheritance_does_not_load() -> None:
    def feats(
        over: dict[str, str] | None = None, extra: list[Feature] | None = None
    ) -> list[Feature]:
        out = []
        for f in M.features.values():
            c = f.cells
            if over and f.feature_id in over:
                c = {b: Cell(v) for b, v in zip(BANDS, over[f.feature_id], strict=True)}
            out.append(Feature(f.feature_id, f.section, f.name, c, f.note, f.catalogue))
        return out + (extra or [])

    args = (M.version, dict(PLANS), "REPORT_ONLY")
    assert build(*args, feats())
    with pytest.raises(BandError, match="mandatory"):  # MFA behind a paid band
        build(*args, feats({"baseline.mfa-passkeys-session-protection": "-MMMM"}))
    with pytest.raises(BandError, match="mandatory"):  # sanctions holds made optional
        build(*args, feats({"baseline.mandatory-sanctions-trust-holds": "IIIII"}))
    with pytest.raises(
        BandError, match="keep"
    ):  # a higher band that loses what the lower one includes
        build(*args, feats({"standard.hosted-authentication-oidc-core": "II-II"}))
    with pytest.raises(BandError, match="mandatory"):  # a non-baseline feature declared mandatory
        build(*args, feats({"advanced.scim-automated-provisioning": "MMMMM"}))
    with pytest.raises(BandError, match="missing|mandatory"):
        build(
            *args,
            [f for f in feats() if f.feature_id != "baseline.mandatory-audit-evidence-integrity"],
        )
    with pytest.raises(BandError, match="five different plans"):
        build(M.version, {b: "LAUNCH" for b in BANDS}, "REPORT_ONLY", feats())
    with pytest.raises(BandError, match="REPORT_ONLY or ENFORCE"):
        build(M.version, dict(PLANS), "MAYBE", feats())
    with pytest.raises(BandError, match="duplicate"):
        build(*args, feats(extra=[next(iter(M.features.values()))]))


# ---- the decision ---------------------------------------------------------------------------------------------------------


def test_acceptance_01_the_protected_baseline_is_never_gated_by_a_plan_a_bill_or_a_suspension() -> (
    None
):
    for fid in BASELINE_IDS:
        for plan in PLANS.values():
            for state in ("ACTIVE", "SUSPENDED", "PAST_DUE"):
                d = decide_feature(
                    ENFORCED,
                    fid,
                    plan,
                    estate=EstateView(False, "NOT_IN_PLAN"),
                    subscription_state=state,
                )
                assert d.allowed and d.state is not None and d.state.value == "BASELINE", (
                    fid,
                    plan,
                    state,
                )


def test_acceptance_02_a_lower_band_cannot_reach_a_higher_band_feature() -> None:
    for fid in (
        "advanced.scim-automated-provisioning",
        "advanced.advanced-agent-governance-attestations",
    ):
        for band in ("C1", "C2", "C3"):
            d = decide_feature(ENFORCED, fid, PLANS[band], estate=OK)
            assert (
                d.outcome == "DENY" and d.reasons == ("NOT_IN_BAND",) and d.upgrade_band == "C4"
            ), (fid, band)
        for band in ("C4", "C5"):
            assert decide_feature(ENFORCED, fid, PLANS[band], estate=OK).allowed


def test_acceptance_03_a_top_plan_buys_no_way_past_a_region_a_hold_or_a_missing_route() -> None:
    top = PLANS["C5"]
    fid = "advanced.enterprise-sso-saml-oidc-federation"
    assert decide_feature(ENFORCED, fid, top, estate=OK).allowed
    assert decide_feature(ENFORCED, fid, top, estate=OK, region_ok=False).reasons == (
        "NOT_AVAILABLE_IN_REGION",
    )
    assert decide_feature(
        ENFORCED, fid, top, estate=OK, subscription_state="SUSPENDED"
    ).reasons == ("SUSPENDED",)
    assert decide_feature(ENFORCED, fid, top, estate=OK, coverage_ok=False).outcome == "HOLD"


def test_acceptance_11_an_out_of_coverage_or_unknown_route_is_a_hold_never_a_pass() -> None:
    fid = "standard.human-id-proofing-orchestration"
    for kw in ({"coverage_ok": False}, {"coverage_ok": None}, {"region_ok": None}):
        d = decide_feature(M, fid, PLANS["C3"], estate=OK, **kw)  # type: ignore[arg-type]
        assert d.outcome == "HOLD" and not d.allowed


def test_an_add_on_needs_a_live_contract_and_a_plan_alone_is_not_one() -> None:
    fid = "advanced.dedicated-isolation-private-deployment"
    assert state_of(row(fid), "C5").value == "ADD_ON"
    assert decide_feature(M, fid, PLANS["C5"], estate=None).reasons == ("ADD_ON_REQUIRED",)
    assert decide_feature(M, fid, PLANS["C5"], estate=None, contract_active=True).allowed
    assert decide_feature(M, fid, PLANS["C4"], estate=None, contract_active=True).reasons == (
        "NOT_IN_BAND",
    )  # a contract is not a band


def test_report_only_defers_to_the_estate_and_enforce_adds_the_band_gate() -> None:
    fid = "advanced.scim-automated-provisioning"  # the band matrix says C4; the Estate sells SCIM as a Launch add-on
    allowed_by_estate = decide_feature(M, fid, PLANS["C2"], estate=OK)
    assert allowed_by_estate.allowed and allowed_by_estate.divergent  # reported, not hidden
    enforced = decide_feature(ENFORCED, fid, PLANS["C2"], estate=OK)
    assert enforced.outcome == "DENY" and enforced.divergent
    refused = decide_feature(M, fid, PLANS["C5"], estate=EstateView(False, "NOT_IN_PLAN"))
    assert (
        refused.outcome == "DENY" and refused.reasons == ("NOT_IN_PLAN",) and refused.divergent
    )  # the Estate said no where the band says yes


def test_unknown_features_and_unmapped_plans_never_allow() -> None:
    assert decide_feature(M, "x.nothing", "LAUNCH", estate=OK).reasons == ("UNKNOWN_FEATURE",)
    d = decide_feature(M, "standard.hosted-authentication-oidc-core", "NO_SUCH_PLAN", estate=OK)
    assert d.outcome == "HOLD" and d.reasons == ("PLAN_NOT_MAPPED_TO_A_BAND",)


def test_acceptance_12_every_decision_pins_the_catalogue_version_and_repeats_exactly() -> None:
    a = decide_feature(M, "standard.custom-domain-hosted-brand-theme", PLANS["C2"], estate=OK)
    b = decide_feature(M, "standard.custom-domain-hosted-brand-theme", PLANS["C2"], estate=OK)
    assert a == b and a.catalog_version == M.version == "ZID-ENG-DEC-008-v1.0" and a.band == "C2"


def test_the_upgrade_message_names_the_first_band_that_has_the_feature() -> None:
    assert lowest_band(row("advanced.scim-automated-provisioning")) == "C4"
    assert lowest_band(row("advanced.dedicated-isolation-private-deployment")) == "C5"
    assert lowest_band(row("baseline.mandatory-audit-evidence-integrity")) == "C1"


# ---- reconciliation with the Commercial Estate ---------------------------------------------------------------------------


ESTATE = {
    "DEVELOPER": {
        "saml_sso": "NONE",
        "scim": "NONE",
        "passkeys_mfa": "INCLUDED",
        "agent_principal": "SANDBOX",
    },
    "LAUNCH": {
        "saml_sso": "ADD_ON",
        "scim": "ADD_ON",
        "passkeys_mfa": "INCLUDED",
        "agent_principal": "METERED",
    },
    "BUSINESS": {
        "saml_sso": "INCLUDED",
        "scim": "ADD_ON",
        "passkeys_mfa": "INCLUDED",
        "agent_principal": "METERED",
    },
    "SCALE": {
        "saml_sso": "INCLUDED",
        "scim": "ADD_ON",
        "passkeys_mfa": "INCLUDED",
        "agent_principal": "METERED",
    },
    "ENTERPRISE": {
        "saml_sso": "CONTRACT",
        "scim": "CONTRACT",
        "passkeys_mfa": "CONTRACT",
        "agent_principal": "CONTRACT",
    },
}


def test_every_disagreement_with_the_estate_is_listed_for_the_owners() -> None:
    found = {(d.feature_id, d.band, d.kind) for d in reconcile(M, ESTATE)}
    # the record includes SSO from C3; the Estate sells it from Launch as an add-on and contracts it at Enterprise
    assert (
        "advanced.enterprise-sso-saml-oidc-federation",
        "C2",
        "ESTATE_MORE_GENEROUS",
    ) not in found  # an add-on at C2 where C2 has none is stricter, not looser
    assert ("advanced.enterprise-sso-saml-oidc-federation", "C5", "ESTATE_STRICTER") in found
    # a baseline right the Estate lists as a contract item is the most serious kind
    assert (
        "baseline.mfa-passkeys-session-protection",
        "C5",
        "BASELINE_NOT_INCLUDED_BY_ESTATE",
    ) in found
    assert (
        "advanced.scim-automated-provisioning",
        "C2",
        "ESTATE_MORE_GENEROUS",
    ) not in found  # an add-on is not an inclusion
    assert all(
        d.estate_mode in {"NONE", "ADD_ON", "INCLUDED", "METERED", "CONTRACT", "SANDBOX"}
        for d in reconcile(M, ESTATE)
    )
    assert reconcile(M, {}) == []  # nothing to compare is no divergence


# ---- downgrade ---------------------------------------------------------------------------------------------------------


def test_acceptance_05_a_downgrade_is_disclosed_and_deletes_nothing() -> None:
    d = downgrade_impact(M, "C4", "C2")
    assert (
        "advanced.scim-automated-provisioning" in d.becomes_inactive
        and "advanced.enterprise-sso-saml-oidc-federation" in d.becomes_inactive
    )
    assert not set(BASELINE_IDS) & set(d.becomes_inactive)  # the baseline stays
    assert (
        d.deletes_anything is False
        and d.effective == "AT_AUTHORIZED_RENEWAL"
        and d.preserved == PRESERVED
    )
    assert (
        "the evidence ledger and audit history" in d.preserved
        and "issued credentials" in d.preserved
    )
    top = downgrade_impact(
        M, "C5", "C4"
    )  # the four contracted add-ons stop being offered; nothing they produced is touched
    assert (
        set(top.becomes_inactive)
        == {
            "advanced.dedicated-isolation-private-deployment",
            "advanced.sovereign-service-configuration",
            "advanced.byok-hyok-and-specialized-key-custody",
            "advanced.custom-sla-named-support-onboarding",
        }
        and top.becomes_add_on == ()
    )
    assert downgrade_impact(M, "C1", "C3").becomes_inactive == ()  # an upgrade removes nothing
    with pytest.raises(BandError):
        downgrade_impact(M, "C9", "C1")


# ---- the meter register -------------------------------------------------------------------------------------------------


def test_the_meter_register_is_the_records_eight_meters_plus_the_cr070_and_cr088_definitions_and_prices_none_of_them() -> (
    None
):
    reg = meter_register()
    assert (
        set(reg)
        == {
            "auth.mau",
            "idv.check",
            "kyb.check",
            "credential.issue",
            "credential.verify",
            "api.request",
            "agent.principal",
            "data.retention",
            "credential.check",  # CR-070: definitions only, added on the engineering owner's instruction (2026-11-12)
            "credential.sensitive_check",
            "screening.check",  # CR-104: definitions backed by usage-only meters, unpriced
            "screening.monitor_period",
        }
    )
    for key, meter in (
        ("credential.check", "CREDENTIAL_CHECK"),
        ("credential.sensitive_check", "CREDENTIAL_SENSITIVE_CHECK"),
    ):
        m = reg[key]
        assert not (m.priced or m.hard_quota or m.bill_on_commit), (
            "defined, not priced, not capped, not billing"
        )
        assert m.existing_meter == meter and m.existing_sku is None, (
            "a usage meter stands behind it (CR-070), and no SKU or price"
        )
    for key, meter in (
        ("screening.check", "SCREEN_CHECK"),
        ("screening.monitor_period", "SCREEN_MONITOR_PERIOD"),
    ):
        m = reg[key]
        assert m.existing_meter == meter and m.existing_sku is None, (
            "a usage meter stands behind it, and no SKU or price"
        )
        assert not (m.priced or m.hard_quota or m.bill_on_commit)
    assert not any(
        m.priced or m.hard_quota for m in reg.values()
    )  # the record approves no price, cap or overage
    assert (
        reg["auth.mau"].existing_meter == "MAH"
        and reg["credential.issue"].existing_meter == "VC_ISSUED"
    )
    assert (
        reg["api.request"].existing_meter is None
        and "internal service chatter" in reg["api.request"].guardrail
    )
    with pytest.raises(BandError, match="not defined"):
        parse_meters({"meters": {"x.y": {"example_unit": "u", "counts": "", "guardrail": "g"}}})
    with pytest.raises(BandError, match="empty"):
        parse_meters({})


def test_the_seeded_estate_and_the_record_differ_in_exactly_the_documented_places() -> None:
    """The list the owners rule on (CR-045). A change to the Estate's seed or to the feature mapping changes this and must be read."""
    import importlib.util
    from collections import Counter
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "commercial"
        / "versions"
        / "0001_catalogue_entitlements.py"
    )
    spec = importlib.util.spec_from_file_location("estate_seed", path)
    assert spec and spec.loader
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    estate = {p: {} for p in seed.PLAN_ORDER}
    for cap, modes in seed.MATRIX.items():
        for plan, mode in zip(seed.PLAN_ORDER, modes, strict=True):
            estate[plan][cap] = mode.split(":")[0]
    found = reconcile(M, estate)
    assert Counter(d.kind for d in found) == {
        "ESTATE_STRICTER": 31,
        "ESTATE_MORE_GENEROUS": 15,
        "BASELINE_NOT_INCLUDED_BY_ESTATE": 2,
    }
    baseline = {
        (d.feature_id, d.band, d.estate_mode)
        for d in found
        if d.kind == "BASELINE_NOT_INCLUDED_BY_ESTATE"
    }
    assert (
        baseline
        == {
            ("baseline.secure-account-registration-recovery", "C5", "CONTRACT"),
            ("baseline.mfa-passkeys-session-protection", "C5", "CONTRACT"),
        }
    )  # the Estate lists two baseline rights as Enterprise contract items; the resolver never gates them whatever the catalogue says


def test_a_compliance_refusal_never_offers_an_upgrade() -> None:
    """A paid upgrade is not a workaround for a region or a missing provider route (record section 06)."""
    fid = "advanced.scim-automated-provisioning"  # not in C1: a plain refusal names the band that would include it
    assert decide_feature(ENFORCED, fid, PLANS["C1"], estate=OK).upgrade_band == "C4"
    for kw in (
        {"region_ok": False},
        {"region_ok": None},
        {"coverage_ok": False},
        {"coverage_ok": None},
    ):
        d = decide_feature(ENFORCED, fid, PLANS["C1"], estate=OK, **kw)  # type: ignore[arg-type]
        assert not d.allowed and d.upgrade_band is None, kw

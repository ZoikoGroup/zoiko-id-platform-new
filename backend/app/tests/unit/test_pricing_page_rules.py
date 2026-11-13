"""The public pricing page's structure and its inquiries (ZID-UX-PRICE-004 v4.0): a complete, price-free, claim-free structure, and a lead that is only ever a conversation."""

from __future__ import annotations

import re

import pytest
from zid_platform.modules.commercial.domain import pricing_page as pp
from zid_platform.modules.commercial.domain import sales_lead as sl
from zid_platform.modules.commercial.domain.service_families import EXPECTED_CODES
from zid_platform.modules.commercial.infrastructure.pricing_page_config import pricing_page

PAGE = pricing_page()
FAMILIES = set(EXPECTED_CODES)


def test_the_shipped_structure_has_three_tiers_two_paths_and_eight_use_cases() -> None:
    assert [t.code for t in PAGE.tiers] == ["ESSENTIALS", "BUSINESS", "ENTERPRISE"]
    assert {p.code for p in PAGE.paths} == {"SANDBOX", "PARTNER"}
    assert len(PAGE.use_cases) == 8
    assert PAGE.status == "PROPOSED", (
        "the packaging is a proposal until Product and the Founder approve it (G0)"
    )
    for t in PAGE.tiers:
        assert set(t.family_state) == FAMILIES, t.code


def test_every_word_on_the_page_structure_is_free_of_prices_discounts_trials_and_status_claims() -> (
    None
):
    texts = [PAGE.version]
    for t in PAGE.tiers:
        texts += [t.label, t.fit, t.cta_label]
    for p in PAGE.paths:
        texts += [p.label, p.text, p.cta_label]
    for u in PAGE.use_cases:
        texts.append(u.label)
    texts += list(PAGE.disclosures.values())
    money = re.compile(r"[$€£¥]|\b(usd|eur|gbp|jpy)\b|\b\d+(\.\d+)?\s?%", re.IGNORECASE)
    claim = re.compile(
        r"\b(trial|certified|accredited|licen[sc]ed|globally|worldwide|guaranteed|unlimited|best|cheapest)\b",
        re.IGNORECASE,
    )
    for t in texts:
        assert not money.search(t), t
        assert not claim.search(t), t


def test_packaging_never_includes_a_regulated_or_residency_service_and_keeps_base_controls_in_every_tier() -> (
    None
):
    for t in PAGE.tiers:
        assert t.family_state["SIGN"] == "quote", (
            "a signature is scoped with Sales; a qualified one only through a proven partner"
        )
        assert t.family_state["SOV"] == "quote", (
            "sovereign options are quoted after residency feasibility"
        )
        for base in ("AUTHN", "EVID", "API"):
            assert t.family_state[base] == "included", (
                f"{base} baseline is not held back in {t.code}"
            )
    assert (
        PAGE.tiers[0].family_state["KYB"] == "quote"
        and PAGE.tiers[1].family_state["KYB"] == "optional"
    )


def test_each_use_case_preloads_real_families_only() -> None:
    for u in PAGE.use_cases:
        assert u.families and set(u.families) <= FAMILIES, u.code


def doc(**over: object) -> dict[str, object]:
    t = lambda code: {
        "code": code,
        "label": code.title(),
        "fit": "Fit",
        "cta_kind": "X",
        "cta_label": "Go",
        "family_state": dict.fromkeys(FAMILIES, "included"),
    }
    base: dict[str, object] = {
        "version": "v", "status": "PROPOSED", "tiers": [t("ESSENTIALS"), t("BUSINESS"), t("ENTERPRISE")],
        "paths": [{"code": "SANDBOX", "label": "S", "text": "Test", "cta_kind": "a", "cta_label": "Go"}, {"code": "PARTNER", "label": "P", "text": "Partner", "cta_kind": "b", "cta_label": "Go"}],
        "use_cases": [{"code": "U", "label": "Use", "families": ["IDV"]}],
        "disclosures": {"price_basis": "x", "tax": "x", "commitment": "x", "no_surprise": "x", "plan_change": "x", "exit": "x"},
    }  # fmt: skip
    return {**base, **over}


def test_a_file_that_breaks_a_rule_never_loads() -> None:
    pp.parse(doc())
    broken_tier = doc()
    broken_tier["tiers"][0]["family_state"].pop("IDV")  # type: ignore[index]
    priced = doc()
    priced["tiers"][0]["fit"] = "From $49 a month"  # type: ignore[index]
    claimed = doc()
    claimed["tiers"][1]["label"] = "Certified Business"  # type: ignore[index]
    for bad, code in (
        (broken_tier, "TIER_FAMILIES_INCOMPLETE"),
        (priced, "PRICE_IN_TEXT"),
        (claimed, "CLAIM_IN_TEXT"),
        (doc(status="LIVE"), "STATUS_INVALID"),
        (
            doc(use_cases=[{"code": "U", "label": "Use", "families": ["GHOST"]}]),
            "USE_CASE_FAMILY_UNKNOWN",
        ),
        (doc(disclosures={"tax": "x"}), "DISCLOSURE_MISSING"),
        (doc(tiers=doc()["tiers"][:2]), "TIERS_CHANGED"),
    ):
        with pytest.raises(pp.PageError) as e:
            pp.parse(bad)
        assert e.value.code == code


def test_public_states_tell_the_reasons_apart_and_never_say_available_for_an_unknown_market() -> (
    None
):
    assert pp.PUBLIC_STATE == {
        "AVAILABLE": "AVAILABLE",
        "CONTACT_SALES": "SALES_ASSISTED",
        "NOT_ELIGIBLE": "RESTRICTED",
        "RESEARCH_ONLY": "NOT_AVAILABLE",
        "MARKET_REQUIRED": "MARKET_REQUIRED",
    }


def lead(**over: object) -> sl.Lead:
    base: dict[str, object] = {
        "kind": "ENTERPRISE", "contact_name": "Ana Silva", "work_email": "Ana.Silva@Example.com", "company": "Acme Bank", "country": "PT", "region": None,
        "capabilities": ["KYB", "IDV"], "volume_band": "FROM_10K_TO_100K", "entities": 12, "referral_source": "conference", "security_needs": "SSO and audit exports",
        "partner_type": None, "notice_version": "privacy-2026-11", "honeypot": None, "known_families": FAMILIES,
    }  # fmt: skip
    return sl.check_lead(**{**base, **over})  # type: ignore[arg-type]


def test_a_good_inquiry_is_normalised_and_the_same_person_company_and_kind_dedupe_alike() -> None:
    a = lead()
    assert a.work_email == "ana.silva@example.com" and a.capabilities == ("IDV", "KYB")
    assert sl.dedupe_key(a) == sl.dedupe_key(
        lead(work_email="ANA.SILVA@example.com", company="acme bank", contact_name="A. Silva")
    )
    assert sl.dedupe_key(a) != sl.dedupe_key(lead(kind="PARTNER", partner_type="RESELLER"))
    assert sl.dedupe_key(a) != sl.dedupe_key(lead(company="Other Bank"))


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"honeypot": "http://spam"}, "SPAM_SUSPECTED"),
        ({"kind": "RETAIL"}, "KIND_UNKNOWN"),
        ({"kind": "PARTNER"}, "PARTNER_TYPE_REQUIRED"),
        ({"partner_type": "RESELLER"}, "PARTNER_TYPE_NOT_ALLOWED"),
        ({"work_email": "not-an-email"}, "EMAIL_INVALID"),
        ({"country": "Portugal"}, "COUNTRY_INVALID"),
        ({"volume_band": "HUGE"}, "VOLUME_BAND_INVALID"),
        ({"entities": 0}, "ENTITIES_INVALID"),
        ({"capabilities": ["GHOST"]}, "CAPABILITY_UNKNOWN"),
        ({"notice_version": " "}, "NOTICE_VERSION_REQUIRED"),
        ({"security_needs": "our passport number is 123456789"}, "IDENTITY_NUMBER_IN_TEXT"),
        ({"security_needs": "card 4111 1111 1111 1111"}, "IDENTITY_NUMBER_IN_TEXT"),
        ({"company": "Acme 1234-5678-90"}, "IDENTITY_NUMBER_IN_TEXT"),
        ({"referral_source": "see https://evil.example"}, "LINK_IN_TEXT"),
        ({"contact_name": "A"}, "FIELD_LENGTH"),
    ],
)
def test_an_inquiry_is_refused_for_the_named_reason(over: dict[str, object], code: str) -> None:
    with pytest.raises(sl.LeadError) as e:
        lead(**over)
    assert e.value.code == code


def test_a_lead_moves_forward_one_step_or_ends_and_nothing_moves_backwards() -> None:
    assert sl.next_states("RECEIVED") == ("QUALIFIED", "CLOSED", "SPAM")
    assert sl.next_states("REVIEW") == ("OFFER_SENT", "CLOSED", "SPAM")
    assert sl.next_states("OFFER_SENT") == ("CLOSED", "SPAM")
    assert sl.next_states("CLOSED") == () == sl.next_states("SPAM")
    assert "RECEIVED" not in sl.next_states("QUALIFIED")


def test_the_lead_carries_no_field_that_could_hold_identity_evidence() -> None:
    fields = set(sl.Lead.__dataclass_fields__)
    assert not {
        f
        for f in fields
        if any(
            w in f
            for w in ("passport", "document", "biometric", "id_number", "birth", "address", "card")
        )
    }, fields

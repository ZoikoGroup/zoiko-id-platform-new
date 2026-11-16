"""DTV-33 Travel Rule: IVMS101 shape, counterparty VASP and threshold rules. Synthetic data only."""

from __future__ import annotations

import copy

import pytest
from zid_platform.modules.verification.domain import travel_rule as tr
from zid_platform.modules.verification.infrastructure.vasp_registry import (
    CONFIG,
    FILE,
    vasp_registry,
)


def lei(base: str) -> str:
    n = int("".join(str(int(c, 36)) for c in base) + "00")
    return base + f"{98 - n % 97:02d}"


ORIGIN, BENEF, STRANGER = (
    lei("5493001KJTIIGC8Y1R"),
    lei("529900T8BM49AURSDO"),
    lei("2138003E2S6R3FNZMD"),
)
REGISTRY = tr.parse_registry(
    {
        "version": "t-1",
        "vasps": [
            {"lei": ORIGIN, "legal_name": "Origin VASP", "jurisdictions": ["GB", "DE"], "status": "ACTIVE", "source_ref": "register:1"},
            {"lei": BENEF, "legal_name": "Beneficiary VASP", "jurisdictions": ["GB"], "status": "SUSPENDED", "source_ref": "register:2"},
        ],
        "thresholds": {"GB": {"amount": "1000", "currency": "GBP", "legal_basis_ref": "LEG-9"}},
    }
)  # fmt: skip


def vasp(code: str, name: str) -> dict:
    return {
        "legalPerson": {
            "name": {
                "nameIdentifier": [
                    {"legalPersonName": name, "legalPersonNameIdentifierType": "LEGL"}
                ]
            },
            "nationalIdentification": {
                "nationalIdentifierType": "LEIX",
                "nationalIdentifier": code,
            },
        }
    }


GOOD = {
    "originator": {
        "originatorPersons": [
            {"naturalPerson": {"name": {"nameIdentifier": [{"primaryIdentifier": "Lee", "secondaryIdentifier": "Dana", "nameIdentifierType": "LEGL"}]}, "countryOfResidence": "GB"}}
        ],
        "accountNumber": ["wallet-origin-1"],
    },
    "beneficiary": {
        "beneficiaryPersons": [{"legalPerson": {"name": {"nameIdentifier": [{"legalPersonName": "Acme Ltd", "legalPersonNameIdentifierType": "LEGL"}]}}}],
        "accountNumber": ["wallet-benef-1"],
    },
    "originatingVASP": vasp(ORIGIN, "Origin VASP"),
    "beneficiaryVASP": vasp(BENEF, "Beneficiary VASP"),
    "transfer": {"amount": "2500.00", "asset": "GBP"},
}  # fmt: skip


def mutated(mutate):
    p = copy.deepcopy(GOOD)
    mutate(p)
    return tr.assess(p, REGISTRY, jurisdiction="GB")


def codes(a: tr.Assessment) -> set[str]:
    return {f.code for f in a.findings}


def test_a_well_formed_payload_is_well_formed_and_says_it_proves_nothing_about_the_people():
    a = tr.assess(GOOD, REGISTRY, jurisdiction="GB")
    assert a.well_formed and a.findings == ()
    assert any("not a verified person" in n for n in a.notes) and any(
        "Nothing was sent" in n for n in a.notes
    )


def test_a_counterparty_is_known_only_when_listed_active_and_permitted_in_the_jurisdiction():
    a = tr.assess(GOOD, REGISTRY, jurisdiction="GB")
    assert a.originating_vasp is tr.Counterparty.KNOWN
    assert a.beneficiary_vasp is tr.Counterparty.NOT_ACTIVE_FOR_JURISDICTION  # listed but suspended
    de = tr.assess(GOOD, REGISTRY, jurisdiction="DE")
    assert (
        de.originating_vasp is tr.Counterparty.KNOWN
        and de.beneficiary_vasp is tr.Counterparty.NOT_ACTIVE_FOR_JURISDICTION
    )
    fr = tr.assess(GOOD, REGISTRY, jurisdiction="FR")
    assert (
        fr.originating_vasp is tr.Counterparty.NOT_ACTIVE_FOR_JURISDICTION
    )  # listed, but not for this jurisdiction


def test_an_unlisted_provider_is_unverified_never_assumed_fine():
    a = mutated(lambda p: p.update(beneficiaryVASP=vasp(STRANGER, "Stranger VASP")))
    assert a.beneficiary_vasp is tr.Counterparty.UNVERIFIED_COUNTERPARTY


def test_a_bad_lei_is_named_as_such_and_a_missing_one_is_not_provided():
    bad = "5493001KJTIIGC8Y1R" + ("00" if ORIGIN[-2:] != "00" else "01")
    a = mutated(lambda p: p.update(originatingVASP=vasp(bad, "Origin VASP")))
    assert a.originating_vasp is tr.Counterparty.LEI_INVALID
    none = mutated(lambda p: p["originatingVASP"]["legalPerson"].pop("nationalIdentification"))
    assert none.originating_vasp is tr.Counterparty.NOT_PROVIDED


def test_the_shipped_registry_is_empty_so_every_counterparty_is_unverified_and_no_threshold_is_assumed():
    shipped = vasp_registry()
    assert shipped.entries == {} and shipped.thresholds == {}
    a = tr.assess(GOOD, shipped, jurisdiction="GB")
    assert a.originating_vasp is tr.Counterparty.UNVERIFIED_COUNTERPARTY
    assert a.threshold is tr.Threshold.UNKNOWN  # never "below"
    assert (CONFIG / FILE).read_text(encoding="utf-8").count("vasps: []") == 1


def test_the_threshold_is_unknown_without_a_configured_value_and_never_compared_across_currencies():
    assert tr.assess(GOOD, REGISTRY, jurisdiction="GB").threshold is tr.Threshold.AT_OR_ABOVE
    below = mutated(lambda p: p["transfer"].update(amount="999.99"))
    assert below.threshold is tr.Threshold.BELOW
    other = mutated(lambda p: p["transfer"].update(asset="BTC"))
    assert (
        other.threshold is tr.Threshold.UNKNOWN
    )  # a threshold in pounds says nothing about bitcoin
    assert tr.assess(GOOD, REGISTRY, jurisdiction="DE").threshold is tr.Threshold.UNKNOWN


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda p: p.pop("originator"), "BLOCK_MISSING"),
        (lambda p: p["originator"].update(originatorPersons=[]), "PERSON_LIST_INVALID"),
        (
            lambda p: p["originator"]["originatorPersons"][0]["naturalPerson"]["name"][
                "nameIdentifier"
            ][0].update(primaryIdentifier=" "),
            "NAME_MISSING",
        ),
        (
            lambda p: p["originator"]["originatorPersons"][0]["naturalPerson"]["name"][
                "nameIdentifier"
            ][0].update(nameIdentifierType="NICK"),
            "NAME_TYPE_INVALID",
        ),
        (
            lambda p: p["originator"]["originatorPersons"][0]["naturalPerson"].update(
                countryOfResidence="gb"
            ),
            "COUNTRY_INVALID",
        ),
        (
            lambda p: p["originator"]["originatorPersons"][0].update(legalPerson={}),
            "PERSON_KIND_INVALID",
        ),
        (lambda p: p["beneficiary"].pop("accountNumber"), "ACCOUNT_MISSING"),
        (lambda p: p.pop("beneficiaryVASP"), "VASP_MISSING"),
        (
            lambda p: p["transfer"].update(amount=2500),
            "AMOUNT_INVALID",
        ),  # a number, not a decimal string
        (lambda p: p["transfer"].update(amount="-1"), "AMOUNT_INVALID"),
        (lambda p: p["transfer"].update(amount="NaN"), "AMOUNT_INVALID"),
        (lambda p: p["transfer"].update(asset="g"), "ASSET_INVALID"),
        (lambda p: p.pop("transfer"), "BLOCK_MISSING"),
    ],
)
def test_each_shape_problem_is_named_by_code_and_path_never_by_value(mutate, code):
    a = mutated(mutate)
    assert not a.well_formed and code in codes(a)
    assert all(not any(ch.isdigit() for ch in f.code) for f in a.findings)
    assert "Lee" not in repr(a.findings) and "wallet-origin-1" not in repr(a.findings)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["vasps"][0].update(lei="NOTALEI"),
        lambda d: d["vasps"].append(dict(d["vasps"][0])),  # repeated
        lambda d: d["vasps"][0].update(status="PAUSED"),
        lambda d: d["vasps"][0].update(jurisdictions=[]),
        lambda d: d["vasps"][0].pop("source_ref"),
        lambda d: d["thresholds"]["GB"].pop("legal_basis_ref"),
        lambda d: d["thresholds"]["GB"].update(amount="abc"),
        lambda d: d["thresholds"]["GB"].update(amount="-5"),
    ],
)
def test_a_malformed_registry_is_refused_whole(mutate):
    doc = {
        "vasps": [{"lei": ORIGIN, "jurisdictions": ["GB"], "status": "ACTIVE", "source_ref": "r"}],
        "thresholds": {"GB": {"amount": "1000", "currency": "GBP", "legal_basis_ref": "L"}},
    }
    mutate(doc)
    with pytest.raises(tr.TravelRuleError):
        tr.parse_registry(doc)


def test_an_invalid_jurisdiction_is_an_error_not_a_default():
    with pytest.raises(tr.TravelRuleError, match="JURISDICTION_INVALID"):
        tr.assess(GOOD, REGISTRY, jurisdiction="gb")

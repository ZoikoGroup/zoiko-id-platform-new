"""The trust-source allowlist (WF03 T02, T03, T38; CR-119): a country's trust material is accepted only from the authority recorded for it; the file ships off and empty; a malformed file is refused whole."""

from __future__ import annotations

import pytest
from zid_platform.modules.evidence.domain import trust_store as rules
from zid_platform.modules.evidence.domain.trust_store import (
    SourcePolicy,
    TrustSource,
    TrustStoreError,
)
from zid_platform.modules.evidence.infrastructure import source_policy as sp

GB = TrustSource("GB", "pki.example.gov.uk")
POLICY = SourcePolicy(True, (GB, TrustSource("FR", "pki.example.gouv.fr")))


def test_the_authority_is_the_host_of_the_address_in_lower_case() -> None:
    assert (
        rules.authority_of("https://PKI.Example.gov.uk:8443/ml/list.ml?x=1") == "pki.example.gov.uk"
    )
    assert rules.authority_of("not a url") == ""


def test_a_list_from_the_recorded_authority_has_no_problem() -> None:
    assert rules.authority_problems(POLICY, ["GB"], "https://pki.example.gov.uk/ml") == ()


def test_a_bundle_that_only_claims_a_country_is_refused_and_a_country_with_no_authority_says_so() -> (
    None
):
    assert rules.authority_problems(POLICY, ["GB"], "https://evil.example.net/ml") == (
        "AUTHORITY_NOT_ALLOWLISTED:GB",
    )
    assert rules.authority_problems(POLICY, ["GB", "DE"], "https://pki.example.gov.uk/ml") == (
        "NO_AUTHORITY_RECORDED_FOR_COUNTRY:DE",
    )
    assert rules.authority_problems(POLICY, ["GB", "FR"], "https://pki.example.gov.uk/ml") == (
        "AUTHORITY_NOT_ALLOWLISTED:FR",
    )


def test_one_authority_cannot_vouch_for_another_countrys_material() -> None:
    assert rules.authority_problems(POLICY, ["FR"], "https://pki.example.gov.uk/ml") == (
        "AUTHORITY_NOT_ALLOWLISTED:FR",
    )


def test_validation_state_needs_the_authority_to_be_ok_as_well_as_the_signature() -> None:
    assert rules.after_validation("VERIFIED", 3) is rules.State.VALIDATED
    assert rules.after_validation("VERIFIED", 3, authority_ok=False) is rules.State.REJECTED
    assert rules.after_validation("NOT_VERIFIED", 3, authority_ok=True) is rules.State.REJECTED


def test_the_shipped_file_is_off_and_empty() -> None:
    assert sp.load() == SourcePolicy(False, ())


def test_a_good_file_loads_and_normalises() -> None:
    p = sp.parse(
        {
            "enforce": True,
            "sources": [{"country": "gb", "authority": "PKI.Example.gov.uk", "kind": "ROOT_LIST"}],
        }
    )
    assert p.enforce is True and p.sources == (GB,)
    assert sp.parse(None) == SourcePolicy()


@pytest.mark.parametrize(
    "doc",
    [
        {"enforce": "yes"},
        {"surprise": 1},
        {"sources": [{"country": "GB"}]},
        {"sources": [{"country": "GBRX", "authority": "a.example"}]},
        {"sources": [{"country": "GB", "authority": "a.example/path"}]},
        {"sources": [{"country": "GB", "authority": "a.example", "kind": "REVOCATION_LIST"}]},
        {"sources": [{"country": "GB", "authority": "a.example", "extra": 1}]},
        ["not", "a", "mapping"],
    ],
)
def test_a_malformed_file_is_refused_whole(doc: object) -> None:
    with pytest.raises(TrustStoreError):
        sp.parse(doc)

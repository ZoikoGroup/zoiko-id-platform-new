"""Service offerings (CR-088): coverage is published only when provider rights, country rules, privacy and independent tests all hold."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from zid_platform.modules.verification.domain import offerings as of

CATALOG = Path(__file__).resolve().parents[3] / "config" / "offerings" / "service-offerings.yaml"
ALL_PASS = {f"T{n:02d}": "PASS" for n in range(1, 49)}


def _catalog() -> list[of.Offering]:
    return of.parse(yaml.safe_load(CATALOG.read_text(encoding="utf-8")))


def _idv(**kw: object) -> of.Offering:
    base: dict[str, object] = {
        "offering_id": "IDENTITY_DOCUMENTS",
        "title": "Identity & documents",
        "wording": "Confirm identities.",
        "capabilities": ("INDIVIDUAL_VERIFICATION",),
        "personal_data": True,
        "biometric": False,
        "cases": ("T01", "T05"),
        "code": (),
    }
    return of.Offering(**(base | kw))  # type: ignore[arg-type]


OK_PRIVACY = of.Privacy(usable=True, biometrics="CONSENT_REQUIRED")


def test_the_shipped_catalog_lists_the_twelve_offerings_and_every_case_exists() -> None:
    items = _catalog()
    assert len(items) == 12
    assert len({o.offering_id for o in items}) == 12
    assert all(o.cases for o in items)  # every offering is tied to independent tests


def test_the_shipped_catalog_has_no_code_pointer_to_nowhere() -> None:
    backend = CATALOG.parents[2]
    src = backend / "app" / "src" / "zid_platform"
    for o in _catalog():
        for p in o.code:
            assert (src / p).exists() or (backend.parent / p).exists(), f"{o.offering_id}: {p}"


def test_available_only_when_every_check_holds() -> None:
    d = of.decide(
        _idv(),
        "GB",
        capability_refusals={"INDIVIDUAL_VERIFICATION": []},
        privacy=OK_PRIVACY,
        accepted_run=ALL_PASS,
    )
    assert d.available and d.reasons == ()


def test_every_missing_check_is_reported_at_once() -> None:
    d = of.decide(
        _idv(),
        "GB",
        capability_refusals={
            "INDIVIDUAL_VERIFICATION": ["pack:NO_APPROVED_PACK", "entitlement:NO_GRANT"]
        },
        privacy=None,
        accepted_run=None,
    )
    assert not d.available
    assert d.reasons == (
        "INDIVIDUAL_VERIFICATION:pack:NO_APPROVED_PACK",
        "INDIVIDUAL_VERIFICATION:entitlement:NO_GRANT",
        "PRIVACY_OVERLAY_MISSING",
        "CONFORMANCE_NOT_ACCEPTED",
    )


def test_a_capability_the_country_does_not_offer_blocks_it() -> None:
    d = of.decide(_idv(), "ZZ", capability_refusals={}, privacy=OK_PRIVACY, accepted_run=ALL_PASS)
    assert d.reasons == ("INDIVIDUAL_VERIFICATION:NOT_OFFERED_IN_COUNTRY",)


def test_a_case_that_is_partial_gap_or_missing_is_not_a_pass() -> None:
    run = ALL_PASS | {"T05": "PARTIAL"}
    run.pop("T01")
    d = of.decide(
        _idv(),
        "GB",
        capability_refusals={"INDIVIDUAL_VERIFICATION": []},
        privacy=OK_PRIVACY,
        accepted_run=run,
    )
    assert d.reasons == ("TEST_NOT_PASSED:T01", "TEST_NOT_PASSED:T05")


def test_biometric_offerings_need_a_country_that_allows_biometrics() -> None:
    bio = _idv(offering_id="BIOMETRICS_FRAUD", biometric=True)
    no_bio = of.Privacy(usable=True, biometrics="PROHIBITED")
    d = of.decide(
        bio,
        "GB",
        capability_refusals={"INDIVIDUAL_VERIFICATION": []},
        privacy=no_bio,
        accepted_run=ALL_PASS,
    )
    assert d.reasons == ("BIOMETRICS_PROHIBITED",)
    assert of.decide(
        _idv(),
        "GB",
        capability_refusals={"INDIVIDUAL_VERIFICATION": []},
        privacy=no_bio,
        accepted_run=ALL_PASS,
    ).available


def test_an_offering_without_tests_is_never_available_and_one_without_personal_data_needs_no_overlay() -> (
    None
):
    api = _idv(offering_id="GLOBAL_APIS", capabilities=(), personal_data=False, cases=())
    assert of.decide(
        api, "GB", capability_refusals={}, privacy=None, accepted_run=ALL_PASS
    ).reasons == ("NO_INDEPENDENT_TESTS",)
    api = _idv(offering_id="GLOBAL_APIS", capabilities=(), personal_data=False, cases=("T34",))
    assert of.decide(
        api, "GB", capability_refusals={}, privacy=None, accepted_run=ALL_PASS
    ).available


@pytest.mark.parametrize(
    ("entry", "code"),
    [
        ({"id": "bad id", "title": "x", "wording": "y"}, "OFFERING_ID_INVALID"),
        (
            {"id": "X", "title": "x", "wording": "Fully certified identity checks"},
            "OFFERING_CLAIMS_CERTIFICATION",
        ),
        (
            {"id": "X", "title": "x", "wording": "y", "capabilities": ["TELEPATHY"]},
            "OFFERING_CAPABILITY_UNKNOWN",
        ),
        ({"id": "X", "title": "x", "wording": "y", "cases": ["T99"]}, "OFFERING_CASE_UNKNOWN"),
        ({"id": "X", "title": "", "wording": "y"}, "OFFERING_TEXT_MISSING"),
    ],
)
def test_a_catalog_entry_that_cannot_be_published_honestly_is_refused(
    entry: dict[str, object], code: str
) -> None:
    with pytest.raises(of.OfferingError, match=code):
        of.parse({"offerings": [entry]})


def test_duplicates_are_refused() -> None:
    e = {"id": "X", "title": "x", "wording": "y"}
    with pytest.raises(of.OfferingError, match="OFFERING_DUPLICATE"):
        of.parse({"offerings": [e, e]})

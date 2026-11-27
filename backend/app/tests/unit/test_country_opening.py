"""Opening a country for sign-up from the operator console (CR-127): the rules, not the database."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from zid_platform.modules.verification.domain.countries import (
    APPROVALS,
    Availability,
    Capability,
    CapabilityEntry,
    Country,
    CountryError,
    Opening,
    Registry,
    apply_openings,
    availability,
    check_opening,
)

NOW = datetime(2026, 11, 19, tzinfo=UTC)
PAST = datetime(2026, 11, 1, tzinfo=UTC)
FUTURE = datetime(2027, 1, 1, tzinfo=UTC)
REFS = {a: f"REF-{i}" for i, a in enumerate(APPROVALS)}


def _registry() -> Registry:
    gb = Country(
        "GB",
        "United Kingdom",
        1,
        {Capability.REGISTRATION: CapabilityEntry(Availability.SANDBOX, provider="simulator")},
    )
    fr = Country("FR", "France", 1, {})
    return Registry("t", {"GB": gb, "FR": fr})


def _open(
    country: str = "GB",
    *,
    effective=PAST,
    approvals=None,
    cells=("uk-1",),
    capability: str = "REGISTRATION",
) -> Opening:
    return Opening(
        country, 1, "OPEN", effective, approvals or REFS, cells, None, "op", NOW, capability
    )


def _state(
    reg: Registry,
    country: str,
    environment: str = "production",
    capability: Capability = Capability.REGISTRATION,
) -> Availability:
    return availability(reg, country, capability, now=NOW, environment=environment)


def test_a_sandbox_country_is_closed_in_production_until_an_opening_is_recorded() -> None:
    reg = _registry()
    assert _state(reg, "GB") is Availability.DISABLED
    assert (
        _state(apply_openings(reg, {("GB", "REGISTRATION"): _open()}), "GB") is Availability.ENABLED
    )


def test_an_opening_that_has_not_taken_effect_is_still_closed() -> None:
    reg = apply_openings(_registry(), {("GB", "REGISTRATION"): _open(effective=FUTURE)})
    assert _state(reg, "GB") is Availability.DISABLED


def test_a_country_the_file_does_not_declare_any_registration_for_can_be_opened() -> None:
    assert (
        _state(
            apply_openings(_registry(), {("FR", "REGISTRATION"): _open("FR", cells=("eu-1",))}),
            "FR",
        )
        is Availability.ENABLED
    )


def test_closing_shuts_a_country_even_where_the_file_says_sandbox() -> None:
    closed = Opening("GB", 2, "CLOSED", recorded_by="op", recorded_at=NOW)
    assert (
        _state(apply_openings(_registry(), {("GB", "REGISTRATION"): closed}), "GB", "local")
        is Availability.DISABLED
    )


def test_an_opening_changes_nothing_but_its_own_capability() -> None:
    reg = apply_openings(_registry(), {("GB", "REGISTRATION"): _open()})
    other = availability(
        reg, "GB", Capability.INDIVIDUAL_VERIFICATION, now=NOW, environment="production"
    )
    assert other is Availability.DISABLED


def test_an_opening_for_a_country_the_file_does_not_list_is_ignored() -> None:
    reg = apply_openings(_registry(), {("ZZ", "REGISTRATION"): _open("ZZ")})
    assert "ZZ" not in reg.countries


def test_the_registry_is_untouched_when_nothing_is_recorded() -> None:
    reg = _registry()
    assert apply_openings(reg, {}) is reg


def test_a_complete_opening_is_accepted_and_cleaned() -> None:
    approvals, cells = check_opening(
        _registry(),
        "GB",
        effective_from=NOW,
        approvals={a: f"  {r}  " for a, r in REFS.items()},
        home_cells=["uk-1", "uk-1", " "],
    )
    assert approvals == REFS and cells == ("uk-1",)


@pytest.mark.parametrize("missing", APPROVALS)
def test_every_one_of_the_six_approvals_is_required(missing: str) -> None:
    refs = {a: r for a, r in REFS.items() if a != missing}
    with pytest.raises(CountryError) as e:
        check_opening(_registry(), "GB", effective_from=NOW, approvals=refs, home_cells=["uk-1"])
    assert e.value.code == "COUNTRY_OPENING_INCOMPLETE" and missing in str(e.value)


def test_a_blank_or_tiny_reference_does_not_count() -> None:
    with pytest.raises(CountryError):
        check_opening(
            _registry(),
            "GB",
            effective_from=NOW,
            approvals={**REFS, "signoff": "  x "},
            home_cells=["uk-1"],
        )


def test_a_date_and_a_cell_are_required() -> None:
    with pytest.raises(CountryError):
        check_opening(_registry(), "GB", effective_from=None, approvals=REFS, home_cells=["uk-1"])
    with pytest.raises(CountryError):
        check_opening(_registry(), "GB", effective_from=NOW, approvals=REFS, home_cells=[])
    with pytest.raises(CountryError):
        check_opening(
            _registry(), "GB", effective_from=NOW, approvals=REFS, home_cells=["Not A Cell!"]
        )


def test_an_unlisted_country_cannot_be_opened_and_an_unknown_approval_is_refused() -> None:
    with pytest.raises(CountryError) as e:
        check_opening(_registry(), "ZZ", effective_from=NOW, approvals=REFS, home_cells=["uk-1"])
    assert e.value.code == "COUNTRY_NOT_LISTED"
    with pytest.raises(CountryError):
        check_opening(
            _registry(),
            "GB",
            effective_from=NOW,
            approvals={**REFS, "extra": "REF-9"},
            home_cells=["uk-1"],
        )


# ------------------------------------------------------------------------------ each feature is its own switch


def test_organization_claims_open_on_their_own_without_touching_sign_up() -> None:
    reg = apply_openings(
        _registry(),
        {("GB", "BUSINESS_VERIFICATION"): _open(capability="BUSINESS_VERIFICATION", cells=())},
    )
    assert _state(reg, "GB", capability=Capability.BUSINESS_VERIFICATION) is Availability.ENABLED
    assert (
        _state(reg, "GB", capability=Capability.REGISTRATION) is Availability.DISABLED
    )  # sign-up is a separate decision


def test_closing_one_feature_leaves_the_others_open() -> None:
    both = {
        ("GB", "REGISTRATION"): _open(),
        ("GB", "BUSINESS_VERIFICATION"): _open(capability="BUSINESS_VERIFICATION", cells=()),
    }
    closed = Opening(
        "GB", 2, "CLOSED", recorded_by="op", recorded_at=NOW, capability="BUSINESS_VERIFICATION"
    )
    reg = apply_openings(_registry(), {**both, ("GB", "BUSINESS_VERIFICATION"): closed})
    assert _state(reg, "GB", capability=Capability.BUSINESS_VERIFICATION) is Availability.DISABLED
    assert _state(reg, "GB", capability=Capability.REGISTRATION) is Availability.ENABLED


@pytest.mark.parametrize(
    "capability", [c.value for c in Capability if c is not Capability.DISCOVERY]
)
def test_every_regulated_capability_can_be_opened(capability: str) -> None:
    approvals, cells = check_opening(
        _registry(),
        "GB",
        capability=capability,
        effective_from=NOW,
        approvals=REFS,
        home_cells=["uk-1"],
    )
    assert approvals == REFS
    assert cells == (
        ("uk-1",) if capability == "REGISTRATION" else ()
    )  # only sign-up names the cell that holds the account


def test_only_sign_up_needs_a_home_cell() -> None:
    check_opening(
        _registry(),
        "GB",
        capability="BUSINESS_VERIFICATION",
        effective_from=NOW,
        approvals=REFS,
        home_cells=[],
    )
    with pytest.raises(CountryError):
        check_opening(
            _registry(),
            "GB",
            capability="REGISTRATION",
            effective_from=NOW,
            approvals=REFS,
            home_cells=[],
        )


def test_discovery_and_unknown_capabilities_cannot_be_opened_here() -> None:
    for capability in ("DISCOVERY", "TELEPORTATION"):
        with pytest.raises(CountryError) as e:
            check_opening(
                _registry(),
                "GB",
                capability=capability,
                effective_from=NOW,
                approvals=REFS,
                home_cells=["uk-1"],
            )
        assert e.value.code == "CAPABILITY_NOT_OPENABLE"

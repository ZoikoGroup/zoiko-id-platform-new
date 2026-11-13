"""England Right to Rent (ZID-RCG-003 section 10.2; T06, T27, T28, T29; CR-084)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from zid_platform.modules.verification.domain import right_to_rent as rtr

ON = date(2026, 11, 12)
GOOD = rtr.ProviderStatus(
    provider="acme-idsp",
    certified_services=frozenset({"RTR", "IDV"}),
    registered_for_rtr=True,
    supplementary_code_valid=True,
    entitled=True,
    suspended=False,
)


def decide(**over: object) -> rtr.Decision:
    args: dict[str, object] = {
        "property_country": "GB",
        "property_subdivision": "GB-ENG",
        "route": rtr.Route.DIGITAL_IDSP,
        "on": ON,
        "provider": GOOD,
    } | over
    return rtr.decide(**args)  # type: ignore[arg-type]


def test_a_certified_registered_entitled_provider_may_produce_evidence_for_a_property_in_england() -> (
    None
):
    d = decide()
    assert (d.outcome, d.zoiko_evidence, d.statutory_excuse_by_zoiko) == (
        rtr.Outcome.ROUTE_AVAILABLE,
        True,
        False,
    )
    assert "LANDLORD_RETAINS_STATUTORY_RESPONSIBILITY" in d.reasons


@pytest.mark.parametrize("nation", ["GB-SCT", "GB-WLS", "GB-NIR"])
def test_t27_a_property_elsewhere_in_the_uk_has_no_england_right_to_rent(nation: str) -> None:
    d = decide(property_subdivision=nation)
    assert (d.outcome, d.route, d.zoiko_evidence) == (rtr.Outcome.NOT_APPLICABLE, None, False)
    assert d.reasons == ("ENGLAND_RTR_NOT_APPLICABLE_IN_UK_NATION",)


def test_never_generalised_beyond_england() -> None:
    assert decide(property_country="IE", property_subdivision=None).reasons == (
        "ENGLAND_RTR_NOT_APPLICABLE_OUTSIDE_ENGLAND",
    )
    assert (
        decide(property_subdivision=None).outcome is rtr.Outcome.NOT_APPLICABLE
    )  # GB with no nation: not assumed to be England


def test_t28_a_provider_certified_for_another_service_is_blocked() -> None:
    d = decide(provider=replace(GOOD, certified_services=frozenset({"IDV"})))
    assert (d.outcome, d.zoiko_evidence) == (
        rtr.Outcome.BLOCKED,
        False,
    ) and "PROVIDER_NOT_CERTIFIED_FOR_RTR" in d.reasons


def test_t06_an_expired_supplementary_code_blocks_new_invocations() -> None:
    d = decide(provider=replace(GOOD, supplementary_code_valid=False))
    assert d.outcome is rtr.Outcome.BLOCKED and "RTR_SUPPLEMENTARY_CODE_EXPIRED" in d.reasons


def test_registration_on_the_dvs_register_is_required_from_1_october_2026() -> None:
    unregistered = replace(GOOD, registered_for_rtr=False)
    assert "PROVIDER_NOT_ON_DVS_REGISTER_FOR_RTR" in decide(provider=unregistered).reasons
    assert (
        decide(provider=unregistered, on=date(2026, 9, 30)).outcome is rtr.Outcome.ROUTE_AVAILABLE
    )  # the day before


def test_t29_the_home_office_share_code_route_is_prescribed_and_nothing_is_substituted() -> None:
    d = decide(route=rtr.Route.HOME_OFFICE_SHARE_CODE, provider=None)
    assert (d.outcome, d.zoiko_evidence) == (rtr.Outcome.PRESCRIBED_ROUTE, False)
    assert d.reasons == ("HOME_OFFICE_ONLINE_CHECK_REQUIRED", "NO_SUBSTITUTED_BIOMETRIC_APPROVAL")
    # even a fully certified provider does not stand in for the Home Office service
    assert decide(route=rtr.Route.HOME_OFFICE_SHARE_CODE).outcome is rtr.Outcome.PRESCRIBED_ROUTE


def test_no_provider_an_unentitled_or_a_suspended_one_is_blocked_with_every_reason() -> None:
    assert decide(provider=None).reasons == ("RTR_PROVIDER_UNKNOWN",)
    d = decide(
        provider=replace(GOOD, entitled=False, suspended=True, certified_services=frozenset())
    )
    assert set(d.reasons) == {
        "PROVIDER_NOT_CERTIFIED_FOR_RTR",
        "PROVIDER_NOT_ENTITLED",
        "PROVIDER_SCOPE_SUSPENDED",
    }


def test_the_landlords_manual_check_is_theirs_and_zoiko_produces_no_evidence() -> None:
    d = decide(route=rtr.Route.LANDLORD_MANUAL, provider=None)
    assert (d.outcome, d.zoiko_evidence) == (rtr.Outcome.ROUTE_AVAILABLE, False)


def test_retention_is_the_configured_schedule_and_is_never_invented() -> None:
    assert "RETENTION_SCHEDULE_NOT_CONFIGURED" in decide().reasons and decide().retention is None
    configured = decide(retention="tenancy plus the statutory period (owner-set)")
    assert configured.retention == "tenancy plus the statutory period (owner-set)"
    assert "RETENTION_SCHEDULE_NOT_CONFIGURED" not in configured.reasons

"""WF06 provider activation controls (CR-113): what still stops a licensed provider being used, by code (S11, ENG-13)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from zid_platform.modules.screening.domain import provider_controls as pc
from zid_platform.modules.screening.infrastructure import providers as registry

GOOD = pc.Provider(
    "vendor-a",
    pc.ProviderState.APPROVED,
    frozenset({"PEP_RCA"}),
    frozenset({"GB"}),
    frozenset({"uk-1"}),
    "c1",
    "p1",
    "finance-owner",
    Decimal("0.40"),
    Decimal(100),
    1000,
)


def test_a_fully_specified_approved_provider_has_no_written_problem() -> None:
    assert pc.activation_problems(GOOD) == []


def test_a_provider_that_is_not_approved_reports_only_its_state() -> None:
    for state in (pc.ProviderState.DISABLED, pc.ProviderState.SANDBOX):
        assert pc.activation_problems(replace(GOOD, state=state, finance_authorised_by=None)) == [
            f"STATE_{state.value}"
        ]


def test_each_missing_control_is_named() -> None:
    cases = {
        "CONTRACT_VERSION_MISSING": {"contract_version": None},
        "PRICE_SCHEDULE_MISSING": {"price_version": None},
        "FINANCE_AUTHORISATION_MISSING": {"finance_authorised_by": None},
        "NO_CONTRACTED_CLASSES": {"classes": frozenset()},
        "NO_CONTRACTED_MARKETS": {"markets": frozenset()},
        "NO_APPROVED_REGIONS": {"regions": frozenset()},
        "UNIT_COST_NOT_SET": {"unit_cost": Decimal(0)},
        "NO_MONTHLY_BUDGET": {"monthly_budget": None},
        "NO_MONTHLY_QUOTA": {"monthly_quota": None},
    }
    for code, change in cases.items():
        assert pc.activation_problems(replace(GOOD, **change)) == [code], code


def test_the_registry_ships_empty_and_reads_strictly() -> None:
    assert registry.providers() == {}
    parsed = registry.parse(
        {
            "providers": {
                "v": {
                    "state": "APPROVED",
                    "classes": ["PEP_RCA"],
                    "unit_cost": "0.5",
                    "monthly_quota": 10,
                }
            }
        }
    )
    assert (
        parsed["v"].provider.unit_cost == Decimal("0.5")
        and parsed["v"].provider.monthly_quota == 10
    )
    for bad in (
        {"providers": {"v": {"state": "LIVE"}}},
        {
            "providers": {"v": {"classes": ["SANCTIONS"]}}
        },  # an official list is not a licensed class
        {"providers": {"v": {"surprise": 1}}},
        {"providers": {"v": {"unit_cost": "free"}}},
        {"providers": {"v": {"unit_cost": "-1"}}},
        {"providers": {"v": "not a mapping"}},
    ):
        try:
            registry.parse(bad)
        except Exception as e:  # noqa: BLE001
            assert getattr(e, "code", "") == "CONFIG_INVALID", bad
        else:
            raise AssertionError(f"accepted {bad}")

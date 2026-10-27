"""Residency gates against the real cell catalogue and residency profiles (ZID-TENANCY-001 §4-6, §11,
§13, §17; DEC-G1). Pure rules over `backend/config`.

- TEN-16 backup region: a recovery/backup cell is only ever one the home cell approves, inside the
  tenant's legal zone for SRP-1+, and never a prohibited cell;
- TEN-17 key boundary: key material is never transferable; it is stored only inside the tenant's
  jurisdiction (SRP-1) or its home cell (SRP-2/3), with no global/other-region fallback;
- TEN-22 transfer minimization: an approved transfer names its exact data class, destination and
  purpose and carries only allowlisted fields;
- TEN-27 analytics gate: raw restricted data does not leave for an analytics/other-cell destination
  without an explicit approved rule for exactly that purpose.

Unproven here: real backup storage locations, real KMS key policies and detection of an
unregistered ETL job (these are deployment facts); the tests prove the platform's gate decisions.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.tenant.domain.residency import (
    DATA_CLASSES,
    Residency,
    ResidencyError,
    TransferRule,
    check_placement,
    check_recovery,
    check_storage,
    check_transfer,
    minimize,
)
from zid_platform.modules.tenant.infrastructure.catalog import catalog

NOW = datetime(2026, 10, 25, tzinfo=UTC)
CAT = catalog()


def tenant(profile: str, home: str = "eu-1", **kw: object) -> Residency:
    base = Residency(
        "t-1", profile, home, ("eu-1-dr",), (), (), "PLATFORM_MANAGED", "eu-gdpr", 1, 1
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def rule(**kw: object) -> TransferRule:
    base = TransferRule(
        "rule-1",
        "DC2",
        "uk-1",
        "SUPPORT",
        "eu_sccs",
        "eu-gdpr",
        ("ticket_id", "severity"),
        NOW - timedelta(days=1),
        None,
        "ACTIVE",
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def decide(r: Residency, rules: list[TransferRule], **kw: object) -> object:
    args: dict[str, object] = {
        "data_class": "DC2",
        "destination_cell_id": "uk-1",
        "purpose_code": "SUPPORT",
        "rules": rules,
        "pack_mechanisms": ("eu_sccs",),
        "now": NOW,
    }
    return check_transfer(CAT, r, **(args | kw))  # type: ignore[arg-type]


def test_the_catalogue_is_what_these_tests_assume() -> None:
    assert CAT.cell("eu-1").legal_zone == "EU" and CAT.cell("eu-1-dr").country == "IE"
    assert CAT.cell("uk-1").legal_zone == "UK" and CAT.cell("eu-1-dr").recovery_only


def test_backup_and_recovery_stay_in_the_approved_region() -> None:  # TEN-16
    check_placement(CAT, "SRP-1", "eu-1", ["eu-1-dr"])  # same EU zone, approved by eu-1
    with pytest.raises(ResidencyError) as e:  # a UK cell is not an approved recovery cell of eu-1
        check_placement(CAT, "SRP-1", "eu-1", ["uk-1"])
    assert e.value.code == "BACKUP_REGION_DENIED"
    with pytest.raises(ResidencyError) as e:  # uk-1 approves no recovery cell at all
        check_placement(CAT, "SRP-1", "uk-1", ["eu-1-dr"])
    assert e.value.code == "BACKUP_REGION_DENIED"
    with pytest.raises(ResidencyError) as e:  # an unknown location fails closed
        check_placement(CAT, "SRP-1", "eu-1", ["mars-1"])
    assert e.value.code == "DEPENDENCY_RESIDENCY_UNKNOWN"

    r = tenant("SRP-1")
    check_recovery(r, "eu-1-dr")
    for cell in ("uk-1", "eu-1", "mars-1"):
        with pytest.raises(ResidencyError) as e:
            check_recovery(r, cell)
        assert e.value.code == "BACKUP_REGION_DENIED"
    # live DC2 may sit anywhere in the legal zone; DC3 only in the home jurisdiction (a backup copy
    # to the approved recovery cell is governed by check_recovery above, not by live placement)
    check_storage(CAT, r, "DC2", "eu-1-dr")
    with pytest.raises(ResidencyError):
        check_storage(CAT, r, "DC3", "eu-1-dr")
    # outside the legal zone, or in a prohibited cell, every restricted class is refused
    for dc in ("DC2", "DC3"):
        with pytest.raises(ResidencyError) as e:
            check_storage(CAT, r, dc, "uk-1")
        assert e.value.code == "DATA_CLASS_PLACEMENT_DENIED"
    with pytest.raises(ResidencyError):
        check_storage(CAT, tenant("SRP-1", prohibited_cells=("eu-1-dr",)), "DC2", "eu-1-dr")


def test_key_material_never_leaves_the_tenant_key_boundary() -> None:  # TEN-17
    # transfer: never, whatever rule exists (KEY_MATERIAL is not ordinary data)
    for profile in ("SRP-0", "SRP-1", "SRP-2", "SRP-3"):
        d = decide(
            tenant(profile),
            [rule(data_class="KEY_MATERIAL", destination_cell_id="eu-1-dr")],
            data_class="KEY_MATERIAL",
            destination_cell_id="eu-1-dr",
        )
        assert not d.allowed and d.code == "KEY_BOUNDARY_MISMATCH", profile  # type: ignore[attr-defined]
    # storage: SRP-1 keeps keys in the home jurisdiction (eu-1-dr is Ireland, eu-1 Germany)
    for profile in ("SRP-1", "SRP-2", "SRP-3"):
        with pytest.raises(ResidencyError) as e:
            check_storage(CAT, tenant(profile), "KEY_MATERIAL", "eu-1-dr")
        assert e.value.code == "DATA_CLASS_PLACEMENT_DENIED", profile
        with pytest.raises(ResidencyError):  # a prohibited-region fallback
            check_storage(CAT, tenant(profile, prohibited_cells=("uk-1",)), "KEY_MATERIAL", "uk-1")
        check_storage(CAT, tenant(profile), "KEY_MATERIAL", "eu-1")  # the home cell is fine
    # only an SRP-0 tenant may place keys anywhere the catalogue knows; unknown cells never
    with pytest.raises(ResidencyError) as e:
        check_storage(CAT, tenant("SRP-1"), "KEY_MATERIAL", "mars-1")
    assert e.value.code == "DEPENDENCY_RESIDENCY_UNKNOWN"


def test_approved_transfers_are_exact_and_minimized() -> None:  # TEN-22
    r = tenant("SRP-1")
    ok = decide(r, [rule()])
    assert ok.allowed and ok.code == "ALLOWED_BY_RULE" and ok.rule_id == "rule-1"  # type: ignore[attr-defined]
    assert ok.field_allowlist == ("ticket_id", "severity")  # type: ignore[attr-defined]

    payload = {"ticket_id": "T-1", "severity": "high", "email": "a@b.example", "name": "Ann"}
    assert minimize(payload, ok.field_allowlist) == {"ticket_id": "T-1", "severity": "high"}  # type: ignore[attr-defined]
    assert minimize(payload, ()) == {}  # no allowlist, no fields
    assert minimize({}, ("ticket_id",)) == {}

    # destination, purpose, data class, mechanism, validity and status must all match exactly
    cases = {
        "other purpose": {"purpose_code": "ANALYTICS"},
        "other destination": {"destination_cell_id": "eu-1-dr"},
        "other data class": {"data_class": "DC3"},
    }
    for name, kw in cases.items():
        d = decide(r, [rule()], **kw)
        assert not d.allowed, name  # type: ignore[attr-defined]
    assert decide(r, [rule()], pack_mechanisms=("uk_idta",)).code == "RESIDENCY_TRANSFER_DENIED"  # type: ignore[attr-defined]
    assert not decide(r, [rule(status="SUSPENDED")]).allowed  # type: ignore[attr-defined]
    assert not decide(r, [rule(valid_to=NOW - timedelta(seconds=1))]).allowed  # type: ignore[attr-defined]
    assert not decide(r, [rule(valid_from=NOW + timedelta(days=1))]).allowed  # type: ignore[attr-defined]
    assert not decide(r, [rule(jurisdiction_pack_id="uk-gdpr")]).allowed  # type: ignore[attr-defined]
    assert not decide(tenant("SRP-1", prohibited_cells=("uk-1",)), [rule()]).allowed  # type: ignore[attr-defined]
    assert decide(r, [rule()], destination_cell_id="mars-1").code == "DEPENDENCY_RESIDENCY_UNKNOWN"  # type: ignore[attr-defined]


def test_raw_restricted_data_does_not_reach_an_analytics_destination_without_a_rule() -> None:
    """TEN-27 (the gate decision; detection of an unregistered ETL job is not part of this)."""
    for profile in ("SRP-1", "SRP-2"):
        r = tenant(profile)
        for dc in ("DC2", "DC3"):
            d = decide(r, [], data_class=dc, purpose_code="ANALYTICS")
            assert not d.allowed and d.code == "RESIDENCY_TRANSFER_DENIED", (profile, dc)  # type: ignore[attr-defined]
        # a rule for support does not authorise analytics, and vice versa
        assert not decide(r, [rule()], purpose_code="ANALYTICS").allowed  # type: ignore[attr-defined]
        assert decide(r, [rule(purpose_code="ANALYTICS")], purpose_code="ANALYTICS").allowed  # type: ignore[attr-defined]
    # public (DC0) data is not restricted; every other class is gated
    assert decide(tenant("SRP-1"), [], data_class="DC0", purpose_code="ANALYTICS").allowed  # type: ignore[attr-defined]
    for dc in DATA_CLASSES[1:]:
        assert not decide(tenant("SRP-2"), [], data_class=dc, purpose_code="ANALYTICS").allowed  # type: ignore[attr-defined]

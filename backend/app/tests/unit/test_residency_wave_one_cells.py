"""The same residency rules hold in each Wave 1 home cell: U.S., EEA and U.K. (ZID-EDR-002 sections 2, 4 and 6; EDR02-01).

Pure rules over `backend/config`. The earlier residency tests prove the rules on one cell; this proves no Wave 1 cell is the exception: tenancy
and evidence stores, audit logs, backups, keys and supplier routes each stay inside the cell's region, recovery is confined to the approved
recovery cell in the same legal zone, and the U.K. cell, which has no approved recovery cell yet, holds its segment instead of borrowing another
region's. What these tests cannot show is that the declared inventory matches the running infrastructure; that is a deployment fact.
"""

from __future__ import annotations

import pytest
from zid_platform.modules.tenant.domain.residency import (
    Residency,
    ResidencyError,
    check_placement,
    check_storage,
    coverage_findings,
    dependency_findings,
)
from zid_platform.modules.tenant.infrastructure.catalog import catalog

CAT = catalog()
# (home cell, its approved recovery cell or None when none is approved yet)
WAVE_ONE = [("us-1", "us-1-dr"), ("eu-1", "eu-1-dr"), ("uk-1", None)]
HOMES = [home for home, _ in WAVE_ONE]
# the places each kind of tenant data lives: tenancy, evidence, audit, backups, keys, suppliers, support
DATA_HOMES = (
    "authoritative_db",
    "object_evidence_store",
    "security_events",
    "logs_traces",
    "backups_snapshots",
    "kms_hsm",
    "subprocessors",
    "support_tooling",
)


def tenant(profile: str, home: str, recovery: str | None) -> Residency:
    return Residency(
        "t-1",
        profile,
        home,
        (recovery,) if recovery else (),
        (),
        (),
        "PLATFORM_MANAGED",
        "pack",
        1,
        1,
    )


@pytest.mark.parametrize(("home", "recovery"), WAVE_ONE)
def test_every_subsystem_that_holds_tenant_data_is_declared_inside_the_cell(
    home: str, recovery: str | None
) -> None:
    cell = CAT.cell(home)
    for profile in ("SRP-1", "SRP-2"):
        assert coverage_findings(CAT, tenant(profile, home, recovery)) == [], (home, profile)
    for subsystem in DATA_HOMES:
        placed = CAT.placements[home][subsystem]
        assert (placed.region, placed.country) == (cell.region, cell.country), (home, subsystem)


@pytest.mark.parametrize(("home", "recovery"), WAVE_ONE)
def test_recovery_is_the_approved_cell_in_the_same_zone_and_nothing_else(
    home: str, recovery: str | None
) -> None:
    if recovery:
        check_placement(CAT, "SRP-1", home, [recovery])
        assert (
            CAT.cell(recovery).recovery_only
            and CAT.cell(recovery).legal_zone == CAT.cell(home).legal_zone
        )
        with pytest.raises(ResidencyError) as e:  # a recovery-only cell never becomes anyone's home
            check_placement(CAT, "SRP-1", recovery, [])
        assert e.value.code == "CELL_NOT_AUTHORITATIVE"
    else:  # the segment is held: no recovery cell is approved, so none can be named
        assert CAT.cell(home).approved_recovery_cells == ()
    others = [c for c in CAT.cells if c != recovery and c != home]
    for (
        cell
    ) in others:  # every other cell, in any zone, is refused as a recovery or backup location
        with pytest.raises(ResidencyError) as e:
            check_placement(CAT, "SRP-1", home, [cell])
        assert e.value.code == "BACKUP_REGION_DENIED", (home, cell)


@pytest.mark.parametrize(("home", "recovery"), WAVE_ONE)
def test_restricted_data_and_keys_never_land_in_another_wave_one_region(
    home: str, recovery: str | None
) -> None:
    r = tenant("SRP-1", home, recovery)
    for data_class in ("DC2", "DC3", "KEY_MATERIAL"):
        check_storage(CAT, r, data_class, home)
        for other in HOMES:
            if other == home:
                continue
            with pytest.raises(ResidencyError) as e:
                check_storage(CAT, r, data_class, other)
            assert e.value.code == "DATA_CLASS_PLACEMENT_DENIED", (home, other, data_class)
    if recovery:
        check_storage(CAT, r, "DC2", recovery)  # live DC2 may sit in the same zone
        if (
            CAT.cell(recovery).country == CAT.cell(home).country
        ):  # the U.S. recovery cell shares the country
            check_storage(CAT, r, "KEY_MATERIAL", recovery)
        else:  # DC3 and keys stay in the home jurisdiction
            with pytest.raises(ResidencyError):
                check_storage(CAT, r, "KEY_MATERIAL", recovery)


@pytest.mark.parametrize(("home", "recovery"), WAVE_ONE)
def test_a_supplier_must_operate_inside_the_cells_zone_or_country(
    home: str, recovery: str | None
) -> None:
    cell = CAT.cell(home)
    assert (
        dependency_findings(
            CAT, tenant("SRP-1", home, recovery), [cell.country, cell.legal_zone, cell.region]
        )
        == []
    )
    assert dependency_findings(CAT, tenant("SRP-2", home, recovery), [cell.country]) == []
    for other in HOMES:
        if CAT.cell(other).legal_zone == cell.legal_zone:
            continue
        foreign = [CAT.cell(other).country, CAT.cell(other).legal_zone]
        assert dependency_findings(CAT, tenant("SRP-1", home, recovery), foreign) != [], (
            home,
            other,
        )
    assert (
        dependency_findings(CAT, tenant("SRP-2", home, recovery), ["XX"]) != []
    )  # an unknown location fails closed

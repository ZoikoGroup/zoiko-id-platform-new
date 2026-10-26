"""Subsystem inventory against residency profiles (ZID-TENANCY-001 §6; TEN-18, TEN-20, TEN-29, TEN-32).

Pure rules over `backend/config`. The inventory is declared configuration; what these tests prove is that the
platform refuses to claim a profile its declared inventory does not satisfy, and that gaps are reported, not
assumed away. Whether the declaration matches the running infrastructure is a deployment fact.
"""

from __future__ import annotations

from dataclasses import replace

from zid_platform.modules.tenant.domain.residency import (
    Placement,
    Residency,
    coverage_findings,
    coverage_matrix,
    dependency_findings,
)
from zid_platform.modules.tenant.infrastructure.catalog import catalog

CAT = catalog()


def tenant(profile: str, home: str = "eu-1") -> Residency:
    return Residency(
        "t-1", profile, home, ("eu-1-dr",), (), (), "PLATFORM_MANAGED", "eu-gdpr", 1, 1
    )


def with_placement(subsystem: str, p: Placement | None, home: str = "eu-1"):  # type: ignore[no-untyped-def]
    placements = {c: dict(m) for c, m in CAT.placements.items()}
    if p is None:
        placements[home].pop(subsystem)
    else:
        placements[home][subsystem] = p
    return replace(CAT, placements=placements)


def test_every_in_scope_subsystem_has_a_declared_location_for_each_serving_cell() -> None:
    """TEN-32: the inventory covers every subsystem the catalogue names, and the matrix reports each one."""
    for cell in ("uk-1", "eu-1"):
        declared = set(CAT.placements[cell])
        assert set(CAT.coverage_subsystems) <= declared, set(CAT.coverage_subsystems) - declared
    matrix = coverage_matrix(CAT, tenant("SRP-1"))
    assert [m["subsystem"] for m in matrix] == list(CAT.coverage_subsystems)
    assert all(m["declared"] and m["compliant"] for m in matrix)
    assert coverage_findings(CAT, tenant("SRP-1")) == []


def test_an_undeclared_subsystem_is_a_finding_not_an_assumption() -> None:
    """TEN-32: remove one subsystem from the inventory and the profile can no longer be proven."""
    cat = with_placement("support_tooling", None)
    [f] = coverage_findings(cat, tenant("SRP-1"))
    assert (f.subsystem, f.code) == ("support_tooling", "NOT_DECLARED")
    row = next(
        m for m in coverage_matrix(cat, tenant("SRP-1")) if m["subsystem"] == "support_tooling"
    )
    assert row["declared"] is False and row["compliant"] is False


def test_logs_and_security_events_outside_the_home_region_break_a_restricted_profile() -> None:
    """TEN-18: a restricted tenant's logs never reach a sink outside its region (SRP-1) or jurisdiction (SRP-2)."""
    elsewhere = Placement("us-east-1", "US", False)
    for sink in ("logs_traces", "security_events"):
        cat = with_placement(sink, elsewhere)
        codes = {f.code for f in coverage_findings(cat, tenant("SRP-1")) if f.subsystem == sink}
        assert codes == {"OUTSIDE_REGION"}
        assert coverage_findings(cat, tenant("SRP-0")) == []  # SRP-0 makes no regional promise
    same_region_other_country = Placement("eu-central-1", "FR", False)
    cat = with_placement("logs_traces", same_region_other_country)
    assert coverage_findings(cat, tenant("SRP-1")) == []
    assert {f.code for f in coverage_findings(cat, tenant("SRP-2"))} == {"OUTSIDE_JURISDICTION"}


def test_a_dedicated_profile_has_no_shared_fallback() -> None:
    """TEN-29: SRP-3 needs every subsystem dedicated; one shared surface is a finding."""
    findings = coverage_findings(CAT, tenant("SRP-3"))
    assert {f.code for f in findings} == {"SHARED_INFRASTRUCTURE"}
    assert {f.subsystem for f in findings} == set(
        CAT.coverage_subsystems
    )  # nothing is dedicated yet
    dedicated = {
        c: {s: replace(p, dedicated=True) for s, p in m.items()} for c, m in CAT.placements.items()
    }
    assert coverage_findings(replace(CAT, placements=dedicated), tenant("SRP-3")) == []
    one_shared = {c: dict(m) for c, m in dedicated.items()}
    one_shared["eu-1"]["cache"] = replace(one_shared["eu-1"]["cache"], dedicated=False)
    [f] = coverage_findings(replace(CAT, placements=one_shared), tenant("SRP-3"))
    assert (f.subsystem, f.code) == ("cache", "SHARED_INFRASTRUCTURE")


def test_a_subprocessor_that_cannot_satisfy_the_profile_is_refused() -> None:
    """TEN-20: locations must sit inside the home zone (SRP-1) or country (SRP-2); SRP-0 and unprofiled pass."""
    assert dependency_findings(CAT, tenant("SRP-1"), ["DE", "IE", "eu"]) == []
    assert dependency_findings(CAT, tenant("SRP-1"), ["US"]) != []
    assert dependency_findings(CAT, tenant("SRP-2"), ["IE"]) != []  # same zone, other country
    assert dependency_findings(CAT, tenant("SRP-2"), ["de"]) == []
    assert dependency_findings(CAT, tenant("SRP-0"), ["US"]) == []
    assert dependency_findings(CAT, None, ["US"]) == []

"""A deployed cell is always a real region; the development cell exists for local and test only.

`local-1` is how one machine places a company and takes sign-ups from every wave 1 country. It must never be what a deployed process
falls back to, and the catalogue must not offer it as a region outside local and test.
"""

from __future__ import annotations

import pytest
from zid_platform.cell_identity import DEVELOPMENT_CELL, resolve_cell_id
from zid_platform.entrypoints.supervisor import plan
from zid_platform.modules.tenant.infrastructure import catalog as catalog_module

DEPLOYED = ("production", "staging", "preprod")


@pytest.mark.parametrize("environment", ["local", "test"])
def test_local_and_test_keep_the_development_cell_as_the_default(environment: str) -> None:
    assert resolve_cell_id({"ZID_ENVIRONMENT": environment}) == DEVELOPMENT_CELL
    assert resolve_cell_id({}) == DEVELOPMENT_CELL  # no environment at all is local
    assert resolve_cell_id({"ZID_ENVIRONMENT": environment, "ZID_CELL_ID": "uk-1"}) == "uk-1"
    assert (
        resolve_cell_id({"ZID_ENVIRONMENT": environment, "ZID_CELL_ID": "  "}) == DEVELOPMENT_CELL
    )


@pytest.mark.parametrize("environment", DEPLOYED)
def test_a_deployed_cell_must_name_its_real_region(environment: str) -> None:
    for cell in ("uk-1", "eu-1", "us-1"):
        assert resolve_cell_id({"ZID_ENVIRONMENT": environment, "ZID_CELL_ID": cell}) == cell
    for missing in ({}, {"ZID_CELL_ID": ""}, {"ZID_CELL_ID": "   "}):
        with pytest.raises(ValueError, match="ZID_CELL_ID is required"):
            resolve_cell_id({"ZID_ENVIRONMENT": environment, **missing})


@pytest.mark.parametrize("environment", DEPLOYED)
def test_a_deployed_cell_is_refused_when_it_names_the_development_cell(environment: str) -> None:
    with pytest.raises(ValueError, match="development cell"):
        resolve_cell_id({"ZID_ENVIRONMENT": environment, "ZID_CELL_ID": DEVELOPMENT_CELL})


def test_the_process_plan_sets_the_resolved_cell_for_the_one_process() -> None:
    assert plan({})[0].env["ZID_CELL_ID"] == DEVELOPMENT_CELL
    assert (
        plan({"ZID_ENVIRONMENT": "production", "ZID_CELL_ID": "eu-1"})[0].env["ZID_CELL_ID"]
        == "eu-1"
    )
    with pytest.raises(ValueError, match="ZID_CELL_ID is required"):
        plan({"ZID_ENVIRONMENT": "production"})
    with pytest.raises(ValueError, match="development cell"):
        plan({"ZID_ENVIRONMENT": "production", "ZID_CELL_ID": DEVELOPMENT_CELL})


def test_the_catalogue_offers_the_development_cell_only_in_local_and_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build = (
        catalog_module.catalog.__wrapped__
    )  # the uncached builder: the catalogue is read once per process
    for environment in ("local", "test"):
        monkeypatch.setenv("ZID_ENVIRONMENT", environment)
        assert DEVELOPMENT_CELL in build().cells
    for environment in DEPLOYED:
        monkeypatch.setenv("ZID_ENVIRONMENT", environment)
        cells = build()
        assert DEVELOPMENT_CELL not in cells.cells and DEVELOPMENT_CELL not in cells.placements
        assert {"uk-1", "eu-1"} <= set(cells.cells)  # the real regions are still all there

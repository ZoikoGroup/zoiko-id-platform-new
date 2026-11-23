"""What people see for a region is a plain place name, never a cell id: "United Kingdom (London)", not "uk-1" or "local-1"."""

from __future__ import annotations

import asyncio
import re

from zid_platform.modules.tenant.api.residency import list_cells
from zid_platform.modules.tenant.infrastructure.catalog import catalog


def test_every_cell_has_a_plain_name_that_is_not_its_id() -> None:
    for cell in catalog().cells.values():
        assert cell.display_name, f"{cell.cell_id} has no display_name"
        assert cell.cell_id not in cell.display_name  # "uk-1" is never what a person reads
        assert not re.search(r"\b[a-z]{2,5}-\d", cell.display_name)  # nor anything shaped like an id
        assert not re.search(r"local|test|dev", cell.display_name, re.IGNORECASE)  # the local test cell reads like the real region it stands for


def test_the_uk_reads_as_the_uk_whether_it_is_the_real_cell_or_the_local_one() -> None:
    cells = catalog().cells
    assert cells["uk-1"].display_name == "United Kingdom (London)"
    assert cells["local-1"].display_name == "United Kingdom (London)"


def test_the_api_returns_the_name_beside_the_id() -> None:
    items = {c["cell_id"]: c for c in asyncio.run(list_cells())["items"]}
    assert items["uk-1"]["display_name"] == "United Kingdom (London)"
    assert items["eu-1"]["display_name"] == "Germany (Frankfurt)"
    assert all("display_name" in c for c in items.values())

"""Which cell a process is, and why a deployed one can never be the development stand-in.

`local-1` is the development cell: it lets one machine place a company and take sign-ups from every wave 1 country, and it is in the cell
catalogue (`backend/config/cells/cells.yaml`) for that reason only. A deployed environment runs as a real region (`uk-1`, `eu-1`, `us-1`):
it has to be told which one with ZID_CELL_ID, there is no default, and it is refused when it names the stand-in.

Local and test environments keep the old behaviour: no ZID_CELL_ID means `local-1`.
"""

from __future__ import annotations

from collections.abc import Mapping

LOCAL_ENVIRONMENTS = ("local", "test")
DEVELOPMENT_CELL = "local-1"


def resolve_cell_id(environ: Mapping[str, str]) -> str:
    """The cell this process runs as, from its environment (pure; unit-tested)."""
    environment = environ.get("ZID_ENVIRONMENT", "local")
    cell = (environ.get("ZID_CELL_ID") or "").strip()
    if environment in LOCAL_ENVIRONMENTS:
        return cell or DEVELOPMENT_CELL
    if not cell:
        raise ValueError(
            f"ZID_CELL_ID is required when ZID_ENVIRONMENT={environment}: name this cell's real region, for example uk-1, eu-1 or us-1. "
            "There is no default, so a cell is never started as a development cell by mistake."
        )
    if cell == DEVELOPMENT_CELL:
        raise ValueError(
            f"ZID_CELL_ID={DEVELOPMENT_CELL} is the development cell and is not allowed when ZID_ENVIRONMENT={environment}: "
            "name a real region, for example uk-1, eu-1 or us-1."
        )
    return cell

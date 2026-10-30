"""List where the Commercial Estate and the capability bands of ZID-ENG-DEC-008 disagree (read only).

    python -m zid_platform.entrypoints.entitlement_report

For each feature that a catalogue capability carries, it compares the record's band cell with the mode the live plan catalogue gives
the plan mapped to that band, and prints every difference with its kind:

  BASELINE_NOT_INCLUDED_BY_ESTATE  the record says mandatory in every band, the Estate sells it as a paid or contract item
  ESTATE_STRICTER                  the record includes it in this band, the Estate does not
  ESTATE_MORE_GENEROUS             the Estate includes it in a band where the record does not

The Estate decides until the owners set `band_matrix_enforcement: ENFORCE` in `config/entitlements/capability-bands.yaml` (and map
the bands to signed SKUs); this list is what they rule on. It changes nothing. Run it against the cell database
(ZID_CELL_DATABASE_URL); the catalogue tables carry no tenant data.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from zid_platform.modules.commercial.domain.bands import reconcile
from zid_platform.modules.commercial.infrastructure.bands import band_matrix


async def report(database_url: str) -> int:
    m = band_matrix()
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as c:
            rows = (
                await c.execute(
                    text("SELECT plan_code, capability_key, mode FROM commercial.plan_capability")
                )
            ).all()
    finally:
        await engine.dispose()
    estate: dict[str, dict[str, str]] = {}
    for plan, key, mode in rows:
        estate.setdefault(str(plan), {})[str(key)] = str(mode)
    found = reconcile(m, estate)
    print(f"Bands {m.version}; band matrix enforcement: {m.enforcement}")
    print(f"{len(found)} difference(s) between the Commercial Estate and the capability bands:")
    for d in sorted(found, key=lambda d: (d.kind, d.feature_id, d.band)):
        print(
            f"  {d.kind:34} {d.band} ({d.plan:10}) record={d.band_cell} estate={d.estate_mode:9} {d.feature_id}"
        )
    return 0


def main() -> int:
    url = os.environ.get("ZID_CELL_DATABASE_URL") or os.environ.get("ZID_DATABASE_URL")
    if not url:
        print("set ZID_CELL_DATABASE_URL", file=sys.stderr)
        return 2
    return asyncio.run(report(url))


if __name__ == "__main__":
    raise SystemExit(main())

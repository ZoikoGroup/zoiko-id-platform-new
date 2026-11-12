"""Record the first residency baseline of a signed-up workspace on a LOCAL cell (development only; synthetic data).

Screening a company's owners stores restricted person records, which the platform holds only once the organization has a residency profile
("fails closed without a residency record"). In production the owner confirms that baseline on the Residency page, which needs the
`tenant.residency.manage` authority, and a brand-new owner has no authority yet, so company verification stops with SCREENING_RESIDENCY_DENIED.
For a local walk-through this does what the console's baseline dialog does, for the workspace you name, through the application's own
`set_residency` (so the placement checks, the evidence and the events are the same): profile SRP-1 on the workspace's own home cell, no recovery
cell, the UK GDPR pack. It grants no authority and changes no role.

    python tools/scripts/set_local_residency.py                    # list the workspaces and whether they have a baseline
    python tools/scripts/set_local_residency.py --tenant-id <id>   # record the baseline for one workspace

Safe to run again (a workspace that has a baseline is left alone). Refuses to run unless ZID_ENVIRONMENT is local or test and the database
is on this machine.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from bootstrap_local import cell_db_url
from localenv import BACKEND_ENV, load_backend_env


async def workspaces(url: str) -> list[dict[str, object]]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT t.tenant_id, t.display_name, t.tenant_slug, t.home_cell_id, a.principal_id, "
                        "EXISTS (SELECT 1 FROM platform.tenant_residency r WHERE r.tenant_id = t.tenant_id) AS has_baseline "
                        "FROM platform.tenant t JOIN platform.tenant_role_assignment a "
                        "ON a.tenant_id = t.tenant_id AND a.role = 'OWNER' AND a.status <> 'REVOKED' "
                        "ORDER BY t.created_at DESC"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    return [dict(r._mapping) for r in rows]


async def baseline(url: str, tenant: str, owner: str, home_cell: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from zid_context import RequestContext
    from zid_db import tenant_transaction
    from zid_platform.modules.tenant.application import residency as service

    engine = create_async_engine(url)
    ctx = RequestContext(
        "req_residency_local",
        "cor_residency_local",
        "set_local_residency",
        home_cell,
        tenant_id=tenant,
    )
    try:
        async with tenant_transaction(engine, ctx) as conn:
            await service.set_residency(
                conn,
                tenant,
                profile="SRP-1",
                home_cell_id=home_cell,
                recovery_cells=[],
                prohibited_cells=[],
                operator_juris=[],
                key_profile="PLATFORM_MANAGED",
                jurisdiction_pack_id="uk-gdpr",
                approval_ref="LOCAL-DEV-FIRST-BASELINE",
                actor=owner,
            )
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tenant-id", help="the workspace to record the baseline for")
    args = parser.parse_args()

    if not load_backend_env():
        print(f"{BACKEND_ENV} not found: copy backend/.env.example to backend/.env first.")
        return 1
    if os.environ.get("ZID_ENVIRONMENT", "local") not in ("local", "test"):
        print("set_local_residency only runs with ZID_ENVIRONMENT=local or test.")
        return 1
    url = cell_db_url()
    if "localhost" not in url and "127.0.0.1" not in url:
        print("Refusing to change a database that is not on this machine.")
        return 1

    found = asyncio.run(workspaces(url))
    if not args.tenant_id:
        for w in found[:15]:
            print(
                f"{w['tenant_id']}  {w['tenant_slug']!s:<26} home cell {w['home_cell_id']:<8} baseline {'yes' if w['has_baseline'] else 'NO'}"
            )
        print("\nRun again with --tenant-id <id> to record the first baseline for a workspace.")
        return 0
    chosen = [w for w in found if str(w["tenant_id"]) == args.tenant_id]
    if not chosen:
        print("No such workspace with an owner.")
        return 1
    w = chosen[0]
    if w["has_baseline"]:
        print(f"{w['tenant_slug']} already has a residency baseline: nothing to do.")
        return 0
    asyncio.run(baseline(url, str(w["tenant_id"]), str(w["principal_id"]), str(w["home_cell_id"])))
    print(
        f"residency baseline recorded for {w['tenant_slug']} (SRP-1, home cell {w['home_cell_id']}, uk-gdpr)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

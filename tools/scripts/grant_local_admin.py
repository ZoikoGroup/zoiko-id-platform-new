"""Make a signed-up workspace a tenant administrator on a LOCAL cell (development only; synthetic data).

A person who signs up gets a DRAFT workspace whose OWNER is PENDING_VERIFICATION, with no organization, root of trust or authority
grant, so every administration page answers "Not permitted". In production that standing comes from verification and review. For a
local walk-through of the console this does what bootstrap_local.py does for its seeded administrator, for the workspace you name:
activates the workspace and its OWNER, creates an organization with a registry root, issues the owner an authority grant for every
platform administration action, and activates a policy release that permits an action exactly when the authority chain is valid.

    python tools/scripts/grant_local_admin.py                    # list the workspaces and their owners
    python tools/scripts/grant_local_admin.py --all              # every "Personal workspace"
    python tools/scripts/grant_local_admin.py --tenant-id <id>   # one workspace

Safe to run again. Refuses to run unless ZID_ENVIRONMENT is local or test and the database is on this machine.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

from bootstrap_local import cell_db_url, ensure_admin
from localenv import BACKEND_ENV, load_backend_env

NAMESPACE = uuid.UUID("5b0f0c52-6a1e-4d0e-9c43-0d9a7a1d3c11")  # so the same workspace always gets the same ids


def ids(tenant: str) -> dict[str, str]:
    return {
        "org_id": str(uuid.uuid5(NAMESPACE, f"org:{tenant}")),
        "root_id": str(uuid.uuid5(NAMESPACE, f"root:{tenant}")),
        "holder_id": str(uuid.uuid5(NAMESPACE, f"holder:{tenant}")),
    }


async def workspaces(url: str) -> list[dict[str, object]]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT t.tenant_id, t.display_name, t.status, a.principal_id, a.status AS owner_status "
                        "FROM platform.tenant t JOIN platform.tenant_role_assignment a "
                        "ON a.tenant_id = t.tenant_id AND a.role = 'OWNER' AND a.status <> 'REVOKED' "
                        "ORDER BY t.created_at"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    return [dict(r._mapping) for r in rows]


async def grant(url: str, tenant: str, principal: str, cell_id: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from zid_context import RequestContext
    from zid_db import tenant_transaction

    engine = create_async_engine(url)
    ctx = RequestContext("req_grant_local", "cor_grant_local", "grant_local_admin", cell_id, tenant_id=tenant)
    try:
        async with tenant_transaction(engine, ctx) as c:
            await ensure_admin(c, tenant_id=tenant, principal_id=principal, **ids(tenant))
            await c.execute(
                text(
                    "UPDATE platform.tenant SET organization_id = :o, status = 'ACTIVE' "
                    "WHERE tenant_id = :t AND (organization_id IS NULL OR status <> 'ACTIVE')"
                ),
                {"t": uuid.UUID(tenant), "o": uuid.UUID(ids(tenant)["org_id"])},
            )
            await c.execute(
                text(
                    "UPDATE platform.tenant_role_assignment SET status = 'ACTIVE', activated_at = now() "
                    "WHERE tenant_id = :t AND principal_id = :p AND role = 'OWNER' AND status = 'PENDING_VERIFICATION'"
                ),
                {"t": uuid.UUID(tenant), "p": uuid.UUID(principal)},
            )
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant-id", help="the workspace to make an administrator")
    parser.add_argument("--all", action="store_true", help='every workspace named "Personal workspace"')
    args = parser.parse_args()

    if not load_backend_env():
        print(f"{BACKEND_ENV} not found: copy backend/.env.example to backend/.env first.")
        return 1
    if os.environ.get("ZID_ENVIRONMENT", "local") not in ("local", "test"):
        print("grant_local_admin only runs with ZID_ENVIRONMENT=local or test.")
        return 1
    url = cell_db_url()
    if "localhost" not in url and "127.0.0.1" not in url:
        print("Refusing to change a database that is not on this machine.")
        return 1

    found = asyncio.run(workspaces(url))
    if not args.tenant_id and not args.all:
        for w in found:
            print(f"{w['tenant_id']}  {w['display_name']!s:<24} workspace {w['status']:<7} owner {w['principal_id']} {w['owner_status']}")
        print("\nRun again with --all or --tenant-id <id> to make a workspace an administrator.")
        return 0
    chosen = [w for w in found if (str(w["tenant_id"]) == args.tenant_id if args.tenant_id else w["display_name"] == "Personal workspace")]
    if not chosen:
        print("No matching workspace.")
        return 1
    cell_id = os.environ.get("ZID_CELL_ID", "local-1")
    for w in chosen:
        asyncio.run(grant(url, str(w["tenant_id"]), str(w["principal_id"]), cell_id))
        print(f"administrator standing granted: workspace {w['tenant_id']} (owner {w['principal_id']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

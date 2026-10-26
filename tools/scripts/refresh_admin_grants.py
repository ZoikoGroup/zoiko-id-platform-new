"""Give an existing company's owner (and administrators) the current tenant-administration actions (development only).

A grant never changes after it is issued, so a company registered before the administration actions grew keeps the
old, smaller set: pages that need a newer action (billing, settings, alerts, contacts, webhooks) answer 403 for it.
This issues the owner one additional grant carrying the current set, on the same register root, that can be handed one
level down, and re-issues each active administrator's delegated grant under it. Nothing is edited or removed: the old
grants stay, and each grant is valid for a year like the original.

    python tools/scripts/refresh_admin_grants.py --workspace nexalytech

Refuses to run unless ZID_ENVIRONMENT is local or test and the database is on this machine.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import site
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "backend" / ".venv" / "Lib" / "site-packages", ROOT / "backend" / ".venv" / "lib"):
    if extra.exists():
        site.addsitedir(str(extra))
sys.path.insert(0, str(Path(__file__).parent))

from localenv import load_backend_env  # noqa: E402

load_backend_env()
os.environ.setdefault("ZID_ACTION_CATALOG", str(ROOT / "contracts" / "action-catalog" / "actions.yaml"))


async def main(workspace: str, db_url: str) -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from zid_context import RequestContext, uuid7
    from zid_db import tenant_transaction
    from zid_platform.modules.authority.ports import (
        Authority,
        ContinuityMode,
        Grant,
        GrantStatus,
        GrantType,
        Window,
        issue_grant,
        load_grants_for_grantee,
    )
    from zid_platform.modules.onboarding.domain.gates import TENANT_ADMIN_ACTIONS
    from zid_platform.modules.onboarding.ports import seed_administrator_grant
    from zid_platform.protected import catalog

    eng = create_async_engine(db_url)
    try:
        async with eng.connect() as c:
            t = (
                await c.execute(
                    text("SELECT tenant_id, home_cell_id FROM platform.tenant WHERE tenant_slug = :s"), {"s": workspace}
                )
            ).first()
            if t is None:
                print(f"No company with workspace name {workspace!r}.")
                return 1
            roles = (
                await c.execute(
                    text(
                        "SELECT principal_id, role FROM platform.tenant_role_assignment "
                        "WHERE tenant_id = :t AND status = 'ACTIVE'"
                    ),
                    {"t": t.tenant_id},
                )
            ).all()
        tenant = str(t.tenant_id)
        owner = next((str(r.principal_id) for r in roles if r.role == "OWNER"), None)
        admins = [str(r.principal_id) for r in roles if r.role == "ADMIN"]
        if owner is None:
            print("This company has no active owner yet.")
            return 1
        ctx = RequestContext("req_refresh", "cor_refresh", "refresh", str(t.home_cell_id), tenant_id=tenant)
        now = datetime.now(UTC)
        wanted = tuple(a for a in TENANT_ADMIN_ACTIONS if a in catalog())
        async with tenant_transaction(eng, ctx) as conn:
            grants = await load_grants_for_grantee(conn, owner)
            top = next((g for g in grants if g.parent_grant_id is None and g.status is GrantStatus.ACTIVE and g.root_id), None)
            if top is None:
                print("The owner holds no active top-level grant to build on.")
                return 1
            have = set().union(*[set(g.authority.actions) for g in grants if g.status is GrantStatus.ACTIVE and g.delegation_allowed])
            if set(wanted) <= have:
                print("The owner already holds a delegable grant with every current action.")
            else:
                await issue_grant(
                    conn,
                    Grant(
                        str(uuid7()),
                        tenant,
                        GrantType.ROLE_ISSUED,
                        owner,
                        owner,
                        top.represented_principal_id,
                        Authority(actions=frozenset(wanted), window=Window(now - timedelta(seconds=1), now + timedelta(days=365))),
                        ContinuityMode.PERSON_BOUND,
                        GrantStatus.ACTIVE,
                        now,
                        root_id=top.root_id,
                        delegation_allowed=True,
                        max_delegation_depth=1,
                    ),
                    risk_level=3,
                    now=now,
                )
                print(f"Issued the owner a grant with {len(wanted)} actions.")
            for admin in admins:
                gid = await seed_administrator_grant(
                    conn, tenant, admin_principal_id=admin, inviter_principal_id=owner, catalog=catalog(), now=now
                )
                print(f"Administrator {admin[:8]}…: {'granted ' + gid[:8] + '…' if gid else 'nothing to grant'}")
        return 0
    finally:
        await eng.dispose()


if __name__ == "__main__":
    if os.environ.get("ZID_ENVIRONMENT", "local") not in ("local", "test"):
        sys.exit("Refusing to run outside a local or test environment.")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workspace", required=True)
    default_url = os.environ.get(
        "ZID_CELL_DATABASE_URL",
        f"postgresql+asyncpg://zid:{os.environ.get('ZID_DB_PASSWORD', 'zid_local_only')}@localhost:{os.environ.get('ZID_DB_HOST_PORT', '5432')}/zid_cell",
    )
    p.add_argument("--database-url", default=default_url)
    a = p.parse_args()
    if "localhost" not in a.database_url and "127.0.0.1" not in a.database_url:
        sys.exit("Refusing to change grants in a database that is not on this machine.")
    sys.exit(asyncio.run(main(a.workspace, a.database_url)))

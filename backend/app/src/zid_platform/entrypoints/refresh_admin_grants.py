"""Give an existing company's owner (and administrators) the current tenant-administration actions.

    python -m zid_platform.entrypoints.refresh_admin_grants --workspace acme            # dry run: says what it would do
    python -m zid_platform.entrypoints.refresh_admin_grants --workspace acme --apply    # does it

A grant never changes after it is issued, so a company registered before the administration actions grew keeps the old,
smaller set: pages that need a newer action (billing, card payments, settings, alerts, contacts, webhooks) answer 403 for
it. This issues the owner one additional grant carrying the current set, on the same register root, that can be handed one
level down, and re-issues each active administrator's delegated grant under it (without the owner-only actions, see
`OWNER_ONLY_ACTIONS`). Nothing is edited or removed: the old grants stay, and each grant is valid for a year like the
original.

It is a platform-operations job, run by someone who can reach the cell database (ZID_CELL_DATABASE_URL), in any
environment: a dry run is the default, and `--apply` is the explicit act. It never touches a company that has no active
owner or no active top-level grant to build on.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from zid_context import RequestContext, uuid7
from zid_db import tenant_transaction


async def refresh(workspace: str, database_url: str, *, apply: bool) -> int:
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

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as c:
            t = (
                await c.execute(
                    text(
                        "SELECT tenant_id, home_cell_id FROM platform.tenant WHERE tenant_slug = :s"
                    ),
                    {"s": workspace},
                )
            ).first()
            if t is None:
                print(f"No company with workspace name {workspace!r}.", file=sys.stderr)
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
            print("This company has no active owner yet.", file=sys.stderr)
            return 1
        ctx = RequestContext(
            "req_refresh", "cor_refresh", "refresh", str(t.home_cell_id), tenant_id=tenant
        )
        now = datetime.now(UTC)
        wanted = tuple(a for a in TENANT_ADMIN_ACTIONS if a in catalog())
        async with tenant_transaction(engine, ctx) as conn:
            grants = await load_grants_for_grantee(conn, owner)
            top = next(
                (
                    g
                    for g in grants
                    if g.parent_grant_id is None and g.status is GrantStatus.ACTIVE and g.root_id
                ),
                None,
            )
            if top is None:
                print("The owner holds no active top-level grant to build on.", file=sys.stderr)
                return 1
            have: set[str] = set()
            for g in grants:
                if g.status is GrantStatus.ACTIVE and g.delegation_allowed:
                    held = g.authority.actions
                    have |= (
                        set(wanted) if not isinstance(held, frozenset) else set(held)
                    )  # a grant for any action
            missing = [a for a in wanted if a not in have]
            if not missing:
                print("The owner already holds a delegable grant with every current action.")
            elif not apply:
                print(
                    f"Would issue the owner a grant adding {len(missing)} action(s): {', '.join(missing)}"
                )
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
                        Authority(
                            actions=frozenset(wanted),
                            window=Window(now - timedelta(seconds=1), now + timedelta(days=365)),
                        ),
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
                print(
                    f"Issued the owner a grant with {len(wanted)} actions (new: {', '.join(missing)})."
                )
            for admin in admins:
                if not apply:
                    print(f"Would re-issue administrator {admin[:8]}… the current delegated set.")
                    continue
                gid = await seed_administrator_grant(
                    conn,
                    tenant,
                    admin_principal_id=admin,
                    inviter_principal_id=owner,
                    catalog=catalog(),
                    now=now,
                )
                print(
                    f"Administrator {admin[:8]}…: {'granted ' + gid[:8] + '…' if gid else 'nothing to grant'}"
                )
        if not apply:
            print("Dry run: nothing was changed. Run again with --apply to do it.")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--workspace", required=True, help="the company's workspace name (slug)")
    parser.add_argument(
        "--apply", action="store_true", help="issue the grants (the default is a dry run)"
    )
    args = parser.parse_args()
    url = os.environ.get("ZID_CELL_DATABASE_URL") or os.environ.get("ZID_DATABASE_URL")
    if not url:
        print("set ZID_CELL_DATABASE_URL", file=sys.stderr)
        return 2
    return asyncio.run(refresh(args.workspace, url, apply=args.apply))


if __name__ == "__main__":
    sys.exit(main())

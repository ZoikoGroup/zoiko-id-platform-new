"""Create this cell's registration tenant: `python -m zid_platform.entrypoints.seed_registration_tenant [--apply]`.

Person-first sign-up (ZID-UX-WF-013A) needs one platform-owned tenant per cell, where requests that have no account yet and the email
log for their verification mail sit. It holds no people and no data of anyone's. Run it once per cell, then set the printed id as
ZID_REGISTRATION_TENANT_ID. It is idempotent: if the workspace already exists it prints the existing id and changes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy.ext.asyncio import create_async_engine

WORKSPACE = "zoiko-registration"


async def seed(database_url: str, cell_id: str, *, apply: bool) -> int:
    from zid_platform.modules.tenant.ports import self_signup, workspace

    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            found = await workspace(conn, WORKSPACE)
            if found is not None:
                print(f"ZID_REGISTRATION_TENANT_ID={found['tenant_id']}  (already exists)")
                return 0
            if not apply:
                print(
                    f"Dry run: would create the registration tenant {WORKSPACE!r} on cell {cell_id}."
                )
                return 0
            made = await self_signup(
                conn,
                display_name="Zoiko iD registration",
                tenant_slug=WORKSPACE,
                deployment_mode="O0",
                home_cell_id=cell_id,
                console=None,
            )
            print(f"ZID_REGISTRATION_TENANT_ID={made.tenant.tenant_id}")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="create it (the default is a dry run)")
    args = parser.parse_args()
    url = os.environ.get("ZID_CELL_DATABASE_URL") or os.environ.get("ZID_DATABASE_URL")
    cell = os.environ.get("ZID_CELL_ID")
    if not url or not cell:
        print("set ZID_CELL_DATABASE_URL and ZID_CELL_ID", file=sys.stderr)
        return 2
    from zid_platform.modules.evidence.ports import configure_runtime, runtime_from_settings
    from zid_platform.settings import Settings

    settings = Settings(database_url=url)
    if settings.environment not in ("local", "test"):
        configure_runtime(runtime_from_settings(settings))
    return asyncio.run(seed(url, cell, apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())

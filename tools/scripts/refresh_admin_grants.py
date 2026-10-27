"""Give an existing company's owner (and administrators) the current tenant-administration actions (development wrapper;
the real tool is `python -m zid_platform.entrypoints.refresh_admin_grants`, which runs anywhere and defaults to a dry run).

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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "backend" / ".venv" / "Lib" / "site-packages", ROOT / "backend" / ".venv" / "lib"):
    if extra.exists():
        site.addsitedir(str(extra))
sys.path.insert(0, str(Path(__file__).parent))

from localenv import load_backend_env

load_backend_env()
os.environ.setdefault("ZID_ACTION_CATALOG", str(ROOT / "contracts" / "action-catalog" / "actions.yaml"))


async def main(workspace: str, db_url: str) -> int:
    """The work lives in the platform package (any environment, dry run by default); this wrapper keeps the local-only
    guard and applies it, as before."""
    from zid_platform.entrypoints.refresh_admin_grants import refresh

    return await refresh(workspace, db_url, apply=True)


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

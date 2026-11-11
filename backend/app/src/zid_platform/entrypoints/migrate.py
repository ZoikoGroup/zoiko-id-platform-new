"""The one-shot migration step: schema upgrade, then the operator account from configuration.

    python -m zid_platform.entrypoints.migrate

Runs every module's migrations on the platform database (ZID_CELL_DATABASE_URL; the global plane's streams use the same
database unless ZID_GLOBAL_DATABASE_URL is set), then, when
ZID_OPERATOR_EMAIL, ZID_OPERATOR_PASSWORD and ZID_OPERATOR_TENANT_ID are set, creates the operator principal, the Operator
Console client and the operator profile if missing (entrypoints/bootstrap_operator.py). Re-running it changes nothing that exists.
"""

from __future__ import annotations

import asyncio
import os
import sys

from zid_platform.entrypoints.bootstrap_operator import BootstrapError, provision_from_settings
from zid_platform.migrations import upgrade
from zid_platform.settings import Settings


def main() -> int:
    cell_url = os.environ["ZID_CELL_DATABASE_URL"]
    upgrade(cell_url)
    # the global plane lives in the same database unless ZID_GLOBAL_DATABASE_URL points elsewhere
    upgrade(os.environ.get("ZID_GLOBAL_DATABASE_URL") or cell_url, role="global")
    print("migrations applied")
    try:
        if asyncio.run(provision_from_settings(Settings(), cell_url)):
            print("operator account ready")
        else:
            print("operator account not configured: skipped")
    except BootstrapError as e:
        print(f"migrate: operator account: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

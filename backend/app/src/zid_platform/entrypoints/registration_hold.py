"""Look at one sign-up request for support: `python -m zid_platform.entrypoints.registration_hold --workspace zoiko-registration --request <id>`.

A person who sees "We couldn't complete registration securely" is told nothing more (ZID-UX-WF-013A S9), so support needs a way to see what
state their request is in. This is read-only and shows only the request's state, country, journey, timestamps and counters: never the
address (only a keyed hash exists once it is proved, and none is shown), the token, or any score. It changes nothing; releasing a held
request is a security decision the owners have not made (docs/signoff/signup-owner-pack.md item 9). Run against the cell database.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from zid_context import RequestContext
from zid_db import tenant_transaction


async def show(database_url: str, workspace: str, request_ref: str) -> int:
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
            print(f"No registration tenant with workspace name {workspace!r}.", file=sys.stderr)
            return 1
        ctx = RequestContext(
            "req_hold",
            "cor_hold",
            "registration-hold",
            str(t.home_cell_id),
            tenant_id=str(t.tenant_id),
        )
        async with tenant_transaction(engine, ctx) as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT state, country, journey, locale, notice_version, home_cell_id, existing_account, resend_count, "
                        "confirm_attempts, created_at, expires_at FROM platform.registration_intent WHERE request_ref = :r"
                    ),
                    {"r": request_ref},
                )
            ).first()
        if row is None:
            print("No such request.", file=sys.stderr)
            return 1
        for key, value in row._mapping.items():
            print(f"{key}: {value}")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--workspace", required=True, help="the registration tenant's workspace name"
    )
    parser.add_argument("--request", required=True, help="the request id the person quotes")
    args = parser.parse_args()
    url = os.environ.get("ZID_CELL_DATABASE_URL") or os.environ.get("ZID_DATABASE_URL")
    if not url:
        print("set ZID_CELL_DATABASE_URL", file=sys.stderr)
        return 2
    return asyncio.run(show(url, args.workspace, args.request))


if __name__ == "__main__":
    raise SystemExit(main())

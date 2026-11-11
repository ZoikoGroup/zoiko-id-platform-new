"""Fill the attestor registration file's approved digests from a company's approved builds (CR-064).

    python -m zid_platform.entrypoints.attestor_sync --workspace acme --registrations workloads.json            # show the difference
    ... --write                                                                                                 # rewrite the file
    ... --check                                                                                                 # exit 1 if the file is out of date

A workload's digest is listed only while an ACTIVE binding for it runs a build a second person approved. Selectors stay hand-written. The
output is a change to a reviewed file: read the difference, commit it, and deploy it like any other change to what an attestor may vouch
for. Reads one company's records through its own transaction (ZID_CELL_DATABASE_URL), so it needs no more access than the cell has.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from zid_context import RequestContext
from zid_db import tenant_transaction

from zid_platform.attestor import sync

# A binding's digest may be spelled `sha-256:` or `sha256:`; builds are stored in the second form.
APPROVED_BY_WORKLOAD = """
    SELECT b.workload_identity_id AS spiffe_id, a.artifact_digest AS digest
    FROM agent.workload_binding b
    JOIN agent.build_artifact a
      ON a.tenant_id = b.tenant_id AND a.artifact_digest = replace(lower(b.artifact_digest), 'sha-256:', 'sha256:')
    WHERE b.status = 'ACTIVE' AND b.valid_to > now() AND a.review_status = 'APPROVED'
"""


async def approved_by_workload(conn: AsyncConnection) -> dict[str, frozenset[str]]:
    """SPIFFE ID -> the approved digests of its active bindings, for the company this transaction is scoped to."""
    found: dict[str, set[str]] = {}
    for r in (await conn.execute(text(APPROVED_BY_WORKLOAD))).all():
        found.setdefault(str(r.spiffe_id), set()).add(str(r.digest))
    return {k: frozenset(v) for k, v in found.items()}


async def run(
    workspace: str, registrations: Path, database_url: str, *, write: bool, check: bool
) -> int:
    try:
        raw = json.loads(registrations.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"cannot read {registrations}: {e}", file=sys.stderr)
        return 2
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
            return 2
        tenant = str(t.tenant_id)
        ctx = RequestContext(
            "req_attestor_sync",
            "cor_attestor_sync",
            "attestor-sync",
            str(t.home_cell_id),
            tenant_id=tenant,
        )
        async with tenant_transaction(engine, ctx) as conn:
            approved = await approved_by_workload(conn)
    finally:
        await engine.dispose()

    plan = sync.plan(raw, approved, tenant_id=tenant)
    for w in plan.warnings:
        print(f"warning: {w}", file=sys.stderr)
    for ch in plan.changes:
        print(ch.spiffe_id)
        for d in ch.added:
            print(f"  + {d}")
        for d in ch.removed:
            print(f"  - {d}")
    if not plan.changed:
        print("The registration file already matches the approved builds.")
        return 0
    if write:
        registrations.write_text(json.dumps(plan.registrations, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {registrations}. Review the difference and commit it.")
        return 0
    print(
        "Run again with --write to apply this."
        if not check
        else "The registration file is out of date.",
        file=sys.stderr,
    )
    return 1 if check else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="attestor_sync", description=__doc__.split("\n")[0])
    p.add_argument("--workspace", required=True, help="the company's workspace name")
    p.add_argument("--registrations", required=True, type=Path)
    p.add_argument("--write", action="store_true")
    p.add_argument(
        "--check", action="store_true", help="exit 1 when the file is out of date (for a pipeline)"
    )
    args = p.parse_args(argv)
    url = os.environ.get("ZID_CELL_DATABASE_URL") or os.environ.get("ZID_DATABASE_URL")
    if not url:
        print("set ZID_CELL_DATABASE_URL", file=sys.stderr)
        return 2
    return asyncio.run(
        run(args.workspace, args.registrations, url, write=args.write, check=args.check)
    )


if __name__ == "__main__":
    raise SystemExit(main())

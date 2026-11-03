"""Activate an organization's proposed independent reviewer after Zoiko iD verified them (ZID-ENG-DEC-012, slice L-10, CR-047).

    python -m zid_platform.entrypoints.verify_reviewer --workspace acme --reviewer <uuid> --evidence-ref TRUST-OPS-123    # dry run
    ... --apply                                                                                                         # does it

A solo organization cannot reach independence from its own members, so it proposes an outside reviewer (an external professional, a
co-director, a trustee, an authorized delegate). The appointment becomes real in one of two ways: an independent A1 approval, or this
step, where a person in Zoiko iD trust operations records that the reviewer's identity and documented authority were checked. The
evidence is a reference, never the document. This is a platform-operations step run against the cell database
(ZID_CELL_DATABASE_URL); no tenant route does it, and it writes an E3 audit record. Who in Zoiko iD may run it, and from what
evidence, is an owner ruling (docs/signoff/approvals-owner-pack.md item 2).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from zid_context import RequestContext
from zid_db import tenant_transaction


async def verify(
    workspace: str, database_url: str, reviewer: str, evidence_ref: str, *, apply: bool
) -> int:
    from zid_platform.modules.tenant.application.reviewers import verify_by_platform
    from zid_platform.modules.tenant.domain.approvals import ApprovalError
    from zid_platform.modules.tenant.infrastructure.approvals import load_reviewer

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
        tenant = str(t.tenant_id)
        ctx = RequestContext(
            "req_reviewer", "cor_reviewer", "verify-reviewer", str(t.home_cell_id), tenant_id=tenant
        )
        async with tenant_transaction(engine, ctx) as conn:
            r = await load_reviewer(conn, reviewer)
            if r is None or r.status != "PROPOSED":
                print("No proposed reviewer with that id; nothing was written.", file=sys.stderr)
                return 1
            if not apply:
                print(
                    f"Dry run: would activate the {r.relationship} reviewer {reviewer} "
                    f"(evidence {evidence_ref})."
                )
                return 0
            try:
                done = await verify_by_platform(conn, reviewer, evidence_ref=evidence_ref)
            except ApprovalError as e:
                print(f"Refused: {e.code}", file=sys.stderr)
                return 1
            print(f"{reviewer}: {done.status} via {done.activated_via}")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--workspace", required=True, help="the company's workspace name (slug)")
    parser.add_argument("--reviewer", required=True, help="the proposed reviewer's id (a UUID)")
    parser.add_argument(
        "--evidence-ref", required=True, help="the trust-operations case that verified them"
    )
    parser.add_argument("--apply", action="store_true", help="activate (the default is a dry run)")
    args = parser.parse_args()
    try:
        uuid.UUID(args.reviewer)
    except ValueError:
        print("--reviewer must be a UUID", file=sys.stderr)
        return 2
    url = os.environ.get("ZID_CELL_DATABASE_URL") or os.environ.get("ZID_DATABASE_URL")
    if not url:
        print("set ZID_CELL_DATABASE_URL", file=sys.stderr)
        return 2
    from zid_platform.modules.evidence.ports import configure_runtime, runtime_from_settings
    from zid_platform.settings import Settings

    settings = Settings(database_url=url)
    if settings.environment not in ("local", "test"):
        configure_runtime(runtime_from_settings(settings))
    return asyncio.run(
        verify(args.workspace, url, args.reviewer, args.evidence_ref, apply=args.apply)
    )


if __name__ == "__main__":
    raise SystemExit(main())

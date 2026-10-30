"""Record that two accounts of one company are the same natural person (ZID-ENG-DEC-007 invariant 2).

    python -m zid_platform.entrypoints.link_person --workspace acme --person <uuid> \\
        --principal <uuid> [--principal <uuid> ...] --source PLATFORM_REVIEW --evidence-ref CASE-123            # dry run
    ... --apply                                                                                                   # does it

Separation of duties counts people, not accounts: an approval by an account that is linked to the same person as the requester is a
self-approval. The link table is append-only (a wrong link is corrected by the platform, not rewritten), and each link writes an E3
audit record. This is a platform-operations step, run by someone who can reach the cell database (ZID_CELL_DATABASE_URL). Who else may
write a link, and from what evidence, is an owner ruling (docs/signoff/roles-owner-pack.md item 4); until then no tenant route does.
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

SOURCES = ("IDENTITY_PROOFING", "PLATFORM_REVIEW", "ENTERPRISE_DIRECTORY")


async def link(
    workspace: str,
    database_url: str,
    person: str,
    principals: list[str],
    source: str,
    evidence_ref: str | None,
    *,
    apply: bool,
) -> int:
    from zid_platform.modules.tenant.application.access import link_person

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
            "req_link", "cor_link", "link-person", str(t.home_cell_id), tenant_id=tenant
        )
        async with tenant_transaction(engine, ctx) as conn:
            for p in principals:
                known = (
                    await conn.execute(
                        text("SELECT 1 FROM core.principal WHERE principal_id = :p"),
                        {"p": uuid.UUID(p)},
                    )
                ).first()
                if known is None:
                    print(
                        f"{p}: not an account of this company; nothing was written.",
                        file=sys.stderr,
                    )
                    return 1
            if not apply:
                print(f"Dry run: would link {len(principals)} account(s) to person {person}.")
                return 0
            for p in principals:
                created = await link_person(
                    conn,
                    tenant,
                    p,
                    person,
                    source=source,
                    evidence_ref=evidence_ref,
                    linked_by=None,
                )
                print(f"{p}: {'linked' if created else 'already linked, left as it was'}")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--workspace", required=True, help="the company's workspace name (slug)")
    parser.add_argument(
        "--person", required=True, help="the person id the accounts share (a UUID you choose once)"
    )
    parser.add_argument(
        "--principal", action="append", required=True, help="an account of that person"
    )
    parser.add_argument("--source", required=True, choices=SOURCES)
    parser.add_argument(
        "--evidence-ref",
        default=None,
        help="the case or document reference that justifies the link",
    )
    parser.add_argument(
        "--apply", action="store_true", help="write the links (the default is a dry run)"
    )
    args = parser.parse_args()
    try:
        uuid.UUID(args.person)
        for p in args.principal:
            uuid.UUID(p)
    except ValueError:
        print("--person and --principal must be UUIDs", file=sys.stderr)
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
        link(
            args.workspace,
            url,
            args.person,
            args.principal,
            args.source,
            args.evidence_ref,
            apply=args.apply,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

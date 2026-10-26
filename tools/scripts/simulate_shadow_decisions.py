"""Record synthetic shadow decisions for one company's application (development only).

Shadow ends when the platform has seen enough real access decisions from the company's application, with full
context. In a local stack nothing is sending them, so onboarding stops at "shadow.observations" and
"shadow.context_coverage". This script plays the application's enforcement point: it records N shadow observations
through the same code path a real decision takes (`apply_overlay`), each carrying the context the application says it
sends, including some at the highest risk class, and each matching the "incumbent" answer, so the exit criteria can be
met and the pilot opens.

    python tools/scripts/simulate_shadow_decisions.py --workspace nexalytech            # 120 decisions
    python tools/scripts/simulate_shadow_decisions.py --workspace nexalytech --count 200

The company must be in the SHADOW stage with an application registered (status TESTING or ACTIVE). Refuses to run
unless ZID_ENVIRONMENT is local or test and the database is on this machine. The data is synthetic and labelled so.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import site
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "backend" / ".venv" / "Lib" / "site-packages", ROOT / "backend" / ".venv" / "lib"):
    if extra.exists():
        site.addsitedir(str(extra))
sys.path.insert(0, str(Path(__file__).parent))

from localenv import load_backend_env  # noqa: E402

load_backend_env()


async def main(workspace: str, count: int, db_url: str) -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from zid_context import RequestContext
    from zid_db import tenant_transaction
    from zid_platform.modules.onboarding.application.overlay import apply_overlay

    eng = create_async_engine(db_url)
    try:
        async with eng.connect() as c:
            row = (
                await c.execute(
                    text("SELECT tenant_id, status, home_cell_id FROM platform.tenant WHERE tenant_slug = :s"),
                    {"s": workspace},
                )
            ).first()
        if row is None:
            print(f"No company with workspace name {workspace!r}.")
            return 1
        tenant, status = str(row.tenant_id), str(row.status)
        if status not in ("SHADOW", "PILOT"):
            print(f"{workspace} is in stage {status}; start shadow first (Onboarding step 7).")
            return 1
        ctx = RequestContext("req_sim", "cor_sim", "simulator", str(row.home_cell_id), tenant_id=tenant)
        async with tenant_transaction(eng, ctx) as conn:
            app = (
                await conn.execute(
                    text(
                        "SELECT application_id, pep_client_id, pep_context_fields FROM platform.integration_application "
                        "WHERE pep_client_id IS NOT NULL AND status IN ('TESTING', 'ACTIVE') ORDER BY created_at LIMIT 1"
                    )
                )
            ).first()
            if app is None:
                print("Register an application with an enforcement point client ID first (Onboarding step 5).")
                return 1
            context = frozenset(app.pep_context_fields or [])
            done = 0
            for i in range(count):
                risk = 3 if i % 10 == 0 else (2 if i % 5 == 0 else 1)  # a tenth at the highest risk class
                await apply_overlay(
                    conn,
                    tenant_id=tenant,
                    tenant_status=status,
                    client_id=app.pep_client_id,
                    subject=f"synthetic-subject-{i % 25}",
                    action="synthetic.action.review",
                    risk=risk,
                    decision_id=f"sim-{uuid.uuid4()}",
                    outcome="ALLOW",
                    decision_allow=True,
                    obligations=[],
                    present_context=context,  # the full context the application says it sends
                    incumbent="ALLOW",  # the existing system agrees: no mismatch, no false allow
                    latency_ms=8.0 + (i % 7),
                )
                done += 1
        print(f"Recorded {done} synthetic shadow decisions for {workspace} (client {app.pep_client_id}).")
        print("Open Onboarding and press Validate: the shadow gates should pass.")
        return 0
    finally:
        await eng.dispose()


if __name__ == "__main__":
    if os.environ.get("ZID_ENVIRONMENT", "local") not in ("local", "test"):
        sys.exit("Refusing to run outside a local or test environment.")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workspace", required=True, help="the company's workspace name")
    p.add_argument("--count", type=int, default=120)
    default_url = os.environ.get(
        "ZID_CELL_DATABASE_URL",
        f"postgresql+asyncpg://zid:{os.environ.get('ZID_DB_PASSWORD', 'zid_local_only')}@localhost:{os.environ.get('ZID_DB_HOST_PORT', '5432')}/zid_cell",
    )
    p.add_argument("--database-url", default=default_url)
    args = p.parse_args()
    if "localhost" not in args.database_url and "127.0.0.1" not in args.database_url:
        sys.exit("Refusing to write synthetic decisions to a database that is not on this machine.")
    sys.exit(asyncio.run(main(args.workspace, args.count, args.database_url)))

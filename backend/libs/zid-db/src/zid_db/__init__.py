"""Database access with server-derived tenant scoping (DEC-D1, ZID-DATA-001 §12).

Every tenant-scoped transaction starts with `set_config(..., true)` — the parameterized,
transaction-local equivalent of `SET LOCAL` — so PostgreSQL row-level security sees the tenant
derived by authentication. Callers can never set it directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from zid_context import RequestContext
from zid_errors import DEPENDENCY_UNAVAILABLE, ZidError

__all__ = [
    "CheckResult",
    "as_role",
    "check_database",
    "create_engine",
    "in_tenant",
    "tenant_transaction",
]


def create_engine(url: str, *, pool_size: int = 5) -> AsyncEngine:
    return create_async_engine(url, pool_size=pool_size, pool_pre_ping=True)


@asynccontextmanager
async def tenant_transaction(
    engine: AsyncEngine, ctx: RequestContext
) -> AsyncIterator[AsyncConnection]:
    """Open a transaction scoped to the request's server-derived tenant and principal."""
    if ctx.tenant_id is None:
        raise ZidError(
            "TENANT_CONTEXT_MISSING",
            "Protected request has no server-derived tenant context.",
            http_status=403,
        )
    async with engine.begin() as conn:
        # One round trip for both settings: every tenant transaction in the platform opens here, so a second statement
        # per transaction was a fixed cost on every request (tests/load/decision-gate/RESULTS.md).
        await conn.execute(
            text(
                "SELECT set_config('app.tenant_id', :t, true), set_config('app.principal_id', :p, true)"
            ),
            {"t": ctx.tenant_id, "p": ctx.principal_id or ""},
        )
        yield conn


@asynccontextmanager
async def in_tenant(conn: AsyncConnection, tenant_id: str) -> AsyncIterator[AsyncConnection]:
    """Run a block under another tenant's RLS scope inside the same transaction, then restore.

    Only for a tenant id the server itself derived or generated (a new tenant being provisioned,
    the target recorded on a grant); never for an id taken from a request."""
    previous = (await conn.execute(text("SELECT current_setting('app.tenant_id', true)"))).scalar()
    await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
    # On an error the transaction is failing anyway (and may be aborted): re-raise untouched.
    yield conn
    await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": previous or ""})


@asynccontextmanager
async def as_role(conn: AsyncConnection, role: str | None) -> AsyncIterator[AsyncConnection]:
    """Run a block as a less-privileged database role inside the same transaction, then go back (SET LOCAL ROLE, parameterized).

    A background worker has to read across companies to find its work, so it connects as a role that bypasses row-level security. The WORK it then does
    for one company must not run with that privilege: a query that forgot to name the company would read every company's rows. Run as the runtime role
    (which row-level security applies to), the database confines the work to the tenant set for it, however a query is written. `None` runs the block
    unchanged, for a connection that is already the runtime role or for a test database that has no such role.

    On an error the block's savepoint (or transaction) is rolled back, which also undoes the role change: re-raise untouched, as `in_tenant` does."""
    if not role:
        yield conn
        return
    await conn.execute(text("SELECT set_config('role', :r, true)"), {"r": role})
    yield conn
    await conn.execute(text("SELECT set_config('role', 'none', true)"))


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


async def check_database(
    engine: AsyncEngine | None, required_schemas: tuple[str, ...]
) -> CheckResult:
    """Readiness: the database answers and the schemas this role's tables live in exist."""
    if engine is None:
        return CheckResult("database", False, "not configured (ZID_DATABASE_URL unset)")
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT schema_name FROM information_schema.schemata WHERE schema_name = ANY(:s)"
                ),
                {"s": list(required_schemas)},
            )
            present = {r[0] for r in rows}
    except Exception as exc:  # noqa: BLE001 — readiness reports, never raises
        return CheckResult("database", False, f"unreachable ({type(exc).__name__})")
    missing = [s for s in required_schemas if s not in present]
    if missing:
        return CheckResult("database", False, f"missing schemas: {', '.join(missing)}")
    return CheckResult(
        "database", True, f"connected; schemas present: {', '.join(required_schemas)}"
    )


def unavailable(name: str) -> ZidError:
    return ZidError(
        DEPENDENCY_UNAVAILABLE,
        f"{name} is unavailable.",
        http_status=503,
        retryable=True,
        retry_after_ms=1000,
    )

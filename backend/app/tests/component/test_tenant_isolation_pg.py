"""Tenant isolation across the whole data plane (ZID-VERIFY-MASTER-001 MPR-04; ZID-TENANCY-001; DEC-D1).

An adversarial sweep, not a sample: every table the ownership registry marks tenant-scoped must have row-level
security enabled AND forced with a policy keyed on the session's tenant, the runtime role must not be able to
bypass it, and with real data in two tenants a caller in the other tenant (or with no tenant at all) can neither
read nor change a row it does not own. A new table that forgets its policy fails this test.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import yaml
from conftest import ctx, engine
from onboarding_fixture import onboard
from sqlalchemy import text
from zid_db import tenant_transaction

REGISTRY = Path(__file__).resolve().parents[3] / "database" / "table-ownership.yaml"

# Registered tenant-scoped tables that no migration creates yet (planned, no data exists). Listed explicitly so a
# table cannot silently go missing: the test fails if this set is wrong in either direction, and a table that is
# later created must then pass the row-level-security checks below.
NOT_CREATED: frozenset[str] = frozenset(
    {
        "core.device",  # DEVICE principals (ZID-DATA-001 §3.4)
        "metering.correction",  # billing corrections; usage counters live in metering.usage_event
        "org.org_unit",  # organizational units (ZID-ROOT-001)
        "platform.entitlement",  # plan entitlements
        "platform.tenant_shard",  # shard placement (ZID-TENANCY-001 §2.2)
    }
)


def tenant_tables() -> list[str]:
    doc = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    return sorted(
        name
        for name, spec in doc["tables"].items()
        if spec.get("tenant_scoped") and spec.get("role", "cell") == "cell"
    )


def test_every_tenant_scoped_table_forces_row_level_security(owner_url: str) -> None:
    tables = tenant_tables()
    assert len(tables) > 100, "the registry should list the whole tenant-scoped data plane"

    async def inspect() -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        async with engine(owner_url) as eng, eng.connect() as c:
            for full in tables:
                schema, table = full.split(".", 1)
                row = (
                    await c.execute(
                        text(
                            "SELECT c.relrowsecurity AS rls, c.relforcerowsecurity AS forced, "
                            "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid "
                            " AND pg_get_expr(p.polqual, p.polrelid) LIKE '%app.tenant_id%') AS keyed "
                            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = :s AND c.relname = :t"
                        ),
                        {"s": schema, "t": table},
                    )
                ).first()
                out[full] = {
                    "exists": row is not None,
                    "rls": row and row.rls,
                    "forced": row and row.forced,
                    "keyed": row and row.keyed,
                }
        return out

    found = asyncio.run(inspect())
    missing = {t for t, f in found.items() if not f["exists"]}
    assert missing == NOT_CREATED, (
        f"registered but not created (or the allow-list is stale): {sorted(missing ^ NOT_CREATED)}"
    )
    weak = [
        f"{t}: rls={f['rls']} forced={f['forced']} tenant-keyed-policies={f['keyed']}"
        for t, f in found.items()
        if f["exists"] and not (f["rls"] and f["forced"] and f["keyed"])
    ]
    assert not weak, (
        "tenant-scoped tables without enforced, tenant-keyed row-level security:\n"
        + "\n".join(weak)
    )


def test_the_runtime_role_cannot_bypass_row_level_security(rt_url: str) -> None:
    async def role() -> Any:
        async with engine(rt_url) as eng, eng.connect() as c:
            return (
                await c.execute(
                    text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
            ).one()

    r = asyncio.run(role())
    assert (r.rolsuper, r.rolbypassrls) == (False, False)


def test_other_tenants_and_tenantless_callers_see_and_change_nothing(
    rt_url: str, owner_url: str
) -> None:
    a = onboard(rt_url)
    b = onboard(rt_url)
    stranger = str(uuid.uuid4())  # a tenant that exists nowhere
    tables = tenant_tables()

    async def sweep() -> dict[str, Any]:
        report: dict[str, Any] = {"checked": [], "leaks": [], "unprotected_writes": []}
        async with engine(owner_url) as eng, eng.connect() as c:
            with_tenant = {
                r.table_name
                for r in (
                    await c.execute(
                        text(
                            "SELECT table_schema || '.' || table_name AS table_name FROM information_schema.columns "
                            "WHERE column_name = 'tenant_id'"
                        )
                    )
                ).all()
            }
        for full in tables:
            if full not in with_tenant:
                continue  # keyed some other way; covered by the structural test above
            async with engine(owner_url) as eng, eng.connect() as c:
                owned = (
                    await c.execute(
                        text(f"SELECT count(*) FROM {full} WHERE tenant_id = :t"),
                        {"t": uuid.UUID(a.tenant)},
                    )
                ).scalar_one()
            if owned == 0:
                continue  # nothing to leak from this table in this run
            report["checked"].append(full)
            async with engine(rt_url) as eng:
                # 1. the owner of the rows can see them (the policy is not a blanket deny)
                async with tenant_transaction(eng, ctx(a.tenant)) as conn:
                    mine = (await conn.execute(text(f"SELECT count(*) FROM {full}"))).scalar_one()
                    if mine == 0:
                        report["leaks"].append(f"{full}: the owning tenant cannot see its own rows")
                # 2. another real tenant, and a tenant that does not exist, see none of A's rows
                for other in (b.tenant, stranger):
                    async with tenant_transaction(eng, ctx(other)) as conn:
                        seen = (
                            await conn.execute(
                                text(f"SELECT count(*) FROM {full} WHERE tenant_id = :t"),
                                {"t": uuid.UUID(a.tenant)},
                            )
                        ).scalar_one()
                        if seen:
                            report["leaks"].append(
                                f"{full}: tenant {other[:8]} read {seen} row(s) of tenant {a.tenant[:8]}"
                            )
                # 3. with no tenant context at all, nothing is visible
                async with eng.begin() as conn:
                    nothing = (
                        await conn.execute(text(f"SELECT count(*) FROM {full}"))
                    ).scalar_one()
                    if nothing:
                        report["leaks"].append(
                            f"{full}: {nothing} row(s) visible with no tenant context"
                        )
                # 4. another tenant cannot change or remove A's rows (0 rows affected, or refused outright)
                for verb, sql in (
                    ("update", f"UPDATE {full} SET tenant_id = tenant_id WHERE tenant_id = :t"),
                    ("delete", f"DELETE FROM {full} WHERE tenant_id = :t"),
                ):
                    try:
                        async with tenant_transaction(eng, ctx(b.tenant)) as conn:
                            n = (await conn.execute(text(sql), {"t": uuid.UUID(a.tenant)})).rowcount
                            if n:
                                report["unprotected_writes"].append(
                                    f"{full}: {verb} changed {n} row(s) of another tenant"
                                )
                    except Exception as e:
                        if (
                            "permission denied" not in str(e)
                            and "append-only" not in str(e)
                            and "row-level security" not in str(e)
                        ):
                            raise
        return report

    report = asyncio.run(sweep())
    print(f"TENANT-ISOLATION-SWEEP tables_with_data_checked={len(report['checked'])}")
    assert len(report["checked"]) >= 8, f"too little data to prove isolation: {report['checked']}"
    assert not report["leaks"], "cross-tenant reads:\n" + "\n".join(report["leaks"])
    assert not report["unprotected_writes"], "cross-tenant writes:\n" + "\n".join(
        report["unprotected_writes"]
    )

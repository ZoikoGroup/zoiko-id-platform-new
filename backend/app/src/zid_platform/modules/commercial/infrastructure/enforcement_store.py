from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def company_mode(conn: AsyncConnection, deployment: str) -> str:
    """The enforcement mode for this company: the deployment's, made stricter when the operator set the company to ENFORCE.
    Never looser: a deployment that enforces enforces for every company. An unreadable policy leaves the deployment's mode."""
    if deployment == "enforce":
        return deployment
    try:
        async with conn.begin_nested():
            row = (
                await conn.execute(
                    text(
                        "SELECT mode FROM commercial.enforcement_policy WHERE tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
                    )
                )
            ).first()
    except Exception:  # noqa: BLE001 - a policy that cannot be read must not loosen or break a gate
        return deployment
    return "enforce" if row is not None and row.mode == "ENFORCE" else deployment


async def read(conn: AsyncConnection, tenant_id: str, deployment: str) -> dict[str, object]:
    """What the operator reads: the company's own setting (if any) and the mode that therefore applies."""
    import uuid

    row = (
        await conn.execute(
            text(
                "SELECT mode, reason, set_by, set_at FROM commercial.enforcement_policy WHERE tenant_id = :t"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    own = row.mode if row is not None else None
    return {
        "tenant_id": tenant_id,
        "deployment_mode": deployment,
        "company_mode": own,
        "effective_mode": "enforce" if deployment == "enforce" or own == "ENFORCE" else deployment,
        "reason": row.reason if row is not None else None,
        "set_at": row.set_at.isoformat() if row is not None else None,
    }


async def write(
    conn: AsyncConnection, tenant_id: str, *, mode: str, reason: str, actor: str
) -> None:
    import uuid

    await conn.execute(
        text(
            "INSERT INTO commercial.enforcement_policy (tenant_id, mode, reason, set_by) VALUES (:t, :m, :r, :a) "
            "ON CONFLICT (tenant_id) DO UPDATE SET mode = :m, reason = :r, set_by = :a, set_at = now()"
        ),
        {"t": uuid.UUID(tenant_id), "m": mode, "r": reason, "a": uuid.UUID(actor)},
    )

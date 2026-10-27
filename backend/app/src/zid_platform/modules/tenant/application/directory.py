"""Publishing this cell's tenants to the global locator (ZID-TENANCY-001 §2, §14 step 11; DEC-G3;
G-W6).

The worker builds each home tenant's global-minimum record - opaque id, directory status, home and
recovery cell, placement epoch, residency profile, key-profile name, routing aliases (the workspace
name and slug) - signs it with the cell's placement key and PUTs it to the global plane. Nothing
else leaves the cell: no names, no emails, no domains. A record is sent only when it changed, always
with a higher revision; the global plane refuses anything that would move a tenant backwards.
A global-plane outage changes nothing in the cell (it only delays the next publication).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_app.resilience import budget_timeout
from zid_crypto import KeyRing

Send = Callable[[str], Awaitable[tuple[int, str]]]  # signed record -> (HTTP status, error code)

LIFECYCLE_STATUS = {
    "DRAFT": "PROVISIONING",
    "ORGANIZATION_VERIFYING": "PROVISIONING",
    "CONFIGURING": "PROVISIONING",
    "BLOCKED": "PROVISIONING",
    "INTEGRATING": "PROVISIONING",
    "SHADOW": "ACTIVE",
    "PILOT": "ACTIVE",
    "ACTIVE": "ACTIVE",
    "SUSPENDED": "SUSPENDED",
    "OFFBOARDING": "DELETING",
    "CLOSED": "DELETED",
}
RECORD_TYP = "zid-directory+jwt"


async def build_record(
    conn: AsyncConnection, tenant_id: str, cell_id: str
) -> dict[str, Any] | None:
    """The record for one tenant this cell is home for (None: not home here)."""
    t = (
        await conn.execute(
            text(
                "SELECT status, tenant_slug, home_cell_id FROM platform.tenant WHERE tenant_id = :t"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    if t is None:
        return None
    r = (
        await conn.execute(
            text(
                "SELECT residency_profile_id, home_cell_id, permitted_recovery_cells, placement_epoch, "
                "key_profile FROM platform.tenant_residency WHERE tenant_id = :t"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    home = r.home_cell_id if r else t.home_cell_id
    if home != cell_id:
        return None
    aliases = {t.tenant_slug}
    ws = (
        (
            await conn.execute(
                text("SELECT workspace FROM platform.workspace_directory WHERE tenant_id = :t"),
                {"t": uuid.UUID(tenant_id)},
            )
        )
        .scalars()
        .all()
    )
    aliases.update(ws)
    return {
        "tenant_id": tenant_id,
        "tenant_status": LIFECYCLE_STATUS[t.status],
        "home_cell_id": home,
        "recovery_cell_id": (list(r.permitted_recovery_cells) or [None])[0] if r else None,
        "placement_epoch": int(r.placement_epoch) if r else 1,
        "residency_profile_id": r.residency_profile_id if r else None,
        "tenant_key_profile_ref": r.key_profile if r else None,
        "routing_aliases": sorted(aliases),
        "service_entitlements": [],
        "policy_release_refs": [],
    }


def _hash(record: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


async def publish_pass(
    conn: AsyncConnection, *, cell_id: str, ring: KeyRing, send: Send, now: datetime
) -> dict[str, str]:
    """Worker pass (BYPASSRLS): publish every home tenant whose record changed."""
    tenants = (await conn.execute(text("SELECT tenant_id FROM platform.tenant"))).scalars().all()
    out: dict[str, str] = {}
    for tid in tenants:
        tenant = str(tid)
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        record = await build_record(conn, tenant, cell_id)
        if record is None:
            continue
        digest = _hash(record)
        last = (
            await conn.execute(
                text(
                    "SELECT record_revision, record_hash FROM platform.directory_publication "
                    "WHERE tenant_id = :t"
                ),
                {"t": uuid.UUID(tenant)},
            )
        ).first()
        if last is not None and last.record_hash == digest:
            continue
        revision = (int(last.record_revision) + 1) if last else 1
        token = ring.sign(
            {
                "iss": cell_id,
                "iat": int(now.timestamp()),
                "record": {**record, "record_revision": revision},
            },
            typ=RECORD_TYP,
        )
        status, error = await send(token)
        if status == 200:
            await conn.execute(
                text("""
                    INSERT INTO platform.directory_publication (tenant_id, record_revision, record_hash,
                        published_at, last_error)
                    VALUES (:t, :r, :h, :now, NULL)
                    ON CONFLICT (tenant_id) DO UPDATE SET record_revision = EXCLUDED.record_revision,
                        record_hash = EXCLUDED.record_hash, published_at = EXCLUDED.published_at,
                        last_error = NULL
                """),
                {"t": uuid.UUID(tenant), "r": revision, "h": digest, "now": now},
            )
            out[tenant] = "PUBLISHED"
        else:
            out[tenant] = error or f"HTTP_{status}"
    return out


def http_sender(locator_url: str, transport: Any = None) -> Send:
    import httpx

    async def send(token: str) -> tuple[int, str]:
        try:
            async with httpx.AsyncClient(
                transport=transport, timeout=budget_timeout(5.0), follow_redirects=False
            ) as c:
                r = await c.put(
                    f"{locator_url.rstrip('/')}/internal/v1/directory/records",
                    json={"record_jws": token},
                )
        except httpx.HTTPError as e:
            return 0, f"LOCATOR_UNREACHABLE:{type(e).__name__}"
        code = ""
        if r.status_code != 200:
            try:
                code = str(r.json().get("error", {}).get("code", ""))
            except ValueError:
                code = ""
        return r.status_code, code

    return send

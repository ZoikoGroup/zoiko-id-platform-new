"""Directory sync use cases: connect a company's directory, and keep the provisioning source in step with it (ZID-OVERLAY-001 §10, §14-16, §22).

A sync is a worker job on top of the connector framework: the integration module holds the write-only sealed secret and tracks health and freshness,
the provider reader (Auth0 today) turns the company's directory into people, and this module applies them to the SCIM source exactly as a SCIM client
would (the same create / replace / deactivate code, so the same rules, events and signals apply), plus a JOINER event for each person who is new and
active, which is what gives them the plain MEMBER role and nothing more.

Safety: a run that would empty the source or deactivate most of it stops before changing anyone (see `check_safe`); one person who cannot be applied
does not stop the others; a failing sync backs off instead of hammering the provider; and every outcome is recorded as a code, never as provider text.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from types import ModuleType
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.integration.ports import (
    connector_secret,
    record_interaction,
    register_directory_connector,
    set_connector_secret,
)
from zid_platform.modules.provisioning.application import scim
from zid_platform.modules.provisioning.application.lifecycle import LifecycleEvent, apply_event
from zid_platform.modules.provisioning.domain.directory import (
    INTERVAL_DEFAULT,
    DirectoryError,
    check_safe,
    next_delay,
    plan,
)
from zid_platform.modules.provisioning.infrastructure import auth0_directory, scim_store
from zid_platform.modules.provisioning.infrastructure import directory_store as store
from zid_platform.modules.provisioning.infrastructure.directory_store import Sync
from zid_platform.modules.provisioning.infrastructure.scim_store import ScimError, Source

log = logging.getLogger("zid.directory_sync")

# One entry per provider: `check_settings` (its addresses and ids, never a secret) and `fetch_users` (its reader). A second provider adds one line.
PROVIDERS: dict[str, ModuleType] = {auth0_directory.PROVIDER: auth0_directory}
Fetch = Callable[[httpx.AsyncClient, Mapping[str, Any], str], Awaitable[list[Any]]]
PAGE = 100


def _provider(name: str) -> ModuleType:
    if name not in PROVIDERS:
        raise DirectoryError("PROVIDER_UNKNOWN")
    return PROVIDERS[name]


async def create_sync(
    conn: AsyncConnection, *, tenant_id: str, source_id: str, provider: str, display_name: str, settings: Mapping[str, Any], secret: str,
    interval_seconds: int = INTERVAL_DEFAULT, owner: str, actor: str, now: datetime,
) -> Sync:  # fmt: skip
    """Connect a directory to a SCIM source. The secret is sealed at once by the connector framework and never shown again."""
    src = await scim_store.get_source(conn, source_id)
    if src is None or src.status != "ACTIVE":
        raise DirectoryError("SOURCE_NOT_ACTIVE")
    if (
        await store.for_source(conn, source_id) is not None
    ):  # before anything is registered: one sync per source
        raise DirectoryError("SYNC_EXISTS")
    cfg = _provider(provider).check_settings(settings)
    if not secret or len(secret) > 512:
        raise DirectoryError("SETTINGS_INVALID", "a client secret is required")
    connector_id = await register_directory_connector(
        conn,
        tenant_id,
        display_name=display_name,
        source_ref=f"directory_sync:{source_id}",
        owner=owner,
        actor=actor,
    )
    await set_connector_secret(conn, tenant_id, connector_id, secret=secret, actor=actor)
    return await store.create(
        conn, tenant_id=tenant_id, source_id=source_id, connector_id=connector_id, provider=provider, display_name=display_name, settings=cfg,
        interval_seconds=interval_seconds, now=now,
    )  # fmt: skip


async def _users_of(conn: AsyncConnection, src: Source) -> list[Any]:
    found: list[Any] = []
    start = 1
    while True:
        total, items = await scim_store.query(
            conn, src.source_id, "User", attribute=None, value=None, start=start, count=PAGE
        )
        found += items
        start += PAGE
        if not items or len(found) >= total:
            return found


async def run_sync(
    conn: AsyncConnection, sync: Sync, *, http: httpx.AsyncClient, home_cell: str, now: datetime, force: bool = False
) -> dict[str, int]:  # fmt: skip
    """One run for one company; the caller has put the connection in that company's scope. Returns what it did."""
    src = await scim_store.get_source(conn, sync.source_id)
    if src is None or src.status != "ACTIVE":
        raise DirectoryError("SOURCE_NOT_ACTIVE")
    secret = await connector_secret(conn, sync.tenant_id, sync.connector_id, now)
    if secret is None:
        raise DirectoryError("CREDENTIAL_MISSING")
    resources = await _users_of(conn, src)
    existing = [scim.to_scim_user(r, "", include_meta=False) for r in resources]
    users = await _provider(sync.provider).fetch_users(http, sync.settings, secret)
    todo = plan(users, existing)
    if not force:
        check_safe(len(users), sum(1 for r in resources if r.active), len(todo.deactivate))
    counts = todo.counts()
    counts["failed"] = 0

    async def attempt(work: Callable[[], Awaitable[object]]) -> None:
        try:
            async with (
                conn.begin_nested()
            ):  # one person who cannot be applied never undoes or stops the rest
                await work()
        except ScimError as e:
            counts["failed"] += 1
            log.warning(
                "directory sync %s: a person was refused (%s)",
                sync.sync_id[:8],
                e.scim_type or e.status,
            )

    for want in todo.create:

        async def create(want: dict[str, Any] = want) -> None:
            r = await scim.create_user(conn, src, want, home_cell=home_cell, now=now)
            if want["active"]:  # a blocked person is provisioned but joins nothing
                await apply_event(
                    conn,
                    src,
                    user_id=r.resource_id,
                    event=LifecycleEvent.JOINER,
                    effective_at=now,
                    now=now,
                )

        await attempt(create)
    for resource_id, want in todo.update:
        await attempt(
            lambda rid=resource_id, w=want: scim.replace_user(  # type: ignore[misc]
                conn, src, rid, w, if_match=None, now=now
            )
        )
    off = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    for resource_id in todo.deactivate:
        await attempt(
            lambda rid=resource_id: scim.patch_user(conn, src, rid, off, if_match=None, now=now)  # type: ignore[misc]
        )
    return counts


async def sync_pass(
    conn: AsyncConnection, now: datetime, *, http: httpx.AsyncClient, home_cell: str
) -> dict[str, int]:
    """Worker pass (sees every company): run each sync that is due, each in its own company's scope and its own savepoint."""
    ran = failed = 0
    for sync in await store.due(conn, now):
        ok, error, counts = True, None, {}
        try:
            async with conn.begin_nested(), in_tenant(conn, sync.tenant_id):
                counts = await run_sync(conn, sync, http=http, home_cell=home_cell, now=now)
            error = "SOME_PEOPLE_REFUSED" if counts.get("failed") else None
        except DirectoryError as e:
            ok, error = False, e.code
        except Exception:
            log.exception("directory sync %s failed", sync.sync_id[:8])
            ok, error = False, "SYNC_ERROR"
        failures = 0 if ok else sync.consecutive_failures + 1
        async with in_tenant(conn, sync.tenant_id):
            await store.record_run(
                conn,
                sync,
                ok=ok,
                error=error,
                counts=counts,
                now=now,
                delay_s=next_delay(sync.interval_seconds, failures),
                failures=failures,
            )
            await record_interaction(conn, f"directory_sync:{sync.source_id}", ok=ok)
        ran += 1
        failed += 0 if ok else 1
    return {"ran": ran, "failed": failed}

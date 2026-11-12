"""Directory sync administration: connect a company's directory (Auth0 first) to one of its provisioning sources.

    GET  /provisioning/directory-syncs                       the company's syncs and how their last run went  (provisioning.source.read, R1)
    POST /provisioning/directory-syncs                       connect a directory; the secret is sealed at once (provisioning.source.manage, R3)
    POST /provisioning/directory-syncs/{id}:run              read the directory on the worker's next pass
    POST /provisioning/directory-syncs/{id}:pause            stop it at once                                  (R3)
    POST /provisioning/directory-syncs/{id}:resume           start it again                                   (R3)
    PUT  /provisioning/directory-syncs/{id}/credential       replace the secret; write-only                   (R3)

The worker does the reading (`directory_sync` step); nothing here talks to the provider. A secret is never returned, logged or echoed, and the view
shows the provider's addresses and ids only. Management is the same risk class as managing a SCIM source, so it is the same protected action: a
second administrator where the tenant's policy asks for one, and a fresh strong sign-in.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import Enforcement, require_feature
from zid_platform.modules.integration.ports import set_connector_secret
from zid_platform.modules.provisioning.application import directory_sync as svc
from zid_platform.modules.provisioning.domain.directory import INTERVAL_DEFAULT, DirectoryError
from zid_platform.modules.provisioning.infrastructure import directory_store as store
from zid_platform.protected import protected_action

router = APIRouter(tags=["scim"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
Trimmed = StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
RUN_NOW_GAP = timedelta(
    seconds=60
)  # every run reads the provider, so "run now" is not a way to hammer it

_STATUS = {
    "SETTINGS_INVALID": 422,
    "PROVIDER_UNKNOWN": 422,
    "SOURCE_NOT_ACTIVE": 409,
    "SYNC_EXISTS": 409,
}
_MESSAGES = {
    "SETTINGS_INVALID": "Check the Auth0 domain, the client id and the secret.",
    "PROVIDER_UNKNOWN": "That directory provider is not supported.",
    "SOURCE_NOT_ACTIVE": "Choose an active provisioning source.",
    "SYNC_EXISTS": "This provisioning source already has a directory sync.",
}


class DirectorySyncIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: uuid.UUID
    provider: Literal["AUTH0"]
    display_name: Annotated[str, Trimmed]
    settings: dict[str, str] = Field(
        max_length=8
    )  # the provider's addresses and ids; the secret is separate
    secret: SecretStr = Field(
        min_length=1, max_length=512
    )  # write-only: sealed at once, never returned
    interval_seconds: int = Field(default=INTERVAL_DEFAULT, ge=300, le=86400)
    approval_request_id: Annotated[str, Trimmed] | None = None


class DirectorySyncCredentialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secret: SecretStr = Field(min_length=1, max_length=512)
    approval_request_id: Annotated[str, Trimmed] | None = None


class DirectorySyncActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_request_id: Annotated[str, Trimmed] | None = None


def _error(e: DirectoryError) -> ZidError:
    return ZidError(
        e.code,
        _MESSAGES.get(e.code, "The directory sync could not be saved."),
        http_status=_STATUS.get(e.code, 422),
    )


def view(s: store.Sync) -> dict[str, Any]:
    """Everything a screen needs and nothing secret: the provider's own addresses and ids, the state, and how the last run went (a code and counts)."""
    return {
        "sync_id": s.sync_id,
        "source_id": s.source_id,
        "provider": s.provider,
        "display_name": s.display_name,
        "settings": s.settings,
        "status": s.status,
        "interval_seconds": s.interval_seconds,
        "next_run_at": s.next_run_at.isoformat(),
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        "last_outcome": s.last_outcome,
        "last_error": s.last_error,
        "last_counts": s.last_counts,
        "consecutive_failures": s.consecutive_failures,
    }


def _owner(caller: Caller) -> str:
    return caller.ctx.principal_id or ""


async def _load(conn: Any, sync_id: uuid.UUID) -> store.Sync:
    s = await store.get(conn, str(sync_id))
    if s is None:
        raise ZidError("DIRECTORY_SYNC_NOT_FOUND", "No such directory sync.", http_status=404)
    return s


@router.get("/provisioning/directory-syncs")
async def list_syncs(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="provisioning.source.read", resource="provisioning_source:*"
        )
        return {"items": [view(s) for s in await store.listing(conn)]}


@router.post("/provisioning/directory-syncs", status_code=201)
async def create_sync(body: DirectorySyncIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant_id = caller.ctx.tenant_id or ""
    now = datetime.now(UTC)
    enforcement: Enforcement = getattr(request.app.state, "commercial_enforcement", "shadow")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="provisioning.source.manage",
            resource=f"provisioning_source:{body.source_id}",
            change=f"directory:create:{body.source_id}",
            approval_request_id=body.approval_request_id,
        )
        await require_feature(conn, "advanced.scim-automated-provisioning", enforcement=enforcement)
        try:
            made = await svc.create_sync(
                conn,
                tenant_id=tenant_id,
                source_id=str(body.source_id),
                provider=body.provider,
                display_name=body.display_name,
                settings=body.settings,
                secret=body.secret.get_secret_value(),
                interval_seconds=body.interval_seconds,
                owner=_owner(caller),
                actor=_owner(caller),
                now=now,
            )
        except DirectoryError as e:
            raise _error(e) from e
    return view(made)


@router.post("/provisioning/directory-syncs/{sync_id}:run", status_code=202)
async def run_now(
    sync_id: uuid.UUID, body: DirectorySyncActionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        s = await _load(conn, sync_id)
        await protected_action(
            conn,
            caller,
            action="provisioning.source.manage",
            resource=f"provisioning_source:{s.source_id}",
            change=f"directory:run:{s.sync_id}",
            approval_request_id=body.approval_request_id,
        )
        if s.last_run_at is not None and now - s.last_run_at < RUN_NOW_GAP:
            raise ZidError(
                "RATE_LIMITED",
                "It ran a moment ago. Try again in a minute.",
                http_status=429,
                retryable=True,
                retry_after_ms=60000,
            )
        await store.run_now(conn, s.sync_id, now)
        return {"sync_id": s.sync_id, "status": "QUEUED"}


async def _set_status(
    sync_id: uuid.UUID, status: str, body: DirectorySyncActionIn, request: Request, caller: Caller
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        s = await _load(conn, sync_id)
        await protected_action(
            conn,
            caller,
            action="provisioning.source.manage",
            resource=f"provisioning_source:{s.source_id}",
            change=f"directory:{status.lower()}:{s.sync_id}",
            approval_request_id=body.approval_request_id,
        )
        changed = await store.set_status(conn, s.sync_id, status, now)
    assert changed is not None
    return view(changed)


@router.post("/provisioning/directory-syncs/{sync_id}:pause")
async def pause(
    sync_id: uuid.UUID, body: DirectorySyncActionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _set_status(sync_id, "PAUSED", body, request, caller)


@router.post("/provisioning/directory-syncs/{sync_id}:resume")
async def resume(
    sync_id: uuid.UUID, body: DirectorySyncActionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _set_status(sync_id, "ACTIVE", body, request, caller)


@router.put("/provisioning/directory-syncs/{sync_id}/credential")
async def put_credential(
    sync_id: uuid.UUID, body: DirectorySyncCredentialIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant_id = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        s = await _load(conn, sync_id)
        await protected_action(
            conn,
            caller,
            action="provisioning.source.manage",
            resource=f"provisioning_source:{s.source_id}",
            change=f"directory:credential:{s.sync_id}",
            approval_request_id=body.approval_request_id,
        )
        version = await set_connector_secret(
            conn,
            tenant_id,
            s.connector_id,
            secret=body.secret.get_secret_value(),
            actor=_owner(caller),
        )
    return {"sync_id": str(sync_id), "version": version}  # the value is never echoed

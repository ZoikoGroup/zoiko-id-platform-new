"""SCIM 2.0 service provider (RFC 7644; ZID-AUTH-001 §11, §18; AUTH-W6).

    GET  /scim/v2/ServiceProviderConfig | /Schemas | /ResourceTypes
    POST /scim/v2/Users            GET /scim/v2/Users?filter=userName eq "x"
    GET|PUT|PATCH|DELETE /scim/v2/Users/{id}
    POST /scim/v2/Groups           GET /scim/v2/Groups
    GET|PATCH|DELETE /scim/v2/Groups/{id}
    GET  /provisioning/sources               list connectors        (provisioning.source.read, R1)
    POST /provisioning/sources               register a connector   (provisioning.source.manage, R3)
    POST /provisioning/sources/{id}:suspend  stop it at once        (R3)

The SCIM client is an OAuth client (client_credentials, scope `scim:provision`) bound to exactly one
ACTIVE provisioning source of its tenant; it sees and changes only that source's resources.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from scim2_models import Group, User
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, http_error, request_engine
from zid_platform.modules.authentication.ports import require_step_up
from zid_platform.modules.commercial.ports import require_entitlement, require_feature
from zid_platform.modules.integration.ports import record_interaction
from zid_platform.modules.provisioning.application import scim
from zid_platform.modules.provisioning.application.lifecycle import LifecycleEvent
from zid_platform.modules.provisioning.application.lifecycle import apply_event as apply_lifecycle
from zid_platform.modules.provisioning.infrastructure import scim_store as store
from zid_platform.modules.provisioning.infrastructure.scim_store import ScimError, Source
from zid_platform.modules.provisioning.ports import count_active_sources
from zid_platform.protected import protected_action

router = APIRouter(tags=["scim"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
SCIM_JSON = "application/scim+json"
FILTER = re.compile(
    r'^\s*(userName|externalId|displayName)\s+eq\s+"([^"]{1,256})"\s*$', re.IGNORECASE
)
MAX_COUNT = 100
Handler = Callable[[AsyncConnection, Source], Awaitable[Response]]


def _out(body: dict[str, Any], status: int = 200, version: int | None = None) -> JSONResponse:
    headers = {"ETag": f'W/"{version}"'} if version is not None else {}
    return JSONResponse(body, status_code=status, media_type=SCIM_JSON, headers=headers)


def _error(e: ScimError) -> JSONResponse:
    body: dict[str, Any] = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
        "status": str(e.status),
        "detail": e.detail,
    }
    if e.scim_type:
        body["scimType"] = e.scim_type
    return JSONResponse(body, status_code=e.status, media_type=SCIM_JSON)


def _base(request: Request) -> str:
    s = getattr(request.app.state, "oauth_settings", None)
    return str(s.issuer).rstrip("/") if s else ""


async def _source(conn: AsyncConnection, caller: Caller) -> Source:
    if "scim:provision" not in caller.scope:
        raise ScimError(403, None, "scope scim:provision is required")
    src = await store.source_for_client(conn, caller.client_id)
    if src is None or src.status != "ACTIVE":
        raise ScimError(403, None, "no active provisioning source for this client")
    # The connector framework tracks the source's health and freshness (OVERLAY-001 §15, §24).
    await record_interaction(conn, f"provisioning_source:{src.source_id}", ok=True)
    return src


async def _body(request: Request) -> dict[str, Any]:
    if len(await request.body()) > 256 * 1024:
        raise ScimError(413, None, "payload too large")
    try:
        data = await request.json()
    except ValueError as e:
        raise ScimError(400, "invalidSyntax", "body must be JSON") from e
    if not isinstance(data, dict):
        raise ScimError(400, "invalidSyntax", "body must be a JSON object")
    return data


async def _guard(request: Request, caller: Caller, fn: Handler) -> Response:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            return await fn(conn, await _source(conn, caller))
    except ScimError as e:
        return _error(e)


@router.get("/scim/v2/ServiceProviderConfig")
async def service_provider_config() -> JSONResponse:
    return _out(
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": MAX_COUNT},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": True},
            "authenticationSchemes": [
                {
                    "type": "oauthbearertoken",
                    "name": "OAuth Bearer Token",
                    "description": "client_credentials access token with scope scim:provision",
                    "primary": True,
                }
            ],
        }
    )


@router.get("/scim/v2/ResourceTypes")
async def resource_types() -> JSONResponse:
    def rt(name: str) -> dict[str, Any]:
        return {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": name,
            "name": name,
            "endpoint": f"/{name}s",
            "schema": f"urn:ietf:params:scim:schemas:core:2.0:{name}",
        }

    return _out(
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": 2,
            "Resources": [rt("User"), rt("Group")],
        }
    )


@router.get("/scim/v2/Schemas")
async def schemas() -> JSONResponse:
    return _out(
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": 2,
            "Resources": [
                User.to_schema().model_dump(mode="json", exclude_none=True),
                Group.to_schema().model_dump(mode="json", exclude_none=True),
            ],
        }
    )


@router.post("/scim/v2/Users")
async def create_user(request: Request, caller: CallerDep) -> Response:
    home = str(getattr(request.app.state, "home_cell", "local-1"))

    async def run(conn: AsyncConnection, src: Source) -> Response:
        r = await scim.create_user(
            conn, src, await _body(request), home_cell=home, now=datetime.now(UTC)
        )
        return _out(scim.to_scim_user(r, _base(request)), 201, r.version)

    return await _guard(request, caller, run)


async def _list(request: Request, caller: Caller, resource_type: str) -> Response:
    q = request.query_params
    attribute = value = None
    if q.get("filter"):
        m = FILTER.match(q["filter"])
        if m is None:
            return _error(ScimError(400, "invalidFilter", 'only `<attribute> eq "<value>"`'))
        attribute = {
            "username": "userName",
            "externalid": "externalId",
            "displayname": "displayName",
        }[m.group(1).lower()]
        value = m.group(2)
    try:
        start = max(int(q.get("startIndex", "1")), 1)
        count = min(max(int(q.get("count", str(MAX_COUNT))), 0), MAX_COUNT)
    except ValueError:
        return _error(ScimError(400, "invalidValue", "startIndex/count must be integers"))

    async def run(conn: AsyncConnection, src: Source) -> Response:
        total, items = await store.query(
            conn,
            src.source_id,
            resource_type,
            attribute=attribute,
            value=value,
            start=start,
            count=count,
        )
        base = _base(request)
        resources = (
            [scim.to_scim_user(r, base) for r in items]
            if resource_type == "User"
            else [await scim.to_scim_group(conn, r, base) for r in items]
        )
        return _out(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                "totalResults": total,
                "startIndex": start,
                "itemsPerPage": len(resources),
                "Resources": resources,
            }
        )

    return await _guard(request, caller, run)


@router.get("/scim/v2/Users")
async def list_users(request: Request, caller: CallerDep) -> Response:
    return await _list(request, caller, "User")


@router.get("/scim/v2/Groups")
async def list_groups(request: Request, caller: CallerDep) -> Response:
    return await _list(request, caller, "Group")


@router.get("/scim/v2/Users/{resource_id}")
async def get_user(resource_id: str, request: Request, caller: CallerDep) -> Response:
    async def run(conn: AsyncConnection, src: Source) -> Response:
        r = await store.load(conn, src.source_id, "User", resource_id)
        return _out(scim.to_scim_user(r, _base(request)), 200, r.version)

    return await _guard(request, caller, run)


@router.put("/scim/v2/Users/{resource_id}")
async def put_user(resource_id: str, request: Request, caller: CallerDep) -> Response:
    async def run(conn: AsyncConnection, src: Source) -> Response:
        r = await scim.replace_user(
            conn,
            src,
            resource_id,
            await _body(request),
            if_match=request.headers.get("if-match"),
            now=datetime.now(UTC),
        )
        return _out(scim.to_scim_user(r, _base(request)), 200, r.version)

    return await _guard(request, caller, run)


@router.patch("/scim/v2/Users/{resource_id}")
async def patch_user(resource_id: str, request: Request, caller: CallerDep) -> Response:
    async def run(conn: AsyncConnection, src: Source) -> Response:
        r = await scim.patch_user(
            conn,
            src,
            resource_id,
            await _body(request),
            if_match=request.headers.get("if-match"),
            now=datetime.now(UTC),
        )
        return _out(scim.to_scim_user(r, _base(request)), 200, r.version)

    return await _guard(request, caller, run)


@router.delete("/scim/v2/Users/{resource_id}")
async def delete_user(resource_id: str, request: Request, caller: CallerDep) -> Response:
    async def run(conn: AsyncConnection, src: Source) -> Response:
        await scim.delete_resource(conn, src, "User", resource_id, now=datetime.now(UTC))
        return Response(status_code=204)

    return await _guard(request, caller, run)


@router.post("/scim/v2/Groups")
async def create_group(request: Request, caller: CallerDep) -> Response:
    async def run(conn: AsyncConnection, src: Source) -> Response:
        r = await scim.create_group(conn, src, await _body(request), now=datetime.now(UTC))
        return _out(await scim.to_scim_group(conn, r, _base(request)), 201, r.version)

    return await _guard(request, caller, run)


@router.get("/scim/v2/Groups/{resource_id}")
async def get_group(resource_id: str, request: Request, caller: CallerDep) -> Response:
    async def run(conn: AsyncConnection, src: Source) -> Response:
        r = await store.load(conn, src.source_id, "Group", resource_id)
        return _out(await scim.to_scim_group(conn, r, _base(request)), 200, r.version)

    return await _guard(request, caller, run)


@router.patch("/scim/v2/Groups/{resource_id}")
async def patch_group(resource_id: str, request: Request, caller: CallerDep) -> Response:
    async def run(conn: AsyncConnection, src: Source) -> Response:
        r = await scim.patch_group(
            conn, src, resource_id, await _body(request), now=datetime.now(UTC)
        )
        return _out(await scim.to_scim_group(conn, r, _base(request)), 200, r.version)

    return await _guard(request, caller, run)


@router.delete("/scim/v2/Groups/{resource_id}")
async def delete_group(resource_id: str, request: Request, caller: CallerDep) -> Response:
    async def run(conn: AsyncConnection, src: Source) -> Response:
        await scim.delete_resource(conn, src, "Group", resource_id, now=datetime.now(UTC))
        return Response(status_code=204)

    return await _guard(request, caller, run)


class SourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=128)
    approval_request_id: str | None = None


def _source_view(s: Source) -> dict[str, Any]:
    """The connector's identity and state; the client's credentials live with the OAuth client."""
    return {
        "source_id": s.source_id,
        "client_id": s.client_id,
        "display_name": s.display_name,
        "status": s.status,
    }


@router.get("/provisioning/sources")
async def list_sources(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="provisioning.source.read", resource="provisioning_source:*"
        )
        return {"items": [_source_view(s) for s in await store.list_sources(conn)]}


@router.post("/provisioning/sources", status_code=201)
async def create_source(body: SourceIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    source_id = str(uuid.uuid4())
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="provisioning.source.manage",
            resource=f"provisioning_source:{source_id}",
            change=f"create:{body.client_id}",
            approval_request_id=body.approval_request_id,
        )
        # A paid extra: a directory connection beyond the plan (CR-024). Never on a SCIM request path.
        await require_entitlement(
            conn,
            "scim",
            used=await count_active_sources(conn),
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        await require_feature(
            conn,
            "advanced.scim-automated-provisioning",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        await require_step_up(
            request,
            conn,
            caller,
            action="change-sso-federation-trust-or-scim-privilege-mapping",
            resource=f"provisioning_source:{source_id}",
            change={"operation": "CREATE", "client_id": body.client_id},
        )
        s = Source(
            source_id, caller.ctx.tenant_id or "", body.client_id, body.display_name, "ACTIVE"
        )
        await store.create_source(conn, s)
    return {"source_id": source_id, "client_id": body.client_id, "status": "ACTIVE"}


@router.post("/provisioning/sources/{source_id}:suspend")
async def suspend_source(
    source_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="provisioning.source.manage",
            resource=f"provisioning_source:{source_id}",
            change="suspend",
        )
        s = await store.set_source_status(conn, str(source_id), "SUSPENDED")
    if s is None:
        raise http_error("PROVISIONING_SOURCE_NOT_FOUND")
    return {"source_id": s.source_id, "status": s.status}


# ---------------------------------------------------------------- HR lifecycle events (G-W5)


class LifecycleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: uuid.UUID  # the SCIM User resource id this source provisioned
    event: LifecycleEvent
    effective_at: datetime | None = None


@router.post("/provisioning/lifecycle-events")
async def lifecycle_event(
    body: LifecycleBody, request: Request, caller: CallerDep
) -> dict[str, object]:
    """OVERLAY §22: joiner / mover / leaver / leave from the HRIS (same source auth as SCIM)."""
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            src = await _source(conn, caller)
            return await apply_lifecycle(
                conn,
                src,
                user_id=str(body.user_id),
                event=body.event,
                effective_at=body.effective_at or now,
                now=now,
            )
        except ScimError as e:
            raise ZidError(
                "SCIM_RESOURCE_NOT_FOUND" if e.status == 404 else "SCIM_SOURCE_FORBIDDEN",
                e.detail or "Lifecycle event refused.",
                http_status=e.status,
            ) from e

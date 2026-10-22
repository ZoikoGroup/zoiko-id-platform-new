"""Global-plane discovery and directory publication (DEC-G3; ZID-TENANCY-001 §2, §9; G-W6).

    GET  /v1/discovery?workspace=<alias> | ?tenant_id=<id>     signed {cell, endpoint, epoch, exp}
    GET  /.well-known/zoiko-discovery-keys                    the discovery signing keys (JWKS)
    PUT  /internal/v1/directory/records                       a cell publishes a signed record

Discovery and bootstrap only: SDKs, PEPs and BFFs call the returned *cell* endpoint directly and
cache this answer until `exp`; the global plane is never in an already-routed decision's path. The
answer is a JWS (typ zid-discovery+jwt) so a client can check it came from the global plane, and it
names the placement epoch the cell will check (`X-Zoiko-Placement-Epoch`). Deleted or suspended
tenants are not discoverable. Publication is authenticated by the record's own signature, made with
the publishing cell's pinned key (ZID_LOCATOR_CELL_KEYS) - there is no other credential to steal.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_crypto import KeyRing
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.locator.ports import DISCOVERY_TYP, LocatorError, publish, resolve

router = APIRouter(tags=["discovery"])
_STATUS = {
    "GLOBAL_DATA_PROHIBITED": 422,
    "VALIDATION_FAILED": 422,
    "PUBLICATION_SIGNATURE_INVALID": 401,
    "CELL_NOT_AUTHORITATIVE": 403,
    "MIGRATION_EPOCH_CONFLICT": 409,
    "PLACEMENT_EPOCH_STALE": 409,
    "ROUTING_ALIAS_TAKEN": 409,
}


def http(e: LocatorError) -> ZidError:
    return ZidError(e.code, e.detail or e.code, http_status=_STATUS.get(e.code, 409))


def _ring(request: Request) -> KeyRing:
    ring: KeyRing | None = getattr(request.app.state, "discovery_ring", None)
    if ring is None:
        raise ZidError(
            "DEPENDENCY_UNAVAILABLE", "Discovery signing is not configured.", http_status=503
        )
    return ring


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_jws: str = Field(min_length=20, max_length=16_384)


@router.put("/internal/v1/directory/records")
async def publish_record(body: Publication, request: Request) -> dict[str, Any]:
    keys = getattr(request.app.state, "locator_cell_keys", {}) or {}
    async with request_engine(request).begin() as conn:
        try:
            rec = await publish(conn, body.record_jws, keys)
        except LocatorError as e:
            raise http(e) from e
    return {
        "tenant_id": rec.tenant_id,
        "placement_epoch": rec.placement_epoch,
        "record_revision": rec.record_revision,
    }


@router.get("/.well-known/zoiko-discovery-keys")
async def discovery_keys(request: Request) -> dict[str, Any]:
    return _ring(request).jwks()


@router.get("/v1/discovery")
async def discover(
    request: Request, workspace: str | None = None, tenant_id: str | None = None
) -> dict[str, Any]:
    if bool(workspace) == bool(tenant_id):
        raise ZidError(
            "VALIDATION_FAILED", "Give exactly one of workspace or tenant_id.", http_status=422
        )
    async with request_engine(request).connect() as conn:
        rec = await resolve(conn, tenant_id=tenant_id, alias=workspace)
    if rec is None or rec.tenant_status in ("DELETED", "SUSPENDED"):
        raise ZidError("TENANT_NOT_FOUND", "No such tenant.", http_status=404)
    endpoints: dict[str, str] = getattr(request.app.state, "cell_endpoints", {}) or {}
    endpoint = endpoints.get(rec.home_cell_id)
    if not endpoint:
        raise ZidError(
            "CELL_UNAVAILABLE", "The tenant's cell has no published endpoint.", http_status=503
        )
    ttl = int(getattr(request.app.state, "discovery_ttl_s", 300))
    now = int(time.time())
    claims = {
        "tid": rec.tenant_id,
        "cell": rec.home_cell_id,
        "cell_endpoint": endpoint,
        "placement_epoch": rec.placement_epoch,
        "tenant_status": rec.tenant_status,
        "iat": now,
        "exp": now + ttl,
    }
    return {**claims, "discovery": _ring(request).sign(claims, typ=DISCOVERY_TYP)}

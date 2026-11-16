"""Ownership graphs (ZID-GAP-001 v2.0 DTV-24; CR-102): who ends up with a material share of a company, and what is not known.

    POST /v2/ownership-graphs:analyze     work out the candidates and gaps of a graph without storing it     (org.ownership.manage)
    POST /v2/ownership-graphs             record a graph as the next version of its target; refused if it loops or is over-owned
    GET  /v2/ownership-graphs             the recorded versions                                              (org.ownership.read)
    GET  /v2/ownership-graphs/{id}        one version with its findings, worked out again from the record

The answer is a list of candidates for a person to confirm and the gaps nobody has explained. It never says someone IS a beneficial owner. Entities are
opaque ids and a kind: no name is kept here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.organization.application import ownership_service as service
from zid_platform.modules.organization.domain.ownership import OwnershipError
from zid_platform.modules.organization.infrastructure import ownership_store as store
from zid_platform.protected import protected_action

router = APIRouter(tags=["organization-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class OwnershipDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
    decision: Literal["CONFIRMED", "NOT_CONFIRMED", "ESCALATED"]
    reason: str = Field(
        min_length=1, max_length=2000
    )  # the service enforces the 20-character floor with a clear code
    evidence_ref: str = Field(min_length=1, max_length=512)


class OwnershipGraphIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
    entities: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    links: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)
    threshold: str = Field(default="25", max_length=10)


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


def _fail(e: OwnershipError) -> ZidError:
    status = {"GRAPH_NOT_FOUND": 404, "REVIEWER_IS_AUTHOR": 403, "ALREADY_DECIDED": 409}.get(
        e.code, 422
    )
    return ZidError(e.code, e.code.replace("_", " ").capitalize() + ".", http_status=status)


@router.post("/v2/ownership-graphs:analyze")
async def analyze(body: OwnershipGraphIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="org.ownership.manage", resource="ownership:analyze"
        )
    try:
        return {**_ids(), **service.analyze(body.entities, body.links, body.target, body.threshold)}
    except OwnershipError as e:
        raise _fail(e) from e


@router.post("/v2/ownership-graphs", status_code=201)
async def record(body: OwnershipGraphIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    if not caller.ctx.principal_id or not caller.ctx.tenant_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="org.ownership.manage", resource="ownership:graph"
        )
        try:
            out = await service.save(
                conn,
                caller.ctx.tenant_id,
                actor=caller.ctx.principal_id,
                target=body.target,
                entities=body.entities,
                links=body.links,
                threshold=body.threshold,
                now=datetime.now(UTC),
            )
        except OwnershipError as e:
            raise _fail(e) from e
    return {**_ids(), **out}


@router.get("/v2/ownership-graphs")
async def list_graphs(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="org.ownership.read", resource="ownership:graph"
        )
        rows = await store.recent(conn)
    items = [
        {
            "graph_id": str(r["graph_id"]),
            "target": r["target"],
            "version": r["version"],
            "content_hash": r["content_hash"],
            "author": str(r["author"]),
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
    return {**_ids(), "items": items}


@router.get("/v2/ownership-graphs/{graph_id}")
async def get_graph(graph_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    try:
        uuid.UUID(graph_id)
    except ValueError as e:
        raise ZidError("GRAPH_NOT_FOUND", "Graph not found.", http_status=404) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="org.ownership.read", resource=f"ownership:graph:{graph_id}"
        )
        row = await store.get(conn, graph_id)
        confirmations = await store.confirmations_for(conn, graph_id) if row else []
    if row is None:
        raise ZidError("GRAPH_NOT_FOUND", "Graph not found.", http_status=404)
    return {**_ids(), **service.graph_view(row, confirmations)}


@router.post("/v2/ownership-graphs/{graph_id}/confirmations", status_code=201)
async def decide(
    graph_id: str, body: OwnershipDecisionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """A person decides on one candidate. The platform never does: a decision needs a reason and an evidence reference, and the person who recorded the graph cannot make it."""
    if not caller.ctx.principal_id or not caller.ctx.tenant_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    try:
        uuid.UUID(graph_id)
    except ValueError as e:
        raise ZidError("GRAPH_NOT_FOUND", "Graph not found.", http_status=404) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="org.ownership.confirm", resource=f"ownership:graph:{graph_id}"
        )
        try:
            out = await service.confirm(
                conn,
                caller.ctx.tenant_id,
                graph_id,
                entity_id=body.entity_id,
                decision=body.decision,
                reason=body.reason,
                evidence_ref=body.evidence_ref,
                reviewer=caller.ctx.principal_id,
                now=datetime.now(UTC),
            )
        except OwnershipError as e:
            raise _fail(e) from e
    return {**_ids(), **out}

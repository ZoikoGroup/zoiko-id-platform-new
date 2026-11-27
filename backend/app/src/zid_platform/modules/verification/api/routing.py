"""Global coverage and deterministic provider routing (ZID-CP-WF09 v2.0; CR-171). Back-office and server-to-server routes; nothing here is called from a browser on an applicant's behalf.

    GET  /v1/routing/capabilities                                   each provider's capability per method, newest version
    POST /v1/routing/capabilities                                   propose one (UNDER_REVIEW); approval is a second person's act
    POST /v1/routing/capabilities/{provider}/{method}:approve | :suspend | :retire
    GET  /v1/routing/coverage                                       deduplicated countries and specimens per tier, from cells
    GET  /v1/routing/claims                                         the public number, from released cells only; refuses when a cell lacks evidence
    POST /v1/routing/resolve                                        the deterministic resolver on a frozen input; one decision per Idempotency-Key
    GET  /v1/routing/decisions/{id}                                 the reason-coded trace: every candidate, every failed gate, the ranking
    POST /v1/routing/decisions/{id}/attempts                        reserve the provider attempt (re-checks legality and quota now)
    GET  /v1/routing/attempts/{id}                                  the attempt and its history
    POST /v1/routing/attempts/{id}:advance | :fail | :reroute       the state machine; an unknown outcome is reconciled, never retried elsewhere
    POST /v1/routing/preflight                                      what a proposed change would do; stores nothing, calls no provider
    POST /v1/routing/releases, GET /v1/routing/releases[/{id}]      a route release
    POST /v1/routing/releases/{id}:preflight | :approve | :publish | :rollback
    POST /v1/routing/reconcile                                      match a provider's invoice lines to the attempt ledger; adjusts nothing

A selected provider is not a verified identity. The owning verifier decides.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.application import routing as svc
from zid_platform.modules.verification.domain import routing as rt
from zid_platform.modules.verification.infrastructure import routing_store as store
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/routing", tags=["verification-routing"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,62}$")]
Country = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
Method = Literal["DOCUMENT", "DOCUMENT_FACE", "NFC_CHIP", "EID", "REGISTRY"]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)]
READ, MANAGE, APPROVE = (
    "verification.routing.read",
    "verification.routing.manage",
    "verification.routing.approve",
)


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    return caller.ctx.principal_id


def _tenant(caller: Caller) -> str:
    return caller.ctx.tenant_id or ""


async def _guarded(request: Request, caller: Caller, action: str, resource: str, fn: Any) -> Any:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action=action, resource=resource)
            return await fn(conn)
    except svc.RoutingError as e:
        raise ZidError(e.code, str(e), http_status=e.status) from e
    except rt.TransitionError as e:
        raise ZidError(e.code, str(e), http_status=409) from e


class CapabilityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: Slug
    method: Method
    manifest: dict[str, Any]
    qualified_until: datetime | None = None
    contract_until: datetime | None = None
    legal_determination_ref: Annotated[str, Field(max_length=200)] | None = None
    legal_determination_valid_until: datetime | None = None


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str, Field(min_length=3, max_length=500)]


@router.get("/capabilities")
async def capabilities(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        return {"items": [svc.capability_view(r) for r in await store.latest_capabilities(conn)]}

    return await _guarded(request, caller, READ, "routing:capabilities", go)  # type: ignore[no-any-return]


@router.post("/capabilities", status_code=201)
async def propose(body: CapabilityBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        return await svc.propose_capability(
            conn, provider_id=body.provider_id, method=body.method, manifest=body.manifest, qualified_until=body.qualified_until, contract_until=body.contract_until,
            legal_determination_ref=body.legal_determination_ref, legal_determination_valid_until=body.legal_determination_valid_until, by=_me(caller),
        )  # fmt: skip

    return await _guarded(request, caller, MANAGE, f"routing:capability:{body.provider_id}", go)  # type: ignore[no-any-return]


def _decide(action: str) -> Any:
    async def handler(
        provider: Slug, method: Method, body: ReasonBody, request: Request, caller: CallerDep
    ) -> dict[str, Any]:
        async def go(conn: Any) -> dict[str, Any]:
            return await svc.decide_capability(
                conn,
                _tenant(caller),
                provider_id=provider,
                method=method,
                action=action,
                by=_me(caller),
                reason=body.reason,
            )

        return await _guarded(request, caller, APPROVE, f"routing:capability:{provider}", go)  # type: ignore[no-any-return]

    return handler


router.add_api_route(
    "/capabilities/{provider}/{method}:approve",
    _decide("APPROVE"),
    methods=["POST"],
    name="approve_capability",
)
router.add_api_route(
    "/capabilities/{provider}/{method}:suspend",
    _decide("SUSPEND"),
    methods=["POST"],
    name="suspend_capability",
)
router.add_api_route(
    "/capabilities/{provider}/{method}:retire",
    _decide("RETIRE"),
    methods=["POST"],
    name="retire_capability",
)


@router.get("/coverage")
async def coverage(
    request: Request, caller: CallerDep, method: Annotated[Method | None, Query()] = None
) -> dict[str, Any]:
    return await _guarded(  # type: ignore[no-any-return]
        request, caller, READ, "routing:coverage", lambda conn: svc.coverage(conn, method=method)
    )


@router.get("/claims")
async def claims(request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _guarded(request, caller, READ, "routing:claims", svc.public_claim)  # type: ignore[no-any-return]


class RouteScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
    workflow_version: Annotated[str, Field(min_length=1, max_length=80)]
    product: Slug
    purpose: Slug
    issuer_country: Country
    residence_country: Country | None = None
    method: Method
    assurance_floor: Annotated[int, Field(ge=0, le=10)] = 0
    channel: Literal["web", "ios", "android"]
    sdk_version: Annotated[str, Field(pattern=r"^[0-9]+(\.[0-9]+){0,3}$")] | None = None
    allowed_regions: Annotated[list[Slug], Field(min_length=1, max_length=20)]
    chip_required: bool = False
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None


class ResolveBody(RouteScenario):
    session_id: Annotated[str, Field(min_length=1, max_length=128)]


@router.post("/resolve", status_code=201)
async def resolve(
    body: ResolveBody, request: Request, caller: CallerDep, key: Key
) -> dict[str, Any]:
    return await _guarded(  # type: ignore[no-any-return]
        request,
        caller,
        MANAGE,
        f"routing:session:{body.session_id}",
        lambda conn: svc.resolve_and_record(
            conn, _tenant(caller), idempotency_key=key, body=body.model_dump()
        ),
    )


@router.get("/decisions/{decision_id}")
async def decision(decision_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        try:
            row = await store.load_decision(conn, decision_id)
        except ValueError as e:
            raise svc.RoutingError("DECISION_NOT_FOUND", "no such route decision", 404) from e
        if row is None:
            raise svc.RoutingError("DECISION_NOT_FOUND", "no such route decision", 404)
        attempts = await store.attempts_of_decision(conn, decision_id)
        return {
            **svc.decision_view(row, replayed=False),
            "attempts": [
                {"attempt_id": str(a.attempt_id), "provider_id": a.provider_id, "state": a.to_state}
                for a in attempts
            ],
            "evidence": await svc.decision_evidence(conn, decision_id),
        }

    return await _guarded(request, caller, READ, f"routing:decision:{decision_id}", go)  # type: ignore[no-any-return]


@router.post("/decisions/{decision_id}/attempts", status_code=201)
async def reserve(
    decision_id: str, body: ResolveBody, request: Request, caller: CallerDep, key: Key
) -> dict[str, Any]:
    return await _guarded(  # type: ignore[no-any-return]
        request,
        caller,
        MANAGE,
        f"routing:decision:{decision_id}",
        lambda conn: svc.reserve_attempt(
            conn,
            _tenant(caller),
            decision_id=decision_id,
            idempotency_key=key,
            body=body.model_dump(),
        ),
    )


class HopBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "INGRESS",
        "TRANSIT",
        "STORAGE",
        "REMOTE_SUPPORT",
        "ANALYTICS",
        "TELEMETRY",
        "BACKUP",
        "SUBPROCESSOR",
    ]
    party: Annotated[str, Field(min_length=1, max_length=120)]
    region: Slug
    data_class: Literal["MEDIA", "TEMPLATE", "DERIVED", "METADATA"]


class DataFlowBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hops: Annotated[list[HopBody], Field(min_length=1, max_length=200)]


@router.post("/providers/{provider}/data-flow", status_code=201)
async def data_flow(
    provider: Slug, body: DataFlowBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Register a version of a provider's data-flow graph. A change from the graph its legal determination covers stops routing to it until a person looks again."""
    return await _guarded(  # type: ignore[no-any-return]
        request,
        caller,
        MANAGE,
        f"routing:data-flow:{provider}",
        lambda conn: svc.record_data_flow(
            conn,
            _tenant(caller),
            provider_id=provider,
            hops=[h.model_dump() for h in body.hops],
            by=_me(caller),
        ),
    )


@router.get("/providers/{provider}/data-flow")
async def read_data_flow(provider: Slug, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        row = await store.latest_data_flow(conn, provider)
        if row is None:
            raise svc.RoutingError(
                "DATA_FLOW_NOT_REGISTERED",
                "no data-flow graph is registered for this provider",
                404,
            )
        return {
            "provider_id": provider,
            "version": row.version,
            "graph_digest": row.graph_digest,
            "hops": [dict(h) for h in row.hops],
            "recorded_by": row.recorded_by,
            "recorded_at": row.recorded_at.isoformat(),
        }

    return await _guarded(request, caller, READ, f"routing:data-flow:{provider}", go)  # type: ignore[no-any-return]


@router.get("/attempts/{attempt_id}")
async def attempt(attempt_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        try:
            return await svc.attempt_view(conn, attempt_id)
        except (IndexError, ValueError) as e:
            raise svc.RoutingError("ATTEMPT_NOT_FOUND", "no such attempt", 404) from e

    return await _guarded(request, caller, READ, f"routing:attempt:{attempt_id}", go)  # type: ignore[no-any-return]


@router.get("/attempts/{attempt_id}/applicant-message")
async def applicant_message(attempt_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    """What to show the applicant for this attempt: one safe next step, never the provider's name, never "failed" for a technical problem."""

    async def go(conn: Any) -> dict[str, Any]:
        try:
            return (await svc.attempt_view(conn, attempt_id))["applicant"]  # type: ignore[no-any-return]
        except (IndexError, ValueError) as e:
            raise svc.RoutingError("ATTEMPT_NOT_FOUND", "no such attempt", 404) from e

    return await _guarded(request, caller, READ, f"routing:attempt:{attempt_id}", go)  # type: ignore[no-any-return]


class AdvanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal[
        "DISPATCHED",
        "PENDING",
        "RESULT_RECEIVED",
        "OUTCOME_UNKNOWN",
        "RECONCILING",
        "EXCEPTION_REVIEW",
        "EXPLICIT_RECAPTURE_APPROVED",
        "NORMALIZED",
        "EVIDENCE_REJECTED",
        "REVIEW_REQUIRED",
        "FINALIZED",
        "CONFIRMED_NOT_PROCESSED",
    ]
    reason: Annotated[str, Field(max_length=300)] | None = None
    external_operation_id: Annotated[str, Field(min_length=3, max_length=200)] | None = None


@router.post("/attempts/{attempt_id}:advance")
async def advance(
    attempt_id: str, body: AdvanceBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    # A person on this route acts as OPERATOR. FINALIZED is refused here by the state machine: only the owning verifier's own service finalises.
    return await _guarded(  # type: ignore[no-any-return]
        request,
        caller,
        MANAGE,
        f"routing:attempt:{attempt_id}",
        lambda conn: svc.advance_attempt(
            conn,
            _tenant(caller),
            attempt_id,
            body.target,
            actor="OPERATOR",
            reason=body.reason,
            external_operation_id=body.external_operation_id,
        ),
    )


class FailureBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport_timeout: bool = False
    provider_confirmed_not_processed: bool = False
    late_result: bool = False
    bad_result: bool = False
    provider_outage: bool = False


@router.post("/attempts/{attempt_id}:fail")
async def fail(
    attempt_id: str, body: FailureBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _guarded(  # type: ignore[no-any-return]
        request,
        caller,
        MANAGE,
        f"routing:attempt:{attempt_id}",
        lambda conn: svc.record_failure(conn, _tenant(caller), attempt_id, **body.model_dump()),
    )


class RerouteBody(ResolveBody):
    downgrade_approval_ref: Annotated[str, Field(max_length=120)] | None = None


@router.post("/attempts/{attempt_id}:reroute", status_code=201)
async def reroute(
    attempt_id: str, body: RerouteBody, request: Request, caller: CallerDep, key: Key
) -> dict[str, Any]:
    data = body.model_dump()
    ref = data.pop("downgrade_approval_ref")
    return await _guarded(  # type: ignore[no-any-return]
        request,
        caller,
        MANAGE,
        f"routing:attempt:{attempt_id}",
        lambda conn: svc.reroute(
            conn,
            _tenant(caller),
            failed_attempt_id=attempt_id,
            idempotency_key=key,
            body=data,
            downgrade_approval_ref=ref,
        ),
    )


class PreflightScenario(RouteScenario):
    required: bool = False


class PreflightBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenarios: Annotated[list[PreflightScenario], Field(min_length=1, max_length=500)]
    proposed: Annotated[list[dict[str, Any]], Field(max_length=50)] = []


@router.post("/preflight")
async def preflight(body: PreflightBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _guarded(  # type: ignore[no-any-return]
        request,
        caller,
        MANAGE,
        "routing:preflight",
        lambda conn: svc.preflight(
            conn,
            _tenant(caller),
            scenarios=[s.model_dump() for s in body.scenarios],
            proposed=body.proposed,
        ),
    )


class ReleaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk: Literal["LOW", "HIGH"]
    scope: dict[str, Annotated[str, Field(max_length=120)]] = {}


@router.post("/releases", status_code=201)
async def create_release(body: ReleaseBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _guarded(  # type: ignore[no-any-return]
        request,
        caller,
        MANAGE,
        "routing:release",
        lambda conn: svc.create_release(conn, maker=_me(caller), risk=body.risk, scope=body.scope),
    )


@router.get("/releases")
async def releases(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        return {"items": [svc.release_view(r) for r in await store.list_releases(conn)]}

    return await _guarded(request, caller, READ, "routing:release", go)  # type: ignore[no-any-return]


@router.get("/releases/{release_id}")
async def release(release_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        try:
            r = await store.latest_release(conn, release_id)
        except ValueError as e:
            raise svc.RoutingError("RELEASE_NOT_FOUND", "no such release", 404) from e
        if r is None:
            raise svc.RoutingError("RELEASE_NOT_FOUND", "no such release", 404)
        return svc.release_view(r, await store.approvals_of(conn, release_id))

    return await _guarded(request, caller, READ, f"routing:release:{release_id}", go)  # type: ignore[no-any-return]


@router.post("/releases/{release_id}:preflight")
async def release_preflight(
    release_id: str, body: PreflightBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        result = await svc.preflight(
            conn,
            _tenant(caller),
            scenarios=[s.model_dump() for s in body.scenarios],
            proposed=body.proposed,
        )
        return await svc.record_preflight(conn, release_id, result=result, by=_me(caller))

    return await _guarded(request, caller, MANAGE, f"routing:release:{release_id}", go)  # type: ignore[no-any-return]


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["PRODUCT", "SECURITY", "PRIVACY", "LEGAL"]


@router.post("/releases/{release_id}:approve")
async def release_approve(
    release_id: str, body: ApproveBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _guarded(  # type: ignore[no-any-return]
        request,
        caller,
        APPROVE,
        f"routing:release:{release_id}",
        lambda conn: svc.approve_release(conn, release_id, approver=_me(caller), role=body.role),
    )


@router.post("/releases/{release_id}:publish")
async def release_publish(release_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _guarded(  # type: ignore[no-any-return]
        request,
        caller,
        APPROVE,
        f"routing:release:{release_id}",
        lambda conn: svc.publish_release(
            conn, release_id, by=_me(caller), tenant_id=_tenant(caller)
        ),
    )


@router.post("/releases/{release_id}:rollback")
async def release_rollback(
    release_id: str, body: ReasonBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _guarded(  # type: ignore[no-any-return]
        request,
        caller,
        APPROVE,
        f"routing:release:{release_id}",
        lambda conn: svc.rollback_release(
            conn, release_id, by=_me(caller), reason=body.reason, tenant_id=_tenant(caller)
        ),
    )


class VendorLineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: Slug
    external_operation_id: Annotated[str, Field(min_length=1, max_length=200)]
    amount_minor: Annotated[int, Field(ge=0)]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class ReconcileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: Annotated[list[VendorLineBody], Field(min_length=1, max_length=5000)]


@router.post("/reconcile")
async def reconcile(body: ReconcileBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    lines = [
        rt.VendorLine(x.provider_id, x.external_operation_id, x.amount_minor, x.currency)
        for x in body.lines
    ]
    return await _guarded(  # type: ignore[no-any-return]
        request, caller, MANAGE, "routing:reconcile", lambda conn: svc.reconciliation(conn, lines)
    )

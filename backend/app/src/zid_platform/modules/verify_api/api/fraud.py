"""The fraud graph for the company's own analysts (CR-168; ZID-CP-WF05 v2.0). A signed-in person, behind the PDP, with four separate duties:

verify_api.fraud.read      read the overview, leads, the masked graph, a link's evidence and the audit trail         (R1, an administrator by default)
verify_api.fraud.analyze   refresh the graph, triage and escalate a lead, challenge a link, add a note or a finding  (R2, granted by the owner)
verify_api.fraud.propose   propose an exact-target action from a CONFIRMED finding                                    (R2, granted by the owner)
verify_api.fraud.approve   approve, reject or revoke a proposal: never the person who made it                         (R2, granted by the owner)

GET  /v1/verify-api/fraud/overview                        alerts, investigations and confirmed findings kept apart, with coverage and definitions
GET  /v1/verify-api/fraud/sources                         the signal registry, the link types, the limits and the features that stay OFF
POST /v1/verify-api/fraud/graph:refresh                   read the sessions in; idempotent
GET  /v1/verify-api/fraud/leads?status=&finding=          leads, newest first
GET  /v1/verify-api/fraud/leads/{id}                      one lead: its current links, notes and proposals
POST /v1/verify-api/fraud/leads/{id}:triage | :escalate | :finding      and POST /leads/{id}/notes
GET  /v1/verify-api/fraud/graphs/{session}?depth=&limit=  a bounded, masked picture around one session
GET  /v1/verify-api/fraud/edges/{id}                      one link with its source observations and what it does NOT show
POST /v1/verify-api/fraud/edges/{id}:challenge            exclude or restore a link, with a reason
POST /v1/verify-api/fraud/actions:propose                 an exact-target proposal (Idempotency-Key header)
GET  /v1/verify-api/fraud/actions[/{id}]  and POST /actions/{id}:approve | :reject | :revoke
GET  /v1/verify-api/fraud/audit/{scope}                   the hash-chained trail of changes and reads

Nothing here changes a session, a decision or a customer. The caller's company is read from the signed-in principal, never from a field of the request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verify_api.application import fraud_service as svc
from zid_platform.modules.verify_api.application import observe
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.protected import protected_action

router = APIRouter(tags=["verify-api"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

READ, ANALYZE, PROPOSE, APPROVE = (
    "verify_api.fraud.read",
    "verify_api.fraud.analyze",
    "verify_api.fraud.propose",
    "verify_api.fraud.approve",
)
RESOURCE = "verify_api_fraud:*"

STATUS = {
    "GRAPH_ACCESS_DENIED": 404,  # the same answer for something that does not exist and for another company's
    "GRAPH_SNAPSHOT_STALE": 409,
    "EDGE_SOURCE_UNAVAILABLE": 410,
    "EDGE_STATE_INVALID": 409,
    "LEAD_STATE_INVALID": 409,
    "LEAD_NOT_ELIGIBLE": 409,
    "CASE_NOT_FINAL": 409,
    "CASE_STATE_INVALID": 409,
    "CASE_FINAL": 409,
    "REGULATORY_HOLD": 423,
    "REVIEWER_NOT_INDEPENDENT": 403,
    "SENSITIVE_FEATURE_DISABLED": 403,
    "ACTION_APPROVAL_REQUIRED": 403,
    "ACTION_TARGET_SET_CHANGED": 409,
    "ACTION_NOT_PROPOSED": 409,
    "ACTION_EXPIRED": 409,
    "QUERY_LIMIT_EXCEEDED": 422,
    "LEGAL_BASIS_REQUIRED": 422,
    "NO_CONFIRMED_FINDING": 409,
    "TARGET_WEAK_ONLY": 422,
    "TARGET_NOT_IN_LEAD": 422,
    "TARGET_UNLINKED": 422,
    "TOO_MANY_TARGETS": 422,
    "NO_TARGETS": 422,
    "DUPLICATE_TARGET": 422,
    "BAD_EXPIRY": 422,
    "UNKNOWN_ACTION_TYPE": 422,
    "SOURCE_ERASED": 409,
    "SIMILARITY_ONLY": 422,
    "ALREADY_LISTED": 409,
    "QUERY_STATE_INVALID": 409,
    "ENTRY_STATE_INVALID": 409,
    "REPLAY_REQUIRED": 409,
    "QUERY_SOURCE_NOT_ENABLED": 422,
    "PROHIBITED_JOIN": 422,
    "UNSUPPORTED_FIELD": 422,
    "RATE_LIMITED": 429,
    "NOTE_REQUIRED": 422,
    "VALIDATION_FAILED": 422,
}


def fail(e: PartnerError) -> ZidError:
    return ZidError(e.code, e.detail, http_status=STATUS.get(e.code, 422))


class FraudVersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int | None = None


class FraudTriageBody(FraudVersionBody):
    status: str = Field(max_length=16)
    note: str | None = Field(default=None, max_length=1000)


class FraudFindingBody(FraudVersionBody):
    finding: str = Field(max_length=16)
    note: str | None = Field(default=None, max_length=1000)


class FraudNoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(max_length=12)
    body: str = Field(max_length=1000)
    edge_id: str | None = Field(default=None, max_length=40)
    source_ref: str | None = Field(default=None, max_length=200)


class FraudChallengeBody(FraudVersionBody):
    action: str = Field(max_length=8)
    reason: str = Field(max_length=1000)


class FraudProposeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lead_id: str = Field(max_length=40)
    proposal_type: str = Field(max_length=24)
    target_ids: list[str] = Field(max_length=60)
    basis_ref: str = Field(max_length=200)
    impact_assessment_ref: str = Field(max_length=200)
    expires_at: datetime
    expected_lead_version: int | None = None


class FraudApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_checksum: str = Field(max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class FraudNoteOnlyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(max_length=1000)


async def _run(request: Request, caller: Caller, action: str, fn: Any, *, metric: str | None = None) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=action, resource=RESOURCE)
        try:
            out = await fn(conn, caller.ctx.tenant_id or "", caller.ctx.principal_id or "")
        except PartnerError as e:
            raise fail(e) from e
    if metric:
        observe.FRAUD.inc(event=metric)
    return out  # type: ignore[no-any-return]


@router.get("/v1/verify-api/fraud/overview")
async def overview(request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.overview(conn))


@router.get("/v1/verify-api/fraud/sources")
async def sources(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any, t: str, me: str) -> dict[str, Any]:
        return svc.sources()

    return await _run(request, caller, READ, go)


@router.post("/v1/verify-api/fraud/graph:refresh")
async def refresh(request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, ANALYZE, lambda conn, t, me: svc.refresh(conn, t, me), metric="refreshed")


@router.get("/v1/verify-api/fraud/leads")
async def leads(
    request: Request,
    caller: CallerDep,
    status: str | None = Query(default=None, max_length=16),
    finding: str | None = Query(default=None, max_length=16),
) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.list_leads(conn, status=status, finding=finding))


@router.get("/v1/verify-api/fraud/leads/{lead_id}")
async def lead(lead_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.get_lead(conn, t, me, lead_id))


@router.post("/v1/verify-api/fraud/leads/{lead_id}:triage")
async def triage(lead_id: str, body: FraudTriageBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, ANALYZE, lambda conn, t, me: svc.triage(conn, t, me, lead_id, body.status, body.note, body.expected_version), metric="triaged")


@router.post("/v1/verify-api/fraud/leads/{lead_id}:escalate")
async def escalate(lead_id: str, body: FraudVersionBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, ANALYZE, lambda conn, t, me: svc.escalate(conn, t, me, lead_id, body.expected_version), metric="escalated")


@router.post("/v1/verify-api/fraud/leads/{lead_id}:finding")
async def finding(lead_id: str, body: FraudFindingBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, ANALYZE, lambda conn, t, me: svc.record_finding(conn, t, me, lead_id, body.finding, body.note, body.expected_version), metric="finding")


@router.post("/v1/verify-api/fraud/leads/{lead_id}/notes", status_code=201)
async def note(lead_id: str, body: FraudNoteBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(
        request, caller, ANALYZE,
        lambda conn, t, me: svc.add_note(conn, t, me, lead_id, kind=body.kind, body=body.body, edge_id=body.edge_id, source_ref=body.source_ref),
    )  # fmt: skip


@router.get("/v1/verify-api/fraud/graphs/{seed}")
async def graph(
    seed: str,
    request: Request,
    caller: CallerDep,
    depth: int = Query(default=2, ge=0, le=10),
    limit: int = Query(default=200, ge=0, le=1000),
) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.graph(conn, t, me, seed, depth=depth, limit=limit))


@router.get("/v1/verify-api/fraud/edges/{edge_id}")
async def edge(edge_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.get_edge(conn, t, me, edge_id))


@router.post("/v1/verify-api/fraud/edges/{edge_id}:challenge")
async def challenge(edge_id: str, body: FraudChallengeBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, ANALYZE, lambda conn, t, me: svc.challenge_edge(conn, t, me, edge_id, body.action, body.reason, body.expected_version), metric="challenged")


@router.post("/v1/verify-api/fraud/actions:propose", status_code=201)
async def propose(
    body: FraudProposeBody,
    request: Request,
    caller: CallerDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=120)] = None,
) -> dict[str, Any]:
    return await _run(
        request, caller, PROPOSE,
        lambda conn, t, me: svc.propose_action(
            conn, t, me, body.lead_id, proposal_type=body.proposal_type, targets=body.target_ids, basis_ref=body.basis_ref, impact_ref=body.impact_assessment_ref,
            expires_at=body.expires_at, idempotency_key=idempotency_key or "", expected_lead_version=body.expected_lead_version,
        ),
        metric="proposed",
    )  # fmt: skip


@router.get("/v1/verify-api/fraud/actions")
async def actions(request: Request, caller: CallerDep, lead_id: str | None = Query(default=None, max_length=40)) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.list_actions(conn, lead_id=lead_id))


@router.get("/v1/verify-api/fraud/actions/{action_id}")
async def action(action_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any, t: str, me: str) -> dict[str, Any]:
        return await svc.get_action(conn, action_id)

    return await _run(request, caller, READ, go)


@router.post("/v1/verify-api/fraud/actions/{action_id}:approve")
async def approve(action_id: str, body: FraudApproveBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, APPROVE, lambda conn, t, me: svc.approve_action(conn, t, me, action_id, body.manifest_checksum, body.note), metric="approved")


@router.post("/v1/verify-api/fraud/actions/{action_id}:reject")
async def reject(action_id: str, body: FraudNoteOnlyBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, APPROVE, lambda conn, t, me: svc.reject_action(conn, t, me, action_id, body.note), metric="rejected")


@router.post("/v1/verify-api/fraud/actions/{action_id}:revoke")
async def revoke(action_id: str, body: FraudNoteOnlyBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, APPROVE, lambda conn, t, me: svc.revoke_action(conn, t, me, action_id, body.note), metric="revoked")


@router.get("/v1/verify-api/fraud/audit/{scope}")
async def audit(scope: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any, t: str, me: str) -> dict[str, Any]:
        return await svc.trail(conn, scope)

    return await _run(request, caller, READ, go)


# ------------------------------------------------------------------------------- queries, the blocklist, the timeline, data quality and honest numbers


class FraudQueryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(max_length=40)
    params: dict[str, Any] = Field(default_factory=dict, max_length=20)


class FraudEntryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lead_id: str = Field(max_length=40)
    reason_code: str = Field(max_length=40)
    basis_ref: str = Field(max_length=200)


class FraudEntryNoteBody(FraudVersionBody):
    note: str = Field(max_length=1000)


class FraudEntryResolveBody(FraudEntryNoteBody):
    uphold: bool


class FraudCheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_reference: str = Field(max_length=128)


@router.post("/v1/verify-api/fraud/queries", status_code=201)
async def create_query(body: FraudQueryBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, ANALYZE, lambda conn, t, me: svc.create_query(conn, t, me, body.key, body.params), metric="query_drafted")


@router.get("/v1/verify-api/fraud/queries")
async def queries(request: Request, caller: CallerDep, status: str | None = Query(default=None, max_length=12)) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.list_queries(conn, status))


@router.post("/v1/verify-api/fraud/queries/{query_id}:replay")
async def replay_query(query_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, ANALYZE, lambda conn, t, me: svc.replay_query(conn, t, me, query_id), metric="query_replayed")


@router.post("/v1/verify-api/fraud/queries/{query_id}:submit")
async def submit_query(query_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, ANALYZE, lambda conn, t, me: svc.submit_query(conn, t, me, query_id))


@router.post("/v1/verify-api/fraud/queries/{query_id}:approve")
async def approve_query(query_id: str, body: FraudNoteOnlyBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, APPROVE, lambda conn, t, me: svc.decide_query(conn, t, me, query_id, True, body.note), metric="query_published")


@router.post("/v1/verify-api/fraud/queries/{query_id}:reject")
async def reject_query(query_id: str, body: FraudNoteOnlyBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, APPROVE, lambda conn, t, me: svc.decide_query(conn, t, me, query_id, False, body.note))


@router.post("/v1/verify-api/fraud/queries/{query_id}:retire")
async def retire_query(query_id: str, body: FraudNoteOnlyBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, APPROVE, lambda conn, t, me: svc.retire_query(conn, t, me, query_id, body.note))


@router.get("/v1/verify-api/fraud/blocklist")
async def blocklist(request: Request, caller: CallerDep, status: str | None = Query(default=None, max_length=12)) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.list_entries(conn, status))


@router.post("/v1/verify-api/fraud/blocklist", status_code=201)
async def propose_entry(body: FraudEntryBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, PROPOSE, lambda conn, t, me: svc.propose_entry(conn, t, me, body.lead_id, body.reason_code, body.basis_ref), metric="entry_proposed")


@router.post("/v1/verify-api/fraud/blocklist:check")
async def check_reference(body: FraudCheckBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.check_reference(conn, t, me, body.customer_reference))


@router.post("/v1/verify-api/fraud/blocklist/{entry_id}:approve")
async def approve_entry(entry_id: str, body: FraudNoteOnlyBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, APPROVE, lambda conn, t, me: svc.decide_entry(conn, t, me, entry_id, True, body.note), metric="entry_approved")


@router.post("/v1/verify-api/fraud/blocklist/{entry_id}:reject")
async def reject_entry(entry_id: str, body: FraudNoteOnlyBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, APPROVE, lambda conn, t, me: svc.decide_entry(conn, t, me, entry_id, False, body.note))


@router.post("/v1/verify-api/fraud/blocklist/{entry_id}:challenge")
async def challenge_entry(entry_id: str, body: FraudEntryNoteBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, ANALYZE, lambda conn, t, me: svc.challenge_entry(conn, t, me, entry_id, body.note, body.expected_version), metric="entry_challenged")


@router.post("/v1/verify-api/fraud/blocklist/{entry_id}:resolve")
async def resolve_entry(entry_id: str, body: FraudEntryResolveBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, APPROVE, lambda conn, t, me: svc.resolve_challenge(conn, t, me, entry_id, body.uphold, body.note, body.expected_version))


@router.post("/v1/verify-api/fraud/blocklist/{entry_id}:remove")
async def remove_entry(entry_id: str, body: FraudEntryNoteBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, APPROVE, lambda conn, t, me: svc.remove_entry(conn, t, me, entry_id, body.note, body.expected_version), metric="entry_removed")


@router.get("/v1/verify-api/fraud/leads/{lead_id}/timeline")
async def lead_timeline(lead_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.timeline(conn, t, me, lead_id))


@router.get("/v1/verify-api/fraud/graphs/{seed}/as-of")
async def graph_as_of(
    seed: str,
    request: Request,
    caller: CallerDep,
    at: datetime = Query(),
    depth: int = Query(default=2, ge=0, le=10),
    limit: int = Query(default=200, ge=0, le=1000),
) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.graph_as_of(conn, t, me, seed, at, depth=depth, limit=limit))


@router.get("/v1/verify-api/fraud/quality")
async def quality(request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.quality(conn))


@router.get("/v1/verify-api/fraud/analytics")
async def analytics(request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.analytics(conn))


@router.get("/v1/verify-api/fraud/graphs/{seed}/export")
async def export_graph(
    seed: str,
    request: Request,
    caller: CallerDep,
    format: str = Query(max_length=4),
    purpose: str = Query(max_length=30),
    reason: str = Query(max_length=1000),
    depth: int = Query(default=2, ge=0, le=10),
    limit: int = Query(default=200, ge=0, le=1000),
) -> Response:
    """A masked file of the picture, for a stated purpose and reason. Never cached, and the company is told when to delete it."""
    out = await _run(request, caller, ANALYZE, lambda conn, t, me: svc.export_graph(conn, t, me, seed, fmt=format, purpose=purpose, reason=reason, depth=depth, limit=limit), metric="exported")
    return Response(
        content=out["content"], media_type=out["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{out["filename"]}"', "Cache-Control": "no-store", "X-Export-Purpose": out["purpose"], "X-Export-Do-Not-Keep-After": out["expires_at"], "X-Content-Type-Options": "nosniff"},
    )  # fmt: skip


@router.get("/v1/verify-api/fraud/metrics")
async def metrics(request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, READ, lambda conn, t, me: svc.metrics(conn))

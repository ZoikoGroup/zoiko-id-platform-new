"""The restricted screening case queue and decisions, operator plane (ZID-DTV-WF-06 S04, S05, S09; CR-113).

    GET  /v1/screening/review/queue                       the cases waiting, across tenants, oldest first; a case alias, the subject type, the market, the age: never a name   (screening.case.*)
    GET  /v1/screening/review/cases/{id}                  one case in full: the subject, each candidate and why, the sources, the four dimensions, the chain                  (screening.case.*; audited)
    POST /v1/screening/review/cases/{id}:propose          propose a disposition; a hold or a request for evidence is made at once, anything else waits for a checker           (analyst, senior or legal)
    POST /v1/screening/review/proposals/{id}:finalise     a second person finalises a stored proposal                                                                           (senior or legal)
    POST /v1/screening/review/proposals/{id}:withdraw     the maker withdraws a proposal not yet finalised                                                                      (the maker)

A person works cases in one role, and the role is the capability they hold. Maker and checker are two people in two requests, the checker never the maker, and a prohibition, a freeze, a block or a
report is Legal's alone. Reading a case is itself an audited act. A decision is appended to the case's chain under the case's own tenant; the chain is never edited. Records carry ids, codes and hashes,
never a name or a list.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from zid_db import in_tenant, tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import has_capability, require_operator
from zid_platform.operator_actions import Actions


class _Screening:
    """The screening module's public interface, imported on first use (avoids a dependency cycle at import time)."""

    def __getattr__(self, name: str) -> Any:
        import zid_platform.modules.screening.ports as ports  # noqa: PLR0402

        return getattr(ports, name)


sc = _Screening()
ROLE_CAPABILITY = {
    "legal": "screening.case.legal",
    "senior": "screening.case.senior",
    "analyst": "screening.case.analyst",
}
CASE_CAPABILITIES = tuple(ROLE_CAPABILITY.values())
CasePath = Annotated[uuid.UUID, Path()]
ProposalPath = Annotated[uuid.UUID, Path()]

router = APIRouter(tags=["screening-cases"])
OperatorDep = Annotated[Any, Depends(require_operator)]

actions = Actions(
    namespace=uuid.UUID("b7a30d65-2e18-4c9f-a4d1-93f0c6e8b152"),
    refused_event="screening.case.refused",
    status={
        "CASE_NOT_FOUND": 404,
        "PROPOSAL_NOT_FOUND": 404,
        "PROPOSAL_NOT_PENDING": 409,
        "ONLY_THE_MAKER_WITHDRAWS": 403,
        "REVISION_CONFLICT": 409,
        "ROLE_CANNOT_DECIDE": 403,
        "LEGAL_ROLE_REQUIRED": 403,
        "CHECKER_IS_THE_MAKER": 403,
        "CHECKER_DECIDED_BEFORE": 403,
        "CHECKER_NEEDS_SENIOR_ROLE": 403,
        "CHECKER_REQUIRED": 409,
        "MAKER_IS_A_SUBJECT": 403,
        "ITEMS_INVALID": 422,
        "DUE_DATE_INVALID": 422,
        "REQUEST_ALREADY_OPEN": 409,
        "REQUEST_NOT_FOUND": 404,
        "REVIEWER_ASKED_FOR_IT": 403,
        "NOTHING_TO_REVIEW": 409,
        "REVIEW_CODE_REQUIRED": 422,
        "REQUEST_CLOSED": 409,
        "REQUEST_EXPIRED": 409,
    },
    errors=lambda: (sc.ScreeningError,),
    refused_code="SCREENING_CASE_REFUSED",
    capability_hint="a screening case role",
)


class ProposeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disposition: str = Field(
        pattern=r"^(CLEARED_FOR_PURPOSE|EDD_REQUIRED|HOLD_PENDING_REVIEW|RESTRICTED|PROHIBITED|UNRESOLVED)$"
    )
    action: str = Field(pattern=r"^(RELEASE|EDD|HOLD|REJECT|FREEZE|BLOCK|REPORT)$")
    expected_revision: int = Field(ge=0, le=100_000)
    rationale: str = Field(min_length=20, max_length=4000)
    reason_codes: list[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]] = Field(
        min_length=1, max_length=10
    )
    evidence_refs: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list, max_length=20
    )
    legal_citation: str | None = Field(default=None, max_length=500)
    review_by: AwareDatetime | None = None


class InformationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Annotated[str, Field(pattern=r"^[A-Z][A-Z_]{2,40}$")]] = Field(
        min_length=1, max_length=6
    )
    due_in_days: int = Field(ge=3, le=60)


class InformationReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted: bool
    code: str | None = Field(
        default=None, pattern=r"^(INCOMPLETE|UNREADABLE|INCONSISTENT|NOT_SUPPORTED_BY_DOCUMENTS)$"
    )


async def _role(
    conn: Any, op: Any, resource: str, allowed: tuple[str, ...] = ("legal", "senior", "analyst")
) -> str:
    """The case role this operator works in, from the capability they hold. Nobody holds two (a conflict rule), so the answer is one or none."""
    for role in ("legal", "senior", "analyst"):
        if role in allowed and await has_capability(
            conn, str(op.principal_id), ROLE_CAPABILITY[role], None, resource, datetime.now(UTC)
        ):
            return role
    raise ZidError(
        "OPERATOR_CAPABILITY_REQUIRED", "This needs a screening case role.", http_status=403
    )


@router.get("/v1/screening/review/queue")
async def queue(
    request: Request,
    op: OperatorDep,
    state: Annotated[
        str | None,
        Query(
            pattern=r"^(NO_CANDIDATE|REVIEW_REQUIRED|SOURCE_UNAVAILABLE|CLOSED_CLEAR_FOR_SCOPE|CLOSED_RESTRICTED|CLOSED_UNRESOLVED)$"
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, Any]:
    after = None
    if cursor:
        try:
            at, rid = cursor.split("|")
            after = (datetime.fromisoformat(at), str(uuid.UUID(rid)))
        except ValueError as e:
            raise ZidError(
                "CURSOR_INVALID", "That cursor is not one this queue gave.", http_status=422
            ) from e
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, CASE_CAPABILITIES, "regulatory:screening-queue")
        return dict(await sc.screening_requests.queue(conn, state=state, limit=limit, after=after))


@router.get("/v1/screening/review/cases/{request_id}")
async def case(request_id: CasePath, request: Request, op: OperatorDep) -> dict[str, Any]:
    cfg = getattr(request.app.state, "screening_config", None) or sc.screening_config()
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, CASE_CAPABILITIES, f"regulatory:{request_id}")
        tenant = await sc.screening_requests.locate(conn, str(request_id))
        if tenant is None:
            raise ZidError("CASE_NOT_FOUND", "There is no such case.", http_status=404)
        async with in_tenant(conn, tenant):
            detail = await sc.screening_requests.restricted_detail(
                conn, str(request_id), now=now, max_age=cfg.policy.list_max_age
            )
        await actions.audit(
            conn, op, event="screening.case.viewed", action="read_restricted", target_type="screening_request", target=str(request_id),
            outcome="SUCCESS", now=now, details={"revision": detail["revision"], "candidates": len(detail["candidates"])}, once=False,
        )  # fmt: skip
        return dict(detail)


@router.post("/v1/screening/review/cases/{request_id}:propose")
async def propose(
    request_id: CasePath, body: ProposeIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        role = await _role(conn, op, f"regulatory:{request_id}")

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        tenant = await sc.screening_requests.locate(conn, str(request_id))
        if tenant is None:
            raise sc.ScreeningError("CASE_NOT_FOUND", str(request_id))
        async with in_tenant(conn, tenant):
            proposal = sc.dispositions.Proposal(
                case_id=str(request_id),
                expected_revision=body.expected_revision,
                disposition=sc.coverage.Disposition(body.disposition),
                action=sc.dispositions.Action(body.action),
                maker=str(op.principal_id),
                maker_role=sc.dispositions.Role(role),
                rationale=body.rationale,
                reason_codes=tuple(body.reason_codes),
                evidence_refs=tuple(body.evidence_refs),
                policy_version=await sc.screening_requests.case_policy_version(
                    conn, str(request_id)
                ),
                legal_citation=body.legal_citation,
                review_by=body.review_by,
            )
            out = await sc.screening_requests.propose(conn, str(request_id), proposal, now=now)
        return (
            out,
            str(request_id),
            {
                "disposition": body.disposition,
                "action": body.action,
                "state": out["state"],
                "role": role,
            },
        )

    return await actions.act(
        request, op, event="screening.case.proposed", action="propose", capability=ROLE_CAPABILITY[role], target_type="screening_request", target=str(request_id), work=work, role=role,
    )  # fmt: skip


@router.get("/v1/screening/review/cases/{request_id}/information-requests")
async def information_requests(
    request_id: CasePath, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, CASE_CAPABILITIES, f"regulatory:{request_id}")
        tenant = await sc.screening_requests.locate(conn, str(request_id))
        if tenant is None:
            raise ZidError("CASE_NOT_FOUND", "There is no such case.", http_status=404)
        async with in_tenant(conn, tenant):
            return {"items": await sc.edd.list_for_case(conn, str(request_id))}


@router.post("/v1/screening/review/cases/{request_id}/information-requests", status_code=201)
async def ask_for_information(
    request_id: CasePath, body: InformationIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        role = await _role(conn, op, f"regulatory:{request_id}")

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        tenant = await sc.screening_requests.locate(conn, str(request_id))
        if tenant is None:
            raise sc.ScreeningError("CASE_NOT_FOUND", str(request_id))
        async with in_tenant(conn, tenant):
            out = await sc.edd.open_request(
                conn,
                tenant,
                str(request_id),
                body.items,
                requested_by=str(op.principal_id),
                due_in_days=body.due_in_days,
                now=now,
            )
        return (
            out,
            out["edd_id"],
            {"items": body.items, "due_in_days": body.due_in_days, "role": role},
        )

    return await actions.act(
        request, op, event="screening.case.information_requested", action="request_information", capability=ROLE_CAPABILITY[role], target_type="screening_request", target=str(request_id), work=work, role=role,
    )  # fmt: skip


@router.post("/v1/screening/review/information-requests/{edd_id}:review")
async def review_information(
    edd_id: Annotated[uuid.UUID, Path()],
    body: InformationReviewIn,
    request: Request,
    op: OperatorDep,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        role = await _role(conn, op, f"regulatory:{edd_id}", ("legal", "senior"))

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        tenant = await sc.edd.locate(conn, str(edd_id))
        if tenant is None:
            raise sc.ScreeningError("REQUEST_NOT_FOUND", str(edd_id))
        async with in_tenant(conn, tenant):
            out = await sc.edd.review(
                conn,
                str(edd_id),
                reviewer=str(op.principal_id),
                accepted=body.accepted,
                code=body.code,
                now=now,
            )
        return out, str(edd_id), {"accepted": body.accepted, "code": body.code, "role": role}

    return await actions.act(
        request, op, event="screening.case.information_reviewed", action="review_information", capability=ROLE_CAPABILITY[role], target_type="screening_edd_request", target=str(edd_id), work=work, role=role,
    )  # fmt: skip


@router.post("/v1/screening/review/proposals/{proposal_id}:finalise")
async def finalise(proposal_id: ProposalPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        role = await _role(conn, op, f"regulatory:{proposal_id}", ("legal", "senior"))

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        tenant = await sc.screening_requests.proposal_tenant(conn, str(proposal_id))
        if tenant is None:
            raise sc.ScreeningError("PROPOSAL_NOT_FOUND", str(proposal_id))
        async with in_tenant(conn, tenant):
            out = await sc.screening_requests.finalise(
                conn,
                str(proposal_id),
                checker=str(op.principal_id),
                checker_role=sc.dispositions.Role(role),
                now=now,
            )
        return (
            out,
            str(proposal_id),
            {
                "disposition": out["disposition"],
                "revision": out["revision"],
                "state": out["state"],
                "role": role,
            },
        )

    return await actions.act(
        request, op, event="screening.case.finalised", action="finalise", capability=ROLE_CAPABILITY[role], target_type="screening_proposal", target=str(proposal_id), work=work, role=role,
    )  # fmt: skip


@router.post("/v1/screening/review/proposals/{proposal_id}:withdraw")
async def withdraw(proposal_id: ProposalPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        role = await _role(conn, op, f"regulatory:{proposal_id}")

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        tenant = await sc.screening_requests.proposal_tenant(conn, str(proposal_id))
        if tenant is None:
            raise sc.ScreeningError("PROPOSAL_NOT_FOUND", str(proposal_id))
        async with in_tenant(conn, tenant):
            out = await sc.screening_requests.withdraw(
                conn, str(proposal_id), by=str(op.principal_id), now=now
            )
        return out, str(proposal_id), {"state": out["state"], "role": role}

    return await actions.act(
        request, op, event="screening.case.withdrawn", action="withdraw", capability=ROLE_CAPABILITY[role], target_type="screening_proposal", target=str(proposal_id), work=work, role=role,
    )  # fmt: skip


__all__ = ["router"]

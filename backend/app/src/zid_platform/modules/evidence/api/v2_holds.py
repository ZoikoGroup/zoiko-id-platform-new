"""/v2/privacy/holds: the document's names for the one legal-hold model (ZID-PRI-004 section 13). One implementation, two names.

    POST /v2/privacy/holds                  [authorization, scope, expires_at, approvers] a scoped hold that needs a second administrator's approval
    POST /v2/privacy/holds/{id}/release     release with a reason, with fresh strong authentication

The hold itself is the evidence module's (`/v1/evidence/legal-holds`): the approval of exactly this scope by someone other than the requester,
the scope (named subjects and/or object types, never tenant-wide), the review date and the audit are enforced there, not copied here. The
document's `authorization` is the approval request that second administrator granted; `approvers` is accepted as a statement of who must be
asked and is checked against the person who actually approved.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.modules.evidence.api import evidence as v1

router = APIRouter(tags=["privacy-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_principal_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    object_types: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{0,63}$")]] = Field(
        default_factory=list, max_length=50
    )


class HoldIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorization: uuid.UUID  # the second administrator's approval request
    basis: str = Field(min_length=3, max_length=200)
    reference: str = Field(min_length=1, max_length=128)
    scope: Scope
    review_at: datetime
    expires_at: datetime | None = None
    approvers: list[uuid.UUID] = Field(default_factory=list, max_length=5)


@router.post("/v2/privacy/holds", status_code=201)
async def create_hold(body: HoldIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    out = await v1.create_hold(
        v1.HoldBody(
            basis=body.basis,
            reference=body.reference,
            subject_principal_ids=body.scope.subject_principal_ids,
            object_types=body.scope.object_types,
            review_at=body.review_at,
            expires_at=body.expires_at,
            approval_request_id=body.authorization,
        ),
        request,
        caller,
    )
    if body.approvers and str(out["approved_by"]) not in {str(a) for a in body.approvers}:
        out = {**out, "approvers_note": "the approver was not among those named in the request"}
    return out


@router.post("/v2/privacy/holds/{hold_id}/release")
async def release_hold(
    hold_id: uuid.UUID, body: v1.ReleaseBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await v1.release_hold(hold_id, body, request, caller)

"""POST /v2/organizations/verify (ZID-INT-002 section 10): is this company on an official register, under this name?

A stateless lookup. It asks the sources the country's route names (an official register, the GLEIF LEI reference, and a supplier only where
its coverage is proven), reconciles them with the repo's existing company-verification rules, and answers with one section 06.1 outcome.
Nothing is stored and no claim is opened: the claim routes (`/v1/org-claims`) remain the only way to a governed verification.

What the answer is NOT: it says nothing about the person asking, their authority to act for the company, ownership, sanctions or adverse
media. A register match is not a person's authority (that comes from an authority root). The names of people are never returned.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.country_gate import enforce
from zid_platform.modules.onboarding.api.org_claims import _adapters, _kyb_error, _require_admin
from zid_platform.modules.organization.ports import (
    KybApplicant,
    KybError,
    KybStatus,
    kyb_check_applicant,
    kyb_decide,
    kyb_routing,
)
from zid_platform.modules.tenant.ports import Mode, matrix_gate
from zid_platform.modules.verification.ports import Capability

router = APIRouter(tags=["organizations-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

# the company-verification vocabulary on the left, the section 06.1 outcome on the right; no entry means PENDING
OUTCOME = {
    KybStatus.VERIFIED: "VERIFIED",
    KybStatus.NEEDS_REVIEW: "INDETERMINATE",
    KybStatus.REJECTED: "NOT_VERIFIED",
    KybStatus.UNAVAILABLE: "UNAVAILABLE",
    KybStatus.EXPIRED: "EXPIRED",
}
LIMITATIONS = [
    "A register match shows the company exists under that number and name; it does not show who is acting for it.",
    "No ownership, sanctions or adverse-media screening is done here, and no person is named.",
    "A source that could not be read is UNAVAILABLE, never 'not found'. A country with no configured source returns UNAVAILABLE.",
    "Nothing is stored. A governed company verification is opened with /v1/org-claims.",
]


class OrganizationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str = Field(pattern=r"^[A-Z]{2}$")  # the incorporation country
    registration_number: str = Field(min_length=1, max_length=40)
    legal_name: str = Field(min_length=1, max_length=300)
    register_id: str | None = Field(default=None, max_length=80)
    lei: str | None = Field(default=None, min_length=20, max_length=20)


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


@router.post(
    "/v2/organizations/verify",
    dependencies=[Depends(matrix_gate("b.submit-refresh-organization-verification", Mode.PREPARE))],
)
async def verify_organization(
    body: OrganizationIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _require_admin(conn, caller)
    enforce(request, body.country, Capability.BUSINESS_VERIFICATION)
    try:
        applicant = kyb_check_applicant(  # a malformed number is refused before any source is asked
            KybApplicant(
                jurisdiction=body.country,
                registration_number=body.registration_number,
                legal_name=body.legal_name,
                lei=body.lei,
                register_id=body.register_id,
            )
        )
        d = await kyb_decide(
            applicant,
            adapters=_adapters(request),
            routing=kyb_routing(),
            now=datetime.now(UTC),
        )
    except KybError as e:
        raise _kyb_error(e) from e
    if not d.sources:
        raise ZidError(
            "COUNTRY_NOT_COVERED",
            "No company source is configured for that country.",
            http_status=422,
        )
    primary = d.primary
    return {
        **_ids(),
        "outcome": OUTCOME.get(d.status, "PENDING"),
        "reasons": list(d.reasons),
        "review_reason": d.review_reason,
        "name_match": d.match_confidence,  # supporting evidence only: a score never decides
        "expires_at": d.expiry_at.isoformat() if d.expiry_at else None,
        "company": None
        if primary is None
        else {
            "legal_name": primary.legal_name,
            "registration_number": primary.registration_number,
            "jurisdiction": primary.jurisdiction,
            "registry_authority": primary.registry_authority,
            "registration_status": primary.registration_status,
            "active": primary.active,
            "source_uri": primary.source_uri,
            "observed_at": primary.observed_at.isoformat(),
        },
        "sources": [
            {
                "source": s.adapter_id,
                "kind": s.kind.value,
                "state": s.state.value,
                "detail": s.detail or None,
            }
            for s in d.sources
        ],
        "limitations": LIMITATIONS,
    }

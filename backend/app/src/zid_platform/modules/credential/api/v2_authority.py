"""POST /v2/mandates/verify and POST /v2/rooms/property-checks (ZID-INT-002 sections 03, 10, 11): authority and property, from supplied evidence.

Both are stateless and use no outside service. They take references to evidence the caller uploaded (scanned clean, within retention), run each
document through the same document and signature checks as `/v2/documents/validate`, and answer one dimension at a time with the document's own
vocabulary. What they establish is deliberately narrow, and every answer says so:

- a signed mandate or title that chains to a trust anchor this deployment configured is VERIFIED_LIMITED at best: the document is as signed, and
  nothing is known about whether the signatory was entitled (no officer register, no land register is configured), nor whether what it states is true;
- a company that exists (a LEI that is merely well-formed) with no mandate on file is INDETERMINATE, never an authority; an official title does not
  let an agent list a property; and a country's rule is never applied to another country;
- the overall property answer is capped at REVIEW_REQUIRED: the marketplace owns the decision, and this check never confirms a right to let.

Mandate checks store nothing. A property check stores its outcomes (never a document or any text from one) per property and host, so that a later
answer that differs from the one before is announced as `property.authority_changed`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.credential.application import property_state
from zid_platform.modules.credential.domain import authority_check as ac
from zid_platform.modules.evidence.ports import assess, trust_config
from zid_platform.protected import protected_action

router = APIRouter(tags=["credential-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
EvRef = Annotated[str, Field(pattern=r"^ev_[0-9a-fA-F-]{36}$")]
Country = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
PACK = "authority-evidence-v1"
VALID_FOR = timedelta(
    hours=24
)  # a document verdict is reread when it is relied on again; nothing here is a standing permission
LIMITATIONS = [
    "A signed document is as signed by the holder of a certificate this deployment trusts. It does not show that the signatory was entitled to bind the company or let the property, and it does not show the facts it states are true.",
    "No officer register, land register or regulator source is configured here: the strongest result is VERIFIED_LIMITED, and never ELIGIBLE.",
    "Evidence that is not usable (unknown, not scanned clean, deleted or past retention) counts for nothing and is not distinguished from evidence that does not exist.",
    "A mandate check stores nothing. A property check stores only its outcomes per property and host, to announce a change.",
]


class Jurisdiction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: Country
    subdivision: str | None = Field(default=None, pattern=r"^[A-Z]{2}-[A-Z0-9]{1,3}$")


class MandateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_ref: Annotated[str, Field(min_length=1, max_length=80)]
    subject_ref: Annotated[str, Field(min_length=1, max_length=80)]
    lei: str | None = Field(default=None, max_length=20)
    authority_claim: Annotated[
        str, Field(min_length=1, max_length=120)
    ]  # the act the person says they may do for the entity
    mandate_evidence_refs: list[EvRef] = Field(default_factory=list, max_length=5)


class PropertyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    property_ref: Annotated[str, Field(min_length=1, max_length=80)]
    host_ref: Annotated[str, Field(min_length=1, max_length=80)]
    relationship: Annotated[str, Field(min_length=1, max_length=40)]
    jurisdiction: Jurisdiction
    requested_dimensions: Annotated[list[str], Field(min_length=1, max_length=6)]
    purpose: str | None = Field(default=None, max_length=64)
    title_evidence_refs: list[EvRef] = Field(default_factory=list, max_length=5)
    mandate_evidence_refs: list[EvRef] = Field(default_factory=list, max_length=5)
    permission_evidence_refs: list[EvRef] = Field(default_factory=list, max_length=5)


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


async def _assessments(
    request: Request, caller: Caller, groups: dict[str, list[str]], resource: str
) -> tuple[dict[str, list[dict[str, Any]]], datetime]:
    """Each distinct reference is assessed once, then handed back to every group that named it."""
    now = datetime.now(UTC)
    config = getattr(request.app.state, "trust_config", None) or trust_config()
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="credential.claim.verify", resource=resource)
        distinct = list(dict.fromkeys(r for refs in groups.values() for r in refs))
        done = {a.evidence_ref: a.public() for a in await assess(conn, distinct, config, now)}
    return {name: [done[r] for r in refs] for name, refs in groups.items()}, now


@router.post("/v2/mandates/verify")
async def verify_mandate(body: MandateIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    grouped, now = await _assessments(
        request, caller, {"mandate": body.mandate_evidence_refs}, "credential-claim:mandate-verify"
    )
    out = ac.mandate_verification(grouped["mandate"], body.lei)
    return {
        **_ids(),
        "organization_ref": body.organization_ref,
        "subject_ref": body.subject_ref,
        "authority_claim": body.authority_claim,
        **out,
        "evidence": grouped["mandate"],
        "policy_pack": {"id": PACK, "version": "1"},
        "checked_at": now.isoformat(),
        "valid_until": (now + VALID_FOR).isoformat(),
        "limitations": LIMITATIONS,
    }


@router.post("/v2/rooms/property-checks")
async def property_check(body: PropertyIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    grouped, now = await _assessments(
        request,
        caller,
        {
            "title": body.title_evidence_refs,
            "mandate": body.mandate_evidence_refs,
            "permission": body.permission_evidence_refs,
        },
        "credential-claim:property-check",
    )
    try:
        out = ac.property_check(
            relationship=body.relationship,
            requested=body.requested_dimensions,
            jurisdiction={
                "country": body.jurisdiction.country,
                "subdivision": body.jurisdiction.subdivision or "",
            },
            title=grouped["title"],
            mandate=grouped["mandate"],
            permission=grouped["permission"],
        )
    except ac.AuthorityError as e:
        raise ZidError(e.code, e.code.replace("_", " ").capitalize() + ".", http_status=422) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        changed = await property_state.record_and_announce(
            conn,
            tenant_id=str(caller.ctx.tenant_id),
            property_ref=body.property_ref,
            host_ref=body.host_ref,
            result=out,
        )
    return {
        **_ids(),
        "authority_changed_event_emitted": changed,
        "property_ref": body.property_ref,
        "host_ref": body.host_ref,
        "relationship": body.relationship,
        "jurisdiction": body.jurisdiction.model_dump(),
        **out,
        "evidence": grouped,
        "policy_pack": {"id": PACK, "version": "1"},
        "checked_at": now.isoformat(),
        "valid_until": (now + VALID_FOR).isoformat(),
        "limitations": LIMITATIONS,
    }

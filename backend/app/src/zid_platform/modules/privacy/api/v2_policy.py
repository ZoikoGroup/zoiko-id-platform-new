"""Two stateless policy checks that wrap the pure rules (ZID-PRI-004 sections 08, 10 and 22).

    POST /v2/privacy/adm-evaluations     may this significant, solely automated decision go ahead under the counsel-set rules for the pack?
                                         Default: no. Without counsel's approved, in-force rules it is BLOCKED and routed to a person.
    POST /v2/privacy/outcome-routing     where does a provider's biometric or document outcome go next? Never to approval, never to a refusal.

Neither stores anything or returns a score, an image or a template. They write no legal rule: counsel's rules are data in
`config/privacy/adm-rules.yaml`, and the routing bands are the provider's own.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.modules.evidence.ports import current_runtime
from zid_platform.modules.privacy.api.v2 import MANAGE, READ, _ids, _run, _tenant
from zid_platform.modules.privacy.application import control as ctl
from zid_platform.modules.privacy.application import export_signing
from zid_platform.modules.privacy.domain import adm, export_mark, outcome_routing

router = APIRouter(tags=["privacy-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


def adm_config() -> dict[str, Any]:
    path = ctl.CONFIG / "adm-rules.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


class AdmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jurisdiction_pack_id: str = Field(min_length=1, max_length=64)
    significant: bool
    solely_automated: bool
    special_category: bool = False
    safeguards_offered: list[str] = Field(default_factory=list, max_length=30)
    special_condition_ref: str | None = Field(default=None, max_length=200)


@router.post("/v2/privacy/adm-evaluations")
async def adm_evaluation(body: AdmIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    rules = (adm_config().get("packs") or {}).get(body.jurisdiction_pack_id)
    override = getattr(
        request.app.state, "adm_rules", None
    )  # a test or a deployment may supply counsel's rules
    if isinstance(override, dict):
        rules = override.get(body.jurisdiction_pack_id, rules)
    out = adm.evaluate(
        rules,
        pack_id=body.jurisdiction_pack_id,
        significant=body.significant,
        solely_automated=body.solely_automated,
        special_category=body.special_category,
        safeguards_offered=body.safeguards_offered,
        special_condition_ref=body.special_condition_ref,
        on=datetime.now(UTC).date(),
    )

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return {
            "allowed": out.allowed,
            "outcome": out.outcome,
            "reason_codes": list(out.reasons),
            "missing_safeguards": list(out.missing_safeguards),
            "route_to_person": out.route_to_person,
            "rules_ref": out.rules_ref,
            "statement": "This check applies the rules counsel set for the jurisdiction pack. It states no rule of its own.",
        }

    return {**_ids(), **await _run(request, caller, READ, "adm_evaluation:new", go)}


class RoutingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match_band: str | None = Field(default=None, max_length=32)
    pad_band: str | None = Field(default=None, max_length=32)
    injection_suspected: bool = False
    capture_quality_ok: bool = True
    high_impact: bool = False


@router.post("/v2/privacy/outcome-routing")
async def outcome_route(body: RoutingIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    r = outcome_routing.route(**body.model_dump())

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return {
            "route": r.route,
            "reason_codes": list(r.reasons),
            "offer_alternative": r.offer_alternative,
            "offer_correction_route": r.offer_correction_route,
            "penalty": r.penalty,
            "statement": "A match is never an approval and no biometric band alone refuses a person; poor capture is never called fraud.",
        }

    return {**_ids(), **await _run(request, caller, READ, "outcome_routing:new", go)}


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: dict[str, Any] = Field(max_length=200)
    include: list[str] = Field(min_length=1, max_length=100)
    recipient: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=200)
    ttl_seconds: int = Field(default=3600, ge=60, le=7 * 24 * 3600)


def _export_failure(e: export_mark.ExportError) -> ZidError:
    return ZidError(
        e.code, e.detail or e.code.replace("_", " ").capitalize() + ".", http_status=422
    )


@router.post("/v2/privacy/exports", status_code=201)
async def create_export(body: ExportIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    """A signed, expiring, recipient-bound export of the named claims only (AT-047). The caller supplies the source claims; nothing is stored."""

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        try:
            payload, missing = export_mark.build(
                body.claims, body.include, recipient=body.recipient, purpose=body.purpose,
                ttl=timedelta(seconds=body.ttl_seconds), tenant_id=_tenant(caller), now=datetime.now(UTC),
            )  # fmt: skip
        except export_mark.ExportError as e:
            raise _export_failure(e) from e
        return {
            "export_id": payload["export_id"],
            "token": export_signing.sign(payload, current_runtime().signer),
            "recipient": body.recipient,
            "purpose": body.purpose,
            "claims_included": sorted(payload["claims"]),
            "claims_not_found": missing,
            "expires_at": datetime.fromtimestamp(payload["exp"], UTC).isoformat(),
            "statement": "Only the claims named are in this export. It names its recipient, is signed and stops being good at its expiry.",
        }

    return {**_ids(), **await _run(request, caller, MANAGE, "export:new", go)}


class ExportVerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=20000)
    expected_recipient: str | None = Field(default=None, max_length=200)


@router.post("/v2/privacy/exports:verify")
async def verify_export(
    body: ExportVerifyIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        try:
            payload = export_signing.verify(
                body.token, keys=current_runtime().trusted_keys(), now=datetime.now(UTC),
                expected_recipient=body.expected_recipient,
            )  # fmt: skip
        except export_mark.ExportError as e:
            raise _export_failure(e) from e
        return {
            "valid": True,
            "export_id": payload["export_id"],
            "recipient": payload["recipient"],
            "claims": payload["claims"],
        }

    return {**_ids(), **await _run(request, caller, READ, "export:verify", go)}

"""POST /v2/age-checks (ZID-INT-002; ZID-GAP-001 v2.0 DTV-19): "is this person at least N", never a date of birth.

A stateless threshold calculation over the rules in `verification/domain/age.py`. Nothing is stored and no birth date, age or estimate is
returned: the answer is AT_LEAST, UNDER or UNDETERMINED with its method, how long it can be relied on, and whether a documented check is
offered instead of a refusal.

One source of verified birth dates exists: a passport or eID chip (`chip`, the same input as /v2/documents/chip-verify). It is passively
authenticated here (the signed object, the chain to a country signing CA the operator configured, every data group against its signed hash),
and its birth date is read only from a verified data group whose MRZ check digits hold. That proves the DOCUMENT says this date, not that the
person presenting it is the holder, so the answer says HOLDER_NOT_BOUND and CLONE_NOT_DETECTED. With no country configured, or any check
failing, the chip gives UNDETERMINED with the reason, never a pass.

What this route cannot do, and says so: a birth date typed into `birth_date` is always SELF_DECLARED and gives UNDETERMINED
(BIRTH_DATE_NOT_VERIFIED), because anyone can type anything. An age ESTIMATE from the caller's own model decides only when its whole margin
plus a safety buffer sits on one side of the line. A government age-verification service or a verified identity case are not connected.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.ports import trust_config, verify_chip
from zid_platform.modules.verification.domain import age
from zid_platform.protected import protected_action

router = APIRouter(tags=["verification-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
LIMITATIONS = [
    "No attested birth-date source is connected: a birth date sent here is self-declared and never gives AT_LEAST.",
    "An estimate decides only when its whole margin plus a safety buffer is on one side of the threshold; near the line it is UNDETERMINED.",
    "UNDETERMINED is neither a pass nor a refusal: offer the person a documented check.",
    "Nothing is stored and no birth date, age or estimate is returned.",
]


class EstimateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    estimated_age: float = Field(ge=0, le=130)
    margin: float = Field(ge=0, le=60)  # the model's own stated error, in years


class ChipIn(BaseModel):
    """What the phone app read from the chip: the signed object (EF.SOD) and the data groups, each as base64. Only group 1 (the MRZ) is read."""

    model_config = ConfigDict(extra="forbid")
    sod_base64: str = Field(min_length=8, max_length=(64 * 1024 * 4) // 3 + 16)
    data_groups: dict[
        Annotated[str, Field(pattern=r"^([1-9]|1[0-6])$")],
        Annotated[str, Field(min_length=4, max_length=(256 * 1024 * 4) // 3 + 16)],
    ] = Field(min_length=1, max_length=16)


class V2AgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold: int
    birth_date: date | None = None
    estimate: EstimateIn | None = None
    chip: ChipIn | None = None

    @model_validator(mode="after")
    def _some_evidence(self) -> V2AgeIn:
        if self.birth_date is None and self.estimate is None and self.chip is None:
            raise ValueError("send a chip, a birth_date or an estimate")
        return self


CHIP_LIMITATIONS = [
    "A chip proves the document says this date, not that the person presenting it is the holder (HOLDER_NOT_BOUND), and a cloned chip is not detected.",
]


def _from_chip(chip: ChipIn, threshold: int, now: datetime, request: Request) -> age.AgeAssertion:
    """A verified birth date from a passively authenticated chip, or UNDETERMINED saying why not. Never a pass from anything less."""
    try:
        sod = base64.b64decode(chip.sod_base64, validate=True)
        groups = {int(k): base64.b64decode(v, validate=True) for k, v in chip.data_groups.items()}
    except (binascii.Error, ValueError):
        raise ZidError(
            "VALIDATION_FAILED", "The chip data is not valid base64.", http_status=422
        ) from None
    config = getattr(request.app.state, "trust_config", None) or trust_config()
    verdict = verify_chip(sod, groups, csca=config.csca, crls=config.crls)

    def undetermined(*reasons: str) -> age.AgeAssertion:
        return age.AgeAssertion(
            threshold, age.Result.UNDETERMINED, age.Method.VERIFIED_BIRTH_DATE, None, reasons, True
        )

    if not verdict.document_authentic:
        return undetermined("CHIP_NOT_VERIFIED", *verdict.reasons[:3])
    document = verdict.detail.get("document")
    born = document.get("birth_date") if isinstance(document, dict) else None
    if (
        not born
        or "MRZ_CHECK_DIGITS_FAILED" in verdict.reasons
        or "DG1_NOT_SUPPLIED" in verdict.reasons
    ):
        return undetermined("CHIP_BIRTH_DATE_NOT_RELIABLE")
    try:
        assertion = age.from_birth_date(
            date.fromisoformat(str(born)), threshold, on=now.date(), verified=True
        )
    except (ValueError, age.AgeError):
        return undetermined("CHIP_BIRTH_DATE_NOT_RELIABLE")
    return replace(assertion, reason_codes=("HOLDER_NOT_BOUND", "CLONE_NOT_DETECTED"))


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


@router.post("/v2/age-checks")
async def age_check(body: V2AgeIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="credential.claim.verify", resource="age-check:new"
        )
    now = datetime.now(UTC)
    try:
        found: list[age.AgeAssertion] = []
        if body.chip is not None:
            found.append(_from_chip(body.chip, body.threshold, now, request))
        if body.birth_date is not None:
            found.append(
                age.from_birth_date(body.birth_date, body.threshold, on=now.date(), verified=False)
            )
        if body.estimate is not None:
            found.append(
                age.from_estimate(
                    body.estimate.estimated_age, body.estimate.margin, body.threshold, now=now
                )
            )
        # one piece of evidence stands as it is (combine() would drop its reason); several are weighed together
        result = found[0] if len(found) == 1 else age.combine(found)
    except age.AgeError as e:
        raise ZidError(e.code, e.code.replace("_", " ").capitalize() + ".", http_status=422) from e
    limitations = [*LIMITATIONS, *CHIP_LIMITATIONS] if body.chip is not None else LIMITATIONS
    return {**_ids(), **result.to_contract(), "limitations": limitations}

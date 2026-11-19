"""The Face lab in the Operator Console (CR-128): measure how alike two photographs are, with an open-source engine, for evaluation.

    GET  /v1/regulatory/face-lab           whether the engine is reachable, and where the approval gate stands (mode and every record still missing)
    POST /v1/regulatory/face-lab/compare   two photographs in, a similarity measurement out

What this is not: an approval. It runs the lab engine on photographs the operator supplies and answers with a score. It stores no photograph, it logs no score against a person (the audit
record says only which engine ran and whether the result was above the engine's suggested threshold), it has no liveness test, and nothing it returns lets anyone through: the platform's
own gate refuses to use a face engine for a live decision until every evaluation, lab test, impact assessment and licence record exists (docs/signoff/face-engine-owner-pack.md).
Use only photographs of people who agreed to be photographed for this test.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import require_operator
from zid_platform.operator_actions import Actions


class _Verification:
    """The verification module's public interface, imported on first use (avoids a dependency cycle at import time)."""

    def __getattr__(self, name: str) -> Any:
        import zid_platform.modules.verification.ports as ports  # noqa: PLR0402

        return getattr(ports, name)


v = _Verification()

router = APIRouter(tags=["regulatory"])
OperatorDep = Annotated[Any, Depends(require_operator)]

RUN = "regulatory.face_lab.run"
MAX_IMAGE = 8 * 1024 * 1024
LAB_NOTE = (
    "A lab measurement, not an approval. This engine has no liveness test, is not evaluated, and cannot approve anyone: a face match alone can be shown another person's "
    "photograph. Use photographs of people who agreed to this test."
)

_STATUS = {
    "FACE_ENGINE_NOT_CONFIGURED": 503,
    "FACE_ENGINE_UNAVAILABLE": 503,
    "FACE_IMAGE_REFUSED": 422,
    "IMAGE_UNREADABLE": 422,
    "IMAGE_TYPE_NOT_ALLOWED": 422,
    "IMAGE_TOO_LARGE": 422,
    "NO_FACE_PROBE": 422,
    "NO_FACE_REFERENCE": 422,
    "MULTIPLE_FACES_PROBE": 422,
    "MULTIPLE_FACES_REFERENCE": 422,
}


def _errors() -> tuple[type[BaseException], ...]:
    return (v.face_lab.FaceLabError,)


actions = Actions(
    namespace=uuid.UUID("9a2d6e41-7b35-4c08-8f1e-3d5a7c9b2e60"),
    refused_event="regulatory.face_lab.refused",
    status=_STATUS,
    errors=_errors,
    refused_code="FACE_LAB_REFUSED",
    capability_hint="the face lab capability",
)


class CompareIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    probe_base64: str = Field(min_length=100, max_length=12_000_000)  # the live photograph
    reference_base64: str = Field(min_length=100, max_length=12_000_000)  # the document photograph


def _decode(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ZidError("IMAGE_UNREADABLE", "That photograph could not be read.", http_status=422) from e
    if len(raw) > MAX_IMAGE:
        raise ZidError("IMAGE_TOO_LARGE", "That photograph is too large (8 MB at most).", http_status=422)
    return raw


@router.get("/v1/regulatory/face-lab")
async def lab_status(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need(conn, op, RUN, "regulatory:face-lab")
    return {
        "configured": v.face_lab.configured_url() is not None,
        "engine": {"id": v.face_lab.ENGINE_ID, "version": v.face_lab.ENGINE_VERSION},
        "liveness": {"available": False, "note": "No open-source liveness model is installed in the lab image."},
        "gate": v.face_lab.gate_status(),
        "note": LAB_NOTE,
    }


@router.post("/v1/regulatory/face-lab/compare")
async def compare(body: CompareIn, request: Request, op: OperatorDep) -> dict[str, Any]:
    probe, reference = _decode(body.probe_base64), _decode(body.reference_base64)

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        got = await v.face_lab.compare(probe, reference)
        suggested = float(got.get("recommended_cosine_threshold", 0.363))
        above = float(got["cosine"]) >= suggested
        out = {**got, "above_suggested_threshold": above, "liveness": {"available": False}, "note": LAB_NOTE}
        # the record names the engine and the side of the suggested threshold, never the photographs or the score
        return out, "face-lab", {"engine": v.face_lab.ENGINE_ID, "result": "ABOVE_SUGGESTED_THRESHOLD" if above else "BELOW_SUGGESTED_THRESHOLD"}

    return await actions.act(
        request,
        op,
        event="regulatory.face_lab.compared",
        action="compare",
        capability=RUN,
        target_type="face_lab",
        target="face-lab",
        work=work,
    )

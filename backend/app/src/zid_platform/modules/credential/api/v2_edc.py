"""POST /v2/credentials/edc/verify (ZID-GAP-001 v2.0 GID-051; ZID-INT-002): check the e-seal of a European Digital Credential.

Stateless: the credential is read and answered, nothing is stored and nothing about the holder is returned. Two separate answers:
- SEAL: is the XAdES e-seal sound, does it chain to a trust anchor THIS deployment configured, and does an authenticated trusted list
  name the sealer's service as a qualified seal service (otherwise NOT_ASSESSED);
- CLAIM: who the credential says awarded it, its title and issue date, read from the XML.

What this does NOT say, and the response says so: that the sealer is the awarding body it names, that the award is true, that the credential
is current (a rescinded degree is a separate source check, GLB-36), or that it is a licence. `primary_source_verified` and
`statutory_license` are always false here. Anchors and trusted lists ship empty: until an operator supplies them, nothing is trusted.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.credential.application import edc
from zid_platform.modules.credential.domain import trust_graph
from zid_platform.modules.credential.infrastructure.registries import issuer_registry
from zid_platform.modules.evidence.ports import check_signed_xml
from zid_platform.protected import protected_action

router = APIRouter(tags=["credential-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
MAX_BYTES = 2 * 1024 * 1024
LIMITATIONS = [
    "A valid seal shows who sealed these exact bytes. It is tied to the awarding body named inside only when that body is a registered issuer and the sealing certificate is one the registry records for it (issuer_binding BOUND); otherwise the tier stays DOCUMENT_INTEGRITY.",
    "Whether the credential is still current (not rescinded) needs the awarding body's own source: this check does not ask it.",
    "This is never a licence or a right to practise, and never a primary-source confirmation.",
    "Nothing about the holder is returned or stored.",
]


class V2EdcIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_base64: str = Field(min_length=4, max_length=(MAX_BYTES * 4) // 3 + 16)


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


def _public(result: Any) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "reasons": list(result.reasons),
        "detail": result.detail,
    }


@router.post("/v2/credentials/edc/verify")
async def verify_edc(body: V2EdcIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="credential.claim.verify", resource="credential-claim:edc-verify"
        )
    try:
        xml = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ZidError(
            "VALIDATION_FAILED", "content_base64 is not valid base64.", http_status=422
        ) from e
    if len(xml) > MAX_BYTES:
        raise ZidError("DOCUMENT_TOO_LARGE", "Document too large.", http_status=413)
    try:
        claim = edc.read(xml)
    except edc.EdcError as e:
        raise ZidError(e.code, e.code.replace("_", " ").capitalize() + ".", http_status=422) from e
    config = getattr(request.app.state, "trust_config", None)
    check = check_signed_xml(xml, config)
    binding = trust_graph.bind_seal(
        issuer_registry(),
        claim.awarding_body,
        check.signer_sha256,
        check.signer_organization_identifier,
        on=datetime.now(UTC).date(),
    )
    # ISSUER_AUTHENTICATED needs both: a sound seal AND the sealer recorded as that issuer's. A sound seal alone is document integrity.
    if check.sealed and binding.result is trust_graph.Binding.BOUND:
        tier = "ISSUER_AUTHENTICATED"
    elif check.sealed:
        tier = "DOCUMENT_INTEGRITY"
    else:
        tier = "SELF_DECLARED"
    return {
        **_ids(),
        "seal": _public(check.signature),
        "qualified_seal": _public(check.seal),
        "sealed_by": check.signer,
        "claim": claim.as_dict(),
        "claim_fields_missing": claim.missing(),
        "accepted_as_claim": check.sealed,
        "assurance_tier": tier,
        "issuer_binding": binding.result.value,
        "issuer_id": binding.issuer_id,
        "issuer_candidates": list(binding.candidates),
        "primary_source_verified": False,
        "statutory_license": False,
        "limitations": LIMITATIONS,
    }

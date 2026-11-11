"""The /v2 evidence manifest (ZID-INT-002 section 03): GET /v2/evidence/{manifest_ref}/manifest.

A manifest is the restricted view of one decision's evidence: the receipt's commitments (never a raw document, a claimed value or an
identifier) and whether the receipt still verifies. The reference is the one a credential answer carries (`manifest_<check id>`), the
receipt reference it lists (`rcpt_<receipt id>`), or a bare id. Reading and verifying are each a decision of their own and leave
their own receipt, exactly as on /v1.

Evidence UPLOADS (`POST /v2/evidence/uploads`) are not here: a signed upload needs content sniffing, malware scanning, EXIF stripping
and an object store with deletion controls, none of which exists in this repository.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.api.evidence import http, receipt_summary
from zid_platform.modules.evidence.application import replay
from zid_platform.modules.evidence.domain.ledger import LedgerError
from zid_platform.modules.evidence.infrastructure import receipt_store
from zid_platform.protected import protected_action

router = APIRouter(tags=["evidence-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


def parse_ref(ref: str) -> uuid.UUID:
    for prefix in ("manifest_", "rcpt_"):
        if ref.startswith(prefix):
            ref = ref[len(prefix) :]
            break
    try:
        return uuid.UUID(ref)
    except ValueError as e:
        raise ZidError("VALIDATION_FAILED", "A reference is not valid.", http_status=422) from e


@router.get("/v2/evidence/{manifest_ref}/manifest")
async def manifest(manifest_ref: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    ident = str(parse_ref(manifest_ref))
    ctx = current_context()
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn, caller, action="evidence.read", resource=f"decision_receipt:{ident}"
            )
            # the reference is a decision id (a credential answer) or a receipt id; both name the same evidence
            row = await receipt_store.load_receipt(conn, ident)
            if row is None:
                found = await receipt_store.receipt_for_decision(conn, ident)
                row = (
                    await receipt_store.load_receipt(conn, str(found.receipt_id)) if found else None
                )
            if row is None:
                raise ZidError("NOT_FOUND", "No such manifest.", http_status=404)
            await protected_action(
                conn,
                caller,
                action="evidence.verify",
                resource=f"decision_receipt:{row.receipt_id}",
            )
            verified = await replay.verify_receipt(conn, str(row.receipt_id))
    except LedgerError as e:
        raise http(e) from e
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "manifest_ref": f"manifest_{row.decision_id}",
        "receipt": receipt_summary(row),
        "verification": verified.to_dict(),
        "contains": "commitments only: no raw document, claimed value or identifier",
    }

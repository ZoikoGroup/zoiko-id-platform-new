"""The ZID-RCG-003 section 09.2 regulatory API, at the document's own paths (CR-085). Operator plane, like the /v1/regulatory routes it reuses.

    POST /v2/regulatory/pack-revisions                         a new versioned draft of a pack (GRC editor; never a direct publish)
    POST /v2/regulatory/pack-revisions/{revision_id}/approve   sign the pack hash in one role (role and segregation checked)
    GET  /v2/regulatory/providers/{provider_id}/services       the certified services and their attestation snapshots (no inferred scope)
    POST /v2/regulatory/provider-attestations/refresh          record what the official register says now (logs the source receipt)
    POST /v2/regulatory/holds                                  the kill switch by exact service, market and purpose
    GET  /v2/regulatory/evidence-bundles/{bundle_id}           a minimal, signed proof export; a stated purpose and legal basis are required

Each mutating route is the same handler as its /v1 twin, so the capabilities, segregation of duties, audit records and outbox events are the same.
`revision_id` is `<pack_id>:v<version>`. An evidence bundle id is `claim:<template_id>` (a public claim's proof) or `run:<run_id>` (a conformance run);
every export, and every refused one, is audited; a bundle is signed with the platform evidence key when one is configured.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.api import regulatory as packs_api
from zid_platform.modules.operator_access.api import regulatory_releases as releases_api
from zid_platform.modules.operator_access.api import regulatory_suspensions as holds_api
from zid_platform.modules.operator_access.ports import CAPABILITIES, require_operator
from zid_platform.operator_actions import Actions, announce


class _Verification:
    def __getattr__(self, name: str) -> Any:
        import zid_platform.modules.verification.ports as ports  # noqa: PLR0402

        return getattr(ports, name)


v = _Verification()
router = APIRouter(tags=["regulatory"])
OperatorDep = Annotated[Any, Depends(require_operator)]
READERS = tuple(sorted(n for n in CAPABILITIES if n.startswith("regulatory.")))
REVISION = re.compile(r"^(?P<pack>[a-z0-9][a-z0-9-]{0,127}):v(?P<version>[1-9][0-9]{0,5})$")
BUNDLE = r"^(claim|run):[a-z0-9][a-z0-9-]{0,127}$"
PURPOSES = Literal["REGULATOR_REQUEST", "AUDIT", "LEGAL_CLAIM", "INTERNAL_REVIEW"]

actions = Actions(
    namespace=uuid.UUID("9a3e5f71-0c24-4b8d-a6e1-52f7b9c03d48"),
    refused_event="regulatory.evidence_bundle.refused",
    status={},
    errors=lambda: (v.claim_model.ClaimTemplateError, v.conformance_model.ConformanceError),
    refused_code="EVIDENCE_BUNDLE_REFUSED",
    capability_hint="a regulatory capability",
)


class PackRevisionIn(packs_api.PackReviseIn):
    pack_id: str = Field(pattern=packs_api.PACK_ID)


@router.post("/v2/regulatory/pack-revisions", status_code=201)
async def create_pack_revision(
    body: PackRevisionIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    revise = packs_api.PackReviseIn(sources=body.sources, rules=body.rules)
    return await packs_api.revise(body.pack_id, revise, request, op)


@router.post("/v2/regulatory/pack-revisions/{revision_id}/approve")
async def approve_pack_revision(
    revision_id: Annotated[str, Path(pattern=REVISION.pattern)],
    body: packs_api.PackApproveIn,
    request: Request,
    op: OperatorDep,
) -> dict[str, Any]:
    m = REVISION.match(revision_id)
    assert m is not None  # the path pattern already holds
    return await packs_api.approve(m["pack"], int(m["version"]), body, request, op)


@router.get("/v2/regulatory/providers/{provider_id}/services")
async def provider_services(
    provider_id: Annotated[str, Path(min_length=1, max_length=128)],
    request: Request,
    op: OperatorDep,
) -> dict[str, Any]:
    """Only what an official-register attestation shows, scope by scope, with the snapshot behind it. A scope that is not certified now is listed
    with its reason; nothing is inferred from the provider's other services."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, f"regulatory:{provider_id}")
        scopes = await v.attestations.scopes_of(conn, entity=provider_id)
        decisions = await v.suspensions.hold_suspended(
            conn,
            await v.attestations.decide_scopes(conn, scopes, at=datetime.now(UTC)),
            at=datetime.now(UTC),
        )
    return {
        "provider_id": provider_id,
        "services": [
            {
                "scheme": s.scheme,
                "country": s.country,
                "service_code": s.service_code,
                "certified": d.certified,
                "reason": d.reason,
                "snapshot": None
                if d.snapshot is None
                else {
                    "snapshot_id": d.snapshot.snapshot_id,
                    "register_url": d.snapshot.register_url,
                    "register_entry_id": d.snapshot.register_entry_id,
                    "fetched_at": d.snapshot.fetched_at.isoformat(),
                    "proof_hash": d.snapshot.proof_hash,
                },
            }
            for s, d in decisions.items()
        ],
    }


@router.post("/v2/regulatory/provider-attestations/refresh", status_code=201)
async def refresh_provider_attestation(
    body: releases_api.AttestationIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await releases_api.record_attestation(body, request, op)


@router.post("/v2/regulatory/holds", status_code=201)
async def place_hold(
    body: holds_api.SuspensionApplyIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await holds_api.suspend(body, request, op)


@router.get("/v2/regulatory/evidence-bundles/{bundle_id}")
async def evidence_bundle(
    bundle_id: Annotated[str, Path(pattern=BUNDLE)],
    request: Request,
    op: OperatorDep,
    purpose: Annotated[PURPOSES | None, Query()] = None,
    legal_basis_ref: Annotated[str | None, Query(min_length=3, max_length=256)] = None,
) -> dict[str, Any]:
    """A minimal proof export: what the claim or run said, why, the evidence references and a digest, signed when a key is configured. Without a
    stated purpose and legal basis the export is refused, and the refusal is audited (ZID-RCG-003 09.2 and T38)."""
    kind, ref = bundle_id.split(":", 1)
    now = datetime.now(UTC)
    if purpose is None or legal_basis_ref is None:
        async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
            await actions.audit(
                conn, op, event="regulatory.evidence_bundle.refused", action="export", target_type="evidence_bundle", target=bundle_id,
                outcome="DENIED", now=now, reason="EXPORT_PURPOSE_REQUIRED", details={"purpose": purpose}, once=False,
            )  # fmt: skip
        raise ZidError(
            "EXPORT_PURPOSE_REQUIRED",
            "State the purpose and the legal basis of this export.",
            http_status=422,
        )
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, f"regulatory:{bundle_id}")
        if kind == "claim":
            given = getattr(request.app.state, "claim_templates", None)
            try:
                body = await v.regulated_claims.proof(
                    conn, given if given is not None else v.claim_templates(), ref, at=now
                )
            except v.claim_model.ClaimTemplateError as e:
                raise ZidError(e.code, str(e), http_status=404) from e
        else:
            run = await v.conformance.load_run(conn, ref)
            if run is None:
                raise ZidError(
                    "CONFORMANCE_RUN_NOT_FOUND", "No such conformance run.", http_status=404
                )
            status = v.conformance_model.exit_status(run, await v.conformance.reviews(conn, ref))
            body = {
                "run_id": run.run_id,
                "commit": run.commit,
                "environment": run.environment,
                "run_hash": run.run_hash,
                "outcomes": {r.case: r.outcome.value for r in run.results},
                "p0_exit": {"met": status.met, "reasons": list(status.reasons)},
            }
        bundle = {
            "bundle_id": bundle_id,
            "exported_at": now.isoformat(),
            "purpose": purpose,
            "legal_basis_ref": legal_basis_ref,
            "content": body,
        }
        digest = hashlib.sha256(
            json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        sign = getattr(request.app.state, "company_evidence_sign", None)
        signature = (
            sign({"bundle_id": bundle_id, "digest": digest, "exported_at": bundle["exported_at"]})
            if sign
            else None
        )
        details = {
            "digest": digest,
            "purpose": purpose,
            "legal_basis_ref": legal_basis_ref,
            "signed": signature is not None,
        }
        await actions.audit(
            conn, op, event="regulatory.evidence_bundle.exported", action="export", target_type="evidence_bundle", target=bundle_id,
            outcome="SUCCESS", now=now, details=details, once=False,
        )  # fmt: skip
        await announce(
            conn, name="evidence.bundle.exported", tenant_id=str(request.state.operator_ctx.tenant_id), aggregate_type="evidence_bundle",
            aggregate_id=bundle_id, data={"target": bundle_id, **details}, occurred_at=now,
        )  # fmt: skip
    return bundle | {
        "digest": digest,
        "signature": signature,
        "signature_status": "SIGNED" if signature else "NO_SIGNING_KEY",
    }

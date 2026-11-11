"""The /v2 verification case (ZID-INT-002 sections 04 and 05.1).

    POST /v2/verifications                          open a case of separate checks (Idempotency-Key); answers 202 PENDING
    GET  /v2/verifications/{case_id}                the case: its state and one outcome per check, never one overall pass
    POST /v2/verifications/{case_id}/tasks/{check}  submit what one check needs (Idempotency-Key)
    POST /v2/verifications/{case_id}/cancel         cancel a pending case; a finished case is 409 incompatible_state

A case orchestrates; it decides nothing. A credential check runs through the same path as /v2/credentials/verify (pack, source
contract, signed assertion, evidence receipt). An identity check is a link to an identity verification session started on /v1: the task is
complete only when that session is itself final. Requested checks must each have a country pack, or the case is refused with 422
`service_unavailable_in_market` and nothing is created.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_idempotency import Scope, begin, complete

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.country_gate import enforce
from zid_platform.modules.credential.api import claim_checks as v1
from zid_platform.modules.credential.api.v2 import V2IssuerClaim, V2Jurisdiction
from zid_platform.modules.credential.application import case_service as svc
from zid_platform.modules.credential.application import v2_view as vw
from zid_platform.modules.credential.domain import cases as cs
from zid_platform.modules.credential.domain.claims import ClaimType
from zid_platform.modules.credential.infrastructure import case_store
from zid_platform.modules.verification.ports import (
    Capability,
    PackError,
    certification_registry,
    pack_registry,
    resolve,
    verification_session_view,
)
from zid_platform.modules.webhook.ports import endpoint_is_active
from zid_platform.protected import protected_action

router = APIRouter(tags=["credential-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
CASE_TTL = timedelta(days=7)


def _ids() -> tuple[str, str]:
    ctx = current_context()
    return (ctx.request_id, ctx.correlation_id) if ctx else ("req_unknown", "cor_unknown")


def _uuid(value: str, prefix: str) -> uuid.UUID:
    try:
        return uuid.UUID(value.removeprefix(prefix))
    except ValueError as e:
        raise ZidError("VALIDATION_FAILED", "A reference is not valid.", http_status=422) from e


def _problem(e: cs.CaseError) -> ZidError:
    status = {"CASE_NOT_FOUND": 404, "INCOMPATIBLE_STATE": 409}.get(e.code, 422)
    return ZidError(e.code, e.code.replace("_", " ").capitalize() + ".", http_status=status)


class V2Workflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=20)


class V2CaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_ref: str | None = Field(default=None, max_length=80)
    tenant_ref: str | None = Field(
        default=None, max_length=80
    )  # informative only: the tenant comes from the token
    purpose: str = Field(min_length=1, max_length=64)
    jurisdiction: V2Jurisdiction
    workflow: V2Workflow
    requested_checks: list[str] = Field(min_length=1, max_length=10)
    consent_ref: str | None = Field(default=None, max_length=200)
    callback_ref: str | None = Field(default=None, max_length=80)
    client_ref: str | None = Field(default=None, max_length=200)


@router.post("/v2/verifications", status_code=202)
async def open_case(
    body: V2CaseIn, request: Request, caller: CallerDep, idempotency_key: IdemKey
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    if body.tenant_ref and body.tenant_ref not in {"server_derived", tenant, f"org_{tenant}"}:
        raise ZidError("FORBIDDEN", "A caller cannot name another tenant.", http_status=403)
    try:
        checks = cs.validate_checks(body.requested_checks)
    except cs.CaseError as e:
        raise _problem(e) from e
    subject = (
        str(_uuid(body.subject_ref, "sub_"))
        if body.subject_ref
        else str(caller.ctx.principal_id or "")
    )
    if not subject:
        raise ZidError("SUBJECT_REQUIRED", "A subject is required.", http_status=422)
    callback = str(_uuid(body.callback_ref, "wh_")) if body.callback_ref else None
    now = datetime.now(UTC)
    environment = str(getattr(request.app.state, "environment", "production"))
    sub = body.jurisdiction.as_v1().subdivision

    # every requested check must have a place to run: no pack or closed country means no case at all (never a positive result)
    packs: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for kind in checks:
        service = cs.SERVICE_OF.get(kind)
        if service is None:  # identity: the country must be open for identity verification
            try:
                enforce(request, body.jurisdiction.country, Capability.INDIVIDUAL_VERIFICATION)
            except ZidError:
                unavailable.append(kind)
            continue
        try:
            p = resolve(
                pack_registry(),
                country=body.jurisdiction.country,
                service_code=service,
                subdivision=sub,
                now=now,
                environment=environment,
                certifications=certification_registry(),
            )
            packs.append({"check": kind, "pack_id": p.pack_id, "version": p.version})
        except PackError:
            unavailable.append(kind)
    if unavailable:
        raise ZidError(
            "SERVICE_UNAVAILABLE_IN_MARKET",
            "No authorized service is available for "
            + ", ".join(unavailable)
            + " in this jurisdiction.",
            http_status=422,
        )

    scope = Scope(tenant, caller.client_id, "POST /v2/verifications")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        stored = await begin(
            conn, scope=scope, key=idempotency_key, payload=body.model_dump(mode="json")
        )
        if stored is not None:
            return dict(stored.body)
        await protected_action(
            conn, caller, action="credential.claim.verify", resource="verification-case:new"
        )
        if callback and not await endpoint_is_active(conn, callback):
            raise ZidError(
                "CALLBACK_NOT_AUTHORIZED",
                "callback_ref must be an active webhook endpoint of this tenant.",
                http_status=422,
            )
        case_id = await svc.open_case(
            conn,
            tenant_id=tenant,
            subject=subject,
            purpose=body.purpose.upper(),
            country=body.jurisdiction.country,
            subdivision=sub,
            profile_id=body.workflow.profile_id,
            profile_version=body.workflow.version,
            consent_ref=body.consent_ref,
            callback_endpoint_id=callback,
            client_ref=body.client_ref,
            checks=checks,
            policy_packs=packs,
            created_by=str(caller.ctx.principal_id or subject),
            created_at=now,
            expires_at=now + CASE_TTL,
        )
        case = await case_store.load_case(conn, case_id)
        tasks = await case_store.load_tasks(conn, case_id)
        rid, cid = _ids()
        out = svc.case_view(case or {}, tasks, request_id=rid, correlation_id=cid)
        await complete(conn, scope=scope, key=idempotency_key, status=202, body=out)
    return out


async def _refresh_identity(conn: Any, task: dict[str, Any], subject: str, now: datetime) -> None:
    """A linked identity session that has become final completes its task."""
    if task["check_kind"] != "identity" or task["state"] != cs.TaskState.IN_PROGRESS.value:
        return
    session = await verification_session_view(conn, str(task["identity_verification_id"]))
    if session is None or session["subject_principal_id"] != subject:
        return
    outcome, state = cs.identity_outcome(session["status"])
    if state is cs.TaskState.COMPLETE and outcome is not None:
        await case_store.complete_task(conn, str(task["task_id"]), outcome=outcome, now=now)


@router.get("/v2/verifications/{case_id}")
async def read_case(case_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    cid = str(_uuid(case_id, "case_"))
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="credential.read", resource=f"verification-case:{cid}"
        )
        case = await case_store.load_case(conn, cid)
        if case is None:
            raise ZidError("NOT_FOUND", "Not found.", http_status=404)
        for t in await case_store.load_tasks(conn, cid):
            await _refresh_identity(conn, t, str(case["subject_principal_id"]), now)
        case = await svc.settle(conn, cid, now)
        tasks = await case_store.load_tasks(conn, cid)
    rid, corr = _ids()
    return svc.case_view(case, tasks, request_id=rid, correlation_id=corr)


class V2TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # a credential check
    issuer_claim: V2IssuerClaim | None = None
    credential_claim: dict[str, Any] | None = Field(default=None, max_length=30)
    # an identity check: the /v1 identity verification session that does the work
    verification_id: uuid.UUID | None = None


@router.post("/v2/verifications/{case_id}/tasks/{check}", status_code=200)
async def submit_task(
    case_id: str,
    check: str,
    body: V2TaskIn,
    request: Request,
    caller: CallerDep,
    idempotency_key: IdemKey,
) -> dict[str, Any]:
    cid = str(_uuid(case_id, "case_"))
    if check not in cs.CHECK_KINDS:
        raise ZidError("CHECK_NOT_SUPPORTED", "That check is not supported.", http_status=422)
    now = datetime.now(UTC)
    scope = Scope(caller.ctx.tenant_id or "", caller.client_id, "POST /v2/verifications/tasks")
    payload = {"case": cid, "check": check, **body.model_dump(mode="json")}
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        stored = await begin(conn, scope=scope, key=idempotency_key, payload=payload)
        if stored is not None:
            return dict(stored.body)
        case = await case_store.load_case(conn, cid, lock=True)
        if case is None:
            raise ZidError("NOT_FOUND", "Not found.", http_status=404)
        try:
            cs.require_open(cs.CaseState(case["state"]))
        except cs.CaseError as e:
            raise _problem(e) from e
        task = next(
            (
                t
                for t in await case_store.load_tasks(conn, cid, lock=True)
                if t["check_kind"] == check
            ),
            None,
        )
        if task is None:
            raise ZidError("NOT_FOUND", "That check is not part of this case.", http_status=404)
        if task["state"] == cs.TaskState.COMPLETE.value:
            raise _problem(cs.CaseError("INCOMPATIBLE_STATE", "task already complete"))
        subject = str(case["subject_principal_id"])
        kind = cs.CHECK_KINDS[check]
        if kind is None:  # identity: link the session; it must be this person's own
            if body.verification_id is None:
                raise ZidError("VALIDATION_FAILED", "verification_id is required.", http_status=422)
            session = await verification_session_view(conn, str(body.verification_id))
            if session is None or session["subject_principal_id"] != subject:
                raise ZidError(
                    "NOT_FOUND", "No such identity verification for this person.", http_status=404
                )
            await case_store.link_identity(
                conn, str(task["task_id"]), str(body.verification_id), now
            )
            refreshed = next(
                t for t in await case_store.load_tasks(conn, cid) if t["task_id"] == task["task_id"]
            )
            await _refresh_identity(conn, refreshed, subject, now)
        else:
            if body.issuer_claim is None or body.credential_claim is None:
                raise ZidError(
                    "VALIDATION_FAILED",
                    "issuer_claim and credential_claim are required.",
                    http_status=422,
                )
            claim = _claim_body(kind, body, case)
            result = await v1.perform_check(kind, claim, request, caller, conn)
            outcome, _ = vw.outcome_of(result.to_contract())
            await case_store.complete_task(
                conn,
                str(task["task_id"]),
                outcome=outcome.value,
                now=now,
                credential_check_id=result.check_id,
            )
        case = await svc.settle(conn, cid, now)
        tasks = await case_store.load_tasks(conn, cid)
        rid, corr = _ids()
        out = svc.case_view(case, tasks, request_id=rid, correlation_id=corr)
        await complete(conn, scope=scope, key=idempotency_key, status=200, body=out)
    return out


def _claim_body(kind: ClaimType, body: V2TaskIn, case: dict[str, Any]) -> v1.CredCheckIn:
    assert body.issuer_claim is not None and body.credential_claim is not None
    return v1.CredCheckIn(
        claim=vw.claim_from_v2(kind, body.issuer_claim.model_dump(), body.credential_claim),
        jurisdiction=v1.CredJurisdictionIn(
            country=case["country"], subdivision=case["subdivision"]
        ),
        subject_principal_id=case["subject_principal_id"],
        purpose=case["purpose"],
        consent_ref=case["consent_ref"],
    )


@router.post("/v2/verifications/{case_id}/cancel")
async def cancel_case(case_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    cid = str(_uuid(case_id, "case_"))
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="credential.claim.verify", resource=f"verification-case:{cid}"
        )
        case = await case_store.load_case(conn, cid, lock=True)
        if case is None:
            raise ZidError("NOT_FOUND", "Not found.", http_status=404)
        case = await svc.settle(
            conn, cid, now
        )  # a case past its time is EXPIRED before anything else
        try:
            cs.require_open(cs.CaseState(case["state"]))
        except cs.CaseError as e:
            raise _problem(e) from e
        await case_store.close_case(conn, cid, cs.CaseState.CANCELED.value, now)
        case = {**case, "state": cs.CaseState.CANCELED.value}
        tasks = await case_store.load_tasks(conn, cid)
    rid, corr = _ids()
    return svc.case_view(case, tasks, request_id=rid, correlation_id=corr)

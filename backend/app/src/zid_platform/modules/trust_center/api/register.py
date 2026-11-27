"""The Trust Center's claims register, operator plane (ZID-CP-WF12 v2.0, WF-12.09 to 12.11; CR-174). Company staff only: nothing here is a public surface.

    GET  /v1/trust-center/claims                                     the latest revision of every claim                        (any trust capability)
    GET  /v1/trust-center/claims/{id}/revisions/{rev}                one revision with its approvals, blockers and history     (any trust capability)
    POST /v1/trust-center/claims                                     draft a new claim                                         (trust.claim.author)
    POST /v1/trust-center/claims/{id}/revisions                      a new revision of an existing claim; never an edit        (trust.claim.author)
    POST /v1/trust-center/claims/{id}/revisions/{rev}:approve        approve this exact hash in one role                       (trust.claim.review_<role>)
    POST /v1/trust-center/claims/{id}/revisions/{rev}:refuse         refuse it, with a reason                                  (trust.claim.review_<role>)
    POST /v1/trust-center/claims/{id}/revisions/{rev}:publish        put it live through the publication gate                  (trust.claim.publish)
    POST /v1/trust-center/claims/{id}:rollback                       back to the last validly approved revision, re-checked    (trust.claim.publish)
    POST /v1/trust-center/claims/{id}:withdraw                       emergency withdrawal by one responder                     (trust.claim.withdraw)
    GET  /v1/trust-center/withdrawals[?open=true]                    withdrawals and whether their review is done              (any trust capability)
    POST /v1/trust-center/withdrawals/{id}:review                    the retrospective review, by someone other than the responder (trust.claim.publish)
    GET  /v1/trust-center/evidence, POST /v1/trust-center/evidence   record evidence                                           (trust.evidence.manage)
    POST /v1/trust-center/evidence/{id}:confirm                      the issuer's confirmation, by someone other than who recorded it (trust.evidence.manage)
    POST /v1/trust-center/evidence/{id}:lifecycle                    expire, suspend, supersede or withdraw; names the claims that rest on it (trust.evidence.manage)

Records carry ids, codes and hashes. A reviewer's role is the capability they hold, never a field in a request."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import CAPABILITIES, require_operator
from zid_platform.modules.trust_center.application import service as svc
from zid_platform.modules.trust_center.infrastructure import store
from zid_platform.operator_actions import Actions

router = APIRouter(tags=["trust-center"])
OperatorDep = Annotated[Any, Depends(require_operator)]
P = "/v1/trust-center"

AUTHOR = "trust.claim.author"
EVIDENCE = "trust.evidence.manage"
PUBLISH = "trust.claim.publish"
WITHDRAW = "trust.claim.withdraw"
READERS = tuple(sorted(n for n in CAPABILITIES if n.startswith("trust.")))
Role = Literal["product", "legal", "security", "privacy", "sre", "finance", "marketing"]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Rev = Annotated[int, Path(ge=1, le=100000)]

actions = Actions(
    namespace=uuid.UUID("9b2f6c14-3d7a-4e58-8a10-5c4e1f7b2d93"),
    refused_event="trust.claim.refused",
    status={
        "CLAIM_NOT_FOUND": 404, "SNAPSHOT_NOT_OPEN": 404, "ADVISORY_NOT_FOUND": 404, "REPORT_NOT_FOUND": 404, "INCIDENT_NOT_FOUND": 404, "EVIDENCE_NOT_FOUND": 404, "WITHDRAWAL_NOT_OPEN": 404,
        "SELF_APPROVAL": 403, "ROLE_NOT_REQUIRED": 422, "ALREADY_APPROVED": 409, "ROLE_ALREADY_COVERED": 409, "EVIDENCE_DIFFERS": 409, "REASON_REQUIRED": 422,
        "REVISION_CLOSED": 409, "REVISION_WITHDRAWN": 409, "ALREADY_PUBLISHED": 409, "ALREADY_WITHDRAWN": 409, "NOTHING_LIVE": 409, "ALREADY_CONFIRMED": 409,
        "PUBLISH_BLOCKED": 409, "ROLLBACK_BLOCKED": 409, "NO_ROLLBACK_TARGET": 409, "EVIDENCE_WITHDRAWN": 409, "SCHEME_TYPE_MISMATCH": 422, "PERIOD_INVALID": 422,
    },
    errors=lambda: (svc.TrustError,),
    refused_code="TRUST_CENTER_REFUSED",
    capability_hint="a trust capability",
)  # fmt: skip


class TrustClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exact_text: str = Field(min_length=3, max_length=2000)
    locale: str = Field(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    claim_type: Literal[
        "numerical",
        "certification",
        "third_party_assessment",
        "legal",
        "customer_endorsement",
        "sla",
        "functionality",
        "roadmap",
    ]
    entity_id: str = Field(min_length=2, max_length=160)
    service: str = Field(min_length=2, max_length=80)
    markets: list[Annotated[str, Field(pattern=r"^[A-Z]{2}$")]] = Field(min_length=1, max_length=60)
    environment: Literal["production", "sandbox"] = "production"
    channels: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")]] = Field(
        min_length=1, max_length=20
    )
    evidence_digests: list[Digest] = Field(default_factory=list, max_length=20)
    valid_until: date | None = None


class TrustApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role
    evidence_digests: list[Digest] = Field(default_factory=list, max_length=20)
    effective_until: datetime | None = None


class TrustRefuseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role
    reason: str = Field(min_length=3, max_length=1000)


class TrustPublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_hash: Digest = Field(
        description="The content hash the approvals were given for; the gate refuses a bundle whose hash differs."
    )


class TrustWithdrawIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=1000)
    revision: int | None = Field(default=None, ge=1)


class TrustNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=3, max_length=1000)


class TrustEvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest: Digest
    evidence_type: Literal[
        "report",
        "attestation",
        "certificate",
        "assessment",
        "registration",
        "self_assessment",
        "declaration",
        "contract_commitment",
        "program_listing",
    ]
    scheme: str = Field(min_length=2, max_length=80)
    issuer: str = Field(min_length=2, max_length=160)
    assessor: str = Field(default="", max_length=160)
    legal_entity: str = Field(min_length=2, max_length=160)
    products: list[str] = Field(min_length=1, max_length=40)
    regions: list[Annotated[str, Field(pattern=r"^[A-Z]{2}$")]] = Field(
        default_factory=list, max_length=60
    )
    exclusions: list[str] = Field(default_factory=list, max_length=40)
    period_start: date
    period_end: date
    holder: str = Field(min_length=2, max_length=160)
    restricted: bool = False


class TrustLifecycleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lifecycle: Literal["SUPERSEDED", "EXPIRED", "SUSPENDED", "WITHDRAWN", "ACTIVE"]


async def _read[T](request: Request, op: Any, resource: str, fn: Any) -> T:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, f"regulatory:trust-{resource}")
        try:
            return await fn(conn)  # type: ignore[no-any-return]
        except svc.TrustError as e:
            raise actions.fail(e) from e


def _iso(d: Any) -> Any:
    return d.isoformat() if hasattr(d, "isoformat") else d


@router.get(f"{P}/claims")
async def claims(
    request: Request,
    op: OperatorDep,
    status: str | None = Query(default=None, pattern=r"^[A-Z_]{4,30}$"),
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        rows = await store.list_claims(conn, status=status)
        return {
            "items": [
                {
                    **r,
                    "claim_id": str(r["claim_id"]),
                    "created_at": _iso(r["created_at"]),
                    "valid_until": _iso(r["valid_until"]),
                }
                for r in rows
            ]
        }

    return await _read(request, op, "claims", go)


@router.get(P + "/claims/{claim_id}/revisions/{revision}")
async def one_revision(
    claim_id: uuid.UUID, revision: Rev, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _read(
        request,
        op,
        str(claim_id),
        lambda conn: svc.detail(conn, str(claim_id), revision, now=datetime.now(UTC)),
    )


@router.post(f"{P}/claims", status_code=201)
async def draft_claim(body: TrustClaimIn, request: Request, op: OperatorDep) -> dict[str, Any]:
    claim_id = str(uuid.uuid4())

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.draft(
            conn, actor=str(op.principal_id), claim_id=claim_id, data=body.model_dump()
        )
        return (
            out,
            claim_id,
            {"revision": 1, "hash": out["content_hash"], "type": out["claim_type"]},
        )

    return await actions.act(
        request,
        op,
        event="trust.claim.drafted",
        action="draft",
        capability=AUTHOR,
        target_type="trust_claim",
        target=claim_id,
        work=work,
    )


@router.post(P + "/claims/{claim_id}/revisions", status_code=201)
async def new_revision(
    claim_id: uuid.UUID, body: TrustClaimIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.draft(
            conn, actor=str(op.principal_id), claim_id=str(claim_id), data=body.model_dump()
        )
        return out, str(claim_id), {"revision": out["revision"], "hash": out["content_hash"]}

    return await actions.act(
        request,
        op,
        event="trust.claim.revised",
        action="revise",
        capability=AUTHOR,
        target_type="trust_claim",
        target=str(claim_id),
        work=work,
    )


@router.post(P + "/claims/{claim_id}/revisions/{revision}:approve")
async def approve(
    claim_id: uuid.UUID, revision: Rev, body: TrustApproveIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.review(
            conn,
            actor=str(op.principal_id),
            claim_id=str(claim_id),
            revision=revision,
            role=body.role,
            approve=True,
            reason="",
            evidence_digests=body.evidence_digests,
            now=now,
            effective_until=body.effective_until,
        )
        return out, str(claim_id), {"revision": revision, "hash": out["content_hash"]}

    return await actions.act(
        request,
        op,
        event="trust.claim.approved",
        action="approve",
        capability=f"trust.claim.review_{body.role}",
        target_type="trust_claim",
        target=str(claim_id),
        work=work,
        role=body.role,
    )


@router.post(P + "/claims/{claim_id}/revisions/{revision}:refuse")
async def refuse(
    claim_id: uuid.UUID, revision: Rev, body: TrustRefuseIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.review(
            conn,
            actor=str(op.principal_id),
            claim_id=str(claim_id),
            revision=revision,
            role=body.role,
            approve=False,
            reason=body.reason,
            evidence_digests=(),
            now=now,
        )
        return out, str(claim_id), {"revision": revision, "hash": out["content_hash"]}

    return await actions.act(
        request,
        op,
        event="trust.claim.refused_by_reviewer",
        action="refuse",
        capability=f"trust.claim.review_{body.role}",
        target_type="trust_claim",
        target=str(claim_id),
        work=work,
        role=body.role,
    )


@router.post(P + "/claims/{claim_id}/revisions/{revision}:publish")
async def publish(
    claim_id: uuid.UUID, revision: Rev, body: TrustPublishIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.publish(
            conn,
            actor=str(op.principal_id),
            claim_id=str(claim_id),
            revision=revision,
            expected_hash=body.expected_hash,
            now=now,
        )
        return (
            out,
            str(claim_id),
            {
                "revision": revision,
                "hash": out["content_hash"],
                "manifest": out["manifest_digest"],
                "channels": out["channels"],
                "locale": out["locale"],
            },
        )

    return await actions.act(
        request,
        op,
        event="trust.claim.published",
        action="publish",
        capability=PUBLISH,
        target_type="trust_claim",
        target=str(claim_id),
        work=work,
        publish="trust.claim.published",
    )


@router.post(P + "/claims/{claim_id}:rollback")
async def rollback(claim_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.rollback(conn, actor=str(op.principal_id), claim_id=str(claim_id), now=now)
        return (
            out,
            str(claim_id),
            {
                "revision": out["revision"],
                "rolled_back_from": out["rolled_back_from"],
                "hash": out["content_hash"],
                "manifest": out["manifest_digest"],
                "channels": out["channels"],
                "locale": out["locale"],
            },
        )

    return await actions.act(
        request,
        op,
        event="trust.claim.rolled_back",
        action="rollback",
        capability=PUBLISH,
        target_type="trust_claim",
        target=str(claim_id),
        work=work,
        publish="trust.claim.published",
    )


@router.post(P + "/claims/{claim_id}:withdraw")
async def withdraw(
    claim_id: uuid.UUID, body: TrustWithdrawIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.withdraw(
            conn,
            actor=str(op.principal_id),
            claim_id=str(claim_id),
            revision=body.revision,
            reason=body.reason,
            now=now,
            review_hours=int(getattr(request.app.state, "trust_withdrawal_review_hours", 72)),
        )
        return (
            out,
            str(claim_id),
            {
                "revision": out["revision"],
                "channels": out["channels"],
                "review_due": out["review_due"],
                "withdrawal": out["withdrawal_id"],
            },
        )

    return await actions.act(
        request,
        op,
        event="trust.claim.withdrawn",
        action="withdraw",
        capability=WITHDRAW,
        target_type="trust_claim",
        target=str(claim_id),
        work=work,
        publish="trust.claim.withdrawn",
    )


@router.get(f"{P}/withdrawals")
async def withdrawals(request: Request, op: OperatorDep, open: bool = False) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        rows = await store.withdrawals(conn, open_only=open)
        return {
            "items": [
                {k: (str(v) if isinstance(v, uuid.UUID) else _iso(v)) for k, v in r.items()}
                for r in rows
            ]
        }

    return await _read(request, op, "withdrawals", go)


@router.post(P + "/withdrawals/{withdrawal_id}:review")
async def review_withdrawal(
    withdrawal_id: uuid.UUID, body: TrustNoteIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.review_withdrawal(
            conn,
            actor=str(op.principal_id),
            withdrawal_id=str(withdrawal_id),
            note=body.note,
            now=now,
        )
        return out, str(withdrawal_id), {}

    return await actions.act(
        request,
        op,
        event="trust.withdrawal.reviewed",
        action="review",
        capability=PUBLISH,
        target_type="trust_withdrawal",
        target=str(withdrawal_id),
        work=work,
    )


@router.get(f"{P}/evidence")
async def list_evidence(request: Request, op: OperatorDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        return {"items": [svc.evidence_view(a) for a in await store.list_evidence(conn)]}

    return await _read(request, op, "evidence", go)


@router.post(f"{P}/evidence", status_code=201)
async def record_evidence(
    body: TrustEvidenceIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.record_evidence(conn, actor=str(op.principal_id), data=body.model_dump())
        return (
            out,
            out["evidence_id"],
            {"digest": out["digest"], "scheme": out["scheme"], "type": out["evidence_type"]},
        )

    return await actions.act(
        request,
        op,
        event="trust.evidence.recorded",
        action="record",
        capability=EVIDENCE,
        target_type="trust_evidence",
        target=body.digest[:16],
        work=work,
    )


@router.post(P + "/evidence/{evidence_id}:confirm")
async def confirm_evidence(
    evidence_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.confirm_evidence(
            conn, actor=str(op.principal_id), evidence_id=str(evidence_id), now=now
        )
        return out, str(evidence_id), {"digest": out["digest"]}

    return await actions.act(
        request,
        op,
        event="trust.evidence.confirmed",
        action="confirm",
        capability=EVIDENCE,
        target_type="trust_evidence",
        target=str(evidence_id),
        work=work,
    )


@router.post(P + "/evidence/{evidence_id}:lifecycle")
async def evidence_lifecycle(
    evidence_id: uuid.UUID, body: TrustLifecycleIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out, affected = await svc.change_lifecycle(
            conn, evidence_id=str(evidence_id), lifecycle=body.lifecycle
        )
        return (
            {**out, "claims_resting_on_it": affected},
            str(evidence_id),
            {"lifecycle": body.lifecycle, "claims": len(affected)},
        )

    return await actions.act(
        request,
        op,
        event="trust.evidence.lifecycle_changed",
        action="lifecycle",
        capability=EVIDENCE,
        target_type="trust_evidence",
        target=str(evidence_id),
        work=work,
        publish="trust.evidence.lifecycle_changed",
    )


# ------------------------------------------------------------------------------------------------------------------------------ availability, advisories, reports


class TrustSnapshotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,62}$")
    region: str = Field(pattern=r"^[a-z0-9-]{2,24}$")
    window_label: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}(-[0-9]{2})?$")
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)
    exclusions: str = Field(default="", max_length=500)
    source_ref: str = Field(min_length=3, max_length=300)
    raw_good: int | None = Field(
        default=None,
        ge=0,
        description="The count read straight from telemetry, to prove the figure reproduces.",
    )
    raw_total: int | None = Field(default=None, gt=0)


class TrustAdvisoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_ref: uuid.UUID
    kind: Literal["PUBLISH", "UPDATE", "CORRECTION", "RESOLVE", "POSTMORTEM"] = "PUBLISH"
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=3, max_length=8000)
    reason: str = Field(default="", max_length=1000)


class TrustTriageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: (
        Literal["ACKNOWLEDGED", "TRIAGED", "FIXING", "FIXED", "DISCLOSED", "CLOSED", "OUT_OF_SCOPE"]
        | None
    ) = None
    assignee: uuid.UUID | None = None
    severity: Literal["critical", "high", "medium", "low", "info"] | None = None
    note: str = Field(default="", max_length=2000)


TRIAGE = "trust.report.triage"


@router.get(f"{P}/availability/snapshots")
async def list_snapshots(request: Request, op: OperatorDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        return {
            "items": [
                svc._snapshot_view(r) for r in await store.snapshots(conn, validated_only=False)
            ]
        }

    return await _read(request, op, "availability", go)


@router.post(f"{P}/availability/snapshots", status_code=201)
async def record_snapshot(
    body: TrustSnapshotIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.record_snapshot(conn, actor=str(op.principal_id), data=body.model_dump())
        return (
            out,
            out["snapshot_id"],
            {
                "service": out["service"],
                "region": out["region"],
                "window": out["window"],
                "state": out["state"],
            },
        )

    return await actions.act(
        request,
        op,
        event="trust.availability.recorded",
        action="record",
        capability="trust.status.record",
        target_type="trust_sli",
        target=f"{body.service}-{body.region}-{body.window_label}",
        work=work,
    )


@router.post(P + "/availability/snapshots/{snapshot_id}:validate")
async def validate_snapshot(
    snapshot_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.validate_snapshot(
            conn, actor=str(op.principal_id), snapshot_id=str(snapshot_id), now=now
        )
        return out, str(snapshot_id), {"service": out["service"], "window": out["window"]}

    return await actions.act(
        request,
        op,
        event="trust.availability.validated",
        action="validate",
        capability="trust.status.validate",
        target_type="trust_sli",
        target=str(snapshot_id),
        work=work,
    )


@router.get(f"{P}/advisories")
async def list_advisories(request: Request, op: OperatorDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        return {
            "items": [
                {
                    "advisory_id": a["advisory_id"],
                    "incident_ref": a["incident_ref"],
                    "versions": [
                        svc._version_view(v) | {"author": str(v["author"])} for v in a["versions"]
                    ],
                }
                for a in await store.advisories(conn, public=False)
            ]
        }

    return await _read(request, op, "advisories", go)


@router.post(f"{P}/advisories", status_code=201)
async def draft_advisory(
    body: TrustAdvisoryIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.draft_advisory(
            conn,
            actor=str(op.principal_id),
            incident_ref=str(body.incident_ref),
            kind=body.kind,
            title=body.title,
            body=body.body,
            reason=body.reason,
        )
        return out, out["advisory_id"], {"seq": out["seq"], "kind": out["kind"]}

    return await actions.act(
        request,
        op,
        event="trust.advisory.drafted",
        action="draft",
        capability="trust.advisory.draft",
        target_type="trust_advisory",
        target=str(body.incident_ref),
        work=work,
    )


@router.post(P + "/advisories/{advisory_id}/versions/{seq}:approve")
async def approve_advisory(
    advisory_id: uuid.UUID, seq: Rev, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.approve_advisory(
            conn, actor=str(op.principal_id), advisory_id=str(advisory_id), seq=seq, now=now
        )
        return out, str(advisory_id), {"seq": seq}

    return await actions.act(
        request,
        op,
        event="trust.advisory.approved",
        action="approve",
        capability="trust.advisory.approve",
        target_type="trust_advisory",
        target=str(advisory_id),
        work=work,
    )


@router.get(f"{P}/reports")
async def list_reports(request: Request, op: OperatorDep, open: bool = False) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need(conn, op, TRIAGE, "regulatory:trust-reports")
        return {
            "items": [
                svc.report_view(r, with_text=False)
                for r in await store.reports(conn, open_only=open)
            ]
        }


@router.get(P + "/reports/{report_id}")
async def one_report(report_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need(conn, op, TRIAGE, f"regulatory:trust-report-{report_id}")
        r = await store.report_detail(conn, str(report_id))
        if r is None:
            raise actions.fail(svc.TrustError("REPORT_NOT_FOUND"))
        return svc.report_view(r, with_text=True)


@router.post(P + "/reports/{report_id}:triage")
async def triage_report(
    report_id: uuid.UUID, body: TrustTriageIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.triage_report(
            conn,
            actor=str(op.principal_id),
            report_id=str(report_id),
            state=body.state,
            assignee=str(body.assignee) if body.assignee else None,
            severity=body.severity,
            note=body.note,
            now=now,
        )
        return out, str(report_id), {"state": out["state"], "severity": out["severity"]}

    return await actions.act(
        request,
        op,
        event="trust.report.triaged",
        action="triage",
        capability=TRIAGE,
        target_type="trust_report",
        target=str(report_id),
        work=work,
    )


# ------------------------------------------------------------------------------------------------------------------------------ privacy disclosures and the evidence room (staff side)


class TrustDisclosureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["subprocessor", "retention", "biometric", "transfer"]
    subject: str = Field(min_length=2, max_length=160)
    market: str = Field(pattern=r"^([A-Z]{2}|GLOBAL)$")
    payload: dict[str, Any]
    effective_from: date


class TrustBlobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_base64: str = Field(min_length=4, max_length=28_000_000)
    content_type: Literal["application/pdf", "text/plain", "text/csv", "application/json"]


class TrustDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool
    reason: str = Field(default="", max_length=1000)
    hours: int = Field(default=72, ge=1, le=720)
    max_downloads: int = Field(default=3, ge=1, le=20)


@router.get(f"{P}/privacy/disclosures")
async def list_disclosures(
    request: Request,
    op: OperatorDep,
    market: str | None = Query(default=None, pattern=r"^([A-Z]{2}|GLOBAL)$"),
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        return {
            "items": [
                svc._disclosure_view(d) | {"author": str(d["author"])}
                for d in await store.disclosures(conn, market=market, active_only=False)
            ]
        }

    return await _read(request, op, "privacy", go)


@router.get(f"{P}/privacy/region-check")
async def region_check(
    request: Request, op: OperatorDep, region: str = Query(pattern=r"^[A-Z]{2}$")
) -> dict[str, Any]:
    return await _read(request, op, "region-check", lambda conn: svc.region_check(conn, region))


@router.post(f"{P}/privacy/disclosures", status_code=201)
async def draft_disclosure(
    body: TrustDisclosureIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.draft_disclosure(
            conn,
            actor=str(op.principal_id),
            kind=body.kind,
            subject=body.subject,
            market=body.market,
            payload=body.payload,
            effective_from=body.effective_from,
        )
        return (
            out,
            out["disclosure_id"],
            {"kind": body.kind, "market": body.market, "revision": out["revision"]},
        )

    return await actions.act(
        request,
        op,
        event="trust.privacy.drafted",
        action="draft",
        capability="trust.privacy.author",
        target_type="trust_disclosure",
        target=f"{body.kind}-{body.market}",
        work=work,
    )


@router.post(P + "/privacy/disclosures/{disclosure_id}:approve")
async def approve_disclosure(
    disclosure_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.approve_disclosure(
            conn, actor=str(op.principal_id), disclosure_id=str(disclosure_id), now=now
        )
        return (
            out,
            str(disclosure_id),
            {"kind": out["kind"], "market": out["market"], "revision": out["revision"]},
        )

    return await actions.act(
        request,
        op,
        event="trust.privacy.approved",
        action="approve",
        capability="trust.privacy.approve",
        target_type="trust_disclosure",
        target=str(disclosure_id),
        work=work,
    )


@router.post(P + "/privacy/disclosures/{disclosure_id}:withdraw")
async def withdraw_disclosure(
    disclosure_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.withdraw_disclosure(conn, disclosure_id=str(disclosure_id))
        return out, str(disclosure_id), {}

    return await actions.act(
        request,
        op,
        event="trust.privacy.withdrawn",
        action="withdraw",
        capability=WITHDRAW,
        target_type="trust_disclosure",
        target=str(disclosure_id),
        work=work,
    )


@router.post(P + "/evidence/{evidence_id}/file", status_code=201)
async def upload_file(
    evidence_id: uuid.UUID, body: TrustBlobIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    import base64
    import binascii

    try:
        content = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ZidError("CONTENT_REFUSED", "The file is not valid base64.", http_status=422) from e

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.upload_blob(
            conn,
            actor=str(op.principal_id),
            evidence_id=str(evidence_id),
            content=content,
            content_type=body.content_type,
            scanner=getattr(request.app.state, "evidence_scanner", None),
        )
        return out, str(evidence_id), {"size": out["size_bytes"], "type": out["content_type"]}

    return await actions.act(
        request,
        op,
        event="trust.evidence.file_stored",
        action="store_file",
        capability=EVIDENCE,
        target_type="trust_evidence",
        target=str(evidence_id),
        work=work,
    )


@router.get(f"{P}/room/requests")
async def room_requests(request: Request, op: OperatorDep, open: bool = False) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        return {
            "items": [
                svc._request_view(r)
                | {"tenant_id": str(r["tenant_id"]), "requester": str(r["requester"])}
                for r in await store.requests(conn, tenant=None, open_only=open)
            ]
        }

    return await _read(request, op, "room", go)


@router.post(P + "/room/requests/{request_id}:decide")
async def decide_room_request(
    request_id: uuid.UUID, body: TrustDecisionIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.decide_access(
            conn,
            actor=str(op.principal_id),
            request_id=str(request_id),
            approve=body.approve,
            reason=body.reason,
            hours=body.hours,
            max_downloads=body.max_downloads,
            now=now,
        )
        return (
            out,
            str(request_id),
            {"status": out["status"], "hours": body.hours, "max_downloads": body.max_downloads},
        )

    return await actions.act(
        request,
        op,
        event="trust.room.decided",
        action="decide",
        capability="trust.room.approve",
        target_type="trust_room_request",
        target=str(request_id),
        work=work,
    )


@router.post(P + "/room/requests/{request_id}:revoke")
async def revoke_room_request(
    request_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await svc.revoke_access(
            conn, actor=str(op.principal_id), request_id=str(request_id), now=now
        )
        return out, str(request_id), {"accesses": len(out["history"])}

    return await actions.act(
        request,
        op,
        event="trust.room.revoked",
        action="revoke",
        capability="trust.room.revoke",
        target_type="trust_room_request",
        target=str(request_id),
        work=work,
    )


# ------------------------------------------------------------------------------------------------------------------------------ monitoring


class TrustPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=4, max_length=500)
    content: str = Field(min_length=1, max_length=2_000_000)


class TrustScanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pages: list[TrustPage] = Field(min_length=1, max_length=200)


@router.get(f"{P}/monitoring")
async def monitoring(request: Request, op: OperatorDep) -> dict[str, Any]:
    return await _read(
        request, op, "monitoring", lambda conn: svc.monitoring_report(conn, now=datetime.now(UTC))
    )


@router.post(f"{P}/scans")
async def scan(body: TrustScanIn, request: Request, op: OperatorDep) -> dict[str, Any]:
    return await _read(
        request, op, "scan", lambda conn: svc.scan_pages(conn, [p.model_dump() for p in body.pages])
    )

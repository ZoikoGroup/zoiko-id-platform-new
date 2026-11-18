"""Official sanctions source releases, operator plane (ZID-DTV-WF-06 S10; CR-113).

    GET  /v1/screening/sources                                    every official source: the corpus in use, how fresh it is, whether a download address is on record   (any screening capability)
    GET  /v1/screening/sources/{source_id}/releases               the releases of one source, newest first                                                              (any screening capability)
    GET  /v1/screening/releases/{release_id}                      one release: receipt, validation, approvals                                                           (any screening capability)
    GET  /v1/screening/releases/{release_id}/comparison           what it would change against the corpus in use: counts and source references, never names           (any screening capability)
    POST /v1/screening/sources/{source_id}/releases:fetch         fetch the official file and stage it, inactive                                                        (screening.source.stage)
    POST /v1/screening/releases/{release_id}:approve              approve a staged release, acknowledging each anomaly by name                                          (screening.source.approve)
    POST /v1/screening/releases/{release_id}:publish              make an approved release the corpus screening reads                                                   (screening.source.publish)
    POST /v1/screening/releases/{release_id}:approve-rollback     approve reactivating an earlier release                                                               (screening.source.approve)
    POST /v1/screening/releases/{release_id}:rollback             reactivate it once two others approved                                                                (screening.source.publish)

There is no upload route: a corpus enters only by being fetched from the publisher's recorded address, so an unsigned file cannot be put in by hand. The corpus in use never changes until two
people other than the stager have approved a validated release. The change and its protected audit record are one transaction, a refusal is audited afterwards, and records carry ids, codes and
hashes, never names.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import CAPABILITIES, require_operator
from zid_platform.operator_actions import Actions


class _Screening:
    """The screening module's public interface, imported on first use (avoids a dependency cycle at import time)."""

    def __getattr__(self, name: str) -> Any:
        import zid_platform.modules.screening.ports as ports  # noqa: PLR0402

        return getattr(ports, name)


sc = _Screening()
READERS = tuple(sorted(n for n in CAPABILITIES if n.startswith("screening.")))
SOURCES = ("OFAC_SDN", "OFAC_NON_SDN", "UK_SANCTIONS", "EU_CONSOLIDATED", "UN_SC")
SourcePath = Annotated[
    str, Path(pattern=r"^(OFAC_SDN|OFAC_NON_SDN|UK_SANCTIONS|EU_CONSOLIDATED|UN_SC)$")
]
ReleasePath = Annotated[uuid.UUID, Path()]

router = APIRouter(tags=["screening-sources"])
OperatorDep = Annotated[Any, Depends(require_operator)]

STAGE = "screening.source.stage"
APPROVE = "screening.source.approve"
PUBLISH = "screening.source.publish"

actions = Actions(
    namespace=uuid.UUID("4c1d8e72-6b39-4a05-9f2e-7d3a0b5c8e41"),
    refused_event="screening.source.refused",
    status={
        "RELEASE_NOT_FOUND": 404,
        "RELEASE_NOT_APPROVABLE": 409,
        "RELEASE_NOT_PUBLISHABLE": 409,
        "RELEASE_HAS_NO_CORPUS": 409,
        "ROLLBACK_TARGET_INVALID": 409,
        "APPROVER_STAGED_THE_RELEASE": 403,
        "APPROVER_ALREADY_APPROVED": 409,
        "FLAGS_NOT_ACKNOWLEDGED": 409,
        "TWO_APPROVERS_REQUIRED": 409,
        "SOURCE_NOT_REGISTERED": 422,
        "NO_ADDRESS": 409,
        "FETCH_FAILED": 503,
        "FETCH_BLOCKED": 503,
        "FETCH_REDIRECT_REFUSED": 503,
        "FETCH_TOO_LARGE": 503,
    },
    errors=lambda: (sc.ScreeningError, sc.FetchError),
    refused_code="SCREENING_SOURCE_REFUSED",
    capability_hint="a screening capability",
)


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledged: list[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]] = Field(
        default_factory=list, max_length=20
    )


def _list_id(source_id: str) -> Any:
    return sc.ListId(source_id)


@router.get("/v1/screening/sources")
async def sources(request: Request, op: OperatorDep) -> dict[str, Any]:
    cfg = getattr(request.app.state, "screening_config", None) or sc.screening_config()
    now = datetime.now(UTC)
    items: list[dict[str, Any]] = []
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, "regulatory:screening-sources")
        for source_id in SOURCES:
            list_id = _list_id(source_id)
            source = cfg.sources.get(list_id)
            health = await sc.source_releases.health(
                conn, list_id, now=now, max_age=cfg.policy.list_max_age
            )
            history = await sc.source_releases.history(conn, list_id)
            items.append(
                {
                    "source_id": source_id,
                    "address_on_record": bool(source and source.configured),
                    "freshness": health["freshness"],
                    "last_validated_at": health["last_validated_at"],
                    "snapshot": health["snapshot"],
                    "awaiting_approval": sum(1 for r in history if r["state"] == "VALIDATED"),
                    "quarantined": sum(1 for r in history if r["state"] == "QUARANTINED"),
                    "max_age_hours": int(cfg.policy.list_max_age.total_seconds() // 3600),
                }
            )
    return {"items": items}


@router.get("/v1/screening/sources/{source_id}/releases")
async def releases(source_id: SourcePath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, f"regulatory:{source_id}")
        return {"items": await sc.source_releases.history(conn, _list_id(source_id))}


@router.get("/v1/screening/releases/{release_id}")
async def one_release(release_id: ReleasePath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, f"regulatory:{release_id}")
        try:
            return dict(await sc.source_releases.get(conn, str(release_id)))
        except sc.ScreeningError as e:
            raise actions.fail(e) from e


@router.get("/v1/screening/releases/{release_id}/comparison")
async def comparison(release_id: ReleasePath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, f"regulatory:{release_id}")
        try:
            return dict(await sc.source_releases.compare(conn, str(release_id)))
        except sc.ScreeningError as e:
            raise actions.fail(e) from e


@router.post("/v1/screening/sources/{source_id}/releases:fetch", status_code=201)
async def fetch(source_id: SourcePath, request: Request, op: OperatorDep) -> dict[str, Any]:
    list_id = _list_id(source_id)
    cfg = getattr(request.app.state, "screening_config", None) or sc.screening_config()
    source = cfg.sources.get(list_id)
    transport = getattr(request.app.state, "screening_fetch_transport", None)

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        if source is None or not source.configured:
            raise sc.FetchError(
                "NO_ADDRESS", "no verified download address is on record for this source"
            )
        files = await sc.fetch_source(source, transport=transport)
        url = ",".join(sorted(source.urls.values()))[:500]
        staged = await sc.source_releases.stage(
            conn, list_id, files, url=url, staged_by=str(op.principal_id), now=now
        )
        return (
            staged,
            staged["release_id"],
            {
                "source_id": source_id,
                "state": staged["state"],
                "sha256": staged["receipt"]["sha256"],
                "blocked": staged["blocked"],
                "flags": staged["flags"],
            },
        )

    return await actions.act(
        request, op, event="screening.source.staged", action="stage", capability=STAGE, target_type="sanction_list_release", target=source_id, work=work,
    )  # fmt: skip


@router.post("/v1/screening/releases/{release_id}:approve")
async def approve(
    release_id: ReleasePath, body: ApproveIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await sc.source_releases.approve(
            conn,
            str(release_id),
            approver=str(op.principal_id),
            acknowledged=body.acknowledged,
            now=now,
        )
        return (
            out,
            str(release_id),
            {"acknowledged": sorted(body.acknowledged), "approvals": len(out["approvals"])},
        )

    return await actions.act(
        request, op, event="screening.source.approved", action="approve", capability=APPROVE, target_type="sanction_list_release", target=str(release_id), work=work,
    )  # fmt: skip


@router.post("/v1/screening/releases/{release_id}:publish")
async def publish(release_id: ReleasePath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await sc.source_releases.publish(conn, str(release_id), now=now)
        return (
            out,
            str(release_id),
            {"source_id": out["source_id"], "sha256": out["receipt"]["sha256"]},
        )

    return await actions.act(
        request, op, event="screening.source.published", action="publish", capability=PUBLISH, target_type="sanction_list_release", target=str(release_id), work=work,
    )  # fmt: skip


@router.post("/v1/screening/releases/{release_id}:approve-rollback")
async def approve_rollback(
    release_id: ReleasePath, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await sc.source_releases.approve_rollback(
            conn, str(release_id), approver=str(op.principal_id), now=now
        )
        return out, str(release_id), {"source_id": out["source_id"]}

    return await actions.act(
        request, op, event="screening.source.rollback_approved", action="approve_rollback", capability=APPROVE, target_type="sanction_list_release", target=str(release_id), work=work,
    )  # fmt: skip


@router.post("/v1/screening/releases/{release_id}:rollback")
async def rollback(release_id: ReleasePath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await sc.source_releases.rollback(conn, str(release_id), now=now)
        return (
            out,
            str(release_id),
            {"source_id": out["source_id"], "sha256": out["receipt"]["sha256"]},
        )

    return await actions.act(
        request, op, event="screening.source.rolled_back", action="rollback", capability=PUBLISH, target_type="sanction_list_release", target=str(release_id), work=work,
    )  # fmt: skip


__all__ = ["router"]
_ = ZidError  # the error type the shared wrapper raises on a refusal

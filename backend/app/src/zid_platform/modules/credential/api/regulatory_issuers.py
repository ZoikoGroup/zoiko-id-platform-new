"""Issuer and accreditation history, operator plane (ZID-RCG-003 RCG-G04; CR-075).

    POST /v1/regulatory/issuers                                  a dated statement about one issuer (regulatory.issuer.record)
    GET  /v1/regulatory/issuers                                  the issuers with a recorded history     (any regulatory capability)
    GET  /v1/regulatory/issuers/{issuer_id}/statements           that issuer's statements, oldest first
    GET  /v1/regulatory/issuers/{issuer_id}/award-check          issuer at award time: what was known at `known_at`, judged for an award date

Each statement is a full issuer record (names with dates, accreditations with dates, authority, claim types, countries) plus the official source it
came from, the hash of what the source returned and where that is kept. Statements are append-only. Going forward is an UPDATE; anything that would
change what an earlier statement said covered a past date (an accreditation removed or shortened, a name removed, a revocation moved or backdated)
is refused unless it is recorded as a CORRECTION with a reason, and both statements stay visible.

Operator plane like the other regulatory routes: the operator gate first, one capability, the change and its protected audit record in one
transaction (regulatory.issuer.recorded), a refusal recorded as regulatory.issuer.refused. The wrapper is `zid_platform.operator_actions`. Records
hold ids, codes, the source hash and the kinds of change, never the issuer's record, a program or an accrediting body.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.credential.domain import issuer_history as ih
from zid_platform.modules.credential.infrastructure import issuer_history_store as store
from zid_platform.modules.operator_access.ports import CAPABILITIES, require_operator
from zid_platform.operator_actions import Actions

RECORD_ISSUER = "regulatory.issuer.record"
# anyone holding any regulatory capability may read the history
READERS = tuple(sorted(n for n in CAPABILITIES if n.startswith("regulatory.")))

router = APIRouter(tags=["regulatory"])
OperatorDep = Annotated[Any, Depends(require_operator)]
IssuerPath = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")]

actions = Actions(
    namespace=uuid.UUID("c41d8a76-2b39-4e05-9f1c-6d7a0e53b8f2"),
    refused_event="regulatory.issuer.refused",
    status={"ISSUER_OUT_OF_ORDER": 409},
    errors=lambda: (ih.IssuerHistoryError,),
    refused_code="ISSUER_REFUSED",
    capability_hint="a regulatory capability",
)


class NameIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str = Field(min_length=1, max_length=300)
    from_: date | None = Field(default=None, alias="from")
    until: date | None = None


class AccreditationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    body: str = Field(min_length=1, max_length=128)
    program: str | None = Field(default=None, max_length=300)  # None = the whole institution
    from_: date = Field(alias="from")
    until: date | None = None


class IssuerStatementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    issuer_kind: Literal["UNIVERSITY", "REGULATOR", "EMPLOYER", "INSURER", "VENDOR", "ACCREDITOR"]
    authority: Literal["OFFICIAL", "UNRECORDED"]
    countries: list[Annotated[str, Field(pattern=r"^[A-Z]{2}$")]] = Field(
        default_factory=list, max_length=100
    )
    claim_types: list[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]] = Field(
        default_factory=list, max_length=50
    )
    names: list[NameIn] = Field(min_length=1, max_length=50)
    accreditations: list[AccreditationIn] = Field(default_factory=list, max_length=200)
    revoked_on: date | None = None
    successor_of: str | None = Field(default=None, max_length=128)
    # the statement itself
    statement: Literal["UPDATE", "CORRECTION"] = "UPDATE"
    reason: str | None = Field(default=None, max_length=500)
    source_url: str = Field(pattern=r"^https://\S{3,480}$")
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_locator: str = Field(min_length=3, max_length=256)

    def record(self) -> dict[str, Any]:
        """The issuer row, in the shape config/credential-trust/issuer-registry.yaml and the trust graph use."""
        row: dict[str, Any] = {
            "issuer_id": self.issuer_id,
            "kind": self.issuer_kind,
            "authority": self.authority,
            "countries": list(self.countries),
            "claim_types": list(self.claim_types),
            "names": [
                {
                    k: v
                    for k, v in {"name": n.name, "from": n.from_, "until": n.until}.items()
                    if v is not None
                }
                for n in self.names
            ],
            "accreditations": [
                {
                    k: v
                    for k, v in {
                        "body": a.body,
                        "program": a.program,
                        "from": a.from_,
                        "until": a.until,
                    }.items()
                    if v is not None
                }
                for a in self.accreditations
            ],
        }
        if self.revoked_on is not None:
            row["revoked_on"] = self.revoked_on.isoformat()
        if self.successor_of:
            row["successor_of"] = self.successor_of
        return row


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _view(s: ih.Statement) -> dict[str, Any]:
    return {
        "issuer_id": s.issuer_id,
        "seq": s.seq,
        "statement_id": s.statement_id,
        "statement": s.kind.value,
        "reason": s.reason,
        "record": dict(s.record),
        "source_url": s.source_url,
        "source_hash": s.source_hash,
        "evidence_locator": s.evidence_locator,
        "recorded_by": s.recorded_by,
        "recorded_at": _iso(s.recorded_at),
    }


@router.post("/v1/regulatory/issuers", status_code=201)
async def record_issuer_statement(
    body: IssuerStatementIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        previous = await store.history(conn, body.issuer_id)
        statement = ih.Statement(
            statement_id=f"iss-{body.issuer_id}-{len(previous) + 1}",
            issuer_id=body.issuer_id,
            seq=len(previous) + 1,
            kind=ih.Kind(body.statement),
            reason=body.reason,
            record=body.record(),
            source_url=body.source_url,
            source_hash=body.source_hash,
            evidence_locator=body.evidence_locator,
            recorded_by=str(op.principal_id),
            recorded_at=now,
        )
        saved, changes = await store.record(conn, statement, now=now)
        details: dict[str, Any] = {
            "statement": saved.kind.value,
            "seq": saved.seq,
            "source_hash": saved.source_hash,
        }
        if changes:  # the kinds of change only: the labels name programs and bodies, which belong to the record, not the audit trail
            details["retroactive_kinds"] = sorted({ch.split(":", 1)[0] for ch in changes})
        return (
            _view(saved) | {"retroactive_changes": changes},
            f"{saved.issuer_id}:s{saved.seq}",
            details,
        )

    return await actions.act(
        request,
        op,
        event="regulatory.issuer.recorded",
        publish="credential.issuer_status_changed",
        action="record",
        capability=RECORD_ISSUER,
        target_type="issuer_statement",
        target=body.issuer_id,
        work=work,
    )


@router.get("/v1/regulatory/issuers")
async def list_issuers(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, "regulatory:issuers")
        return {"items": await store.issuer_ids(conn)}


@router.get("/v1/regulatory/issuers/{issuer_id}/statements")
async def issuer_statements(
    issuer_id: IssuerPath, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, f"regulatory:{issuer_id}")
        history = await store.history(conn, issuer_id)
        if not history:
            raise ZidError(
                "ISSUER_NOT_FOUND",
                "No statement has been recorded for this issuer.",
                http_status=404,
            )
        return {"items": [_view(s) for s in history]}


@router.get("/v1/regulatory/issuers/{issuer_id}/award-check")
async def award_check(
    issuer_id: IssuerPath,
    request: Request,
    op: OperatorDep,
    program: Annotated[str, Query(min_length=1, max_length=300)],
    awarded_on: Annotated[date, Query()],
    known_at: Annotated[AwareDatetime | None, Query()] = None,
    claim_type: Annotated[str | None, Query(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")] = None,
) -> dict[str, Any]:
    """Issuer at award time: judged for an award on `awarded_on`, with what the ledger knew at `known_at` (default now). A preview for operators."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, f"regulatory:{issuer_id}")
        result: dict[str, Any] = ih.award_check(
            await store.history(conn, issuer_id),
            program=program,
            awarded_on=awarded_on,
            known_at=known_at or datetime.now(UTC),
            claim_type=claim_type,
        )
        return result

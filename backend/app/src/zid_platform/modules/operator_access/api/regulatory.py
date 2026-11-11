"""Regulatory policy packs, operator plane (ZID-RCG-003 RCG-G01, WF-02; CR-067).

    GET  /v1/regulatory/policy-packs                                        versions, newest first         (any regulatory capability)
    GET  /v1/regulatory/policy-packs/{pack_id}/versions/{version}           one version with its approvals (any regulatory capability)
    GET  /v1/regulatory/resolution                                          which approved pack governs a request, or why none
    POST /v1/regulatory/policy-packs                                        a new DRAFT                    (regulatory.pack.author)
    POST /v1/regulatory/policy-packs/{pack_id}:revise                       a new DRAFT version of a published pack (regulatory.pack.author)
    POST /v1/regulatory/policy-packs/{pack_id}/versions/{version}:approve   sign the exact hash in one role (regulatory.pack.approve_<role>)
    POST /v1/regulatory/policy-packs/{pack_id}/versions/{version}:publish   DRAFT to APPROVED, four approvals needed (regulatory.pack.publish)
    POST /v1/regulatory/policy-packs/{pack_id}/versions/{version}:retire    APPROVED to RETIRED; the last approved version governs again

Packs are platform configuration, never tenant data, so they are operator-plane routes: a customer tenant's roles grant nothing here. The
operator gate (operator client, operator tenant, fresh strong sign-in, no delegation) comes first, then one operator capability per action.
The capabilities form a separation-of-duties set: whoever authors a pack signs none of it, and each approver holds one role. The domain and
the tables repeat those rules, so a caller that skips this API still cannot get around them. No audit event is emitted yet: an event must
be declared in the event catalogue first.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import has_capability, require_operator


class _Verification:
    """The verification module's public interface, imported on first use. verification reaches operator_access through tenant and
    resilience, so importing it when this file loads would close a dependency cycle (test_module_dependency_cycles)."""

    def __getattr__(self, name: str) -> Any:
        # The architecture guard wants the `.ports` module named in the import; ruff would rewrite it to the other form.
        import zid_platform.modules.verification.ports as ports  # noqa: PLR0402

        return getattr(ports, name)


v = _Verification()
# The four approver roles, as in verification's domain; a unit test keeps the two lists equal.
APPROVER_ROLES = ("legal", "grc", "security", "business_owner")

router = APIRouter(tags=["regulatory"])
OperatorDep = Annotated[Any, Depends(require_operator)]

AUTHOR = "regulatory.pack.author"
PUBLISH = "regulatory.pack.publish"
APPROVE = {role: f"regulatory.pack.approve_{role}" for role in APPROVER_ROLES}
ALL_CAPABILITIES = (AUTHOR, PUBLISH, *APPROVE.values())

PACK_ID = r"^[a-z0-9][a-z0-9-]{0,127}$"
PackIdPath = Annotated[str, Path(pattern=PACK_ID)]
VersionPath = Annotated[int, Path(ge=1, le=100_000)]

_STATUS = {
    "PACK_NOT_FOUND": 404,
    "PACK_EXISTS": 409,
    "PACK_DRAFT_OPEN": 409,
    "PACK_NO_CHANGE": 409,
    "PACK_NOT_DRAFT": 409,
    "PACK_NOT_APPROVED": 409,
    "PACK_NOT_PUBLISHABLE": 409,
    "PACK_APPROVAL_HASH_MISMATCH": 409,
    "PACK_APPROVER_IS_AUTHOR": 403,
    "PACK_APPROVER_NOT_DISTINCT": 403,
    "PACK_APPROVER_ROLE_UNKNOWN": 422,
    "PACK_STATUS_UNKNOWN": 422,
    "PACK_STORED_HASH_MISMATCH": 500,  # a stored pack no longer matches its hash: never served, never skipped
}


def _fail(e: Any) -> ZidError:
    text = str(e)
    detail = text.split(": ", 1)[1] if ": " in text else ""
    return ZidError(
        e.code,
        detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 422),
    )


async def _need(conn: Any, op: Any, capability: str, resource: str) -> None:
    if not await has_capability(
        conn, str(op.principal_id), capability, None, resource, datetime.now(UTC)
    ):
        raise ZidError(
            "OPERATOR_CAPABILITY_REQUIRED",
            f"This needs an approved, unexpired {capability} grant.",
            http_status=403,
        )


async def _need_any(conn: Any, op: Any, resource: str) -> None:
    for capability in ALL_CAPABILITIES:
        if await has_capability(
            conn, str(op.principal_id), capability, None, resource, datetime.now(UTC)
        ):
            return
    raise ZidError(
        "OPERATOR_CAPABILITY_REQUIRED",
        "This needs a regulatory policy pack capability.",
        http_status=403,
    )


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _view(p: Any) -> dict[str, Any]:
    return {
        "pack_id": p.pack_id,
        "version": p.version,
        "status": p.status.value,
        "market": {
            "country": p.key.country,
            "subdivision": p.key.subdivision,
            "profession": p.key.profession,
            "product": p.key.product,
            "purpose": p.key.purpose,
        },
        "sources": [
            {
                "instrument": s.instrument,
                "url": s.url,
                "status": s.status.value,
                "retrieved_at": _iso(s.retrieved_at),
                "effective_from": _iso(s.effective_from),
                "effective_to": _iso(s.effective_to),
                "owner": s.owner,
            }
            for s in p.sources
        ],
        "rules": {
            "evidence_minimum": list(p.rules.evidence_minimum),
            "alternate_routes": list(p.rules.alternate_routes),
            "lawful_basis": p.rules.lawful_basis,
            "validity_days": p.rules.validity_days,
            "escalation": p.rules.escalation,
        },
        "author": p.author,
        "pack_hash": v.pack_hash(p),
        "approvals": {
            r: {"approver": a.approver, "pack_hash": a.pack_hash} for r, a in p.approvals.items()
        },
        "missing_approvals": [r for r in APPROVER_ROLES if r not in p.approvals],
    }


# ------------------------------------------------------------------------------------ bodies


class PackSourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument: str = Field(min_length=1, max_length=300)
    url: str = Field(pattern=r"^https://\S{3,500}$")
    status: Literal["LAW", "GUIDANCE", "PROPOSAL"]
    retrieved_at: AwareDatetime
    effective_from: AwareDatetime
    effective_to: AwareDatetime | None = None
    owner: str = Field(min_length=1, max_length=128)

    def domain(self) -> Any:
        return v.LegalSource(
            instrument=self.instrument,
            url=self.url,
            status=v.SourceStatus(self.status),
            retrieved_at=self.retrieved_at,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
            owner=self.owner,
        )


class PackRulesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_minimum: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        min_length=1, max_length=30
    )
    alternate_routes: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=30
    )
    lawful_basis: str = Field(default="", max_length=200)
    validity_days: int | None = Field(default=None, ge=1, le=3650)
    escalation: str | None = Field(default=None, max_length=200)

    def domain(self) -> Any:
        return v.Rules(
            evidence_minimum=tuple(self.evidence_minimum),
            alternate_routes=tuple(self.alternate_routes),
            lawful_basis=self.lawful_basis,
            validity_days=self.validity_days,
            escalation=self.escalation,
        )


class PackDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pack_id: str = Field(pattern=PACK_ID)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    subdivision: str | None = Field(default=None, pattern=r"^[A-Z]{2}-[A-Z0-9]{1,3}$")
    profession: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    product: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    purpose: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    sources: list[PackSourceIn] = Field(max_length=20)
    rules: PackRulesIn


class PackReviseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[PackSourceIn] | None = Field(default=None, max_length=20)
    rules: PackRulesIn | None = None


class PackApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["legal", "grc", "security", "business_owner"]
    signed_hash: str = Field(pattern=r"^[0-9a-f]{64}$")  # the hash the approver was shown


# ------------------------------------------------------------------------------------ reads


@router.get("/v1/regulatory/policy-packs")
async def list_policy_packs(
    request: Request,
    op: OperatorDep,
    country: Annotated[str | None, Query(pattern=r"^[A-Z]{2}$")] = None,
    status: Annotated[Literal["DRAFT", "APPROVED", "RETIRED"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, "regulatory:policy-packs")
        try:
            packs = await v.policy_packs.list_packs(
                conn, country=country, status=status, limit=limit
            )
        except v.PackError as e:
            raise _fail(e) from e
        return {"items": [_view(p) for p in packs]}


@router.get("/v1/regulatory/policy-packs/{pack_id}/versions/{version}")
async def get_policy_pack(
    pack_id: PackIdPath, version: VersionPath, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, f"regulatory:{pack_id}")
        try:
            return _view(await v.policy_packs.get_pack(conn, pack_id, version))
        except v.PackError as e:
            raise _fail(e) from e


@router.get("/v1/regulatory/resolution")
async def resolution(
    request: Request,
    op: OperatorDep,
    country: Annotated[str, Query(pattern=r"^[A-Za-z]{2}$")],
    product: Annotated[str, Query(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")],
    purpose: Annotated[str, Query(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")],
    subdivision: Annotated[str | None, Query(pattern=r"^[A-Z]{2}-[A-Z0-9]{1,3}$")] = None,
    profession: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,63}$")] = None,
) -> dict[str, Any]:
    """Which approved pack governs this request, or why none does. A preview for operators; it is not a verification result."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need_any(conn, op, "regulatory:resolution")
        try:
            r = await v.policy_packs.governing_pack(
                conn,
                country=country,
                product=product,
                purpose=purpose,
                at=datetime.now(UTC),
                subdivision=subdivision,
                profession=profession,
            )
        except v.PackError as e:
            raise _fail(e) from e
        return {
            "outcome": r.outcome.value,
            "reason": r.reason,
            "pack": None
            if r.pack is None
            else {
                "pack_id": r.pack.pack_id,
                "version": r.pack.version,
                "pack_hash": v.pack_hash(r.pack),
            },
        }


# ------------------------------------------------------------------------------------ writes


@router.post("/v1/regulatory/policy-packs", status_code=201)
async def create_draft(body: PackDraftIn, request: Request, op: OperatorDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need(conn, op, AUTHOR, f"regulatory:{body.pack_id}")
        pack = v.PolicyPack(
            pack_id=body.pack_id,
            key=v.PackKey(
                country=body.country,
                product=body.product,
                purpose=body.purpose,
                subdivision=body.subdivision,
                profession=body.profession,
            ),
            version=1,
            status=v.PackStatus.DRAFT,
            sources=tuple(s.domain() for s in body.sources),
            rules=body.rules.domain(),
            author=str(op.principal_id),
        )
        try:
            return _view(await v.policy_packs.open_draft(conn, pack, now=now))
        except v.PackError as e:
            raise _fail(e) from e
        except DBAPIError as e:
            raise _database_refused() from e


@router.post("/v1/regulatory/policy-packs/{pack_id}:revise", status_code=201)
async def revise(
    pack_id: PackIdPath, body: PackReviseIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need(conn, op, AUTHOR, f"regulatory:{pack_id}")
        try:
            return _view(
                await v.policy_packs.revise_pack(
                    conn,
                    pack_id,
                    author=str(op.principal_id),
                    now=now,
                    sources=None
                    if body.sources is None
                    else tuple(s.domain() for s in body.sources),
                    rules=None if body.rules is None else body.rules.domain(),
                )
            )
        except v.PackError as e:
            raise _fail(e) from e
        except DBAPIError as e:
            raise _database_refused() from e


@router.post("/v1/regulatory/policy-packs/{pack_id}/versions/{version}:approve")
async def approve(
    pack_id: PackIdPath,
    version: VersionPath,
    body: PackApproveIn,
    request: Request,
    op: OperatorDep,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need(
            conn, op, APPROVE[body.role], f"regulatory:{pack_id}"
        )  # the capability IS the role: one person, one role
        try:
            return _view(
                await v.policy_packs.sign(
                    conn,
                    pack_id,
                    version,
                    role=body.role,
                    approver=str(op.principal_id),
                    signed_hash=body.signed_hash,
                    now=now,
                )
            )
        except v.PackError as e:
            raise _fail(e) from e
        except DBAPIError as e:
            raise _database_refused() from e


@router.post("/v1/regulatory/policy-packs/{pack_id}/versions/{version}:publish")
async def publish(
    pack_id: PackIdPath, version: VersionPath, request: Request, op: OperatorDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need(conn, op, PUBLISH, f"regulatory:{pack_id}")
        try:
            return _view(await v.policy_packs.publish_pack(conn, pack_id, version, now=now))
        except v.PackError as e:
            raise _fail(e) from e
        except DBAPIError as e:
            raise _database_refused() from e


@router.post("/v1/regulatory/policy-packs/{pack_id}/versions/{version}:retire")
async def retire(
    pack_id: PackIdPath, version: VersionPath, request: Request, op: OperatorDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _need(conn, op, PUBLISH, f"regulatory:{pack_id}")
        try:
            return _view(await v.policy_packs.retire_pack(conn, pack_id, version, now=now))
        except v.PackError as e:
            raise _fail(e) from e
        except DBAPIError as e:
            raise _database_refused() from e


def _database_refused() -> ZidError:
    return ZidError(
        "PACK_REFUSED", "The database refused the change; nothing was stored.", http_status=409
    )

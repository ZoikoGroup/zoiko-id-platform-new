"""Company-verification market policy packs, operator plane (ZID-DTV-WF-02 K09; CR-109).

    GET  /v1/kyb/market-policies                        every stored version: who proposed it, who has signed in which role, which one is in force   (any kyb.policy capability)
    GET  /v1/kyb/market-policies/{hash}                 one version with everything that still stops it being published, by code
    POST /v1/kyb/market-policies                        propose a version; the digest is its identity                                              (kyb.policy.author)
    POST /v1/kyb/market-policies/{hash}:approve         sign that exact digest in one role                                                         (kyb.policy.approve_<role>)
    POST /v1/kyb/market-policies/{hash}:publish         put it in force for its market, refused with every missing control named                   (kyb.policy.publish)
    POST /v1/kyb/market-policies:rollback               put an earlier version back in force, with a reason                                        (kyb.policy.publish)

Same gate and discipline as the other regulatory operator APIs: the operator gate first, one capability per action, the change and its protected audit record in one transaction, a refusal audited
afterwards. The capabilities form a separation-of-duties set (the author approves and publishes nothing, each approver signs in one role, the publisher signs nothing), and the domain and the tables
repeat the rules: the author of a version cannot approve it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import CAPABILITIES, require_operator
from zid_platform.operator_actions import Actions


class _Organization:
    """The organization module's public interface, imported on first use (avoids a dependency cycle at import time)."""

    def __getattr__(self, name: str) -> Any:
        import zid_platform.modules.organization.ports as ports  # noqa: PLR0402

        return getattr(ports, name)


org = _Organization()
READERS = tuple(sorted(n for n in CAPABILITIES if n.startswith("kyb.policy.")))
HASH = r"^sha256:[0-9a-f]{64}$"

router = APIRouter(tags=["kyb-policy"])
OperatorDep = Annotated[Any, Depends(require_operator)]
HashPath = Annotated[str, Path(pattern=HASH)]

actions = Actions(
    namespace=uuid.UUID("9d2b6f4e-3a71-4c58-8e0d-5b1f7a96c3e2"),
    refused_event="kyb.policy.refused",
    status={
        "POLICY_NOT_PUBLISHABLE": 409,
        "POLICY_VERSION_EXISTS": 409,
        "POLICY_NOT_FOUND": 404,
        "POLICY_ALREADY_ACTIVE": 409,
        "POLICY_NEVER_ACTIVE": 409,
        "POLICY_DIGEST_MISMATCH": 409,
        "SIGN_OFF_ROLE_INVALID": 422,
        "REASON_REQUIRED": 422,
        "POLICY_UNREADABLE": 422,
    },
    errors=lambda: (org.kyb_policy.PolicyError,),
    refused_code="KYB_POLICY_REFUSED",
    capability_hint="a company-verification policy capability",
)


class ProposeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pack: dict[str, Any]


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern=r"^(COMPLIANCE|LEGAL|PRIVACY|SECURITY)$")


class ActivateNowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledge: str = Field(pattern=r"^activate-without-signoff$")


class RollbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market_key: str = Field(min_length=3, max_length=200)
    to_hash: str = Field(pattern=HASH)
    reason: str = Field(min_length=10, max_length=2000)


def _operator_tenant(request: Request) -> str:
    return str(request.state.operator_ctx.tenant_id)


@router.get("/v1/kyb/market-policies")
async def list_policies(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, "regulatory:kyb-policy")
        return {"items": await org.kyb_policy.versions(conn)}


@router.get("/v1/kyb/market-policies/{policy_hash}")
async def one_policy(policy_hash: HashPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, f"regulatory:{policy_hash}")
        try:
            return dict(await org.kyb_policy.describe(conn, policy_hash))
        except org.kyb_policy.PolicyError as e:
            raise actions.fail(e) from e


@router.post("/v1/kyb/market-policies", status_code=201)
async def propose(body: ProposeIn, request: Request, op: OperatorDep) -> dict[str, Any]:
    try:
        pack = org.kyb_policy_rules.policy_from_dict(body.pack)
    except ValueError as e:
        raise ZidError(
            "POLICY_UNREADABLE", "The policy pack is not in the published form.", http_status=422
        ) from e

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        digest = await org.kyb_policy.propose(conn, pack, author=str(op.principal_id))
        problems = org.kyb_policy_rules.activation_problems(pack)
        return (
            {
                "policy_hash": digest,
                "market_key": org.kyb_policy.market_key(pack),
                "version": pack.version,
                "complete": not problems,
                "problems": problems,
            },
            digest,
            {"market_key": org.kyb_policy.market_key(pack), "version": pack.version},
        )

    return await actions.act(
        request, op, event="kyb.policy.proposed", action="propose", capability="kyb.policy.author", target_type="kyb_market_policy", target=org.kyb_policy_rules.policy_hash(pack), work=work,
    )  # fmt: skip


@router.post("/v1/kyb/market-policies/{policy_hash}:approve")
async def approve(
    policy_hash: HashPath, body: ApproveIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        await org.kyb_policy.approve(conn, policy_hash, person=str(op.principal_id), role=body.role)
        return (
            {"policy_hash": policy_hash, "role": body.role, "approved": True},
            f"{policy_hash}:{body.role}",
            {"gate": "SIGN_OFF"},
        )

    return await actions.act(
        request, op, event="kyb.policy.approved", action="approve", capability=f"kyb.policy.approve_{body.role.lower()}", target_type="kyb_market_policy", target=policy_hash, work=work, role=body.role,
    )  # fmt: skip


@router.post("/v1/kyb/market-policies/{policy_hash}:publish")
async def publish(policy_hash: HashPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        await org.kyb_policy.activate(
            conn, policy_hash, by=str(op.principal_id), tenant_id=_operator_tenant(request), now=now
        )
        return {"policy_hash": policy_hash, "in_force": True}, policy_hash, {}

    return await actions.act(
        request, op, event="kyb.policy.published", action="publish", capability="kyb.policy.publish", target_type="kyb_market_policy", target=policy_hash, work=work,
    )  # fmt: skip


@router.post("/v1/kyb/market-policies/{policy_hash}:activate-now")
async def activate_now(
    policy_hash: HashPath, body: ActivateNowIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """CR-133: put a stored pack in force without its sign-offs. The operator must send the acknowledgement; the activation note and the audit details name every control the pack lacked."""

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        skipped = await org.kyb_policy.activate(
            conn,
            policy_hash,
            by=str(op.principal_id),
            tenant_id=_operator_tenant(request),
            now=now,
            without_signoff=True,
        )
        return (
            {
                "policy_hash": policy_hash,
                "in_force": True,
                "without_signoff": True,
                "skipped": list(skipped),
            },
            policy_hash,
            {"without_signoff": True, "skipped": list(skipped)},
        )

    return await actions.act(
        request, op, event="kyb.policy.published", action="publish", capability="kyb.policy.publish", target_type="kyb_market_policy", target=policy_hash, work=work,
    )  # fmt: skip


@router.post("/v1/kyb/market-policies:rollback")
async def rollback(body: RollbackIn, request: Request, op: OperatorDep) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        await org.kyb_policy.rollback(
            conn,
            body.market_key,
            body.to_hash,
            by=str(op.principal_id),
            reason=body.reason,
            tenant_id=_operator_tenant(request),
            now=now,
        )
        return (
            {"market_key": body.market_key, "policy_hash": body.to_hash, "in_force": True},
            body.to_hash,
            {"market_key": body.market_key},
        )

    return await actions.act(
        request, op, event="kyb.policy.rolled_back", action="rollback", capability="kyb.policy.publish", target_type="kyb_market_policy", target=body.to_hash, work=work,
    )  # fmt: skip

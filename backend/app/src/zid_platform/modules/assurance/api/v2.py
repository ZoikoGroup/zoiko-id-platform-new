"""/v2/assurance: the independent assurance plane (ZID-UAT-005 section 06; CR-074 slices S2 to S5).

    POST /v2/assurance/role-assignments                       who holds which assurance role (assurance.roles.manage)
    POST /v2/assurance/baselines | GET /v2/assurance/requirements   import the case register; read cases with their derived status
    POST /v2/assurance/fixtures | .../{id}/revisions | .../{id}/review   synthetic fixtures; a second person approves; approved is locked by hash
    POST /v2/assurance/test-runs | .../{id}/events | .../{id}/cases/{case}/manifest | GET .../{id}/evidence
    POST /v2/assurance/evidence:verify                        third-party check of a manifest bundle; a failure locks the releases built on it
    POST /v2/assurance/defects
    POST /v2/assurance/release-candidates | GET .../{id}     build a candidate from evidence; the preview never shows missing evidence as ready
    POST /v2/assurance/releases/{id}/decisions | .../rollback  one role's signed decision; scoped emergency disable

Nothing here marks a case PASS because someone said so: a PASS is the verdict of a complete signed manifest from the run's own tester.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.assurance.application import provider_lab, registry_source
from zid_platform.modules.assurance.application import service as svc
from zid_platform.modules.assurance.domain import provider_watch
from zid_platform.modules.assurance.domain.rules import RuleError
from zid_platform.protected import protected_action

router = APIRouter(tags=["assurance-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
READ, RUN, FIXTURE, REVIEW, DECIDE, ROLLBACK, ROLES = (
    "assurance.read", "assurance.run.manage", "assurance.fixture.manage", "assurance.fixture.review",
    "assurance.release.decide", "assurance.release.rollback", "assurance.roles.manage",
)  # fmt: skip
SHA = r"^[0-9a-f]{64}$"
UUID = r"^[0-9a-fA-F-]{36}$"
CASE = r"^[A-Z]{2,3}-\d{2}$"
NOT_FOUND = {
    "RUN_NOT_FOUND",
    "MANIFEST_NOT_FOUND",
    "RELEASE_NOT_FOUND",
    "FIXTURE_NOT_FOUND",
    "REVISION_NOT_FOUND",
}
FORBIDDEN = {
    "ROLE_NOT_ASSIGNED_TO_THE_CALLER", "NOT_THE_RUNS_TESTER", "TESTER_ROLE_REQUIRED", "TESTER_NOT_INDEPENDENT", "REVIEWER_IS_THE_AUTHOR",
    "ROLLBACK_NEEDS_A_RELEASE_ROLE", "ROLE_SELF_ASSIGNMENT",
}  # fmt: skip
CONFLICT = {
    "IDEMPOTENCY_KEY_REUSED", "REVISION_ALREADY_REVIEWED", "ALREADY_ROLLED_BACK", "RELEASE_NOT_OPEN_FOR_DECISIONS", "ONE_PERSON_ONE_ROLE",
    "REVISION_IS_IDENTICAL", "SIGNATURE_REFUSED",
}  # fmt: skip


def _fail(e: RuleError) -> ZidError:
    status = (
        404
        if e.code in NOT_FOUND
        else 403
        if e.code in FORBIDDEN
        else 409
        if e.code in CONFLICT
        else 422
    )
    return ZidError(
        e.code, e.detail or e.code.replace("_", " ").capitalize() + ".", http_status=status
    )


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError("UNAUTHENTICATED", "A signed-in principal is required.", http_status=401)
    return caller.ctx.principal_id


def _tenant(caller: Caller) -> str:
    return caller.ctx.tenant_id or ""


async def _run[T](
    request: Request,
    caller: Caller,
    action: str,
    resource: str,
    fn: Callable[[AsyncConnection], Awaitable[T]],
) -> T:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action=action, resource=resource)
            return await fn(conn)
    except RuleError as e:
        raise _fail(e) from e
    except ValueError as e:  # a malformed id
        raise ZidError("VALIDATION_FAILED", "A reference is not valid.", http_status=422) from e


# ------------------------------------------------------------------------------------------------------------------------ roles, baseline


class UatRoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal[
        "QA_LEAD", "CTO", "SECURITY_GRC", "PRIVACY", "COUNTRY_LEGAL", "TESTER", "FIXTURE_REVIEWER"
    ]
    principal: str = Field(pattern=UUID)


@router.post("/v2/assurance/role-assignments", status_code=201)
async def assign_role(body: UatRoleIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            ROLES,
            "uat_role:new",
            lambda c: svc.assign_role(c, _tenant(caller), me, **body.model_dump()),
        ),
    }


class UatCaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=CASE)
    suite: str = Field(min_length=2, max_length=3)
    risk: Literal["PROPOSED_P0", "PROPOSED_P1", "P0", "P1", "P2"]
    dependency: Literal["OWN", "THIRD_PARTY", "PEOPLE"]
    assertion: str = Field(min_length=1, max_length=1000)
    oracle_version: str = Field(default="v1", max_length=64)


class UatBaselineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=64)
    cases: list[UatCaseIn] = Field(min_length=1, max_length=500)


@router.post("/v2/assurance/baselines", status_code=201)
async def import_baseline(
    body: UatBaselineIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)
    cases = [c.model_dump() for c in body.cases]
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            RUN,
            "uat_baseline:new",
            lambda c: svc.import_baseline(
                c, _tenant(caller), me, version=body.version, cases=cases
            ),
        ),
    }


@router.get("/v2/assurance/requirements")
async def requirements(
    request: Request, caller: CallerDep, suite: Annotated[str | None, Query(pattern=r"^[A-Z]{2,3}$")] = None,
    status: Annotated[str | None, Query(pattern=r"^[A-Z_]{3,16}$")] = None,
) -> dict[str, Any]:  # fmt: skip
    _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            READ,
            "uat_requirements:list",
            lambda c: svc.requirements(c, suite=suite, status=status),
        ),
    }


# --------------------------------------------------------------------------------------------------------------------------------- fixtures


class UatFixtureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    content_sha256: str = Field(pattern=SHA)


@router.post("/v2/assurance/fixtures", status_code=201)
async def create_fixture(body: UatFixtureIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            FIXTURE,
            "uat_fixture:new",
            lambda c: svc.create_fixture(c, _tenant(caller), me, **body.model_dump()),
        ),
    }


class UatRevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_sha256: str = Field(pattern=SHA)


@router.post("/v2/assurance/fixtures/{fixture_id}/revisions", status_code=201)
async def add_revision(
    fixture_id: str, body: UatRevisionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            FIXTURE,
            f"uat_fixture:{fixture_id}",
            lambda c: svc.add_revision(
                c, _tenant(caller), me, fixture_id=fixture_id, **body.model_dump()
            ),
        ),
    }


class UatFixtureReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    approve: bool
    rationale: str = Field(min_length=1, max_length=1000)


@router.post("/v2/assurance/fixtures/{fixture_id}/review")
async def review_fixture(
    fixture_id: str, body: UatFixtureReviewIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            REVIEW,
            f"uat_fixture:{fixture_id}",
            lambda c: svc.review_fixture(c, me, fixture_id=fixture_id, **body.model_dump()),
        ),
    }


# ------------------------------------------------------------------------------------------------------------------------ runs and evidence


class UatRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_unit: dict[str, Any] = Field(min_length=1)
    environment_id: str = Field(min_length=1, max_length=120)
    build_sha256: str = Field(pattern=SHA)
    config_digest: str = Field(pattern=SHA)
    case_ids: list[str] = Field(min_length=1, max_length=500)
    fixture_hashes: list[str] = Field(default_factory=list, max_length=200)
    commit_authors: list[str] = Field(default_factory=list, max_length=200)
    pins: dict[str, str] = Field(default_factory=dict, max_length=20)
    kind: Literal["INDEPENDENT", "DEVELOPER"] = "INDEPENDENT"


@router.post("/v2/assurance/test-runs", status_code=201)
async def create_run(body: UatRunIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            RUN,
            "uat_run:new",
            lambda c: svc.create_run(c, _tenant(caller), me, **body.model_dump()),
        ),
    }


class UatEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str | None = Field(default=None, pattern=CASE)
    kind: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=128)


@router.post("/v2/assurance/test-runs/{run_id}/events", status_code=201)
async def append_event(
    run_id: str, body: UatEventIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request, caller, RUN, f"uat_run:{run_id}",
            lambda c: svc.append_event(c, _tenant(caller), me, run_id=run_id, case_id=body.case_id, kind=body.kind, payload=body.payload, idem_key=body.idempotency_key),
        ),
    }  # fmt: skip


class UatManifestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: str = Field(min_length=3, max_length=16)
    requirement_ids: list[str] = Field(default_factory=list, max_length=50)
    trace_refs: list[str] = Field(default_factory=list, max_length=100)
    provider_receipt_refs: list[str] = Field(default_factory=list, max_length=100)
    source_snapshot_refs: list[str] = Field(default_factory=list, max_length=100)
    human_approval_refs: list[str] = Field(default_factory=list, max_length=50)
    oracle_version: str = Field(default="v1", max_length=64)
    defect_id: str | None = Field(default=None, pattern=UUID)
    measurements: dict[str, float] = Field(default_factory=dict, max_length=20)


@router.post("/v2/assurance/test-runs/{run_id}/cases/{case_id}/manifest", status_code=201)
async def sign_manifest(
    run_id: str,
    case_id: Annotated[str, Field(pattern=CASE)],
    body: UatManifestIn,
    request: Request,
    caller: CallerDep,
) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            RUN,
            f"uat_run:{run_id}",
            lambda c: svc.sign_manifest(
                c, _tenant(caller), me, run_id=run_id, case_id=case_id, **body.model_dump()
            ),
        ),
    }


@router.get("/v2/assurance/test-runs/{run_id}/evidence")
async def evidence(
    run_id: str,
    request: Request,
    caller: CallerDep,
    audience: Annotated[str, Query(pattern=r"^[a-z]{7,9}$")] = "auditor",
) -> dict[str, Any]:
    _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            READ,
            f"uat_run:{run_id}",
            lambda c: svc.evidence_view(c, run_id=run_id, audience=audience),
        ),
    }


class UatVerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_id: str | None = Field(default=None, pattern=UUID)
    manifest: dict[str, Any] | None = None
    jws: str | None = Field(default=None, max_length=20000)
    events: list[dict[str, Any]] | None = Field(default=None, max_length=20000)


@router.post("/v2/assurance/evidence:verify")
async def verify_evidence(body: UatVerifyIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        if body.manifest is not None and body.jws:
            return await svc.verify_bundle(
                conn,
                _tenant(caller),
                manifest=body.manifest,
                jws=body.jws,
                events=body.events,
                manifest_id=body.manifest_id,
            )
        if body.manifest_id:
            return await svc.verify_stored(conn, _tenant(caller), body.manifest_id)
        raise RuleError("NOTHING_TO_VERIFY", "send a manifest bundle or a stored manifest id")

    return {**_ids(), **await _run(request, caller, READ, "uat_evidence:verify", go)}


class UatDefectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=CASE)
    severity: Literal["BLOCKER", "CRITICAL", "MAJOR", "MINOR"]
    owner: str = Field(min_length=1, max_length=120)


@router.post("/v2/assurance/defects", status_code=201)
async def open_defect(body: UatDefectIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            RUN,
            "uat_defect:new",
            lambda c: svc.open_defect(c, _tenant(caller), me, **body.model_dump()),
        ),
    }


# ------------------------------------------------------------------------------------------------------------------------ release decisions


class UatPrivacyGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: str = Field(pattern=r"^[A-Za-z]{2,8}$")
    product: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)


class UatCandidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_unit: dict[str, Any] = Field(min_length=1)
    build_sha256: str = Field(pattern=SHA)
    policy_pack: str = Field(min_length=1, max_length=120)
    engineering_owner: str | None = Field(default=None, pattern=UUID)
    commit_authors: list[str] = Field(default_factory=list, max_length=200)
    privacy_gate: UatPrivacyGate | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    current_inputs: dict[str, str] = Field(default_factory=dict, max_length=20)


@router.post("/v2/assurance/release-candidates", status_code=201)
async def create_candidate(
    body: UatCandidateIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)
    gate = body.privacy_gate.model_dump() if body.privacy_gate else None
    return {
        **_ids(),
        **await _run(
            request, caller, DECIDE, "uat_release:new",
            lambda c: svc.create_candidate(
                c, _tenant(caller), me, release_unit=body.release_unit, build_sha256=body.build_sha256, policy_pack=body.policy_pack, facts=body.facts,
                engineering_owner=body.engineering_owner, commit_authors=body.commit_authors, privacy_gate=gate, current_inputs=body.current_inputs,
            ),
        ),
    }  # fmt: skip


@router.get("/v2/assurance/release-candidates/{release_id}")
async def preview(release_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    _me(caller)
    return {
        **_ids(),
        **await _run(
            request, caller, READ, f"uat_release:{release_id}", lambda c: svc.preview(c, release_id)
        ),
    }


class UatDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["QA_LEAD", "CTO", "SECURITY_GRC", "PRIVACY", "COUNTRY_LEGAL"]
    decision: Literal["APPROVE", "REJECT"]
    rationale: str = Field(min_length=1, max_length=2000)


@router.post("/v2/assurance/releases/{release_id}/decisions", status_code=201)
async def decide(
    release_id: str, body: UatDecisionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            DECIDE,
            f"uat_release:{release_id}",
            lambda c: svc.sign_decision(
                c, _tenant(caller), me, release_id=release_id, **body.model_dump()
            ),
        ),
    }


class UatRollbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: dict[str, Any] = Field(min_length=1)
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/v2/assurance/releases/{release_id}/rollback")
async def rollback(
    release_id: str, body: UatRollbackIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            ROLLBACK,
            f"uat_release:{release_id}",
            lambda c: svc.rollback(
                c, _tenant(caller), me, release_id=release_id, **body.model_dump()
            ),
        ),
    }


# ------------------------------------------------------------------------------------------------------------------------------ provider lab


@router.get("/v2/assurance/provider-lab")
async def provider_lab_report(request: Request, caller: CallerDep) -> dict[str, Any]:
    """Every documented fault injected into the platform's own guards, with what each guard did. It talks to no provider."""
    _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        rows = provider_lab.run_all()
        return {
            "faults": rows,
            "all_held": all(r["held"] for r in rows),
            "note": "These are the platform's guards, not any provider's behaviour.",
        }

    return {**_ids(), **await _run(request, caller, READ, "uat_provider_lab:run", go)}


class UatParityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: dict[str, Any]
    candidate: dict[str, Any]
    keys: list[str] = Field(min_length=1, max_length=100)


@router.post("/v2/assurance/provider-lab/parity")
async def provider_parity(body: UatParityIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return provider_watch.parity(body.reference, body.candidate, keys=body.keys)

    return {**_ids(), **await _run(request, caller, READ, "uat_provider_lab:parity", go)}


# ------------------------------------------------------------------------------------------------------------ risk, listings and registry drift


class UatRiskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risks: dict[str, Literal["P0", "P1", "P2"]] = Field(min_length=1, max_length=500)


@router.post("/v2/assurance/baselines/risk-confirmations", status_code=201)
async def confirm_risks(body: UatRiskIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            RUN,
            "uat_baseline:risk",
            lambda c: svc.confirm_risks(c, _tenant(caller), me, risks=body.risks),
        ),
    }


@router.get("/v2/assurance/fixtures")
async def list_fixtures(request: Request, caller: CallerDep) -> dict[str, Any]:
    _me(caller)
    return {
        **_ids(),
        "items": await _run(request, caller, READ, "uat_fixture:list", svc.list_fixtures),
    }


@router.get("/v2/assurance/test-runs")
async def list_runs(request: Request, caller: CallerDep) -> dict[str, Any]:
    _me(caller)
    return {**_ids(), "items": await _run(request, caller, READ, "uat_run:list", svc.list_runs)}


class UatServiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str = Field(min_length=1, max_length=64)
    service_code: str = Field(min_length=1, max_length=64)
    status: Literal["REGISTERED", "SUSPENDED", "WITHDRAWN"]
    valid_until: date | None = None
    scheme_version: str | None = Field(default=None, max_length=32)


class UatWatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previous: list[UatServiceIn] = Field(max_length=2000)
    current: list[UatServiceIn] = Field(max_length=2000)


@router.post("/v2/assurance/registry-watch")
async def registry_watch(body: UatWatchIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Two snapshots of an official register in, drift out, and the exact provider routes that drifted closed. Nothing is fetched."""
    _me(caller)
    prev = [i.model_dump() for i in body.previous]
    cur = [i.model_dump() for i in body.current]
    today = datetime.now(UTC).date()
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            ROLLBACK,
            "uat_registry:watch",
            lambda c: svc.watch_registry(
                c, _tenant(caller), previous=prev, current=cur, today=today
            ),
        ),
    }


# ------------------------------------------------------------------------------------------------------------------------------- policy


class UatPolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_unit: dict[str, Any] = Field(min_length=1)
    thresholds: dict[str, float] = Field(min_length=1, max_length=20)


@router.post("/v2/assurance/policies", status_code=201)
async def propose_policy(body: UatPolicyIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            DECIDE,
            "uat_policy:new",
            lambda c: svc.propose_policy(
                c, _tenant(caller), me, release_unit=body.release_unit, thresholds=body.thresholds
            ),
        ),
    }


class UatPolicySignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["QA_LEAD", "CTO", "SECURITY_GRC"]
    decision: Literal["APPROVE", "REJECT"]
    rationale: str = Field(min_length=1, max_length=2000)


@router.post("/v2/assurance/policies/{policy_id}/signatures", status_code=201)
async def sign_policy(
    policy_id: str, body: UatPolicySignIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            DECIDE,
            f"uat_policy:{policy_id}",
            lambda c: svc.sign_policy(
                c, _tenant(caller), me, policy_id=policy_id, **body.model_dump()
            ),
        ),
    }


@router.get("/v2/assurance/policies/{policy_id}")
async def get_policy(policy_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            READ,
            f"uat_policy:{policy_id}",
            lambda c: svc.policy_view(c, policy_id),
        ),
    }


class UatDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_unit: dict[str, Any] = Field(min_length=1)


@router.post("/v2/assurance/policies:from-draft", status_code=201)
async def propose_from_draft(
    body: UatDraftIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """An unsigned proposal from the numbers a controlled document already proposes. Nobody's signature is implied."""
    me = _me(caller)
    return {
        **_ids(),
        **await _run(
            request,
            caller,
            DECIDE,
            "uat_policy:new",
            lambda c: svc.propose_policy_from_draft(
                c, _tenant(caller), me, release_unit=body.release_unit
            ),
        ),
    }


@router.post("/v2/assurance/registry-sources/{name}:run")
async def run_registry_source(name: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Fetch one enabled official register, compare it with the last snapshot and close what drifted. A disabled source, or no registered fetcher, fetches nothing."""
    _me(caller)
    override = getattr(request.app.state, "registry_sources", None)
    today = datetime.now(UTC).date()
    return {
        **_ids(),
        **await _run(
            request, caller, ROLLBACK, f"uat_registry:{name}",
            lambda c: registry_source.run_source(c, _tenant(caller), name, config=override, today=today),
        ),
    }  # fmt: skip

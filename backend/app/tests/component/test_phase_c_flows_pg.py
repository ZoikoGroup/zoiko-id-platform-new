"""Phase C cross-module flows over real PostgreSQL.

AT-07 / MAND-02/03/06/19/20 (root revocation -> grants -> mandates), MAND-16/17 (approvals),
MACH-02/03/09/20 (agent and workload identity), AUTHZ §18 (policy releases) and AUTHZ §24
(decision records bound to the active release).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from conftest import CELL, ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.authority.application.cascade import on_root_changed
from zid_platform.modules.authority.domain.algebra import (
    Authority,
    Request,
    Window,
    scope,
)
from zid_platform.modules.authority.domain.composite import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalState,
    CompositeMode,
    Verdict,
)
from zid_platform.modules.authority.domain.grants import (
    AuthorityError,
    ContinuityMode,
    Grant,
    GrantStatus,
    GrantType,
)
from zid_platform.modules.authority.infrastructure import approval_store
from zid_platform.modules.authority.infrastructure.grant_store import load_chain
from zid_platform.modules.authority.ports import issue_grant
from zid_platform.modules.decision.application.pdp_service import action_spec, evaluate_request
from zid_platform.modules.decision.domain.pdp import AuthnFact, DecisionRequest, Outcome
from zid_platform.modules.machine.domain.agents import (
    ActivationEvidence,
    AgentProfile,
    AgentStatus,
    Attestation,
    BindingStatus,
    BlueprintRelease,
    CredentialRequest,
    MachineError,
    ReviewStatus,
    WorkloadBinding,
)
from zid_platform.modules.machine.infrastructure import machine_store
from zid_platform.modules.mandate.application.service import activate_mandate
from zid_platform.modules.mandate.domain.ledger import Amounts, Budget, LedgerError, MandateStatus
from zid_platform.modules.mandate.domain.subagent import BudgetMode, CascadeEffect, MandateTerms
from zid_platform.modules.mandate.infrastructure.ledger_store import MandateRecord, create_mandate
from zid_platform.modules.mandate.infrastructure.mandate_store import (
    create_sub_mandate,
    load_terms,
    pause_or_revoke_for_grants,
)
from zid_platform.modules.organization.domain.roots import (
    Assurance,
    Fact,
    OrgEvent,
    RootAssertion,
    RootStatus,
    RootType,
    SourceEvent,
)
from zid_platform.modules.organization.infrastructure.root_store import (
    apply_source_event,
    create_organization,
    save_root,
)
from zid_platform.modules.policy_authoring.infrastructure import release_store
from zid_platform.modules.policy_runtime.domain.release import (
    PolicyCase,
    PolicyRelease,
    ReleaseStatus,
)
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal

REPO = Path(__file__).resolve().parents[4]
CATALOG = yaml.safe_load((REPO / "contracts/action-catalog/actions.yaml").read_text("utf-8"))[
    "actions"
]
NOW = datetime.now(UTC)
YEAR = Window(NOW - timedelta(days=30), NOW + timedelta(days=335))


def uid() -> str:
    return str(uuid.uuid4())


async def seed_fence(owner_url: str, tenant: str) -> None:
    async with engine(owner_url) as eng, eng.begin() as c:
        await c.execute(
            text("INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 1)"),
            {"t": uuid.UUID(tenant), "c": uuid.UUID(CELL)},
        )


async def org_root(c: object, tenant: str, holder: str) -> tuple[str, str]:
    org, root = uid(), uid()
    await create_organization(
        c,
        organization_id=org,
        tenant_id=tenant,
        legal_name="Acme",
        jurisdiction="GB",  # type: ignore[arg-type]
        valid_from=NOW - timedelta(days=400),
    )
    await save_root(
        c,
        tenant,
        RootAssertion(  # type: ignore[arg-type]
            root_id=root,
            organization_id=org,
            holder_principal_id=holder,
            root_type=RootType.REGISTRY,
            facts=frozenset({Fact.ENTITY_EXISTENCE, Fact.OFFICIAL_ROLE}),
            assurance=Assurance.AR3,
            status=RootStatus.ACTIVE,
            jurisdiction="GB",
            valid_from=NOW - timedelta(days=100),
            valid_to=None,
            fresh_until=NOW + timedelta(days=1),
        ),
    )
    return org, root


def grant(tenant: str, grantor: str, grantee: str, org: str, **kw: object) -> Grant:
    base: dict[str, object] = {
        "grant_id": uid(),
        "tenant_id": tenant,
        "grant_type": GrantType.ROLE_ISSUED,
        "grantor_principal_id": grantor,
        "grantee_principal_id": grantee,
        "represented_principal_id": org,
        "authority": Authority(
            actions=scope("purchase.create"), resources=scope("supplier:*"), window=YEAR
        ),
        "continuity_mode": ContinuityMode.ROLE_CONTINUOUS,
        "status": GrantStatus.ACTIVE,
        "issued_at": NOW - timedelta(hours=1),
        "delegation_allowed": True,
        "max_delegation_depth": 2,
    } | kw
    return Grant(**base)  # type: ignore[arg-type]


def mandate_record(
    tenant: str, grant_id: str, org: str, executor: str, budget: Budget, **kw: object
) -> MandateRecord:
    base: dict[str, object] = {
        "mandate_id": uuid.uuid4(),
        "tenant_id": uuid.UUID(tenant),
        "authority_grant_id": uuid.UUID(grant_id),
        "represented_principal_id": uuid.UUID(org),
        "executor_principal_id": uuid.UUID(executor),
        "purpose_id": uuid.UUID(int=7),
        "resource_scope_id": uuid.UUID(int=8),
        "allowed_actions": ("purchase.create",),
        "budget": budget,
        "home_cell_id": uuid.UUID(CELL),
        "status": MandateStatus.ACTIVE,
        "valid_from": NOW - timedelta(hours=1),
        "valid_to": NOW + timedelta(days=2),
        "created_by_principal_id": uuid.uuid4(),
    } | kw
    return MandateRecord(**base)  # type: ignore[arg-type]


def terms(rec: MandateRecord, **kw: object) -> MandateTerms:
    base: dict[str, object] = {
        "mandate_id": str(rec.mandate_id),
        "executor_principal_id": str(rec.executor_principal_id),
        "represented_principal_id": str(rec.represented_principal_id),
        "purpose_id": str(rec.purpose_id),
        "actions": frozenset(rec.allowed_actions),
        "resource_scope": frozenset({"rs_suppliers"}),
        "tools": frozenset({"tool_order", "tool_search"}),
        "jurisdictions": frozenset({"GB"}),
        "valid_from": rec.valid_from,
        "valid_to": rec.valid_to,
        "delegation_allowed": True,
        "max_delegation_depth": 2,
        "status": rec.status,
    } | kw
    return MandateTerms(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------- AT-07: root -> grants -> mandates


def test_root_revocation_cascades_to_grants_and_mandates(rt_url: str, owner_url: str) -> None:
    tenant, cfo, controller, agent = uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            org, root = await org_root(c, tenant, cfo)
            personal = grant(
                tenant,
                cfo,
                controller,
                org,
                root_id=root,
                continuity_mode=ContinuityMode.PERSON_BOUND,
            )
            role = grant(tenant, cfo, controller, org, root_id=root)
            for g in (personal, role):
                await issue_grant(c, g, risk_level=1, now=NOW)
            m_personal = mandate_record(tenant, personal.grant_id, org, agent, Budget())
            m_role = mandate_record(tenant, role.grant_id, org, agent, Budget())
            for m in (m_personal, m_role):
                await create_mandate(c, m, terms=terms(m))

            out = await apply_source_event(
                c, tenant, SourceEvent("ch-1", org, OrgEvent.OFFICER_CESSATION, cfo)
            )
            assert out.applied
            changed = await on_root_changed(c, root)
            assert changed == {
                personal.grant_id: GrantStatus.REVOKED,
                role.grant_id: GrantStatus.REVALIDATION_PENDING,
            }
            assert (await load_terms(c, str(m_personal.mandate_id)))[
                0
            ].status is MandateStatus.REVOKED
            assert (await load_terms(c, str(m_role.mandate_id)))[0].status is MandateStatus.PAUSED

            # MAND-06: the paused mandate can't resume while its source grant is pending revalidation
            with pytest.raises(LedgerError) as e:
                await activate_mandate(
                    c, tenant_id=tenant, mandate_id=str(m_role.mandate_id), risk_level=1, now=NOW
                )
            assert e.value.code == "MANDATE_SOURCE_INVALID"
            assert (await load_chain(c, role.grant_id))[
                role.grant_id
            ].status is GrantStatus.REVALIDATION_PENDING

    asyncio.run(seed_fence(owner_url, tenant))
    asyncio.run(run())


# ---------------------------------------------------------------- MAND-19/20: sub-mandates


def test_sub_mandates_share_the_parent_budget_and_stop_with_it(rt_url: str, owner_url: str) -> None:
    tenant, cfo, controller, agent, child_agent = uid(), uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            org, root = await org_root(c, tenant, cfo)
            g = grant(tenant, cfo, controller, org, root_id=root)
            await issue_grant(c, g, risk_level=1, now=NOW)
            parent = mandate_record(
                tenant, g.grant_id, org, agent, Budget(currency="GBP", value_limit=Decimal(1000))
            )
            await create_mandate(c, parent, terms=terms(parent))

            def child_of(i: int) -> tuple[MandateRecord, MandateTerms]:
                rec = mandate_record(
                    tenant, g.grant_id, org, child_agent, Budget(), valid_to=NOW + timedelta(days=1)
                )
                t = terms(
                    rec,
                    parent_mandate_id=str(parent.mandate_id),
                    budget_mode=BudgetMode.ALLOCATED_SUB_BUDGET,
                    max_delegation_depth=1,
                    delegation_allowed=False,
                    tools=frozenset({"tool_search"}),
                )
                return rec, t

            r1, t1 = child_of(1)
            await create_sub_mandate(
                c,
                parent_id=str(parent.mandate_id),
                child=r1,
                child_terms=t1,
                allocation=Amounts(Decimal(600), 0, 0),
                cell_id=uuid.UUID(CELL),
                writer_epoch=1,
                now=NOW,
            )
            r2, t2 = child_of(2)
            with pytest.raises(
                LedgerError
            ) as e:  # 600 + 600 > 1000: the parent budget is never multiplied
                async with c.begin_nested():
                    await create_sub_mandate(
                        c,
                        parent_id=str(parent.mandate_id),
                        child=r2,
                        child_terms=t2,
                        allocation=Amounts(Decimal(600), 0, 0),
                        cell_id=uuid.UUID(CELL),
                        writer_epoch=1,
                        now=NOW,
                    )
            assert e.value.code == "MANDATE_VALUE_LIMIT"
            _, child_budget, _ = await load_terms(c, str(r1.mandate_id))
            assert child_budget.value_limit == Decimal(600)

            wider_rec, _ = child_of(3)
            wider = terms(
                wider_rec,
                parent_mandate_id=str(parent.mandate_id),
                budget_mode=BudgetMode.SHARED_PARENT_LEDGER,
                max_delegation_depth=1,
                tools=frozenset({"tool_wire"}),
            )
            with pytest.raises(LedgerError):  # MAND-18: tools outside the parent
                await create_sub_mandate(
                    c,
                    parent_id=str(parent.mandate_id),
                    child=wider_rec,
                    child_terms=wider,
                    allocation=Amounts(),
                    cell_id=uuid.UUID(CELL),
                    writer_epoch=1,
                    now=NOW,
                )

            changes = await pause_or_revoke_for_grants(
                c, [g.grant_id], CascadeEffect.REVOKE
            )  # MAND-20
            assert changes[str(parent.mandate_id)] is MandateStatus.REVOKED
            assert changes[str(r1.mandate_id)] is MandateStatus.REVOKED

            with pytest.raises(DBAPIError, match="immutable"):  # terms can't be edited afterwards
                async with c.begin_nested():
                    await c.execute(
                        text(
                            "UPDATE agent.mandate SET tools = '{tool_wire}' WHERE mandate_id = :m"
                        ),
                        {"m": r1.mandate_id},
                    )

    asyncio.run(seed_fence(owner_url, tenant))
    asyncio.run(run())


# ---------------------------------------------------------------- MAND-16/17: approvals


def test_approvals_are_one_vote_per_person_and_transaction_bound(rt_url: str) -> None:
    tenant, requester, a, b = uid(), uid(), uid(), uid()
    request_id, tx = uid(), "purchase:42|GBP|9000|tool_order"

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await approval_store.create_request(
                c,
                tenant_id=tenant,
                request_id=request_id,
                requester_id=requester,
                subject_ref="mandate:m1",
                transaction_context=tx,
                policy=ApprovalPolicy(CompositeMode.K_OF_N, ("finance_approver",), 2),
                expires_at=NOW + timedelta(hours=1),
            )

            def vote(who: str, session: str) -> ApprovalDecision:
                return ApprovalDecision(
                    who,
                    frozenset({"finance_approver"}),
                    uid(),
                    True,
                    Verdict.APPROVE,
                    "",
                    NOW,
                    session,
                )

            await approval_store.record_decision(
                c,
                tenant_id=tenant,
                request_id=request_id,
                decision=vote(a, "s1"),
                transaction_context=tx,
            )
            with pytest.raises(
                AuthorityError
            ):  # same person, second session: refused by the database
                await approval_store.record_decision(
                    c,
                    tenant_id=tenant,
                    request_id=request_id,
                    decision=vote(a, "s2"),
                    transaction_context=tx,
                )
            out = await approval_store.evaluate(
                c, request_id=request_id, current_transaction_context=tx, now=NOW
            )
            assert out.state is ApprovalState.PENDING
            await approval_store.record_decision(
                c,
                tenant_id=tenant,
                request_id=request_id,
                decision=vote(b, "s9"),
                transaction_context=tx,
            )
            out = await approval_store.evaluate(
                c, request_id=request_id, current_transaction_context=tx, now=NOW
            )
            assert out.state is ApprovalState.APPROVED
            changed = await approval_store.evaluate(
                c,
                request_id=request_id,
                current_transaction_context=tx.replace("9000", "9500"),
                now=NOW,
            )
            assert changed.state is ApprovalState.STALE

    asyncio.run(run())


# ---------------------------------------------------------------- MACH: agents and workloads


def test_agent_activation_binding_and_blueprint_revocation(rt_url: str) -> None:
    tenant = uid()
    agent_id, sponsor, org = uid(), uid(), uid()
    bp = BlueprintRelease(
        "bp_ap", "1.0.0", "sha256:approved", "sha256:tools", uid(), ReviewStatus.APPROVED
    )
    evidence = ActivationEvidence(True, None, True, True, True, True, True)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await machine_store.save_blueprint(c, tenant, bp)
            draft = AgentProfile(
                agent_id,
                tenant,
                "AP",
                "bp_ap",
                "1.0.0",
                bp.publisher_principal_id,
                uid(),
                sponsor,
                org,
                "any",
                frozenset({"tool_search"}),
                1,
                AgentStatus.PENDING_APPROVAL,
            )
            await machine_store.save_agent(c, draft)
            active = await machine_store.change_agent_status(
                c, agent_id, AgentStatus.ACTIVE, evidence=evidence
            )
            assert (
                active.status is AgentStatus.ACTIVE
            )  # blueprint gate read from the stored release

            wid = "spiffe://uk-1.prod.id.zoiko/agents/ap/a"
            await machine_store.save_binding(
                c,
                tenant,
                WorkloadBinding(
                    uid(),
                    agent_id,
                    wid,
                    "1.0.0",
                    "sha256:approved",
                    "prod-uk-1",
                    Attestation.ATTEST_1,
                    BindingStatus.ACTIVE,
                    NOW - timedelta(hours=1),
                    NOW + timedelta(days=1),
                ),
            )
            creq = CredentialRequest(
                wid, "sha256:approved", Attestation.ATTEST_2, "prod-uk-1", NOW, 2
            )
            assert await machine_store.credential_check(c, creq) == []
            assert "WORKLOAD_ARTIFACT_NOT_APPROVED" in await machine_store.credential_check(
                c, CredentialRequest(wid, "sha256:other", Attestation.ATTEST_2, "prod-uk-1", NOW, 2)
            )

            await machine_store.set_blueprint_review(
                c, "bp_ap", "1.0.0", ReviewStatus.REVOKED
            )  # MACH-20
            assert "BLUEPRINT_REVOKED" in await machine_store.credential_check(c, creq)

            await machine_store.change_agent_status(c, agent_id, AgentStatus.REVOKED)  # MACH-09
            assert "AGENT_NOT_ACTIVE" in await machine_store.credential_check(c, creq)
            with pytest.raises(MachineError):
                await machine_store.change_agent_status(
                    c, agent_id, AgentStatus.ACTIVE, evidence=evidence
                )
            with pytest.raises(DBAPIError, match="never return"):  # the database refuses it too
                async with c.begin_nested():
                    await c.execute(
                        text(
                            "UPDATE agent.agent_profile SET status = 'ACTIVE' WHERE agent_principal_id = :a"
                        ),
                        {"a": uuid.UUID(agent_id)},
                    )

    asyncio.run(run())


# ---------------------------------------------------------------- AUTHZ §18 + decision records


POLICY = (
    '@id("buy") permit (principal, action == Action::"purchase.create", resource) '
    "when { context.authority_valid };"
)
OK_CTX = {
    "tenant_match": True,
    "authority_valid": True,
    "authority_stale": False,
    "security_state": "NORMAL",
    "risk_class": 3,
}


def test_policy_release_lifecycle_and_decisions_bound_to_the_active_release(rt_url: str) -> None:
    tenant, author, reviewer, cfo, buyer = uid(), uid(), uid(), uid(), uid()

    def release(rid: str) -> PolicyRelease:
        return PolicyRelease(
            rid,
            tenant,
            POLICY,
            "1",
            ReleaseStatus.DRAFT,
            (
                PolicyCase("buy", "p", "purchase.create", "r", OK_CTX, True),
                PolicyCase(
                    "no authority",
                    "p",
                    "purchase.create",
                    "r",
                    {**OK_CTX, "authority_valid": False},
                ),
            ),
            author=author,
        )

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r1 = uid()
            await release_store.create_release(c, release(r1), policy_name="purchasing", version=1)
            await release_store.move(c, r1, ReleaseStatus.VALIDATING, catalog=CATALOG, now=NOW)
            await release_store.move(c, r1, ReleaseStatus.REVIEW, catalog=CATALOG, now=NOW)
            with pytest.raises(
                Exception, match="POLICY_REVIEW_REQUIRED"
            ):  # R3 policy: two independent reviewers
                await release_store.move(
                    c, r1, ReleaseStatus.ACTIVE, catalog=CATALOG, now=NOW, high_risk=True
                )
            await release_store.add_reviewer(c, r1, reviewer)
            await release_store.move(c, r1, ReleaseStatus.ACTIVE, catalog=CATALOG, now=NOW)
            with pytest.raises(DBAPIError, match="immutable"):
                async with c.begin_nested():
                    await c.execute(
                        text(
                            "UPDATE authz.policy_release SET source_text = 'permit(principal,action,resource);' "
                            "WHERE release_id = :r"
                        ),
                        {"r": uuid.UUID(r1)},
                    )

            # a decision without an explicit release uses the tenant's ACTIVE one and is recorded
            await create_principal(
                c, Principal(buyer, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            org, root = await org_root(c, tenant, cfo)
            await issue_grant(
                c,
                grant(
                    tenant,
                    cfo,
                    buyer,
                    org,
                    root_id=root,
                    delegation_allowed=False,
                    max_delegation_depth=0,
                ),
                risk_level=3,
                now=NOW,
            )
            req = DecisionRequest(
                uid(),
                tenant,
                action_spec("purchase.create", CATALOG),
                "supplier:9",
                org,
                None,
                None,
                NOW,
            )
            d = await evaluate_request(
                c,
                req=req,
                executor_id=buyer,
                authority_request=Request("purchase.create", "supplier:9", NOW),
                authn=AuthnFact(3, NOW - timedelta(minutes=1)),
                security_state="NORMAL",
                catalog=CATALOG,
            )
            assert d.outcome is Outcome.ALLOW, d.reason_codes
            row = (
                await c.execute(
                    text(
                        "SELECT outcome, policy_release_id, relation_revision, security_revision, "
                        "input_commitment FROM authz.policy_decision WHERE decision_id = :d"
                    ),
                    {"d": uuid.UUID(req.decision_id)},
                )
            ).one()
            assert (row.outcome, str(row.policy_release_id)) == ("ALLOW", r1)
            assert (
                row.relation_revision == 0
                and row.security_revision == 0
                and row.input_commitment.startswith("sha256:")
            )

            r2 = uid()  # activating a new release retires the old one
            await release_store.create_release(c, release(r2), policy_name="purchasing", version=2)
            await release_store.move(c, r2, ReleaseStatus.VALIDATING, catalog=CATALOG, now=NOW)
            await release_store.move(c, r2, ReleaseStatus.REVIEW, catalog=CATALOG, now=NOW)
            await release_store.add_reviewer(c, r2, reviewer)
            await release_store.move(c, r2, ReleaseStatus.ACTIVE, catalog=CATALOG, now=NOW)
            assert (await release_store.active_release(c, tenant)).release_id == r2  # type: ignore[union-attr]
            assert (await release_store.load_release(c, r1)).status is ReleaseStatus.RETIRED

            # The database itself allows only one ACTIVE release per tenant (e.g. two racing activations).
            r3 = uid()
            await release_store.create_release(c, release(r3), policy_name="purchasing", version=3)
            with pytest.raises(DBAPIError, match="one_active_release_per_tenant"):
                async with c.begin_nested():
                    await c.execute(
                        text(
                            "UPDATE authz.policy_release SET status = 'ACTIVE' WHERE release_id = :r"
                        ),
                        {"r": uuid.UUID(r3)},
                    )

    asyncio.run(run())

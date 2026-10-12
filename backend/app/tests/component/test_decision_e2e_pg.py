"""End to end: PostgreSQL authority + Cedar policy + decision algorithm (AUTHZ §12; AT-02/09 slice).

Real grants, roots and principals in PostgreSQL (RLS on), a real Cedar release with the platform
floor, and the decision service gathering facts through module ports.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import yaml
from conftest import ctx, engine
from zid_db import tenant_transaction
from zid_platform.modules.authority.domain.algebra import (
    Authority,
    Request,
    ValueLimit,
    Window,
    scope,
)
from zid_platform.modules.authority.domain.grants import (
    ContinuityMode,
    Grant,
    GrantStatus,
    GrantType,
)
from zid_platform.modules.authority.ports import issue_grant, revoke_grant
from zid_platform.modules.decision.application.pdp_service import action_spec, evaluate_request
from zid_platform.modules.decision.domain.pdp import AuthnFact, DecisionRequest, Outcome
from zid_platform.modules.organization.domain.roots import (
    Assurance,
    Fact,
    RootAssertion,
    RootStatus,
    RootType,
)
from zid_platform.modules.organization.infrastructure.root_store import (
    create_organization,
    save_root,
)
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
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    set_status,
)

REPO = Path(__file__).resolve().parents[4]
CATALOG = yaml.safe_load((REPO / "contracts/action-catalog/actions.yaml").read_text("utf-8"))[
    "actions"
]
NOW = datetime.now(UTC)
YEAR = Window(NOW - timedelta(days=30), NOW + timedelta(days=335))
RELEASE = PolicyRelease(
    release_id="rel_ap_1",
    tenant_id="t",
    schema_version="1",
    status=ReleaseStatus.ACTIVE,
    policies='@id("ap") permit (principal, action == Action::"invoice.approve", resource) '
    'when { context.authority_valid && context.purpose == "accounts_payable" };',
    cases=(PolicyCase("ok", "p", "invoice.approve", "r", {}, False),),
)


def uid() -> str:
    return str(uuid.uuid4())


def test_decision_path_over_real_authority_and_policy(rt_url: str) -> None:
    tenant, cfo, controller, org, root_id = uid(), uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c,
                Principal(controller, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"),
            )
            await create_organization(
                c,
                organization_id=org,
                tenant_id=tenant,
                legal_name="Acme",
                jurisdiction="GB",
                valid_from=NOW - timedelta(days=400),
            )
            await save_root(
                c,
                tenant,
                RootAssertion(
                    root_id=root_id,
                    organization_id=org,
                    holder_principal_id=cfo,
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
            grant = Grant(
                grant_id=uid(),
                tenant_id=tenant,
                grant_type=GrantType.ROLE_ISSUED,
                grantor_principal_id=cfo,
                grantee_principal_id=controller,
                represented_principal_id=org,
                authority=Authority(
                    actions=scope("invoice.approve"),
                    resources=scope("invoice:*"),
                    purposes=scope("accounts_payable"),
                    value=ValueLimit(Decimal(50_000), "GBP"),
                    window=YEAR,
                ),
                continuity_mode=ContinuityMode.ROLE_CONTINUOUS,
                status=GrantStatus.ACTIVE,
                issued_at=NOW - timedelta(hours=1),
                root_id=root_id,
                delegation_allowed=False,
                max_delegation_depth=0,
            )
            await issue_grant(c, grant, risk_level=2, now=NOW)

            async def decide(**kw: object):  # type: ignore[no-untyped-def]
                r = DecisionRequest(
                    decision_id=uid(),
                    tenant_id=tenant,
                    action=action_spec("invoice.approve", CATALOG),
                    resource_id="invoice:7",
                    represented_principal_id=org,
                    purpose=str(kw.get("purpose", "accounts_payable")),
                    tool_id=None,
                    at=NOW,
                )
                return await evaluate_request(
                    c,
                    req=r,
                    executor_id=controller,
                    authority_request=Request(
                        "invoice.approve",
                        "invoice:7",
                        NOW,
                        purpose=r.purpose,
                        value=ValueLimit(Decimal(str(kw.get("value", 100))), "GBP"),
                    ),
                    authn=AuthnFact(3, NOW - timedelta(minutes=2)),
                    security_state=str(kw.get("security", "NORMAL")),
                    release=RELEASE,
                    catalog=CATALOG,
                )

            ok = await decide()
            assert ok.outcome is Outcome.ALLOW, ok.reason_codes
            assert ok.policy_release_id == "rel_ap_1" and ok.authority_chain_commitment.startswith(
                "sha256:"
            )

            over = await decide(value=60_000)  # beyond the grant's value limit
            assert over.outcome is Outcome.DENY and "AUTHORITY_SCOPE_EXCEEDED" in over.reason_codes

            wrong_purpose = await decide(purpose="marketing")
            assert wrong_purpose.outcome is Outcome.DENY

            unknown_security = await decide(security="UNKNOWN")  # no signals fact: R2 fails closed
            assert unknown_security.outcome is Outcome.STATE_UNAVAILABLE

            await revoke_grant(c, grant.grant_id)
            revoked = await decide()
            assert revoked.outcome is Outcome.DENY and "AUTHORITY_REVOKED" in revoked.reason_codes

            await set_status(c, controller, PrincipalStatus.SUSPENDED)
            suspended = await decide()
            assert (
                suspended.outcome is Outcome.DENY and "PRINCIPAL_INACTIVE" in suspended.reason_codes
            )

    asyncio.run(run())

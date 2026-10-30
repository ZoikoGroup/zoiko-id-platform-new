"""An onboarded tenant for Phase G component tests: bootstrap, organization gate, two administrators.

The organization gate itself is exercised in test_onboarding_organization_pg.py; here it is
replaced by the same end state (tenant CONFIGURING, OWNER active, a valid authority chain for the
tenant-administration actions via admin_fixture) so the later slices can be tested on their own.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.modules.onboarding.domain.gates import TENANT_ADMIN_ACTIONS
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.tenant.application import lifecycle
from zid_platform.modules.tenant.domain.lifecycle import Move
from zid_platform.protected import transaction_context

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
RESOURCES = (
    "tenant:*",
    "integration_application:*",
    "integration_connector:*",
    "provisioning_source:*",
    "onboarding_plan:*",
    "onboarding_stage:*",
    "mapping_release:*",
    "federation_connection:*",
    "policy_release:*",
)


def uid() -> str:
    return str(uuid.uuid4())


@dataclass
class Onboarded:
    tenant: str
    owner: str
    second: str
    ring: KeyRing
    sessions: dict[str, str] = field(default_factory=dict)  # principal -> session id

    def bearer(self, p: str, *, scope: str = "", client: str = "console") -> dict[str, str]:
        now = datetime.now(UTC)
        tok = issue_access_token(
            self.ring,
            issuer=ISS,
            audience=AUD,
            subject=p,
            tenant_id=self.tenant,
            client_id=client,
            now=now,
            session_id=self.sessions.get(p),
            auth_time=now - timedelta(seconds=5) if p in self.sessions else None,
            scope=frozenset(scope.split()),
        )
        return {"Authorization": f"Bearer {tok}"}


async def _session(c: Any, tenant: str, p: str) -> str:
    principal, _ = await load_principal(c, p)
    sid, now = uid(), datetime.now(UTC)
    await create_session(
        c,
        Session(
            sid,
            tenant,
            p,
            now - timedelta(seconds=5),
            "native:webauthn",
            2,
            principal.security_revision,
            1,
            SessionStatus.ACTIVE,
            now + timedelta(hours=1),
            now,
        ),
    )
    return sid


def onboard(
    rt_url: str,
    *,
    extra_actions: Sequence[str] = (),
    extra_resources: Sequence[str] = (),
    delegable: bool = False,
) -> Onboarded:
    async def run() -> Onboarded:
        signup, creator, second = uid(), uid(), uid()
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(signup)) as c:
                await create_principal(
                    c,
                    Principal(creator, signup, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"),
                )
                r = await lifecycle.bootstrap_tenant(
                    c,
                    creating_tenant_id=signup,
                    creator_principal_id=creator,
                    display_name="Acme",
                    tenant_slug=f"acme-{uid()[:8]}",
                    deployment_mode="O1",
                    home_cell_id="uk-1",
                    console=None,
                )
            t, owner = r.tenant.tenant_id, r.owner_principal_id
            o = Onboarded(t, owner, second, KeyRing(LocalSigner(f"issuer-{t[:8]}")))
            async with tenant_transaction(eng, ctx(t)) as c:
                for _ in range(2):
                    await lifecycle.transition(
                        c, t, Move.ADVANCE, reason_code="ORG_GATE", actor=owner
                    )
                await lifecycle.activate_bootstrap_owner(c, t)
                await create_principal(
                    c, Principal(second, t, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
                for p in (owner, second):
                    o.sessions[p] = await _session(c, t, p)
                await make_admin(
                    c,
                    t,
                    [owner, second],
                    [*TENANT_ADMIN_ACTIONS, "authority.grant.read", *extra_actions],
                    [*RESOURCES, *extra_resources],
                    delegable=delegable,
                )
        return o

    return asyncio.run(run())


async def grant_of(owner_url: str, principal: str) -> str:
    async with engine(owner_url) as eng, eng.begin() as c:
        return str(
            (
                await c.execute(
                    text(
                        "SELECT authority_grant_id FROM authz.authority_grant "
                        "WHERE grantee_principal_id = :p"
                    ),
                    {"p": uuid.UUID(principal)},
                )
            ).scalar_one()
        )


def approval(
    api: TestClient,
    o: Onboarded,
    owner_url: str,
    *,
    action: str,
    resource: str,
    change: str,
    requester: str | None = None,
    approver: str | None = None,
) -> str:
    """A second administrator's APPROVED approval bound to exactly this action/resource/change."""
    requester = requester or o.owner
    approver = approver or o.second
    tx = transaction_context(action, resource, change)
    r = api.post(
        "/v1/approval-requests",
        headers=o.bearer(requester),
        json={"subject_ref": resource, "transaction_context": tx, "roles": [action]},
    )
    assert r.status_code == 201, r.text
    rid = str(r.json()["approval_request_id"])
    r = api.post(
        f"/v1/approval-requests/{rid}/decisions",
        headers=o.bearer(approver),
        json={
            "verdict": "APPROVE",
            "authority_grant_id": asyncio.run(grant_of(owner_url, approver)),
            "transaction_context": tx,
        },
    )
    assert r.status_code == 201, r.text
    return rid


def give_role(rt_url: str, o: Onboarded, principal: str, role: str) -> None:
    """A tenant role record for an administrator who so far holds only authority grants (as an accepted invitation would)."""
    from zid_platform.modules.tenant.domain.lifecycle import Role, RoleOrigin, RoleStatus
    from zid_platform.modules.tenant.infrastructure import tenant_store

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await tenant_store.insert_role(
                c, tenant_id=o.tenant, principal_id=principal, role=Role(role), status=RoleStatus.ACTIVE,
                origin=RoleOrigin.INVITATION, granted_by=None, approved_by=None, now=datetime.now(UTC),
            )  # fmt: skip

    asyncio.run(run())

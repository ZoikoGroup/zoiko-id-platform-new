"""The role matrix on real routes (ZID-ENG-DEC-007): the five roles are stored, an Owner or Admin invites an Operator or a Viewer,
a privileged role is never assigned directly, the matrix and a caller's rights are readable as a projection, and every
refusal and every change leaves redacted decision evidence."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.settings import Settings


def test_invitations_and_the_decision_evidence(rt_url: str) -> None:
    """EDR07 acceptance 02 (no direct privileged grant, no Owner by API), 08 and 10 (the decision proof is recorded, redacted and
    queryable), with the matrix and the projection readable."""
    o = onboard(rt_url)
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"),
        keyring=o.ring,
    )  # fmt: skip

    def audit() -> list[Any]:
        async def rows() -> list[Any]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
                return list(
                    (
                        await c.execute(
                            text(
                                "SELECT outcome, evidence_class::text AS ec, reason_code, body FROM evidence.audit_record "
                                "WHERE tenant_id = :t AND event_type = 'tenant.access.decision' ORDER BY created_at"
                            ),
                            {"t": uuid.UUID(o.tenant)},
                        )
                    ).all()
                )

        return asyncio.run(rows())

    with TestClient(app) as api:
        own = o.bearer(o.owner)

        # the five roles are storable and invitable where the matrix says so
        op = api.post("/v1/tenant/members", json={"role": "OPERATOR"}, headers=own)
        viewer = api.post("/v1/tenant/members", json={"role": "VIEWER"}, headers=own)
        assert op.status_code == 201 and op.json()["role"] == "OPERATOR", op.text
        assert viewer.status_code == 201 and viewer.json()["role"] == "VIEWER"
        assert len(audit()) == 2 and {a.outcome for a in audit()} == {"SUCCESS"}

        # a privileged role is never assigned directly: the Approver is a request an independent checker approves, the Owner is
        # the protected owner change
        refused = api.post("/v1/tenant/members", json={"role": "APPROVER"}, headers=own)
        assert (
            refused.status_code == 403
            and refused.json()["error"]["code"] == "ROLE_GRANT_NOT_PERMITTED"
        ), refused.text
        assert refused.json()["error"]["details"][0]["issue"] == "PRIVILEGED_ROLE_NEEDS_APPROVAL"
        assert api.post("/v1/tenant/members", json={"role": "OWNER"}, headers=own).status_code in (
            403,
            422,
        )
        assert api.post("/v1/tenant/members", json={"role": "NOPE"}, headers=own).status_code == 422
        # the shipped Team page's MEMBER invitation is unchanged
        assert (
            api.post("/v1/tenant/members", json={"role": "MEMBER"}, headers=own).status_code == 201
        )

        # the evidence: E3, one record per decision, the policy hash and the role that allowed it, no content
        rows = audit()
        assert [r.outcome for r in rows] == ["SUCCESS", "SUCCESS", "DENIED", "SUCCESS"]
        assert (
            all(r.ec == "E3" for r in rows)
            and rows[2].reason_code == "PRIVILEGED_ROLE_NEEDS_APPROVAL"
        )
        matrix = api.get("/v1/roles/matrix", headers=own).json()
        details = rows[0].body["details"] if "details" in rows[0].body else rows[0].body
        flat = str(details)
        assert matrix["policy_hash"] in flat and "a.invite-operator-or-viewer" in flat

        # the matrix is readable as published policy
        assert matrix["version"] == "ZID-ENG-DEC-007-v1.0" and matrix["roles"] == [
            "OWNER",
            "ADMIN",
            "APPROVER",
            "OPERATOR",
            "VIEWER",
        ]
        assert [m["matrix"] for m in matrix["matrices"]] == list("ABCDEF")
        assert sum(len(m["actions"]) for m in matrix["matrices"]) == 68
        assert api.get("/v1/roles/matrix").status_code == 401

        # the caller's rights are a projection and say so
        mine = api.get("/v1/tenant/me/permissions", headers=own).json()
        assert (
            mine["roles"] == ["OWNER"]
            and mine["enforcement"] is False
            and mine["policy_hash"] == matrix["policy_hash"]
        )
        assert mine["rights"]["a.approve-privileged-role-grant"] == ["A"]
        assert "b.issue-override-zoiko-id-verified-status" not in mine["rights"]
        assert api.get("/v1/tenant/me/permissions", headers=o.bearer(o.second)).status_code == 200


# ---- the grant request, the person link, the gate and channel independence ---------------------------------------------


def _seed_role(rt_url: str, tenant: str, principal: str, role: str, *, create: bool = True) -> str:
    from datetime import UTC, datetime

    from zid_platform.modules.principal.domain.principal import (
        Principal,
        PrincipalStatus,
        PrincipalType,
    )
    from zid_platform.modules.principal.infrastructure.principal_store import create_principal
    from zid_platform.modules.tenant.domain.lifecycle import Role, RoleOrigin, RoleStatus
    from zid_platform.modules.tenant.infrastructure import tenant_store as store

    async def run() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            if create:
                await create_principal(
                    c,
                    Principal(
                        principal, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"
                    ),
                )
            a = await store.insert_role(
                c, tenant_id=tenant, principal_id=principal, role=Role(role), status=RoleStatus.ACTIVE,
                origin=RoleOrigin.INVITATION, granted_by=None, approved_by=None, now=datetime.now(UTC),
            )  # fmt: skip
            return a.assignment_id

    return asyncio.run(run())


def _app(rt_url: str, o: Any) -> Any:
    return create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"),
        keyring=o.ring,
    )  # fmt: skip


def test_admin_and_approver_grants_are_requests_an_independent_approver_decides(
    rt_url: str, owner_url: str, monkeypatch: Any
) -> None:
    from onboarding_fixture import approval

    o = onboard(rt_url)
    app = _app(rt_url, o)
    resource = f"tenant:{o.tenant}"
    _seed_role(
        rt_url, o.tenant, o.second, "ADMIN", create=False
    )  # the second administrator is only an Admin

    with TestClient(app) as api:
        own = o.bearer(o.owner)
        # direct Administrator invitations stay allowed until the owners rule, then one switch refuses them
        assert (
            api.post("/v1/tenant/members", json={"role": "ADMIN"}, headers=own).status_code == 201
        )
        monkeypatch.setattr(
            "zid_platform.modules.tenant.api.tenants.direct_admin_invitation_allowed", lambda: False
        )
        r = api.post("/v1/tenant/members", json={"role": "ADMIN"}, headers=own)
        assert (
            r.status_code == 403
            and r.json()["error"]["details"][0]["issue"] == "PRIVILEGED_ROLE_NEEDS_APPROVAL"
        )

        def ask(role: str, rid: str) -> Any:
            return api.post(
                "/v1/tenant/role-grants",
                json={"role": role, "approval_request_id": rid},
                headers=own,
            )

        rid = approval(
            api,
            o,
            owner_url,
            action="tenant.member.manage",
            resource=resource,
            change="GRANT_ROLE:ADMIN",
        )
        # an Admin holds the second signature of the protected approval but may not approve a privileged grant (matrix A)
        r = ask("ADMIN", rid)
        assert (
            r.status_code == 403
            and r.json()["error"]["details"][0]["issue"] == "ROLE_DOES_NOT_HOLD_PERMISSION"
        ), r.text
        _seed_role(rt_url, o.tenant, o.second, "APPROVER", create=False)  # now an Approver as well
        # the approval binds the exact change: a grant approved for ADMIN cannot be spent on APPROVER
        assert ask("APPROVER", rid).status_code == 403
        r = ask("ADMIN", rid)
        assert (
            r.status_code == 201
            and r.json()["role"] == "ADMIN"
            and r.json()["approved_by"] == o.second
        ), r.text
        # a viewer cannot even prepare the request
        viewer = str(uuid.uuid4())
        _seed_role(rt_url, o.tenant, viewer, "VIEWER")
        r = api.post(
            "/v1/tenant/role-grants",
            json={"role": "ADMIN", "approval_request_id": rid},
            headers=o.bearer(viewer),
        )
        assert r.status_code == 403


def test_two_accounts_of_one_person_cannot_approve_each_other(rt_url: str, owner_url: str) -> None:
    from onboarding_fixture import approval
    from zid_platform.modules.tenant.infrastructure import tenant_store as store

    o = onboard(rt_url)
    app = _app(rt_url, o)
    _seed_role(rt_url, o.tenant, o.second, "APPROVER", create=False)
    person = str(uuid.uuid4())

    async def link() -> tuple[bool, bool, str, str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            a = await store.link_person(
                c,
                tenant_id=o.tenant,
                principal_id=o.owner,
                person_id=person,
                source="IDENTITY_PROOFING",
            )
            b = await store.link_person(
                c,
                tenant_id=o.tenant,
                principal_id=o.second,
                person_id=person,
                source="IDENTITY_PROOFING",
            )
            again = await store.link_person(
                c,
                tenant_id=o.tenant,
                principal_id=o.second,
                person_id=str(uuid.uuid4()),
                source="PLATFORM_REVIEW",
            )
            return (
                a and b,
                again,
                await store.person_of(c, o.second),
                await store.person_of(c, str(uuid.uuid4())),
            )

    linked, rewritten, who, stranger = asyncio.run(link())
    assert (
        linked and not rewritten and who == person and stranger != person
    )  # append-only; an unlinked account is its own person

    with TestClient(app) as api:
        rid = approval(
            api,
            o,
            owner_url,
            action="tenant.member.manage",
            resource=f"tenant:{o.tenant}",
            change="GRANT_ROLE:APPROVER",
        )
        r = api.post(
            "/v1/tenant/role-grants",
            json={"role": "APPROVER", "approval_request_id": rid},
            headers=o.bearer(o.owner),
        )
        assert (
            r.status_code == 403 and r.json()["error"]["details"][0]["issue"] == "SELF_APPROVAL"
        ), r.text


def test_the_matrix_gate_and_the_engine_work_without_a_request(rt_url: str) -> None:
    """EDR07-04, 08, 09: a Viewer is refused at the route with evidence; the same engine answers a worker with no HTTP request, and a
    role that ends changes the answer at once."""
    from datetime import UTC, datetime

    from zid_platform.modules.tenant.application import access
    from zid_platform.modules.tenant.domain.lifecycle import RoleStatus
    from zid_platform.modules.tenant.domain.role_matrix import Mode
    from zid_platform.modules.tenant.infrastructure import tenant_store as store

    o = onboard(rt_url)
    app = _app(rt_url, o)
    viewer, operator, service = (str(uuid.uuid4()) for _ in range(3))
    _seed_role(rt_url, o.tenant, viewer, "VIEWER")
    assignment = _seed_role(rt_url, o.tenant, operator, "OPERATOR")

    with TestClient(app) as api:
        for method, path in (
            ("GET", "/v1/tenant/members"),
            ("POST", f"/v1/tenant/members/{uuid.uuid4()}:revoke"),
            ("POST", f"/v1/tenant/members/{uuid.uuid4()}:reissue"),
        ):
            r = api.request(method, path, headers=o.bearer(viewer))
            assert r.status_code == 403 and r.json()["error"]["code"] == "ROLE_NOT_PERMITTED", (
                path,
                r.text,
            )
        assert (
            api.get("/v1/tenant/members", headers=o.bearer(o.owner)).status_code == 200
        )  # the Owner passes the same gate

    async def worker() -> list[str]:
        out = []
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            act, kw = (
                "b.upload-organization-kyb-evidence",
                {"object_id": "case:1", "session_ok": True},
            )
            out.append(
                (
                    await access.evaluate(c, o.tenant, operator, act, Mode.EXECUTE, **kw)
                ).outcome.value
            )  # type: ignore[arg-type]
            out.append(
                (
                    await access.evaluate(
                        c, o.tenant, operator, act, Mode.EXECUTE, resource_in_scope=False, **kw
                    )
                ).outcome.value
            )  # type: ignore[arg-type]
            out.append(
                (await access.evaluate(c, o.tenant, service, act, Mode.EXECUTE, **kw)).outcome.value
            )  # type: ignore[arg-type]
            await store.set_role_status(
                c,
                assignment,
                expect=(RoleStatus.ACTIVE,),
                status=RoleStatus.REVOKED,
                actor=o.owner,
                now=datetime.now(UTC),
            )
            d = await access.evaluate(c, o.tenant, operator, act, Mode.EXECUTE, **kw)  # type: ignore[arg-type]
            out += [d.outcome.value, ",".join(d.reasons)]
        return out

    assert asyncio.run(worker()) == ["ALLOW", "DENY", "DENY", "DENY", "MEMBERSHIP_NOT_ACTIVE"]


# ---- delegation, the bootstrap Owner, and the person-link writer --------------------------------------------------------


def test_a_delegation_counts_only_while_the_grant_is_live(monkeypatch: Any) -> None:
    """EDR07-07: a revoked, suspended or expired grant is not a delegation, and a grant from another person is not this one."""
    from dataclasses import dataclass
    from datetime import UTC, datetime, timedelta

    from zid_platform.modules.authority.ports import GrantStatus
    from zid_platform.modules.tenant.application import access

    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

    @dataclass
    class Window:
        valid_from: datetime | None
        valid_to: datetime | None

    @dataclass
    class Authority:
        window: Window

    @dataclass
    class Grant:
        grantor_principal_id: str
        status: GrantStatus
        authority: Authority

    def grant(
        status: GrantStatus = GrantStatus.ACTIVE,
        start: int = -1,
        end: int | None = 1,
        grantor: str = "boss",
    ) -> Grant:
        return Grant(
            grantor,
            status,
            Authority(
                Window(
                    now + timedelta(hours=start),
                    None if end is None else now + timedelta(hours=end),
                )
            ),
        )

    def valid(*grants: Grant) -> bool:
        async def load(conn: Any, who: str) -> list[Grant]:
            return list(grants)

        monkeypatch.setattr(access, "load_grants_for_grantee", load)
        return asyncio.run(access.delegation_valid(None, "deputy", "boss", now))  # type: ignore[arg-type]

    assert valid(grant()) and valid(grant(end=None)) and valid(grant(start=-5, end=1))
    assert not valid()  # no grant at all
    assert (
        not valid(grant(GrantStatus.REVOKED))
        and not valid(grant(GrantStatus.SUSPENDED))
        and not valid(grant(GrantStatus.EXPIRED))
    )
    assert not valid(grant(end=0))  # the window ends at this very moment
    assert not valid(grant(start=1, end=2))  # not yet
    assert not valid(grant(grantor="someone-else"))  # a delegation from another person
    assert valid(grant(GrantStatus.REVOKED), grant())  # another live grant still counts


def test_the_pending_bootstrap_owner_may_verify_the_company_and_do_nothing_else(
    rt_url: str,
) -> None:
    """The organization gate is passed through company verification, so the OWNER who is still PENDING_VERIFICATION may take the
    verification actions the matrix gives an Owner, and no other action."""
    from zid_platform.modules.tenant.application import access, lifecycle
    from zid_platform.modules.tenant.domain.role_matrix import BOOTSTRAP_ACTIONS, Mode

    signup, creator = str(uuid.uuid4()), str(uuid.uuid4())

    async def run() -> list[str]:
        from zid_platform.modules.principal.domain.principal import (
            Principal,
            PrincipalStatus,
            PrincipalType,
        )
        from zid_platform.modules.principal.infrastructure.principal_store import create_principal

        out: list[str] = []
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(signup)) as c:
                await create_principal(
                    c,
                    Principal(creator, signup, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"),
                )
                r = await lifecycle.bootstrap_tenant(
                    c, creating_tenant_id=signup, creator_principal_id=creator, display_name="Acme", tenant_slug=f"acme-{signup[:8]}",
                    deployment_mode="O1", home_cell_id="uk-1", console=None,
                )  # fmt: skip
            tenant, owner = r.tenant.tenant_id, r.owner_principal_id
            async with tenant_transaction(eng, ctx(tenant)) as c:
                kw = {"object_id": "claim:1", "session_ok": True}
                for action in sorted(BOOTSTRAP_ACTIONS):
                    mode = Mode.READ if action.startswith(("a.view", "b.view")) else Mode.PREPARE
                    if action == "b.upload-organization-kyb-evidence":
                        mode = Mode.EXECUTE
                    d = await access.evaluate(c, tenant, owner, action, mode, **kw)  # type: ignore[arg-type]
                    out.append(f"{action}={d.outcome.value}")
                for action, mode in (
                    ("a.invite-operator-or-viewer", Mode.EXECUTE),
                    ("a.approve-privileged-role-grant", Mode.APPROVE),
                    ("f.change-commercial-subscription-contract", Mode.EXECUTE),
                ):
                    d = await access.evaluate(c, tenant, owner, action, mode, **kw)  # type: ignore[arg-type]
                    out.append(f"{action}={d.outcome.value}:{','.join(d.reasons)}")
        return out

    got = asyncio.run(run())
    assert all(g.endswith("=ALLOW") for g in got[:5]), got
    assert got[5:] == [
        "a.invite-operator-or-viewer=DENY:MEMBERSHIP_NOT_ACTIVE",
        "a.approve-privileged-role-grant=DENY:MEMBERSHIP_NOT_ACTIVE",
        "f.change-commercial-subscription-contract=DENY:MEMBERSHIP_NOT_ACTIVE",
    ]


def test_the_person_link_writer_is_evidenced_idempotent_and_dry_by_default(
    rt_url: str, owner_url: str
) -> None:
    from zid_platform.entrypoints.link_person import link
    from zid_platform.modules.tenant.infrastructure import tenant_store as store

    o = onboard(rt_url)
    person = str(uuid.uuid4())

    async def slug() -> str:
        async with engine(owner_url) as eng, eng.begin() as c:
            return str(
                (
                    await c.execute(
                        text("SELECT tenant_slug FROM platform.tenant WHERE tenant_id = :t"),
                        {"t": uuid.UUID(o.tenant)},
                    )
                ).scalar_one()
            )

    async def state() -> tuple[str, str, list[Any]]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT outcome, body FROM evidence.audit_record WHERE tenant_id = :t AND event_type = 'tenant.person.linked' ORDER BY created_at"
                    ),
                    {"t": uuid.UUID(o.tenant)},
                )
            ).all()
            return await store.person_of(c, o.owner), await store.person_of(c, o.second), list(rows)

    name = asyncio.run(slug())
    args = (name, owner_url, person, [o.owner, o.second], "PLATFORM_REVIEW", "CASE-7")
    assert asyncio.run(link(*args, apply=False)) == 0  # a dry run writes nothing
    assert asyncio.run(state())[:2] == (o.owner, o.second)
    assert asyncio.run(link(*args, apply=True)) == 0
    first, second, rows = asyncio.run(state())
    assert first == second == person and [r.outcome for r in rows] == ["SUCCESS", "SUCCESS"]
    assert (
        asyncio.run(link(*args, apply=True)) == 0
    )  # again: nothing changes and the attempt is recorded as a failure to link
    assert [r.outcome for r in asyncio.run(state())[2]] == [
        "SUCCESS",
        "SUCCESS",
        "FAILURE",
        "FAILURE",
    ]
    assert (
        asyncio.run(
            link(name, owner_url, person, [str(uuid.uuid4())], "PLATFORM_REVIEW", None, apply=True)
        )
        == 1
    )  # not our account
    assert (
        asyncio.run(
            link(
                "no-such-company", owner_url, person, [o.owner], "PLATFORM_REVIEW", None, apply=True
            )
        )
        == 1
    )

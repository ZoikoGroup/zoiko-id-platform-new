"""Prepare a LOCAL cell so the web app can sign in against it (development only; synthetic data).

1. Generates development signing keys into backend/.env (ZID_DEV_SIGNING_KEYS) if none are set,
   so tokens keep verifying across backend restarts and the consumer shares the API's keys.
2. Applies every module's migrations to the cell database.
3. Seeds, idempotently: a local tenant, a human principal, the web app's first-party OAuth
   client (`zoiko-web`) and this cell as the tenant's mandate writer (ZID_CELL_UUID).
4. Gives the tenant its tenant record (ACTIVE, Native mode) with that principal as its ACTIVE
   OWNER, so the Team page can invite people (G-W0).
5. Makes that principal a tenant administrator through the real authority path: organization,
   registry root of trust, an authority grant for every platform action, and an ACTIVE policy
   release (re-run after the action catalog grows to cover new actions).
6. Issues a one-time enrollment ticket and prints the web app URL that registers a passkey.

Usage (from backend/, with UV_PROJECT_ENVIRONMENT set as in the README; Postgres running):
    python -m uv run python ../tools/scripts/bootstrap_local.py
    python -m uv run python ../tools/scripts/bootstrap_local.py --ticket-only   # another passkey link

Refuses to run unless ZID_ENVIRONMENT is local or test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from localenv import BACKEND_ENV, load_backend_env

# Fixed synthetic ids, so re-running keeps the same tenant and user.
TENANT_ID = "00000000-0000-4000-8000-0000000000a1"
PRINCIPAL_ID = "00000000-0000-4000-8000-0000000000b1"
ORG_ID = "00000000-0000-4000-8000-0000000000d1"
ROOT_ID = "00000000-0000-4000-8000-0000000000d2"
ROOT_HOLDER_ID = "00000000-0000-4000-8000-0000000000d3"  # registry-confirmed officer (synthetic)
# "evidence": EVIDENCE_SIGNING for checkpoints and exports, shared by the API and the worker (DEC-F2)
KEY_PURPOSES = ("token", "set", "federation", "credential", "verifier", "evidence")
# Local operator plane (Phase F, DEC-F4): a separate operator tenant and Operator Console client.
OPERATOR_TENANT_ID = "00000000-0000-4000-8000-0000000000e1"
OPERATOR_PRINCIPAL_ID = "00000000-0000-4000-8000-0000000000e2"
# Scopes the web app requests (verification and presentation APIs check them).
WEB_SCOPES = frozenset({"openid", "verification:read", "verification:execute", "presentation:request"})
ADMIN_POLICY = '@id("admin") permit (principal, action, resource) when { context.authority_valid };'


def ensure_dev_keys() -> None:
    """Create missing development keys (an older backend/.env gains the newer purposes)."""
    existing = json.loads(os.environ.get("ZID_DEV_SIGNING_KEYS") or "{}")
    missing = [p for p in KEY_PURPOSES if p not in existing]
    if not missing:
        return
    pems = dict(existing)
    for p in missing:
        pems[p] = (
            ec.generate_private_key(ec.SECP256R1())
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            .decode()
        )
    value = json.dumps(pems, separators=(",", ":"))
    lines = BACKEND_ENV.read_text(encoding="utf-8").splitlines()
    out = [ln for ln in lines if not ln.startswith("ZID_DEV_SIGNING_KEYS=")]
    out.append(f"ZID_DEV_SIGNING_KEYS='{value}'")  # single quotes: dotenv keeps the JSON literal
    BACKEND_ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ["ZID_DEV_SIGNING_KEYS"] = value
    print("generated development signing keys into backend/.env (restart the backend to use them)")


async def ensure_admin(
    c: object,
    *,
    tenant_id: str = TENANT_ID,
    principal_id: str = PRINCIPAL_ID,
    org_id: str = ORG_ID,
    root_id: str = ROOT_ID,
    holder_id: str = ROOT_HOLDER_ID,
) -> None:
    """Make the local user a tenant administrator the way production does (no role flag):
    an organization with a registry root, an authority grant from the root holder covering every
    platform administration action, and an ACTIVE policy release permitting an action exactly when
    the authority chain is valid (same model as tests/component/admin_fixture.py)."""
    from zid_platform.modules.authority.domain.algebra import ANY, Authority, Window, scope
    from zid_platform.modules.authority.domain.grants import (
        ContinuityMode,
        Grant,
        GrantStatus,
        GrantType,
    )
    from zid_platform.modules.authority.infrastructure.grant_store import load_grants_for_grantee
    from zid_platform.modules.authority.ports import issue_grant
    from zid_platform.modules.organization.domain.roots import (
        Assurance,
        Fact,
        RootAssertion,
        RootStatus,
        RootType,
    )
    from zid_platform.modules.organization.infrastructure.root_store import (
        create_organization,
        load_root,
        save_root,
    )
    from zid_platform.modules.policy_authoring.infrastructure import release_store
    from zid_platform.modules.policy_runtime.domain.release import (
        PolicyCase,
        PolicyRelease,
        ReleaseStatus,
    )
    from zid_platform.protected import catalog

    now = datetime.now(UTC)
    # Every catalogued action the platform itself owns (customer PEP actions are left out).
    actions = sorted(a for a, spec in catalog().items() if "customer-application" not in spec["owner"])
    if await load_root(c, root_id) is None:
        await create_organization(
            c,
            organization_id=org_id,
            tenant_id=tenant_id,
            legal_name="Local Development Org",
            jurisdiction="GB",
            valid_from=now - timedelta(days=400),
        )
        await save_root(
            c,
            tenant_id,
            RootAssertion(
                root_id=root_id,
                organization_id=org_id,
                holder_principal_id=holder_id,
                root_type=RootType.REGISTRY,
                facts=frozenset({Fact.ENTITY_EXISTENCE, Fact.OFFICIAL_ROLE}),
                assurance=Assurance.AR3,
                status=RootStatus.ACTIVE,
                jurisdiction="GB",
                valid_from=now - timedelta(days=100),
                valid_to=None,
                fresh_until=now + timedelta(days=365),
            ),
        )
        print("local organization and root of trust created")
    held = [
        g
        for g in await load_grants_for_grantee(c, principal_id)
        if g.status is GrantStatus.ACTIVE and g.grantor_principal_id == holder_id
    ]
    # Like a real owner's grant (onboarding), it is not limited by resource: the routes name many resource types and the list grows.
    covered = any(
        (g.authority.actions is ANY or set(actions) <= g.authority.actions)  # type: ignore[operator]
        and g.authority.resources is ANY
        for g in held
    )
    if not covered:  # first run, the catalog gained actions, or the grant was limited to a list of resources
        await issue_grant(
            c,
            Grant(
                str(uuid.uuid4()),
                tenant_id,
                GrantType.ROLE_ISSUED,
                holder_id,
                principal_id,
                org_id,
                Authority(
                    actions=scope(*actions),
                    window=Window(now - timedelta(days=1), now + timedelta(days=300)),
                ),
                ContinuityMode.ROLE_CONTINUOUS,
                GrantStatus.ACTIVE,
                now - timedelta(hours=1),
                root_id=root_id,
                delegation_allowed=True,
                max_delegation_depth=3,
            ),
            risk_level=3,
            now=now,
        )
        print(f"administrator grant issued for {len(actions)} actions")
    if await release_store.active_release(c, tenant_id) is None:
        rid = str(uuid.uuid4())
        ok = {
            "tenant_match": True,
            "authority_valid": True,
            "authority_stale": False,
            "security_state": "NORMAL",
            "risk_class": 3,
            "purpose": "",
        }
        await release_store.create_release(
            c,
            PolicyRelease(
                rid,
                tenant_id,
                ADMIN_POLICY,
                "1",
                ReleaseStatus.DRAFT,
                (
                    PolicyCase("ok", "p", actions[0], "r", ok, True),
                    PolicyCase("no authority", "p", actions[0], "r", {**ok, "authority_valid": False}),
                ),
                author=principal_id,
            ),
            policy_name="admin",
            version=1,
        )
        for target in (ReleaseStatus.VALIDATING, ReleaseStatus.REVIEW):
            await release_store.move(c, rid, target, catalog=catalog(), now=now)
        await release_store.add_reviewer(c, rid, holder_id)
        await release_store.move(c, rid, ReleaseStatus.ACTIVE, catalog=catalog(), now=now)
        print("policy release activated")


async def ensure_tenant_record(c: object, cell_id: str) -> None:
    """The local tenant's platform.tenant row and its OWNER (idempotent; local only)."""
    from sqlalchemy import text

    t, p = uuid.UUID(TENANT_ID), uuid.UUID(PRINCIPAL_ID)
    inserted = await c.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO platform.tenant (tenant_id, display_name, tenant_slug, status, "
            "deployment_mode, home_cell_id, organization_id, creator_tenant_id, creator_principal_id) "
            "VALUES (:t, 'Local Company', 'local-company', 'ACTIVE', 'O0', :cell, :o, :t, :p) "
            "ON CONFLICT (tenant_id) DO NOTHING"
        ),
        {"t": t, "cell": cell_id, "o": uuid.UUID(ORG_ID), "p": p},
    )
    if inserted.rowcount:
        print("tenant record created (ACTIVE)")
    owner = await c.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO platform.tenant_role_assignment (assignment_id, tenant_id, principal_id, "
            "role, status, origin, activated_at) "
            "SELECT :id, :t, :p, 'OWNER', 'ACTIVE', 'BOOTSTRAP', now() WHERE NOT EXISTS ("
            "SELECT 1 FROM platform.tenant_role_assignment WHERE tenant_id = :t AND principal_id = :p "
            "AND role = 'OWNER' AND status <> 'REVOKED')"
        ),
        {"id": uuid.uuid4(), "t": t, "p": p},
    )
    if owner.rowcount:
        print(f"principal {PRINCIPAL_ID} is the tenant OWNER")


def cell_db_url() -> str:
    pw = os.environ.get("ZID_DB_PASSWORD", "zid_local_only")
    port = os.environ.get("ZID_DB_HOST_PORT", "5432")
    return os.environ.get(
        "ZID_CELL_DATABASE_URL", f"postgresql+asyncpg://zid:{pw}@localhost:{port}/zid_cell"
    )


async def seed(url: str, args: argparse.Namespace) -> str:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from zid_context import RequestContext
    from zid_db import tenant_transaction
    from zid_platform.modules.authentication.infrastructure.authenticator_store import issue_ticket
    from zid_platform.modules.principal.domain.principal import (
        Principal,
        PrincipalStatus,
        PrincipalType,
    )
    from zid_platform.modules.principal.infrastructure.principal_store import create_principal
    from zid_platform.modules.session.domain.oauth import OAuthClient
    from zid_platform.modules.session.infrastructure.oauth_store import create_client, load_client

    cell_id = os.environ.get("ZID_CELL_ID", "local-1")
    audience = os.environ.get("ZID_TOKEN_AUDIENCE", "https://api.zoiko.id")
    ctx = RequestContext("req_bootstrap", "cor_bootstrap", "bootstrap", cell_id, tenant_id=TENANT_ID)
    engine = create_async_engine(url)
    try:
        async with tenant_transaction(engine, ctx) as c:
            if not args.ticket_only:
                exists = (
                    await c.execute(
                        text("SELECT 1 FROM core.principal WHERE principal_id = :p"),
                        {"p": uuid.UUID(PRINCIPAL_ID)},
                    )
                ).first()
                if not exists:
                    await create_principal(
                        c,
                        Principal(
                            PRINCIPAL_ID,
                            TENANT_ID,
                            PrincipalType.HUMAN,
                            PrincipalStatus.ACTIVE,
                            cell_id,
                        ),
                    )
                    print(f"principal {PRINCIPAL_ID} created")
                client = await load_client(c, args.client_id)
                if client is None:
                    await create_client(
                        c,
                        OAuthClient(
                            client_id=args.client_id,
                            tenant_id=TENANT_ID,
                            client_type="PUBLIC",
                            profile="STANDARD",
                            redirect_uris=(args.redirect_uri,),
                            grant_types=frozenset({"authorization_code", "refresh_token"}),
                            scopes=WEB_SCOPES,
                            audiences=(audience,),
                            token_auth_method="none",
                            dpop_bound=False,
                            status="ACTIVE",
                            first_party=True,
                        ),
                    )
                    print(f"OAuth client {args.client_id} created (redirect {args.redirect_uri})")
                elif (
                    args.redirect_uri not in client.redirect_uris
                    or audience not in client.audiences
                    or not WEB_SCOPES <= client.scopes
                ):
                    await c.execute(
                        text(
                            "UPDATE authn.oauth_client SET redirect_uris = :r, audiences = :a, "
                            "scopes = :s WHERE tenant_id = :t AND client_id = :c"
                        ),
                        {
                            "r": [args.redirect_uri],
                            "a": [audience],
                            "s": sorted(WEB_SCOPES),
                            "t": uuid.UUID(TENANT_ID),
                            "c": args.client_id,
                        },
                    )
                    print(f"OAuth client {args.client_id} updated")
                cell_uuid = os.environ.get("ZID_CELL_UUID")
                if cell_uuid:
                    await c.execute(
                        text(
                            "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) "
                            "VALUES (:t, :c, 1) ON CONFLICT (tenant_id) DO UPDATE SET cell_id = :c"
                        ),
                        {"t": uuid.UUID(TENANT_ID), "c": uuid.UUID(cell_uuid)},
                    )
                await ensure_admin(c)
                await ensure_tenant_record(c, cell_id)
            return await issue_ticket(
                c,
                tenant_id=TENANT_ID,
                principal_id=PRINCIPAL_ID,
                purpose="BOOTSTRAP",
                issued_by="bootstrap_local",
                expires_at=datetime.now(UTC) + timedelta(hours=23),  # DB cap: 24h from transaction start
            )
    finally:
        await engine.dispose()


async def seed_operator(url: str, args: argparse.Namespace) -> None:
    """Local operator plane: operator tenant, one operator (REQUESTER/APPROVER/REVIEWER) and the
    Operator Console client. Approving needs a *second* operator: run this with another
    --operator-principal to add one. Set ZID_OPERATOR_TENANT_ID in backend/.env as printed."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from zid_context import RequestContext
    from zid_db import tenant_transaction
    from zid_platform.modules.operator_access.application.service import register_operator
    from zid_platform.modules.operator_access.domain.model import OperatorRole
    from zid_platform.modules.principal.domain.principal import (
        Principal,
        PrincipalStatus,
        PrincipalType,
    )
    from zid_platform.modules.principal.infrastructure.principal_store import create_principal
    from zid_platform.modules.session.domain.oauth import OAuthClient
    from zid_platform.modules.session.infrastructure.oauth_store import create_client, load_client

    cell_id = os.environ.get("ZID_CELL_ID", "local-1")
    audience = os.environ.get("ZID_TOKEN_AUDIENCE", "https://api.zoiko.id")
    principal = args.operator_principal
    ctx = RequestContext("req_bootstrap", "cor_bootstrap", "bootstrap", cell_id, tenant_id=OPERATOR_TENANT_ID)
    engine = create_async_engine(url)
    try:
        async with tenant_transaction(engine, ctx) as c:
            if not (
                await c.execute(text("SELECT 1 FROM core.principal WHERE principal_id = :p"), {"p": uuid.UUID(principal)})
            ).first():
                await create_principal(
                    c, Principal(principal, OPERATOR_TENANT_ID, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, cell_id)
                )
            if await load_client(c, "zoiko-operator") is None:
                await create_client(
                    c,
                    OAuthClient(
                        client_id="zoiko-operator",
                        tenant_id=OPERATOR_TENANT_ID,
                        client_type="PUBLIC",
                        profile="STANDARD",
                        redirect_uris=("http://127.0.0.1:3100/auth/callback",),
                        grant_types=frozenset({"authorization_code", "refresh_token"}),
                        scopes=frozenset({"openid"}),
                        audiences=(audience,),
                        token_auth_method="none",
                        dpop_bound=False,
                        status="ACTIVE",
                        first_party=True,
                    ),
                )
                print("OAuth client zoiko-operator created (Operator Console, http://localhost:3100)")
            await register_operator(
                c,
                tenant_id=OPERATOR_TENANT_ID,
                principal_id=principal,
                jurisdiction="GB",
                roles=frozenset({OperatorRole.REQUESTER, OperatorRole.APPROVER, OperatorRole.REVIEWER}),
                now=datetime.now(UTC),
            )
            from zid_platform.modules.authentication.infrastructure.authenticator_store import (
                issue_ticket,
            )

            ticket = await issue_ticket(
                c,
                tenant_id=OPERATOR_TENANT_ID,
                principal_id=principal,
                purpose="BOOTSTRAP",
                issued_by="bootstrap_local",
                expires_at=datetime.now(UTC) + timedelta(hours=23),
            )
    finally:
        await engine.dispose()
    print(f"operator {principal} registered in operator tenant {OPERATOR_TENANT_ID}")
    print(f"set ZID_OPERATOR_TENANT_ID={OPERATOR_TENANT_ID} in backend/.env and add http://localhost:3100 to ZID_WEBAUTHN_ORIGINS")
    print("Register the operator's passkey (then sign in at http://localhost:3100):")
    print(f"  http://localhost:3100/enroll?ticket={ticket}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a local cell for the web app.")
    parser.add_argument("--client-id", default="zoiko-web")
    parser.add_argument("--web-origin", default="http://localhost:3000")
    # Exact https or loopback-IP redirect (authn.oauth_client_redirects); the BFF reads the code
    # server-side, so the browser never needs to open it for passkey sign-in.
    parser.add_argument("--redirect-uri", default="http://127.0.0.1:3000/auth/callback")
    parser.add_argument("--ticket-only", action="store_true", help="only issue a new enrollment link")
    parser.add_argument("--operator", action="store_true", help="also seed the local operator plane (Phase F)")
    parser.add_argument("--operator-principal", default=OPERATOR_PRINCIPAL_ID)
    args = parser.parse_args()

    if not load_backend_env():
        print(f"{BACKEND_ENV} not found: copy backend/.env.example to backend/.env first.")
        return 1
    if os.environ.get("ZID_ENVIRONMENT", "local") not in ("local", "test"):
        print("bootstrap_local only runs with ZID_ENVIRONMENT=local or test.")
        return 1

    url = cell_db_url()
    if not args.ticket_only:
        ensure_dev_keys()
        from zid_platform.migrations import upgrade

        upgrade(url)
        print("migrations applied to the cell database")
        # the global plane (locator + discovery only, DEC-G3) lives in the same database unless told otherwise
        upgrade(os.environ.get("ZID_GLOBAL_DATABASE_URL") or url, role="global")
        print("migrations applied to the global plane")
    ticket = asyncio.run(seed(url, args))
    print()
    print("Register the first passkey (link valid 23 hours, single use):")
    print(f"  {args.web_origin}/enroll?ticket={ticket}")
    if args.operator:
        print()
        asyncio.run(seed_operator(url, args))
    return 0


if __name__ == "__main__":
    sys.exit(main())

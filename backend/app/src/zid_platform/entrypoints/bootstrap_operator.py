"""First operator account from configuration: `python -m zid_platform.entrypoints.bootstrap_operator [--ticket]`.

Creates, only where missing, what the Operator Console needs (DEC-F4), from the environment instead of
tools/scripts/bootstrap_local.py (which is local-only):

    ZID_OPERATOR_TENANT_ID               the dedicated operator tenant
    ZID_OPERATOR_BOOTSTRAP_PRINCIPAL_ID  uuid of the first operator's principal (generate it once and keep it)
    ZID_OPERATOR_ORIGIN                  public origin of the console, e.g. https://operator.example.com
    ZID_OPERATOR_REDIRECT_URIS           optional JSON list; default [<origin>/auth/callback]
    ZID_OPERATOR_JURISDICTION, ZID_OPERATOR_BOOTSTRAP_ROLES, ZID_OPERATOR_CLIENT_IDS (see settings.py)
    ZID_CELL_DATABASE_URL                the cell database (the runtime role is enough: it acts as the operator tenant)

It is idempotent and creates no passkey: with --ticket it issues one single-use, short-lived enrollment link and
prints it once (to the terminal of whoever runs it, never to a log pipeline). The link opens the console's own /enroll
page; the operator registers the passkey there and is signed in. It refuses a redirect that is not https or a loopback IP.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from zid_context import RequestContext
from zid_db import tenant_transaction

from zid_platform.modules.operator_access.domain.model import OperatorRole
from zid_platform.settings import Settings


class BootstrapError(Exception):
    pass


def redirect_uris(settings: Settings) -> tuple[str, ...]:
    origin = settings.operator_origin
    uris = settings.operator_redirect_uris or (
        (f"{origin.rstrip('/')}/auth/callback",) if origin else ()
    )
    if not uris:
        raise BootstrapError("set ZID_OPERATOR_ORIGIN (or ZID_OPERATOR_REDIRECT_URIS)")
    for uri in uris:
        parsed = urlparse(uri)
        loopback = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1")
        if not (parsed.scheme == "https" or loopback) or parsed.fragment or not parsed.hostname:
            raise BootstrapError(
                f"redirect {uri!r} must be https (or an http loopback IP) with no fragment"
            )
    return uris


def enrollment_base(settings: Settings) -> str:
    """The console's own origin: the ticket belongs to the operator tenant, so it is redeemed at /enroll on the console
    (client `zoiko-operator`), never on the customer app. Passkeys need a name, not an IP, so a loopback IP origin
    (the OAuth redirect form) becomes `localhost` for the link."""
    origin = (settings.operator_origin or "").rstrip("/")
    if not origin:
        raise BootstrapError("set ZID_OPERATOR_ORIGIN")
    parsed = urlparse(origin)
    if parsed.hostname in ("127.0.0.1", "::1"):
        origin = f"{parsed.scheme}://localhost" + (f":{parsed.port}" if parsed.port else "")
    if origin not in {o.rstrip("/") for o in settings.webauthn_origins}:
        raise BootstrapError(
            f"{origin} must be listed in ZID_WEBAUTHN_ORIGINS or the passkey ceremony will be refused"
        )
    return origin


async def require_platform_authorization(
    c: AsyncConnection,
    settings: Settings,
    tenant: str,
    principal: str,
    roles: frozenset[OperatorRole],
) -> None:
    """Registering an operator is a root or super-admin grant: a critical platform action (Decision 11). In a deployed environment every
    operator after the first needs an executed platform approval for exactly this account and these roles; the guard uses it up. The
    first operator is the bootstrap exception: nobody independent exists yet to approve it, so it is created from configuration by whoever
    controls the deployment, and the owners' pack records that. Local and test environments are not gated."""
    if settings.environment in ("local", "test"):
        return
    existing = (
        await c.execute(text("SELECT count(*) FROM platform.operator_profile"))
    ).scalar_one()
    if not existing:
        return
    from zid_platform.modules.tenant.application.approvals import consume_authorization
    from zid_platform.modules.tenant.domain.approvals import ApprovalError

    try:
        await consume_authorization(
            c,
            tenant,
            action_id="root-super-admin-or-global-break-glass-grant",
            target_id=f"principal:{principal}",
            payload={"principal_id": principal, "roles": sorted(r.value for r in roles)},
            consumer="bootstrap_operator",
        )
    except ApprovalError as e:
        raise BootstrapError(
            f"a platform approval is required to register another operator ({e.code})"
        ) from e


async def bootstrap(
    settings: Settings, database_url: str, *, issue_ticket_link: bool, now: datetime | None = None
) -> str | None:
    from zid_platform.modules.authentication.infrastructure.authenticator_store import issue_ticket
    from zid_platform.modules.operator_access.application.service import register_operator
    from zid_platform.modules.operator_access.infrastructure.store import load_profile
    from zid_platform.modules.principal.domain.principal import (
        Principal,
        PrincipalStatus,
        PrincipalType,
    )
    from zid_platform.modules.principal.infrastructure.principal_store import create_principal
    from zid_platform.modules.session.domain.oauth import OAuthClient
    from zid_platform.modules.session.infrastructure.oauth_store import create_client, load_client

    from zid_platform.modules.authentication.domain.operator_password import operator_principal_id

    tenant = settings.operator_tenant_id
    if not tenant or not (settings.operator_bootstrap_principal_id or settings.operator_email):
        raise BootstrapError(
            "set ZID_OPERATOR_TENANT_ID and ZID_OPERATOR_BOOTSTRAP_PRINCIPAL_ID (or ZID_OPERATOR_EMAIL)"
        )
    try:
        uuid.UUID(tenant)
        principal = operator_principal_id(
            tenant, settings.operator_email or "", settings.operator_bootstrap_principal_id
        )
    except ValueError as e:
        raise BootstrapError("the operator tenant and principal ids must be uuids") from e
    if not settings.operator_client_ids:
        raise BootstrapError("ZID_OPERATOR_CLIENT_IDS is empty")
    try:
        roles = frozenset(OperatorRole(r.upper()) for r in settings.operator_bootstrap_roles)
    except ValueError as e:
        raise BootstrapError(f"unknown operator role: {e}") from e
    uris = redirect_uris(settings)
    base = enrollment_base(settings) if issue_ticket_link else ""
    now = now or datetime.now(UTC)
    client_id = settings.operator_client_ids[0]
    ctx = RequestContext(
        "req_bootstrap", "cor_bootstrap", "bootstrap", settings.cell_id, tenant_id=tenant
    )
    engine = create_async_engine(database_url)
    ticket: str | None = None
    try:
        async with tenant_transaction(engine, ctx) as c:
            known = (
                await c.execute(
                    text("SELECT 1 FROM core.principal WHERE principal_id = :p"),
                    {"p": uuid.UUID(principal)},
                )
            ).first()
            if not known:
                await create_principal(
                    c,
                    Principal(
                        principal,
                        tenant,
                        PrincipalType.HUMAN,
                        PrincipalStatus.ACTIVE,
                        settings.cell_id,
                    ),
                )
                print(f"operator principal {principal} created")
            if await load_client(c, client_id) is None:
                await create_client(
                    c,
                    OAuthClient(
                        client_id=client_id,
                        tenant_id=tenant,
                        client_type="PUBLIC",
                        profile="STANDARD",
                        redirect_uris=uris,
                        grant_types=frozenset({"authorization_code", "refresh_token"}),
                        scopes=frozenset({"openid"}),
                        audiences=(settings.token_audience or settings.token_issuer or "",),
                        token_auth_method="none",
                        dpop_bound=False,
                        status="ACTIVE",
                        first_party=True,
                    ),
                )
                print(f"OAuth client {client_id} created, redirect {', '.join(uris)}")
            if await load_profile(c, principal) is None:
                await require_platform_authorization(c, settings, tenant, principal, roles)
                await register_operator(
                    c,
                    tenant_id=tenant,
                    principal_id=principal,
                    jurisdiction=settings.operator_jurisdiction,
                    roles=roles,
                    now=now,
                )
                print(
                    f"operator {principal} registered with roles {', '.join(sorted(r.value for r in roles))}"
                )
            else:
                print(f"operator {principal} already registered (roles and status unchanged)")
            if issue_ticket_link:
                ticket = await issue_ticket(
                    c,
                    tenant_id=tenant,
                    principal_id=principal,
                    purpose="BOOTSTRAP",
                    issued_by="bootstrap_operator",
                    expires_at=now + timedelta(minutes=settings.operator_bootstrap_ticket_minutes),
                )
    finally:
        await engine.dispose()
    return f"{base}/enroll?ticket={ticket}" if ticket else None


async def provision_from_settings(settings: Settings, database_url: str) -> bool:
    """The operator account from ZID_OPERATOR_EMAIL / ZID_OPERATOR_PASSWORD, for the migration step.

    Does nothing (False) unless the email, the password and the operator tenant are all configured, so a deployment
    that has not opted in is untouched. Idempotent like `bootstrap`; it issues no passkey link."""
    if not (settings.operator_email and settings.operator_password and settings.operator_tenant_id):
        return False
    await bootstrap(settings, database_url, issue_ticket_link=False)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the first operator account from configuration."
    )
    parser.add_argument(
        "--ticket", action="store_true", help="also issue a single-use passkey enrollment link"
    )
    args = parser.parse_args()
    settings = Settings()
    url = os.environ.get("ZID_CELL_DATABASE_URL") or os.environ.get("ZID_DATABASE_URL")
    if not url:
        print("set ZID_CELL_DATABASE_URL", file=sys.stderr)
        return 2
    try:
        link = asyncio.run(bootstrap(settings, url, issue_ticket_link=args.ticket))
    except BootstrapError as e:
        print(f"bootstrap_operator: {e}", file=sys.stderr)
        return 2
    if link:
        print(
            f"\nRegister the operator's passkey (single use, {settings.operator_bootstrap_ticket_minutes} minutes):"
        )
        print(f"  {link}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

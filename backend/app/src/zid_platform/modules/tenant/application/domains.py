"""Domain verification use cases (ZID-OVERLAY-001 §5, §24, §31; OVL-15; G-W2).

A challenge is created, published by the tenant, then checked: DNS TXT at `_zoiko-challenge.<domain>`
or the exact body at `https://<domain>/.well-known/zoiko-domain-verification` (TLS validated, no
redirects, bounded). Checks are injectable (`DomainChecks`) so tests and a sovereign cell's egress
proxy supply them. VERIFIED domains are re-checked every 30 days; lost control makes the
verification STALE and bumps the tenant's security revision; nothing is transferred.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_app.resilience import budget_timeout
from zid_outbox import emit

from zid_platform import egress
from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, commitment, record_audit
from zid_platform.modules.tenant.domain.domains import (
    CHALLENGE_TTL,
    REVERIFY_EVERY,
    TXT_PREFIX,
    WELL_KNOWN,
    DomainStatus,
    Method,
    body_proves,
    normalize_domain,
    txt_proves,
)
from zid_platform.modules.tenant.domain.lifecycle import TenantError, TenantStatus
from zid_platform.modules.tenant.infrastructure import domain_store as store
from zid_platform.modules.tenant.infrastructure import tenant_store
from zid_platform.modules.tenant.infrastructure.domain_store import Domain

TxtLookup = Callable[[str], Awaitable[list[str]]]
HttpsFetch = Callable[[str], Awaitable[str | None]]
_CLOSED = frozenset({TenantStatus.SUSPENDED, TenantStatus.OFFBOARDING, TenantStatus.CLOSED})


@dataclass(frozen=True, slots=True)
class DomainChecks:
    txt: TxtLookup
    https: HttpsFetch


async def dns_txt(name: str) -> list[str]:
    import dns.asyncresolver
    import dns.exception

    try:
        answer = await dns.asyncresolver.resolve(name, "TXT", lifetime=3.0)
    except (dns.exception.DNSException, OSError):
        return []
    return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]


def https_fetcher(transport: httpx.AsyncBaseTransport | None = None) -> HttpsFetch:
    async def fetch(url: str) -> str | None:
        try:  # SEC-19: a claimed domain may resolve anywhere; refuse non-public destinations
            await egress.guard(url, resolve=transport is None)
        except egress.EgressError:
            return None
        try:
            async with httpx.AsyncClient(
                transport=transport,
                timeout=budget_timeout(5.0),
                follow_redirects=False,
                verify=True,
            ) as c:
                r = await c.get(url)
        except httpx.HTTPError:
            return None
        if r.status_code != 200 or len(r.content) > 1024:
            return None
        return r.text

    return fetch


def default_checks(transport: httpx.AsyncBaseTransport | None = None) -> DomainChecks:
    return DomainChecks(dns_txt, https_fetcher(transport))


async def _record(
    conn: AsyncConnection, d: Domain, name: str, *, actor: str | None, outcome: str = "SUCCESS"
) -> None:
    data = {
        "domain_id": d.domain_id,
        "domain_commitment": commitment(d.domain, tenant_id=d.tenant_id, kind="domain"),
        "method": d.method.value,
        "status": d.status.value,
        "evidence_class": "E2",
    }
    event_id = await emit(
        conn,
        name=name,
        tenant_id=d.tenant_id,
        aggregate_type="tenant_domain",
        aggregate_id=d.domain_id,
        aggregate_version=int(datetime.now(UTC).timestamp() * 1000),
        data=data,
    )
    await record_audit(
        conn,
        d.tenant_id,
        AuditFacts(
            event_type=name,
            actor_type="HUMAN" if actor else "SERVICE",
            action=name.rsplit(".", 1)[-1],
            target_type="tenant_domain",
            target_ref=d.domain_id,
            outcome=outcome,
            evidence_class=EvidenceClass.E2,
            occurred_at=datetime.now(UTC),
            actor_principal_id=actor,
            source_service="tenant",
            source_event_id=event_id,
            details={k: v for k, v in data.items() if k != "evidence_class"},
        ),
    )


async def start(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    domain: str,
    method: Method,
    actor: str,
    now: datetime | None = None,
) -> Domain:
    now = now or datetime.now(UTC)
    tenant = await tenant_store.load_tenant(conn, tenant_id)
    if tenant is None:
        raise TenantError("TENANT_NOT_FOUND")
    if tenant.status in _CLOSED:
        raise TenantError("TENANT_STATE_INVALID", f"No domain claims while {tenant.status}.")
    d = await store.insert(
        conn,
        tenant_id=tenant_id,
        domain=normalize_domain(domain),
        method=method,
        token=secrets.token_urlsafe(32),
        created_by=actor,
        expires_at=now + CHALLENGE_TTL,
    )
    await _record(conn, d, "tenant.domain.changed", actor=actor)
    return d


async def _proves(d: Domain, checks: DomainChecks) -> bool:
    if d.method is Method.DNS_TXT:
        return txt_proves(await checks.txt(f"{TXT_PREFIX}.{d.domain}"), d.challenge_token)
    body = await checks.https(f"https://{d.domain}{WELL_KNOWN}")
    return body is not None and body_proves(body, d.challenge_token)


async def verify(
    conn: AsyncConnection,
    domain_id: str,
    *,
    checks: DomainChecks,
    actor: str,
    now: datetime | None = None,
) -> Domain:
    now = now or datetime.now(UTC)
    d = await store.load(conn, domain_id, lock=True)
    if d is None:
        raise TenantError("DOMAIN_NOT_FOUND")
    if d.status is DomainStatus.VERIFIED:
        return d
    if d.status is not DomainStatus.PENDING:
        raise TenantError("DOMAIN_VERIFICATION_FAILED", f"The challenge is {d.status}.")
    if now >= d.expires_at:
        d = await store.set_status(conn, domain_id, DomainStatus.EXPIRED, now=now, error="EXPIRED")
        await _record(conn, d, "tenant.domain.changed", actor=actor, outcome="FAILURE")
        raise TenantError("DOMAIN_VERIFICATION_FAILED", "The challenge expired; start a new one.")
    if not await _proves(d, checks):
        await store.set_status(conn, domain_id, DomainStatus.PENDING, now=now, error="NOT_PROVEN")
        raise TenantError(
            "DOMAIN_VERIFICATION_FAILED", "The challenge is not published (yet) or does not match."
        )
    d = await store.set_status(
        conn, domain_id, DomainStatus.VERIFIED, now=now, reverify_due_at=now + REVERIFY_EVERY
    )
    await tenant_store.bump_configuration(
        conn,
        d.tenant_id,
        change_kind="domain.verified",
        change_commitment=commitment(d.domain, tenant_id=d.tenant_id, kind="domain"),
        actor_principal_id=actor,
        approval_ref=None,
        now=now,
    )
    await _record(conn, d, "tenant.domain_verified", actor=actor)
    return d


async def revoke(conn: AsyncConnection, domain_id: str, *, actor: str) -> Domain:
    d = await store.load(conn, domain_id, lock=True)
    if d is None:
        raise TenantError("DOMAIN_NOT_FOUND")
    d = await store.set_status(conn, domain_id, DomainStatus.REVOKED, now=datetime.now(UTC))
    await _record(conn, d, "tenant.domain.changed", actor=actor)
    return d


async def reverify_due(
    conn: AsyncConnection, *, checks: DomainChecks, now: datetime
) -> dict[str, str]:
    """Worker pass (BYPASSRLS): re-prove VERIFIED domains; lost control -> STALE (OVERLAY §31)."""
    from sqlalchemy import text

    out: dict[str, str] = {}
    for d in await store.due_for_reverification(conn, now):
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": d.tenant_id})
        if await _proves(d, checks):
            await store.set_status(
                conn,
                d.domain_id,
                DomainStatus.VERIFIED,
                now=now,
                reverify_due_at=now + REVERIFY_EVERY,
            )
            out[d.domain_id] = "VERIFIED"
            continue
        stale = await store.set_status(conn, d.domain_id, DomainStatus.STALE, now=now, error="LOST")
        await tenant_store.bump_configuration(
            conn,
            d.tenant_id,
            change_kind="domain.stale",
            change_commitment=commitment(d.domain, tenant_id=d.tenant_id, kind="domain"),
            actor_principal_id=None,
            approval_ref=None,
            now=now,
            security=True,
        )
        await _record(conn, stale, "tenant.domain.stale", actor=None, outcome="FAILURE")
        out[d.domain_id] = "STALE"
    return out


__all__ = [
    "DomainChecks",
    "default_checks",
    "https_fetcher",
    "reverify_due",
    "revoke",
    "start",
    "verify",
]

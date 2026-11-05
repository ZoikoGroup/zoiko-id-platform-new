"""Background worker process: `python -m zid_platform.entrypoints.worker` (DEC-D5, DEC-D6).

Same image as the API, separate process. Each pass, in its own transaction per step:
- evidence: sequence receipts/audit records into the ledger, seal due checkpoints (E3 sooner),
  anchor them at the TSA when configured, dispose supporting evidence past retention (F-W1/F-W2);
- operator access: expire elapsed JIT grants and never-completed requests (F-W4);
- privacy: retention sweep of governed objects, rights-request deadline warnings (F-W3);
- registry: re-check registry authority roots before they lapse; officer cessation or dissolution
  revokes the root and cascades to its grants (G-W1);
- domains / connector_credentials: re-prove verified domains (lost control -> STALE) and raise
  30/14/7/1-day connector credential expiry alerts (G-W2);
- stale_connectors / offboarding: connectors past their freshness drop to DEGRADED; OFFBOARDING
  tenants are drained (integrations, sessions) and CLOSED (G-W5);
- directory: changed tenant records are signed and published to the global locator (G-W6);
- webhooks: due deliveries are sent, signed and retried (H-1);
- sandbox_identities: developer test identities past their expiry are retired;
- directory_sync: each connected company directory (Auth0 first) is read and its SCIM source brought in step with it;
- sanctions_lists / screening: lists with a verified address are fetched and loaded, and cases a list change or an expiry has
  made stale are screened again (ED-04).
A failing step is logged and retried on the next pass; it never stops the others.

    ZID_WORKER_DATABASE_URL  the worker role: BYPASSRLS, SELECT/INSERT/UPDATE on the evidence,
                             platform and privacy schemas, no DELETE (falls back to
                             ZID_DATABASE_URL in local/test only)
    ZID_WORKER_INTERVAL_S    seconds between passes (default 10)
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from zid_telemetry import configure_logging

from zid_platform.settings import Settings

log = logging.getLogger("zid.worker")
Step = Callable[[AsyncConnection, datetime], Awaitable[object]]


def steps(settings: Settings) -> list[tuple[str, Step]]:
    from zid_platform.modules.evidence.infrastructure.tsa import Rfc3161Client
    from zid_platform.modules.evidence.ports import worker_pass
    from zid_platform.modules.operator_access.ports import expire_due
    from zid_platform.modules.privacy.ports import privacy_pass

    tsa = Rfc3161Client(settings.evidence_tsa_url) if settings.evidence_tsa_url else None

    async def evidence(conn: AsyncConnection, now: datetime) -> object:
        return await worker_pass(conn, now=now, tsa=tsa)

    async def operator(conn: AsyncConnection, now: datetime) -> object:
        return await expire_due(conn, now)

    async def privacy(conn: AsyncConnection, now: datetime) -> object:
        return await privacy_pass(conn, now=now)

    async def registry(conn: AsyncConnection, now: datetime) -> object:
        from zid_platform.main import _secret_broker
        from zid_platform.modules.authority.ports import on_root_changed
        from zid_platform.modules.organization.ports import adapter_for, refresh_registry_roots

        broker = _secret_broker(settings)

        def key() -> str | None:
            try:
                return str(broker.get("companies-house-api-key")) if broker else None  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - the source then reports unavailable
                return None

        return await refresh_registry_roots(
            conn,
            adapter_for=lambda ns: adapter_for(ns, transport=None, companies_house_key=key),
            now=now,
            on_root_changed=on_root_changed,
        )

    async def domains(conn: AsyncConnection, now: datetime) -> object:
        from zid_platform.modules.tenant.application.domains import default_checks, reverify_due

        return await reverify_due(conn, checks=default_checks(), now=now)

    async def credentials(conn: AsyncConnection, now: datetime) -> object:
        from zid_platform.modules.integration.application.service import expiry_alerts

        return await expiry_alerts(conn, now)

    async def stale_connectors(conn: AsyncConnection, now: datetime) -> object:
        from zid_platform.modules.integration.application.service import stale_sweep

        return await stale_sweep(conn, now)

    async def sandbox_identities(conn: AsyncConnection, now: datetime) -> object:
        """Integration: retire the developer test identities whose time is up."""
        from zid_platform.modules.integration.application.sandbox import expire_due

        return await expire_due(conn, now)

    async def offboarding(conn: AsyncConnection, now: datetime) -> object:
        from zid_platform.modules.onboarding.ports import offboarding_pass

        return await offboarding_pass(conn, now)

    async def deletion(conn: AsyncConnection, now: datetime) -> object:
        from zid_platform.modules.onboarding.ports import deletion_pass

        return await deletion_pass(conn, now)

    async def directory(conn: AsyncConnection, now: datetime) -> object:
        """DEC-G3: publish changed tenant records to the global locator (skipped when unset)."""
        from zid_crypto import KeyRing

        from zid_platform.crypto_runtime import placement_signer
        from zid_platform.modules.tenant.application.directory import http_sender, publish_pass

        if not settings.global_locator_url:
            return "NOT_CONFIGURED"
        signer = placement_signer(
            settings,
            dev_purpose="federation",
            kid=f"directory-{settings.cell_id}",
            kms_key_id=settings.directory_kms_key_id,
        )
        if signer is None:
            return "NO_SIGNING_KEY"
        return await publish_pass(
            conn,
            cell_id=settings.cell_id,
            ring=KeyRing(signer),
            send=http_sender(settings.global_locator_url),
            now=now,
        )

    async def webhooks(conn: AsyncConnection, now: datetime) -> object:
        """EVENTS §14-20: deliver due webhook attempts (re-resolves DNS and re-checks every send)."""
        from zid_platform.crypto_runtime import placement_signer
        from zid_platform.modules.webhook.ports import deliver_due, http_client, system_resolver

        signer = placement_signer(
            settings,
            dev_purpose="set",
            kid=f"webhook-{settings.cell_id}",
            kms_key_id=settings.webhook_kms_key_id,
        )
        if signer is None:
            return "NO_SIGNING_KEY"
        async with http_client() as client:
            return await deliver_due(
                conn, client=client, resolve=system_resolver, sign=signer.sign, now=now
            )

    async def directory_sync(conn: AsyncConnection, now: datetime) -> object:
        """Provisioning: read each connected company directory (Auth0 first) and bring its SCIM source in step with it."""
        import httpx

        from zid_platform.modules.provisioning.ports import directory_sync_pass

        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
            return await directory_sync_pass(conn, now, http=http, home_cell=settings.cell_id)

    async def sanctions_lists(conn: AsyncConnection, now: datetime) -> object:
        """ED-04 section 07: fetch and load the sanctions lists that have a verified address."""
        from zid_platform.modules.screening.ports import ingest_pass, screening_config

        return await ingest_pass(conn, screening_config(), now)

    async def screening(conn: AsyncConnection, now: datetime) -> object:
        """ED-04 section 07: screen again every case a list change or an expiry has made stale."""
        from zid_platform.crypto_runtime import company_evidence_signer
        from zid_platform.modules.screening.ports import rescreen_pass, screening_config

        sign = company_evidence_signer(settings)
        if sign is None:
            return "NO_SIGNING_KEY"
        from zid_platform.modules.verification.ports import Capability, registry, require

        def gate(
            country: str,
        ) -> (
            None
        ):  # raises unless business verification is open for the country in this environment
            require(
                registry(),
                country,
                Capability.BUSINESS_VERIFICATION,
                now=now,
                environment=settings.environment,
            )

        return await rescreen_pass(conn, screening_config(), sign, now, country_gate=gate)

    async def register_watch(conn: AsyncConnection, now: datetime) -> object:
        """ED-04 section 07: read the official register again for every live case; a change asks for a rescreen (answered by `screening`)."""
        from zid_platform.main import _secret_broker
        from zid_platform.modules.onboarding.application.monitoring import register_watch_pass
        from zid_platform.modules.organization.ports import default_adapters
        from zid_platform.modules.verification.ports import Capability, registry, require

        def gate(country: str) -> None:
            require(
                registry(),
                country,
                Capability.BUSINESS_VERIFICATION,
                now=now,
                environment=settings.environment,
            )

        return await register_watch_pass(
            conn, default_adapters(_secret_broker(settings)), now, country_gate=gate
        )

    async def company_refresh(conn: AsyncConnection, now: datetime) -> object:
        """ED-03 section 08: read the sources again before a company verification lapses (country gate first)."""
        from zid_platform.crypto_runtime import company_evidence_signer
        from zid_platform.main import _secret_broker
        from zid_platform.modules.organization.application.passes import refresh_pass
        from zid_platform.modules.organization.ports import default_adapters, kyb_routing
        from zid_platform.modules.verification.ports import Capability, registry, require

        sign = company_evidence_signer(settings)
        if sign is None:
            return "NO_SIGNING_KEY"

        def gate(country: str) -> None:
            require(
                registry(),
                country,
                Capability.BUSINESS_VERIFICATION,
                now=now,
                environment=settings.environment,
            )

        return await refresh_pass(
            conn,
            default_adapters(_secret_broker(settings)),
            kyb_routing(),
            sign,
            now,
            country_gate=gate,
        )

    async def verification_reconcile(conn: AsyncConnection, now: datetime) -> object:
        """ZID-EDR-005: ask the provider for a decision whose webhook is late, and apply it exactly as the signed callback would be."""
        from zid_platform.main import _secret_broker
        from zid_platform.modules.verification.ports import reconcile_delayed

        broker = _secret_broker(settings)

        def secrets(name: str) -> str | None:
            if broker is None:
                return None
            try:
                return str(broker.get(name))  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - a missing secret is "not available", never a crash or a pass
                return None

        return await reconcile_delayed(conn, now, secrets=secrets)

    async def claim_housekeeping(conn: AsyncConnection, now: datetime) -> object:
        """WF-13B: close the claims nobody submitted in time (abandoned), so no half-made claim watches people for ever."""
        from zid_platform.modules.onboarding.application.claims import abandon_drafts

        return await abandon_drafts(conn, now)

    async def claim_outcomes(conn: AsyncConnection, now: datetime) -> object:
        """WF-13B: follow every open claim and announce, once, whatever changed since the last pass (a ruling, a refresh, a lapse)."""
        from zid_platform.modules.onboarding.application.claims import sync_outcomes

        return await sync_outcomes(conn, now)

    async def approval_reminders(conn: AsyncConnection, now: datetime) -> object:
        """WF-013C: remind, once, about approval requests that are about to expire (an in-app alert)."""
        from zid_platform.modules.tenant.application.reminders import remind_due

        return await remind_due(conn, now)

    async def refunds(conn: AsyncConnection, now: datetime) -> object:
        """ZID-UX-COM-009 F03-A: send each approved refund once under its own idempotency key, then ask the processor what it holds for the ones still open."""
        from zid_platform.main import build_refund_port
        from zid_platform.modules.commercial.ports import refunds as refund_service

        port = build_refund_port(settings)
        if port is None:
            return "no processor configured"
        return {
            "sent": await refund_service.send_pending(conn, port, now),
            "verified": await refund_service.verify_open(conn, port, now),
        }

    return [
        ("evidence", evidence),
        ("operator_access", operator),
        ("privacy", privacy),
        ("registry", registry),
        ("domains", domains),
        ("connector_credentials", credentials),
        ("stale_connectors", stale_connectors),
        ("sandbox_identities", sandbox_identities),
        ("offboarding", offboarding),
        ("deletion", deletion),
        ("directory", directory),
        ("webhooks", webhooks),
        ("directory_sync", directory_sync),
        ("sanctions_lists", sanctions_lists),
        ("register_watch", register_watch),
        ("screening", screening),
        ("company_refresh", company_refresh),
        ("verification_reconcile", verification_reconcile),
        ("claim_housekeeping", claim_housekeeping),
        ("claim_outcomes", claim_outcomes),
        ("approval_reminders", approval_reminders),
        ("refunds", refunds),
    ]


async def run_pass(
    engine: AsyncEngine, settings: Settings, now: datetime | None = None
) -> dict[str, str]:
    """One pass over every step; returns step -> "ok" | error code (for logs and tests)."""
    now = now or datetime.now(UTC)
    out: dict[str, str] = {}
    for name, step in steps(settings):
        try:
            async with engine.begin() as conn:
                await step(conn, now)
            out[name] = "ok"
        except Exception as e:
            out[name] = getattr(e, "code", type(e).__name__)
            log.exception("worker step %s failed", name)
    return out


def database_url(settings: Settings) -> str:
    url = os.environ.get("ZID_WORKER_DATABASE_URL")
    if url:
        return url
    if settings.environment not in ("local", "test") or not settings.database_url:
        raise RuntimeError("ZID_WORKER_DATABASE_URL (the BYPASSRLS worker role) is required")
    return settings.database_url


async def main_loop(settings: Settings, *, passes: int | None = None) -> None:
    from zid_platform.main import register_privacy_stores
    from zid_platform.modules.decision.ports import register_evidence_replayer
    from zid_platform.modules.evidence.ports import configure_runtime, runtime_from_settings
    from zid_platform.protected import catalog

    configure_runtime(runtime_from_settings(settings))
    register_evidence_replayer(catalog())
    register_privacy_stores(
        settings.registration_tenant_id
    )  # the retention sweep and every erasure dispose through the owning modules
    engine = create_async_engine(database_url(settings), pool_size=2, max_overflow=0)
    interval = float(os.environ.get("ZID_WORKER_INTERVAL_S", "10"))
    try:
        n = 0
        while passes is None or n < passes:
            await run_pass(engine, settings)
            n += 1
            if passes is None or n < passes:
                await asyncio.sleep(interval)
    finally:
        await engine.dispose()


def main() -> None:
    settings = Settings()
    configure_logging("zid-worker", settings.cell_id)
    asyncio.run(main_loop(settings))


if __name__ == "__main__":
    main()

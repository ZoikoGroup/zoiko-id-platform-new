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
  made stale are screened again (ED-04);
- access_readiness: queued access readiness runs are preflighted again and executed, a bounded number per pass (CR-091; off by default).
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
    from zid_platform import api_agents_switch

    api_agents_switch.configure(settings.api_agents, settings.environment)  # API agents ship OFF in production (CR-144)
    from zid_platform.modules.evidence.infrastructure.tsa import Rfc3161Client
    from zid_platform.modules.evidence.infrastructure.tsa_token import (
        timestamp_verifier_from_settings,
    )
    from zid_platform.modules.evidence.ports import worker_pass
    from zid_platform.modules.operator_access.ports import expire_due
    from zid_platform.modules.privacy.ports import privacy_pass

    tsa = None
    if settings.evidence_tsa_url:
        verifier = timestamp_verifier_from_settings(settings)
        if verifier is None:
            raise RuntimeError(
                "ZID_EVIDENCE_TSA_URL is set without ZID_EVIDENCE_TSA_TRUST_FILE: a timestamp is anchored only after it is verified"
            )
        tsa = Rfc3161Client(settings.evidence_tsa_url, verifier=verifier)

    async def evidence(conn: AsyncConnection, now: datetime) -> object:
        return await worker_pass(
            conn,
            now=now,
            tsa=tsa,
            require_timestamp=settings.evidence_tsa_required,
            verify_interval=settings.evidence_verify_interval,
            verify_batch=settings.evidence_verify_batch,
        )

    async def key_events(conn: AsyncConnection, now: datetime) -> object:
        """CR-060: copy the software key store's audit trail (create, rotate, retire, destroy, re-wrap) into the evidence ledger."""
        from zid_platform.key_audit import ledger_key_events

        return await ledger_key_events(conn)

    async def operator(conn: AsyncConnection, now: datetime) -> object:
        return await expire_due(conn, now)

    async def privacy(conn: AsyncConnection, now: datetime) -> object:
        from zid_platform.modules.privacy.ports import retry_failed_erasures

        return {
            **await privacy_pass(conn, now=now),
            "erasure_retry": await retry_failed_erasures(conn, now),
        }

    async def biometric_reauthorization(conn: AsyncConnection, now: datetime) -> object:
        """WF-04 BIO-20 (CR-110): warn before a biometric DPIA lapses; hold a model whose evaluation went stale."""
        from zid_platform.modules.privacy.ports import reauthorization_pass

        return await reauthorization_pass(conn, now=now)

    async def erasure_retry(conn: AsyncConnection, now: datetime) -> object:
        """WF-04 QA-023 (CR-113): ask a provider again for an overdue or failed deletion; alert once when the attempts run out."""
        from zid_platform.modules.privacy.ports import erasure_retry_pass

        return await erasure_retry_pass(conn, now=now)

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

    async def identity_signal_feed(conn: AsyncConnection, now: datetime) -> object:
        """Identity signals: turn new device and contact observations into review items (DTV-20, DTV-21, DTV-22)."""
        from zid_platform.modules.verification.ports import identity_feed_pass

        return await identity_feed_pass(conn, now)

    async def verification_reverification(conn: AsyncConnection, now: datetime) -> object:
        """Verification: expire what has lapsed, mark stale what rests on a distrusted provider, remind before the rest lapses (DTV-29)."""
        from zid_platform.modules.verification.ports import reverification_sweep

        return await reverification_sweep(conn, now)

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

    async def master_lists(conn: AsyncConnection, now: datetime) -> object:
        """WF01 (CR-114): fetch published CSCA master lists and stage what changed. Staging only: people validate, approve and activate (CR-107)."""
        from zid_platform.modules.evidence.ports import master_list_refresh_pass
        from zid_platform.modules.operator_access.infrastructure.store import in_tenant

        return await master_list_refresh_pass(conn, now, in_tenant=in_tenant)

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

        from zid_platform.modules.screening.ports import (
            MonitoringKind,
            configure_monitoring_scope,
            require_monitoring,
        )

        configure_monitoring_scope(settings.monitoring_scope)  # the worker is its own process

        def monitoring(
            country: str,
        ) -> None:  # CR-081: screening again over time is ongoing monitoring, only where approved
            require_monitoring(country=country, kind=MonitoringKind.SANCTIONS_ONGOING, at=now)

        return await rescreen_pass(
            conn, screening_config(), sign, now, country_gate=gate, monitoring_gate=monitoring
        )

    async def screening_watches(conn: AsyncConnection, now: datetime) -> object:
        """WF06 S12: screen again every active watch whose cadence has run out, where ongoing monitoring is approved for its market."""
        from zid_platform.modules.organization.ports import company_claim
        from zid_platform.modules.screening.ports import (
            MonitoringKind,
            market_packs,
            require_monitoring,
            screening_config,
            watch_pass,
        )
        from zid_platform.modules.verification.ports import current_identity_claim

        async def identity(
            c: AsyncConnection, tenant: str, subject_type: object, subject_ref: str, at: datetime
        ) -> object:
            if getattr(subject_type, "value", "") == "BUSINESS":
                return await company_claim(c, tenant) if subject_ref == tenant else None
            return await current_identity_claim(c, subject_ref, at)

        def monitoring(market: str) -> None:
            require_monitoring(country=market, kind=MonitoringKind.SANCTIONS_ONGOING, at=now)

        from zid_platform.modules.screening.ports import gates

        async def released(c: AsyncConnection, market: str) -> bool:
            return await gates.is_go(c, market, "MONITORING", now=now)

        return await watch_pass(
            conn,
            screening_config(),
            now,
            packs=market_packs(),
            identity_lookup=identity,
            monitoring_gate=monitoring,
            gate_check=released,
        )

    async def screening_edd(conn: AsyncConnection, now: datetime) -> object:
        """WF06 EDD information requests: record reminders that fell due and expire what ran out. Expiry clears nothing (CR-121)."""
        from zid_platform.modules.screening.ports import edd

        return await edd.tick_all(conn, now)

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

    async def company_grade_recheck(conn: AsyncConnection, now: datetime) -> object:
        """WF02 K10: reassess each company whose next check is due, from what is recorded (the company refresh above has just read the registers again)."""
        from zid_platform.modules.organization.application.kyb_operations import recheck_pass

        return await recheck_pass(conn, now)

    async def register_refresh(conn: AsyncConnection, now: datetime) -> object:
        """ZID-RCG-003 RCG-G02 (CR-087): ask official registers again before a connector-recorded certification goes stale."""
        from zid_platform.modules.verification.ports import register_refresh as rr

        return await rr.refresh_due(conn, environment=settings.environment, now=now)

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

    async def verification_policy_review(conn: AsyncConnection, now: datetime) -> object:
        """T40: a pending verification session whose region's policy pack changed, or whose provider was suspended, is re-evaluated and its organization told."""
        from zid_platform.modules.verification.ports import reevaluate_pending

        return await reevaluate_pending(conn, now, mode=settings.regulatory_packs)

    async def claim_housekeeping(conn: AsyncConnection, now: datetime) -> object:
        """WF-13B: close the claims nobody submitted in time (abandoned), so no half-made claim watches people for ever."""
        from zid_platform.modules.onboarding.application.claims import abandon_drafts

        return await abandon_drafts(conn, now)

    async def claim_outcomes(conn: AsyncConnection, now: datetime) -> object:
        """WF-13B: follow every open claim and announce, once, whatever changed since the last pass (a ruling, a refresh, a lapse)."""
        from zid_platform.modules.onboarding.application.claims import sync_outcomes

        return await sync_outcomes(conn, now)

    async def claim_autoverify(conn: AsyncConnection, now: datetime) -> object:
        """LOCAL/TEST ONLY: with ZID_ORG_CLAIM_DEV_AUTOVERIFY, create the organization as soon as a claim's identity check is complete (no page, no button)."""
        from zid_platform.modules.onboarding.application import claims
        from zid_platform.modules.onboarding.application.organization import auto_establish_pass
        from zid_platform.protected import catalog

        claims.configure_dev_shortcut(settings.org_claim_dev_autoverify, settings.environment)
        return await auto_establish_pass(conn, now, catalog=catalog())

    async def owner_grant_top_up(conn: AsyncConnection, now: datetime) -> object:
        """CR-137: every company's owner holds every customer-side action, new ones in the catalogue included."""
        from zid_platform.modules.onboarding.application.organization import owner_grant_top_up_pass
        from zid_platform.protected import catalog

        return await owner_grant_top_up_pass(conn, now, catalog=catalog())

    async def approval_reminders(conn: AsyncConnection, now: datetime) -> object:
        """WF-013C: remind, once, about approval requests that are about to expire (an in-app alert)."""
        from zid_platform.modules.tenant.application.reminders import remind_due

        return await remind_due(conn, now)

    async def signal_housekeeping(conn: AsyncConnection, now: datetime) -> object:
        """CR-068: drop attempt rows no velocity window can still count (signals and their review items are never pruned)."""
        from zid_platform.modules.screening.ports import (
            SIGNAL_EVENT_CHANNEL,
            prune_velocity,
            retry_failed_notifications,
        )

        return {
            "pruned": await prune_velocity(conn, now),
            "retried": await retry_failed_notifications(conn, SIGNAL_EVENT_CHANNEL, now),
        }

    async def agent_lifecycle(conn: AsyncConnection, now: datetime) -> object:
        """WP-10: suspend agents whose sponsor has left or whose expiry has passed (with their sub-agents and bindings)."""
        from zid_platform.modules.machine.ports import lifecycle_sweep

        return await lifecycle_sweep(conn, now)

    async def access_readiness(conn: AsyncConnection, now: datetime) -> object:
        """CR-091 (ZID-AG-WF-001 sections 04 and 11): take the oldest queued access readiness runs, each preflighted again under its own
        tenant and in its own savepoint. Off (nothing read) unless ZID_ACCESS_READINESS_ENABLED; no model gateway in the worker."""
        if not settings.access_readiness_enabled:
            return "off"
        from zid_platform.modules.access_readiness.ports import Runtime, drain_queued

        return await drain_queued(
            conn,
            Runtime(
                enabled=True,
                regions=frozenset(settings.access_readiness_regions),
                region=settings.cell_id,
                cell_uuid=settings.cell_uuid,
                ai_explain=settings.access_readiness_ai_explain,
                run_role=settings.agent_run_role,
            ),
        )

    async def agent_governance(conn: AsyncConnection, now: datetime) -> object:
        """ZID-AG-WF-002: free allowances a crashed run left reserved, end runs past their deadline, expire unsigned change requests."""
        from zid_platform.modules.machine.ports import configure_agent_governance, governance_sweep

        configure_agent_governance(
            settings.agent_governance, settings.commercial_enforcement
        )  # the worker is its own process
        return await governance_sweep(conn, now)

    async def daara_queue(conn: AsyncConnection, now: datetime) -> object:
        """ZID-AG-WF-001: run the queued access readiness runs, each re-checked when it is claimed."""
        from zid_platform.modules.machine.ports import configure_agent_governance, daara_work_queue

        configure_agent_governance(settings.agent_governance, settings.commercial_enforcement)
        return await daara_work_queue(conn, now, run_role=settings.agent_run_role)

    async def daara_schedule(conn: AsyncConnection, now: datetime) -> object:
        """ZID-AG-WF-001: queue the daily run of each agent whose policy and company baseline allow a schedule (none, until an owner allows it)."""
        from zid_platform.modules.machine.ports import (
            configure_agent_governance,
            daara_schedule_due,
        )

        configure_agent_governance(settings.agent_governance, settings.commercial_enforcement)
        return await daara_schedule_due(conn, now)

    async def credential_recheck(conn: AsyncConnection, now: datetime) -> object:
        """GID-058: announce, once a day each, the current credential checks that expire soon or have gone stale."""
        from zid_platform.modules.credential.ports import recheck_due, review_proofs_of_lost_issuers

        # T42: proofs under an issuer or accreditation body that lost its status are flagged and their recheck made due first,
        # so the announcement that follows carries the reason
        reviewed = await review_proofs_of_lost_issuers(conn, now)
        return {"reviewed": reviewed, "announced": await recheck_due(conn, now)}

    async def fairness(conn: AsyncConnection, now: datetime) -> object:
        """T44: hold, for a person, the automated adverse name-match answers of a cohort with a material disparity (or of every cohort until the owners approve a threshold). Off by default."""
        if settings.fairness_check != "on":
            return "off"
        from zid_platform.modules.credential.ports import check_disparity

        return await check_disparity(conn, now)

    async def evidence_purge(conn: AsyncConnection, now: datetime) -> object:
        """CR-071: erase the bytes of uploaded evidence whose retention has run out (the row stays as the record)."""
        from zid_platform.modules.evidence.ports import purge_expired_uploads

        return {"purged": await purge_expired_uploads(conn, now)}  # type: ignore[no-untyped-call]

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

    async def analytics_retention(conn: AsyncConnection, now: datetime) -> object:
        """WF-02 section 09: Command Center facts are kept only for the retention period, never indefinitely."""
        from datetime import timedelta

        from zid_platform.modules.verify_analytics.ports import purge_expired

        return {"purged": await purge_expired(conn, before=now - timedelta(days=settings.verify_analytics_retention_days))}

    return [
        ("evidence", evidence),
        ("key_events", key_events),
        ("operator_access", operator),
        ("privacy", privacy),
        ("biometric_reauthorization", biometric_reauthorization),
        ("erasure_retry", erasure_retry),
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
        ("master_lists", master_lists),
        ("sanctions_lists", sanctions_lists),
        ("register_watch", register_watch),
        ("screening", screening),
        ("screening_watches", screening_watches),
        ("screening_edd", screening_edd),
        ("company_refresh", company_refresh),
        ("company_grade_recheck", company_grade_recheck),
        ("register_refresh", register_refresh),
        ("verification_reconcile", verification_reconcile),
        ("verification_policy_review", verification_policy_review),
        ("verification_reverification", verification_reverification),
        ("identity_signal_feed", identity_signal_feed),
        ("claim_housekeeping", claim_housekeeping),
        ("claim_outcomes", claim_outcomes),
        ("claim_autoverify", claim_autoverify),
        ("owner_grant_top_up", owner_grant_top_up),
        ("approval_reminders", approval_reminders),
        ("agent_lifecycle", agent_lifecycle),
        (
            "access_readiness",
            access_readiness,
        ),  # after the lifecycle sweep, so a run sees an agent it has just suspended
        ("agent_governance", agent_governance),
        *(
            (("daara_schedule", daara_schedule), ("daara_queue", daara_queue))
            if settings.daara_enabled
            else ()
        ),
        ("signal_housekeeping", signal_housekeeping),
        ("credential_recheck", credential_recheck),
        ("fairness", fairness),
        ("evidence_purge", evidence_purge),
        ("analytics_retention", analytics_retention),
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
            from zid_platform.modules.verification.ports import country_opening_store

            await country_opening_store.refresh(engine)  # the countries an operator has opened (CR-127), for checks the worker runs
            await run_pass(engine, settings)
            n += 1
            if passes is None or n < passes:
                await asyncio.sleep(interval)
    finally:
        await engine.dispose()


def main() -> None:
    settings = Settings()
    configure_logging("zid-worker", settings.cell_id)
    from zid_platform.metrics_server import start_from_environment

    start_from_environment()  # ZID_METRICS_PORT: scrape endpoint for the evidence gauges below (off unless set)
    asyncio.run(main_loop(settings))


if __name__ == "__main__":
    main()

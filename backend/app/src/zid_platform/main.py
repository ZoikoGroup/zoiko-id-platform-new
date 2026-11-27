"""App factory for the Zoiko iD modular monolith (DEC-D6).

Composes the shared service shell (zid-app) with the modules of the configured role.
Module routers are mounted here as each module's api/ layer is implemented; modules talk to
each other only through their `ports.py` interfaces.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request
from zid_app import Journey, ServiceSpec, create_service_app
from zid_crypto import KeyRing

from .keys import KeyRings
from .modules.session.ports import ServerSettings, TokenSettings
from .roles import Role, modules_for, schemas_for
from .settings import Settings


def create_app(
    settings: Settings | None = None,
    *,
    keyring: KeyRing | None = None,
    keyrings: KeyRings | None = None,
) -> FastAPI:
    """`keyrings`: signing keys by purpose (KMS/HSM-backed in deployed environments, DEC-F5);
    a single `keyring` serves every purpose (tests). Locally, with ZID_TOKEN_ISSUER set and no keys
    given, development keys are created per purpose (or loaded from ZID_DEV_SIGNING_KEYS); any
    other environment refuses to start without real keys rather than minting its own."""
    settings = settings or Settings()
    role = Role(settings.role)
    from .modules.operations.ports import catalogue_journeys, service_catalogue

    ops_catalogue = service_catalogue(settings.ops_catalogue_path)
    # one process, one port: a cell may also serve the global plane (locator + discovery) from the same database
    with_global = role is Role.CELL and settings.serve_global_plane
    spec = ServiceSpec(
        name=f"zoiko-id-{role.value}",
        plane="global" if role is Role.GLOBAL else "cell",
        modules=modules_for(role, with_global=with_global),
        required_schemas=schemas_for(role, with_global=with_global),
        journeys=[
            Journey(n, prefix, latency) for n, prefix, latency in catalogue_journeys(ops_catalogue)
        ],
    )
    app = create_service_app(spec, settings)
    _install_crypto_degradation(app)
    app.state.ops_catalogue = ops_catalogue
    app.state.cell_uuid = settings.cell_uuid
    app.state.public_base_url = settings.public_base_url
    app.state.token_audience = settings.token_audience
    app.state.tenant_deletion = _tenant_deletion()  # operator data deletion (TEN-25)
    app.state.mtls_cert_header = settings.mtls_client_cert_header
    app.state.home_cell = settings.cell_id
    app.state.tenant_bootstrap_tenant_id = settings.tenant_bootstrap_tenant_id
    app.state.tenant_console = (
        (
            tuple(settings.tenant_console_redirect_uris),
            (settings.token_audience,),
            tuple(settings.tenant_console_scopes),
        )
        if settings.tenant_console_redirect_uris
        else None
    )
    if settings.tenant_signup_dev_verify and settings.environment not in ("local", "test"):
        raise RuntimeError("ZID_TENANT_SIGNUP_DEV_VERIFY is for local/test only")
    if settings.org_claim_dev_autoverify and settings.environment not in ("local", "test"):
        raise RuntimeError("ZID_ORG_CLAIM_DEV_AUTOVERIFY is for local/test only")
    from .modules.onboarding.application import claims as _claims

    _claims.configure_dev_shortcut(settings.org_claim_dev_autoverify, settings.environment)
    if settings.dev_simulators and settings.environment not in ("local", "test"):
        raise RuntimeError("ZID_DEV_SIMULATORS is for local/test only")
    app.state.operator_password_logins = _operator_password_logins(settings)
    app.state.operator_origins = _operator_origins(settings)
    app.state.registry_simulator = False
    app.state.registry_credential_override = None
    app.state.dev_simulator = None
    app.state.tenant_signup = settings.tenant_signup_enabled
    app.state.tenant_signup_dev_verify = settings.tenant_signup_dev_verify
    from .modules.onboarding.ports import require_owner_approval_ack

    require_owner_approval_ack(
        settings.onboarding_approval, settings.environment, settings.onboarding_owner_ack
    )
    app.state.onboarding_approval = settings.onboarding_approval
    app.state.tenant_signup_notice_version = settings.tenant_signup_notice_version
    app.state.registration_tenant = settings.registration_tenant_id
    app.state.email_config = None
    app.state.email_sender = None
    if settings.registration_tenant_id:
        from .modules.communication.ports import EmailConfig, build_sender
        from .modules.onboarding.application.registration import invitation_hook
        from .modules.tenant.ports import register_invitation_hook

        register_invitation_hook("registration", invitation_hook(settings.registration_tenant_id))
        sender = build_sender(
            settings
        )  # None when email is off; fails fast when it is on but not fully configured
        if sender is not None:
            app.state.email_config = EmailConfig.from_settings(settings)
            app.state.email_sender = sender
    from .invitation_mail import send_invitation_email
    from .modules.tenant.ports import configure_invitation_mailer

    configure_invitation_mailer(
        send_invitation_email
    )  # the tenant module emails an invitation through this, never by importing communication
    app.state.environment = settings.environment
    # WF-01: where the hosted verification page is served (the web app), for the launch links a business hands to a person
    app.state.hosted_verify_base = (
        settings.email_public_base_url or settings.public_base_url or ""
    ).rstrip("/")
    app.state.hosted_verify_languages = list(settings.hosted_verify_languages)
    # WF-02 Command Center policy (D-01): smallest group a figure may describe, freshness limit; the cell a customer's incidents are filtered by
    app.state.trust_security_contact = settings.trust_security_contact
    app.state.trust_security_expires = settings.trust_security_expires
    app.state.trust_security_policy_url = settings.trust_security_policy_url
    app.state.trust_security_encryption_url = settings.trust_security_encryption_url
    app.state.trust_security_acknowledgments_url = settings.trust_security_acknowledgments_url
    app.state.trust_report_salt = settings.trust_report_salt
    app.state.trust_withdrawal_review_hours = settings.trust_withdrawal_review_hours
    app.state.trust_room_terms_version = settings.trust_room_terms_version
    app.state.trust_report_tenant = settings.operator_tenant_id
    app.state.verify_analytics_min_group = settings.verify_analytics_min_group
    app.state.verify_analytics_stale_minutes = settings.verify_analytics_stale_minutes
    app.state.cell_id = settings.cell_id
    from datetime import timedelta as _td

    from .modules.privacy.ports import applicant_session as _applicant_session

    _applicant_session.configure_lifetimes(  # the owners' D09 values, from settings
        launch=_td(minutes=settings.hosted_verify_launch_minutes), handoff=_td(minutes=settings.hosted_verify_handoff_minutes),
        access=_td(minutes=settings.hosted_verify_access_minutes), session=_td(hours=settings.hosted_verify_session_hours),
    )  # fmt: skip
    app.state.evidence_tsa_configured = bool(settings.evidence_tsa_url)  # WF-05 trust-list view
    if settings.agent_builds == "off" and settings.environment not in ("local", "test", "dev"):
        raise RuntimeError(
            "ZID_AGENT_BUILDS=off would let any typed digest back a blueprint release; outside local, test and dev "
            "builds must be approved (set it to auto or enforce)"
        )
    app.state.agent_builds = settings.agent_builds
    app.state.daara_enabled = settings.daara_enabled
    app.state.self_run_enabled = settings.self_run_enabled
    app.state.access_readiness_enabled = settings.access_readiness_enabled
    app.state.access_readiness_regions = tuple(settings.access_readiness_regions)
    app.state.access_readiness_ai_explain = settings.access_readiness_ai_explain
    # no model gateway ships in the platform image (CR-091): a deployment that approves one sets app.state.access_readiness_explainer
    app.state.access_readiness_explainer = None
    # the usage and budget hook (CR-091, section 08): None is the Q1 hook with no thresholds; the Q2 limits are not set. Any hook handed in
    # here is asked before a run and each step, and an error, a timeout or any answer but ALLOW denies with BUDGET_POLICY_DENY.
    app.state.access_readiness_usage_policy = None
    if settings.regulatory_packs == "off" and settings.environment not in ("local", "test", "dev"):
        raise RuntimeError(
            "ZID_REGULATORY_PACKS=off would let an enabled country run a regulated capability with no approved policy pack; "
            "outside local, test and dev use shadow or enforce"
        )
    app.state.regulatory_packs = settings.regulatory_packs
    if settings.market_releases == "off" and settings.environment not in ("local", "test", "dev"):
        raise RuntimeError(
            "ZID_MARKET_RELEASES=off would let an enabled country run a regulated capability with no market release; "
            "outside local, test and dev use shadow or enforce"
        )
    app.state.market_releases = settings.market_releases
    if settings.provider_entitlements == "off" and settings.environment not in (
        "local",
        "test",
        "dev",
    ):
        raise RuntimeError(
            "ZID_PROVIDER_ENTITLEMENTS=off would let an enabled country call a provider Zoiko may not be entitled to use; "
            "outside local, test and dev use shadow or enforce"
        )
    app.state.provider_entitlements = settings.provider_entitlements
    if settings.privacy_overlay == "off" and settings.environment not in ("local", "test", "dev"):
        raise RuntimeError(
            "ZID_PRIVACY_OVERLAY=off would let an enabled country route identity data without its approved privacy overlay; "
            "outside local, test and dev use shadow or enforce"
        )
    app.state.privacy_overlay = settings.privacy_overlay
    if settings.eudi_wallet == "off" and settings.environment not in ("local", "test", "dev"):
        raise RuntimeError(
            "ZID_EUDI_WALLET=off would let Zoiko ask an EUDI Wallet for attributes without its relying-party registration; "
            "outside local, test and dev use shadow or enforce"
        )
    from .modules.credential.application import eudi_gate

    eudi_gate.configure(settings.eudi_wallet)
    app.state.eudi_wallet = settings.eudi_wallet
    if settings.monitoring_scope == "off" and settings.environment not in ("local", "test", "dev"):
        raise RuntimeError(
            "ZID_MONITORING_SCOPE=off would let ongoing monitoring run in markets with no legal and commercial approval; "
            "outside local, test and dev use shadow or enforce"
        )
    from .modules.commercial.ports import credential_usage_sink, screening_usage_sink
    from .modules.credential.ports import configure_usage_sink as configure_credential_usage_sink
    from .modules.screening.ports import configure_monitoring_scope, configure_usage_sink

    configure_monitoring_scope(settings.monitoring_scope)
    configure_usage_sink(
        screening_usage_sink
    )  # usage facts for the screening meters (CR-104); screening itself never imports commercial
    from .modules.organization.application.kyb_company import (
        configure_sources as configure_kyb_sources,
    )
    from .modules.screening.ports import case_for_kyb
    from .modules.verification.ports import person_assurance_current

    async def _screening_status(conn, tenant_id, case_id, now):  # type: ignore[no-untyped-def]
        found = await case_for_kyb(conn, tenant_id, case_id, now=now)
        return None if found is None else found[0].status.value

    configure_kyb_sources(
        person_assurance_current, _screening_status
    )  # the company grade reads identity and screening; organization imports neither (the composition root wires them)
    from .modules.organization.ports import company_claim as _company_claim
    from .modules.screening.api.requests import configure_sources as configure_screening_sources
    from .modules.tenant.ports import Role as _TenantRole
    from .modules.tenant.ports import live_roles as _live_roles
    from .modules.verification.ports import current_identity_claim as _person_claim

    async def _screening_admin(conn, principal_id):  # type: ignore[no-untyped-def]
        return any(
            a.role in (_TenantRole.OWNER, _TenantRole.ADMIN)
            for a in await _live_roles(conn, principal_id)
        )

    async def _screening_claim(conn, tenant_id, subject_type, subject_ref, now):  # type: ignore[no-untyped-def]
        if getattr(subject_type, "value", "") == "BUSINESS":
            return await _company_claim(conn, tenant_id) if subject_ref == tenant_id else None
        try:
            import uuid as _uuid

            _uuid.UUID(subject_ref)
        except ValueError:
            return None
        return await _person_claim(conn, subject_ref, now)

    from .modules.verification.ports import partner_capture as _partner_capture
    from .modules.verify_api.application import live_service as _verify_live
    from .modules.verify_api.ports import go_live_service as _verify_go_live

    _verify_live.configure(
        lambda: _partner_capture.services_from_state(app.state)
    )  # a production session is made only where the owners have published the face route and acknowledged its limits

    async def _go_live_company_claim(conn, tenant_id):  # type: ignore[no-untyped-def]
        """The company check go-live reads: a registry match (K2 or higher) as before, or the company's own claim whose COMPANY check is complete, which includes a certificate of
        incorporation whose reading agrees with the claim (CR-132, the owner's decision of 20 Nov 2026: that completes the company check without a reviewer). The label says which.
        Anything else, a claim that is not complete, one on the development shortcut, or any error, is "not verified": it fails closed."""
        registry = await _company_claim(conn, tenant_id)
        if registry is not None:
            return registry
        try:
            from datetime import UTC as _UTC
            from datetime import datetime as _datetime

            from .modules.onboarding.application import claims as _claims
            from .modules.organization.infrastructure.kyb_store import (
                latest_case_id as _latest_case,
            )

            case_id = await _latest_case(conn, tenant_id)
            if case_id is None:
                return None
            now = _datetime.now(_UTC)
            seen = await _claims.status(conn, tenant_id, case_id, now)
            if seen["checks"].get("company") != "COMPLETED" or seen.get("development_shortcut"):
                return None
            label = (
                "COMPANY_CERTIFICATE_MATCHED"
                if seen.get("company_basis") == "DOCUMENT_OCR"
                else "COMPANY_REGISTRY_MATCHED"
            )
            return label, now
        except Exception:  # noqa: BLE001 - fail closed
            return None

    _verify_go_live.configure_company_source(
        _go_live_company_claim
    )  # production for a Verify API partner needs the company's own completed company check; the verify_api module imports no organization code

    async def _go_live_person_ok(conn, principal):  # type: ignore[no-untyped-def]
        """The signed-in person's own identity check is complete and current (self-serve go-live)."""
        from datetime import UTC as _UTC
        from datetime import datetime as _datetime

        try:
            return bool(await person_assurance_current(conn, principal, _datetime.now(_UTC)))
        except Exception:  # noqa: BLE001 - fail closed: a doubt is "not met"
            return False

    async def _go_live_plan_ok(conn):  # type: ignore[no-untyped-def]
        """The company holds a paid plan (self-serve go-live)."""
        from .modules.commercial.ports import has_paid_plan as _has_paid_plan

        try:
            return await _has_paid_plan(conn)
        except Exception:  # noqa: BLE001 - fail closed
            return False

    _verify_go_live.configure_self_serve_sources(
        _go_live_person_ok, _go_live_plan_ok
    )  # production opens by itself when the person's identity, the company and a paid plan are all complete; the verify_api module imports none of those modules
    configure_screening_sources(
        _screening_admin, _screening_claim
    )  # screening asks who may request it and which identity claim a subject rests on; it imports neither the tenant, verification nor organization module
    configure_credential_usage_sink(
        credential_usage_sink
    )  # usage facts for the credential check meters (CR-070); the credential module never imports commercial
    from .modules.commercial.ports import idv_usage_sink
    from .modules.verification.ports import configure_idv_usage_sink

    configure_idv_usage_sink(
        idv_usage_sink
    )  # one IDV_SUBMISSION per submitted hosted session (WF-01 Q33); verification never imports commercial
    app.state.monitoring_scope = settings.monitoring_scope
    if settings.agent_governance == "off" and settings.environment not in ("local", "test", "dev"):
        raise RuntimeError(
            "ZID_AGENT_GOVERNANCE=off would let agents call tools with no policy, approval, quota or budget check; "
            "outside local, test and dev use shadow or enforce"
        )
    from . import api_agents_switch
    from .modules.machine.ports import configure_agent_governance

    api_agents_switch.configure(
        settings.api_agents, settings.environment
    )  # API agents ship OFF in production (CR-144)

    configure_agent_governance(settings.agent_governance, settings.commercial_enforcement)
    app.state.agent_governance = settings.agent_governance
    app.state.build_registries = tuple(h.lower() for h in settings.build_registries)
    app.state.build_registries_private = tuple(h.lower() for h in settings.build_registries_private)
    app.state.build_images = tuple(i.strip() for i in settings.build_images if i.strip())
    app.state.registry_transport = None  # tests supply a simulated registry
    app.state.registry_resolver = None
    if (
        settings.mesh_trusted_proxies
        and not settings.mesh_proxy_secret_file
        and settings.environment not in ("local", "test", "dev")
    ):
        raise RuntimeError(
            "ZID_MESH_TRUSTED_PROXIES names a proxy whose forwarded certificate is believed; outside local, test and dev it must also "
            "prove itself: set ZID_MESH_PROXY_SECRET_FILE"
        )
    app.state.proof_scanner_address = settings.proof_scanner_address
    app.state.proof_scanner = None  # tests supply their own scanner
    app.state.federation_authority_hints = settings.federation_authority_hints
    app.state.verification_transport = None  # real network; tests inject a provider simulator
    app.state.credential_transport = None  # status-list / federation fetches (simulated in tests)
    from .modules.evidence.application.scanner import SimulatorScanner, scanner_from_address

    app.state.upload_retention_days = settings.upload_retention_days
    # the real clamd when its address is set (the same one proof documents use); the EICAR-only simulator for local and test; otherwise
    # NONE, and uploads are refused: a deployed cell never pretends to scan
    app.state.evidence_scanner = (
        scanner_from_address(settings.proof_scanner_address)
        if settings.proof_scanner_address
        else SimulatorScanner()
        if settings.environment in ("local", "test")
        else None
    )
    app.state.registry_transport = None  # Companies House / GLEIF (simulated in tests)
    app.state.kyb_transport = None  # company-verification sources (real network; tests inject)
    app.state.kyb_adapters = None  # tests supply their own sources
    from .crypto_runtime import company_evidence_signer

    app.state.company_evidence_sign = company_evidence_signer(settings)
    from .crypto_runtime import verify_go_live_signer

    app.state.verify_go_live_sign = verify_go_live_signer(settings)
    app.state.review_agent_enabled = settings.review_agent_enabled
    app.state.mandate_signing = (
        settings.mandate_signing if settings.mandate_signing in ("off", "enforce") else "off"
    )
    app.state.review_adviser = (
        None  # no model is installed here; a deployment that has an approved one sets it
    )
    from .modules.verify_api.application import edge_cert as _edge_cert

    app.state.partner_mtls = _edge_cert.config_from_settings(
        settings
    )  # client-certificate sign-in works only with trusted edge proxies AND their shared secret; otherwise it is off
    app.state.domain_checks = None  # DNS TXT / HTTPS well-known (real network; tests inject)
    app.state.secret_broker = _secret_broker(settings)
    app.state.token_settings = None
    app.state.oauth_settings = None
    app.state.webauthn_settings = None
    app.state.federation_settings = None
    if settings.webauthn_rp_id:
        from .modules.authentication.application.webauthn import WebAuthnSettings

        app.state.webauthn_settings = WebAuthnSettings(
            settings.webauthn_rp_id,
            settings.webauthn_rp_name,
            tuple(settings.webauthn_origins),
            related_origins=tuple(settings.webauthn_related_origins),
        )
    app.state.keyrings = None
    app.state.verifier_x5c = ()
    if settings.dev_signing_keys and settings.environment not in ("local", "test"):
        raise RuntimeError("ZID_DEV_SIGNING_KEYS is for local/test only (DEC-F5)")
    if settings.token_issuer:
        if keyrings is None and keyring is not None:
            keyrings = KeyRings.single(keyring)
        if keyrings is None and (
            settings.environment not in ("local", "test") or settings.key_provider == "db"
        ):
            if settings.key_provider == "local":
                raise RuntimeError(
                    "token issuer keys must come from the key broker outside local/test (DEC-F5)"
                )
            from .crypto_runtime import kms_keyrings_from_settings

            keyrings = kms_keyrings_from_settings(settings)
        if keyrings is None:
            keyrings = KeyRings.development(settings.cell_id, settings.dev_signing_keys)
        app.state.keyrings = keyrings
        app.state.verifier_x5c = (
            keyrings.verifier_x5c(
                settings.verifier_certificate_chain,
                development=settings.environment in ("local", "test"),
            )
            if settings.verifier_client_id_prefix == "x509_hash"
            else ()
        )
        keyring = keyrings.token
        app.state.token_settings = TokenSettings(
            settings.token_issuer, settings.token_audience, keyring
        )
        app.state.oauth_settings = ServerSettings(
            settings.token_issuer,
            keyring,
            settings.token_audience,
            consent_url=settings.consent_url,
        )
        from .modules.federation.application.oidc_login import FederationSettings, env_secrets

        app.state.federation_settings = FederationSettings(
            issuer=settings.token_issuer,
            keyring=keyring,
            home_cell=settings.cell_id,
            # Local/test read brokered secrets from the environment; deployed cells get the
            # secrets broker (DEC-F5). Without one, only private_key_jwt federation works.
            secrets=env_secrets()
            if settings.environment in ("local", "test")
            else env_secrets("ZID_UNAVAILABLE__"),
        )
    app.state.cell_endpoints = dict(settings.cell_endpoints)
    from .tls_guard import install_tls_group_guard

    install_tls_group_guard(app, settings)
    app.state.key_provider = settings.key_provider
    _configure_metering(app, settings)
    _configure_commercial(app, settings)
    from .crypto_runtime import placement_signer

    app.state.webhook_signer = placement_signer(
        settings,
        dev_purpose="set",
        kid=f"webhook-{settings.cell_id}",
        kms_key_id=settings.webhook_kms_key_id,
    )
    from datetime import UTC, datetime

    from .modules.webhook.domain.webhooks import build_signing_keys

    app.state.webhook_keys = (
        build_signing_keys(
            app.state.webhook_signer.public_jwk(),
            settings.webhook_retiring_keys,
            now=datetime.now(UTC),
        )
        if app.state.webhook_signer is not None
        else None
    )
    app.state.webhook_allow_http = settings.webhook_allow_http and settings.environment in (
        "local",
        "test",
    )
    app.state.webhook_resolver = None  # real DNS; tests inject
    app.state.webhook_client = None  # real network; tests inject
    if settings.dev_simulators and role is Role.CELL:
        _configure_simulators(app)
        if settings.smtp_host:  # WF-05 codes to the local mail sink; no real sender ships
            from .modules.verification.infrastructure.dev_code_sink import (
                DevChallengeSender,
                DevPostalSender,
            )

            app.state.contact_challenge_sender = DevChallengeSender(
                settings.smtp_host, settings.smtp_port
            )
            app.state.postal_sender = DevPostalSender(settings.smtp_host, settings.smtp_port)
    if role is Role.GLOBAL or with_global:
        _configure_global_plane(app, settings)
    if role is Role.CELL:
        _configure_phase_f(app, settings)
        from .modules.authentication.ports import route_step_up_guard
        from .modules.tenant.ports import route_guard

        matrix_guard, step_up_guard = route_guard(), route_step_up_guard()

        async def route_guards(request: Any, caller: Any) -> None:
            await matrix_guard(request, caller)  # the role matrix on every mapped route (L-7)
            await step_up_guard(
                request, caller
            )  # fresh strong authentication on every listed route (L-9)

        app.state.route_guard = route_guards
        from .modules.onboarding.ports import organization_state

        app.state.organization_state = (
            organization_state  # read by verification (it cannot import onboarding)
        )
        from .modules.access_readiness.api.readiness import router as access_readiness_router
        from .modules.assurance.api.v2 import router as assurance_v2_router
        from .modules.authentication.api.recovery import router as recovery_router
        from .modules.authentication.api.step_up import router as step_up_router
        from .modules.authentication.api.webauthn import router as webauthn_router
        from .modules.authority.api.access_requests import router as access_requests_router
        from .modules.authority.api.approvals import router as approvals_router
        from .modules.authority.api.grants import router as grants_router
        from .modules.authority.api.templates import router as delegation_templates_router
        from .modules.commercial.api.activations import router as activations_router
        from .modules.commercial.api.billing_workspace import router as billing_workspace_router
        from .modules.commercial.api.commercial import router as commercial_router
        from .modules.commercial.api.service_requests import router as service_requests_router
        from .modules.commercial.api.stripe_webhook import router as stripe_webhook_router
        from .modules.communication.api.communication import router as communication_router
        from .modules.configuration.api.settings import router as settings_router
        from .modules.credential.api.claim_checks import router as claim_checks_router
        from .modules.credential.api.credentials import router as credentials_router
        from .modules.credential.api.regulatory_issuers import (
            router as regulatory_issuers_router,
        )
        from .modules.credential.api.trust import router as credential_trust_router
        from .modules.credential.api.v2 import router as credential_v2_router
        from .modules.credential.api.v2_authority import router as credential_v2_authority_router
        from .modules.credential.api.v2_cases import router as credential_v2_cases_router
        from .modules.credential.api.v2_edc import router as credential_v2_edc_router
        from .modules.credential.api.v2_identity import router as credential_v2_identity_router
        from .modules.credential.api.v2_issuer import router as credential_v2_issuer_router
        from .modules.credential.api.v2_presentations import router as credential_v2_pres_router
        from .modules.decision.api.authzen import router as authzen_router
        from .modules.decision.api.insights import router as insights_router
        from .modules.evidence.api.evidence import router as evidence_router
        from .modules.evidence.api.public_proof import router as evidence_public_proof_router
        from .modules.evidence.api.v2 import router as evidence_v2_router
        from .modules.evidence.api.v2_documents import router as evidence_v2_documents_router
        from .modules.evidence.api.v2_extract import router as evidence_v2_extract_router
        from .modules.evidence.api.v2_holds import router as privacy_holds_v2_router
        from .modules.evidence.api.v2_trust_store import router as evidence_v2_trust_store_router
        from .modules.evidence.api.v2_uploads import router as evidence_v2_uploads_router
        from .modules.evidence.api.wf08 import public_router as evidence_status_public_router
        from .modules.evidence.api.wf08 import router as evidence_wf08_router
        from .modules.federation.api.federation import router as federation_router
        from .modules.integration.api.integration import router as integration_router
        from .modules.machine.api.agent_credentials import router as agent_credentials_router
        from .modules.machine.api.agents import router as agents_router
        from .modules.machine.api.api_agents import router as api_agents_router
        from .modules.machine.api.daara import router as agent_daara_router
        from .modules.machine.api.governance import router as agent_governance_router
        from .modules.machine.api.idp import router as idp_router
        from .modules.machine.api.mcp import metadata_router as mcp_metadata_router
        from .modules.machine.api.mcp import router as mcp_router
        from .modules.machine.api.revocation import router as revocation_router
        from .modules.machine.api.tool_actions import router as tool_actions_router
        from .modules.machine.api.tool_calls import router as tool_calls_router
        from .modules.machine.api.workloads import router as workloads_router
        from .modules.mandate.api.mandates import router as mandates_router
        from .modules.metering.api.usage import router as usage_router
        from .modules.notification.api.alerts import router as alerts_router
        from .modules.onboarding.api.approval_attachments import (
            router as approval_attachments_router,
        )
        from .modules.onboarding.api.onboarding import router as onboarding_router
        from .modules.onboarding.api.org_claim_proofs import router as org_claim_proofs_router
        from .modules.onboarding.api.org_claim_screening import router as org_claim_screening_router
        from .modules.onboarding.api.org_claims import router as org_claims_router
        from .modules.onboarding.api.readiness import router as readiness_router
        from .modules.onboarding.api.registration import router as registration_router
        from .modules.onboarding.api.signup import router as signup_router
        from .modules.onboarding.api.v2_organizations import router as v2_organizations_router
        from .modules.operations.api.ops import router as operations_router
        from .modules.operator_access.api.commercial_admin import router as commercial_admin_router
        from .modules.operator_access.api.commercial_finance import (
            router as commercial_finance_router,
        )
        from .modules.operator_access.api.commercial_payment_ops import (
            router as commercial_payment_ops_router,
        )
        from .modules.operator_access.api.country_openings import (
            router as country_openings_router,
        )
        from .modules.operator_access.api.face_lab import router as face_lab_router
        from .modules.operator_access.api.kyb_policy import router as kyb_policy_router
        from .modules.operator_access.api.lockbox import router as lockbox_router
        from .modules.operator_access.api.operator import router as operator_router
        from .modules.operator_access.api.regulatory import router as regulatory_router
        from .modules.operator_access.api.regulatory_biometric_methods import (
            router as regulatory_biometric_methods_router,
        )
        from .modules.operator_access.api.regulatory_check_methods import (
            router as regulatory_check_methods_router,
        )
        from .modules.operator_access.api.regulatory_conformance import (
            router as regulatory_conformance_router,
        )
        from .modules.operator_access.api.regulatory_releases import (
            router as regulatory_releases_router,
        )
        from .modules.operator_access.api.regulatory_suspensions import (
            router as regulatory_suspensions_router,
        )
        from .modules.operator_access.api.regulatory_v2 import router as regulatory_v2_router
        from .modules.operator_access.api.screening_cases import router as screening_cases_router
        from .modules.operator_access.api.screening_exports import (
            router as screening_exports_router,
        )
        from .modules.operator_access.api.screening_gates import router as screening_gates_router
        from .modules.operator_access.api.screening_providers import (
            router as screening_providers_router,
        )
        from .modules.operator_access.api.screening_sources import (
            router as screening_sources_router,
        )
        from .modules.operator_access.api.screening_watches import (
            router as screening_watches_router,
        )
        from .modules.organization.api.kyb_company import router as kyb_company_router
        from .modules.organization.api.ownership import router as v2_ownership_router
        from .modules.policy_authoring.api.policies import router as policy_router
        from .modules.principal.api.principals import router as principals_router
        from .modules.privacy.api.privacy import router as privacy_router
        from .modules.privacy.api.v2 import router as privacy_v2_router
        from .modules.privacy.api.v2_policy import router as privacy_policy_v2_router
        from .modules.privacy.api.v2_rights import router as privacy_rights_v2_router
        from .modules.privacy.api.v2_sessions import router as privacy_sessions_v2_router
        from .modules.provisioning.api.directory import router as directory_sync_router
        from .modules.provisioning.api.scim import router as scim_router
        from .modules.resilience.api.platform import router as resilience_router
        from .modules.screening.api.provider_events import (
            router as screening_provider_events_router,
        )
        from .modules.screening.api.requests import router as screening_requests_router
        from .modules.screening.api.signals import router as screening_signals_router
        from .modules.search.api.search import router as search_router
        from .modules.session.api.oauth import router as oauth_router
        from .modules.session.api.sessions import router as sessions_router
        from .modules.signals.api.signals import router as signals_router
        from .modules.tenant.api.approvals import router as approval_cases_router
        from .modules.tenant.api.billing_access import router as billing_access_router
        from .modules.tenant.api.commitments import router as commitments_router
        from .modules.tenant.api.custom_roles import router as custom_roles_router
        from .modules.tenant.api.domains import router as domains_router
        from .modules.tenant.api.org_approvals import router as org_approvals_router
        from .modules.tenant.api.org_units import router as org_units_router
        from .modules.tenant.api.residency import router as residency_router
        from .modules.tenant.api.tenants import router as tenants_router
        from .modules.trust_center.api.public import router as trust_center_public_router
        from .modules.trust_center.api.register import router as trust_center_router
        from .modules.trust_center.api.room import router as trust_center_room_router
        from .modules.verification.api.address_checks import router as address_checks_router
        from .modules.verification.api.applicant import hosted_gate
        from .modules.verification.api.applicant import router as applicant_router
        from .modules.verification.api.biometric_methods import (
            router as biometric_methods_router,
        )
        from .modules.verification.api.check_cases import router as check_cases_router
        from .modules.verification.api.check_methods import router as check_methods_router
        from .modules.verification.api.check_policies import router as check_policies_router
        from .modules.verification.api.contact_challenges import (
            router as contact_challenges_router,
        )
        from .modules.verification.api.countries import router as countries_router
        from .modules.verification.api.device_checks import router as device_checks_router
        from .modules.verification.api.idv_cases import router as idv_cases_router
        from .modules.verification.api.offerings import router as offerings_router
        from .modules.verification.api.routing import router as verification_routing_router
        from .modules.verification.api.tiers import router as verification_tiers_router
        from .modules.verification.api.v2_age import router as v2_age_router
        from .modules.verification.api.v2_alternatives import router as v2_alternatives_router
        from .modules.verification.api.v2_providers import router as v2_providers_router
        from .modules.verification.api.v2_self_run import router as v2_self_run_router
        from .modules.verification.api.v2_source_of_funds import router as v2_sof_router
        from .modules.verification.api.v2_travel_rule import router as v2_travel_rule_router
        from .modules.verification.api.v2_workflows import router as v2_workflows_router
        from .modules.verification.api.verification import router as verification_router
        from .modules.verify_analytics.api.analytics import router as verify_analytics_router
        from .modules.verify_api.api.approvals import router as verify_api_approvals_router
        from .modules.verify_api.api.cases import router as verify_api_cases_router
        from .modules.verify_api.api.console_sessions import router as verify_api_console_router
        from .modules.verify_api.api.fraud import router as verify_api_fraud_router
        from .modules.verify_api.api.go_live import router as verify_api_go_live_router
        from .modules.verify_api.api.logos import router as verify_api_logos_router
        from .modules.verify_api.api.operator_admin import router as verify_api_admin_router
        from .modules.verify_api.api.results import router as verify_api_results_router
        from .modules.verify_api.api.review import router as verify_api_review_router
        from .modules.verify_api.api.sessions import router as verify_api_sessions_router
        from .modules.verify_api.api.usage import router as verify_api_usage_router
        from .modules.verify_api.api.verify_api import router as verify_api_router
        from .modules.webhook.api.v2 import router as webhooks_v2_router
        from .modules.webhook.api.webhooks import router as webhooks_router

        for router in (
            authzen_router,
            grants_router,
            mandates_router,
            approvals_router,
            oauth_router,
            sessions_router,
            webauthn_router,
            step_up_router,
            signals_router,
            federation_router,
            scim_router,
            directory_sync_router,
            verification_router,
            verification_tiers_router,
            countries_router,
            offerings_router,
            recovery_router,
            credentials_router,
            claim_checks_router,
            credential_trust_router,
            credential_v2_router,
            credential_v2_issuer_router,
            credential_v2_pres_router,
            credential_v2_cases_router,
            credential_v2_edc_router,
            credential_v2_authority_router,
            credential_v2_identity_router,
            evidence_v2_extract_router,
            principals_router,
            evidence_public_proof_router,
            evidence_status_public_router,
            evidence_wf08_router,
            evidence_router,
            evidence_v2_router,
            evidence_v2_documents_router,
            evidence_v2_uploads_router,
            evidence_v2_trust_store_router,
            operator_router,
            lockbox_router,
            privacy_router,
            residency_router,
            approval_cases_router,
            tenants_router,
            org_approvals_router,
            approval_attachments_router,
            billing_access_router,
            custom_roles_router,
            service_requests_router,
            activations_router,
            org_units_router,
            insights_router,
            delegation_templates_router,
            access_requests_router,
            commitments_router,
            onboarding_router,
            org_claims_router,
            v2_organizations_router,
            v2_age_router,
            v2_sof_router,
            v2_travel_rule_router,
            v2_workflows_router,
            v2_self_run_router,
            idv_cases_router,
            applicant_router,
            verify_analytics_router,
            trust_center_router,
            trust_center_public_router,
            trust_center_room_router,
            biometric_methods_router,
            check_methods_router,
            check_cases_router,
            contact_challenges_router,
            address_checks_router,
            device_checks_router,
            check_policies_router,
            v2_ownership_router,
            kyb_company_router,
            v2_alternatives_router,
            v2_providers_router,
            verification_routing_router,
            privacy_v2_router,
            privacy_rights_v2_router,
            privacy_policy_v2_router,
            privacy_sessions_v2_router,
            privacy_holds_v2_router,
            assurance_v2_router,
            org_claim_proofs_router,
            org_claim_screening_router,
            signup_router,
            registration_router,
            readiness_router,
            domains_router,
            integration_router,
            resilience_router,
            operations_router,
            policy_router,
            workloads_router,
            webhooks_router,
            webhooks_v2_router,
            alerts_router,
            search_router,
            agents_router,
            api_agents_router,
            agent_governance_router,
            agent_daara_router,
            access_readiness_router,
            tool_calls_router,
            tool_actions_router,
            revocation_router,
            idp_router,
            mcp_router,
            mcp_metadata_router,
            agent_credentials_router,
            screening_signals_router,
            screening_requests_router,
            screening_provider_events_router,
            settings_router,
            usage_router,
            commercial_router,
            billing_workspace_router,
            stripe_webhook_router,
            verify_api_router,
            verify_api_console_router,
            verify_api_go_live_router,
            verify_api_usage_router,
            verify_api_results_router,
            verify_api_cases_router,
            verify_api_review_router,
            verify_api_fraud_router,
            verify_api_logos_router,
            verify_api_approvals_router,
            verify_api_admin_router,
            verify_api_sessions_router,
            commercial_admin_router,
            commercial_finance_router,
            commercial_payment_ops_router,
            regulatory_router,
            regulatory_releases_router,
            country_openings_router,
            face_lab_router,
            regulatory_issuers_router,
            regulatory_suspensions_router,
            kyb_policy_router,
            screening_sources_router,
            screening_providers_router,
            screening_exports_router,
            screening_gates_router,
            screening_watches_router,
            screening_cases_router,
            regulatory_biometric_methods_router,
            regulatory_check_methods_router,
            regulatory_conformance_router,
            regulatory_v2_router,
            communication_router,
        ):
            app.include_router(router)
        # WF-01: a hosted session passes the verification module's market and return-address gate; privacy never imports verification for it
        from .modules.privacy.ports import applicant_session

        applicant_session.configure_hosted_gate(hosted_gate)
        if app.state.operator_password_logins:
            from .modules.authentication.api.operator_password import (
                router as operator_password_router,
            )

            app.include_router(operator_password_router)
    return app


def _operator_password_logins(settings: Settings) -> dict[str, dict[str, str]]:
    """The Operator Console's password logins, checked at start-up: a deployed cell takes hashes only."""
    import json
    import logging
    import uuid

    from .modules.authentication.domain.operator_password import (
        MIN_LENGTH,
        hash_password,
        operator_principal_id,
        valid_hash,
    )

    raw = dict(settings.operator_password_logins)
    if settings.operator_email and settings.operator_password:
        if not settings.operator_tenant_id:
            raise RuntimeError("ZID_OPERATOR_EMAIL needs ZID_OPERATOR_TENANT_ID")
        if len(settings.operator_password) < MIN_LENGTH:
            logging.getLogger("zid.operator").warning(
                "ZID_OPERATOR_PASSWORD is shorter than %d characters; use a longer one", MIN_LENGTH
            )
        raw[settings.operator_email] = {
            "principal_id": operator_principal_id(
                settings.operator_tenant_id,
                settings.operator_email,
                settings.operator_bootstrap_principal_id,
            ),
            "password_hash": hash_password(settings.operator_password, min_length=1),
        }
    if settings.operator_logins_file:
        with open(settings.operator_logins_file, encoding="utf-8") as f:
            raw.update(json.load(f))
    if raw and not settings.operator_tenant_id:
        raise RuntimeError("operator password logins need ZID_OPERATOR_TENANT_ID")
    local = settings.environment in ("local", "test")
    logins: dict[str, dict[str, str]] = {}
    for email, entry in raw.items():
        key = email.strip().lower()
        try:
            uuid.UUID(entry["principal_id"])
        except (KeyError, ValueError) as e:
            raise RuntimeError(f"operator login {key}: principal_id must be a uuid") from e
        stored = entry.get("password_hash")
        if stored is None and local and entry.get("password"):
            stored = hash_password(
                entry["password"], min_length=1
            )  # local convenience; never a deployed cell
        if stored is None or not valid_hash(stored):
            raise RuntimeError(
                f"operator login {key}: a scrypt password_hash is required (entrypoints/hash_operator_password.py)"
            )
        logins[key] = {"principal_id": entry["principal_id"], "password_hash": stored}
    return logins


def _operator_origins(settings: Settings) -> frozenset[str]:
    """The Operator Console's own origins (the configured origin and redirect origins, a loopback IP also as localhost)."""
    from urllib.parse import urlparse

    found: set[str] = set()
    for url in (settings.operator_origin, *settings.operator_redirect_uris):
        parsed = urlparse(url or "")
        if not parsed.scheme or not parsed.hostname:
            continue
        port = f":{parsed.port}" if parsed.port else ""
        found.add(f"{parsed.scheme}://{parsed.hostname}{port}")
        if parsed.hostname in ("127.0.0.1", "::1"):
            found.add(f"{parsed.scheme}://localhost{port}")
    return frozenset(found)


def _configure_commercial(app: FastAPI, settings: Settings) -> None:
    from .modules.commercial.ports import plan_throughput_status

    if settings.environment not in ("local", "test") and plan_throughput_status() != "CONFIRMED":
        import logging

        logging.getLogger("zid.commercial").warning(
            "plan request allowances are an ENGINEERING DEFAULT, not signed off by Commercial and Finance (config/entitlements/plan-throughput.yaml); "
            "do not quote them to a customer"
        )
    """Plan enforcement mode and the usage counters behind each capped capability (CR-024).

    The counters are wired here, at the composition root, so the commercial module never imports the modules
    whose rows it counts."""
    from .modules.commercial.ports import configure_ledger, require_feature
    from .modules.federation.ports import count_active_connections
    from .modules.provisioning.ports import count_active_sources

    configure_ledger(environment=settings.environment, region=settings.cell_id)
    app.state.commercial_enforcement = settings.commercial_enforcement
    app.state.activation_payment = settings.activation_payment
    app.state.step_up_enforcement = settings.step_up_enforcement
    from . import approval_wiring  # noqa: F401  (registers approval executors that need other modules)

    app.state.platform_tenant_id = (
        settings.operator_tenant_id
    )  # where Zoiko iD operations approve platform actions
    # The session module sits below authentication and cannot import it, so the guard is handed in here (ZID-ENG-DEC-009).
    from .modules.authentication.ports import require_step_up as _step_up_guard

    app.state.step_up_guard = _step_up_guard
    from .modules.commercial.ports import NoTaxConfigured, RecordingPayments

    # No tax engine and no payment processor are chosen yet (CR-027): tax answers 'not configured' and
    # local/test cells use a recording processor; a deployed cell refuses to collect until one is wired.
    app.state.commercial_issuing_entity = settings.commercial_issuing_entity
    app.state.commercial_region = settings.cell_id
    app.state.tax_port = NoTaxConfigured()
    app.state.payment_port = (
        RecordingPayments() if settings.environment in ("local", "test") else None
    )
    app.state.screening_velocity_mode = settings.screening_velocity_mode
    app.state.screening_signal_pepper = b""
    if settings.screening_signal_pepper_file:
        from pathlib import Path as _PepperPath

        pepper = _PepperPath(settings.screening_signal_pepper_file).read_bytes().strip()
        if not pepper:
            raise RuntimeError("ZID_SCREENING_SIGNAL_PEPPER_FILE is empty")
        app.state.screening_signal_pepper = pepper
    if settings.stripe_secret_key_file or settings.stripe_secret_key:
        from pathlib import Path

        from .modules.commercial.ports import StripePayments, StripeTax

        # The processor and the tax engine (CR-027): the key is read once from its file and never logged.
        if settings.stripe_secret_key_file:
            stripe_key = Path(settings.stripe_secret_key_file).read_text(encoding="utf-8").strip()
        else:
            stripe_key = (settings.stripe_secret_key or "").strip()
        if not stripe_key:
            raise RuntimeError("the Stripe secret key is empty")
        app.state.tax_port = StripeTax(stripe_key, base_url=settings.stripe_api_base)
        app.state.payment_port = StripePayments(stripe_key, base_url=settings.stripe_api_base)
        from urllib.parse import urlparse

        from .modules.commercial.ports import StripeCustomers

        # The saved-card page (CR-034): Stripe's hosted form returns the buyer to the console's own origin, taken from
        # the configuration and never from the request.
        app.state.payment_setup = StripeCustomers(stripe_key, base_url=settings.stripe_api_base)
        parsed = urlparse(settings.billing_return_origin or settings.consent_url or "")
        app.state.billing_return_origin = (
            f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None
        )
    # The launch link is {origin}/verify/session/{token}, so the origin must be only scheme and host. An explicit ZID_VERIFY_HOSTED_ORIGIN is used as written; the two fallbacks are
    # page addresses (the consent page is .../consent), so they are cut down to their origin, or every link would have the page's path in the middle of it.
    from urllib.parse import urlparse as _urlparse

    def _origin_only(url: str | None) -> str:
        p = _urlparse((url or "").strip())
        return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""

    app.state.verify_hosted_origin = (
        (settings.verify_hosted_origin or "").strip().rstrip("/")
        or _origin_only(settings.billing_return_origin)
        or _origin_only(settings.consent_url)
        or None
    )
    # The built-in rate table answers only when Stripe Tax cannot, and only once Finance has approved it (config/commercial/tax-rate-table.yaml).
    import os
    from pathlib import Path as _Path

    from .modules.commercial.ports import FallbackTax, RateTableTax

    config_root = _Path(
        os.environ.get("ZID_PLATFORM_CONFIG") or _Path(__file__).resolve().parents[3] / "config"
    )
    app.state.tax_port = FallbackTax(
        app.state.tax_port,
        RateTableTax.from_file(config_root / "commercial" / "tax-rate-table.yaml"),
    )
    app.state.refund_port = build_refund_port(settings)
    if settings.attestor_keys_file:
        import json as _json
        from pathlib import Path as _Path

        keys = _json.loads(_Path(settings.attestor_keys_file).read_text(encoding="utf-8")).get(
            "keys", []
        )
        if not keys or any("d" in k for k in keys):
            raise RuntimeError(
                "ZID_ATTESTOR_KEYS_FILE must hold public keys only, and at least one"
            )
        app.state.attestor_keys = keys
    if settings.stripe_webhook_secret_file or settings.stripe_webhook_secret:
        from pathlib import Path

        # Signing secrets for Stripe's webhook (CR-040): one per line, so a secret can be rolled without a gap.
        webhook_text = (
            Path(settings.stripe_webhook_secret_file).read_text(encoding="utf-8")
            if settings.stripe_webhook_secret_file
            else (settings.stripe_webhook_secret or "").replace(",", "\n")
        )
        app.state.stripe_webhook_secrets = [ln.strip() for ln in webhook_text.splitlines() if ln.strip()]
        if not app.state.stripe_webhook_secrets:
            raise RuntimeError("the Stripe webhook signing secret is empty")
    from .modules.commercial.ports import key_auth, key_vc_issued
    from .modules.credential.ports import credentials_issued_between
    from .modules.session.ports import sessions_created_between

    # meter -> (authoritative rows in a window, deduplication key from a row id, principal class):
    # what reconciliation compares the ledger against (CR-025). Wired here so commercial imports neither.
    app.state.commercial_sources = {
        "AUTH_EVENT": (sessions_created_between, key_auth, "HUMAN"),
        "VC_ISSUED": (credentials_issued_between, key_vc_issued, "HUMAN"),
    }

    async def plan_feature_gate(conn: Any, feature_id: str) -> None:
        """The plan check for a feature, for the modules that must not import commercial (they find it on app.state)."""
        await require_feature(
            conn, feature_id, enforcement=getattr(app.state, "commercial_enforcement", "shadow")
        )

    app.state.plan_feature_gate = plan_feature_gate
    app.state.commercial_counters = {
        "saml_sso": count_active_connections,
        "scim": count_active_sources,
    }


def _configure_metering(app: FastAPI, settings: Settings) -> None:
    """Per-tenant request limit and usage counters (CR-023); both act only on authenticated calls."""
    from .modules.metering.application.recorder import UsageRecorder
    from .modules.metering.domain.usage import RateLimiter

    app.state.rate_limiter = RateLimiter(settings.api_rate_limit_per_minute)

    # A company's request allowance follows its plan (config/entitlements/plan-throughput.yaml). The plan is read at most once a minute per company, so a
    # plan change reaches the limiter within a minute and an ordinary request costs nothing extra.
    plan_limits: dict[str, tuple[float, int]] = {}

    async def plan_request_limit(engine: Any, ctx: Any) -> int | None:
        import logging
        from time import monotonic

        from zid_db import tenant_transaction

        from .modules.commercial.ports import plan_request_limit as limit_for

        tenant = ctx.tenant_id or ""
        now = monotonic()
        hit = plan_limits.get(tenant)
        if hit is not None and hit[0] > now:
            return hit[1]
        try:
            async with tenant_transaction(engine, ctx) as conn:
                limit = await limit_for(conn, settings.api_rate_limit_per_minute)
        except Exception:
            logging.getLogger("zid.commercial").exception("plan request limit unavailable")
            return None
        plan_limits[tenant] = (now + 60.0, limit)
        return limit

    app.state.plan_request_limit = plan_request_limit
    recorder = UsageRecorder(lambda: getattr(app.state, "engine", None), settings.cell_id)
    app.state.usage_recorder = recorder

    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: FastAPI) -> AsyncIterator[Any]:
        async with inner(a) as state:
            yield state
            await recorder.flush()  # write the last batch while the database engine is still open

    app.router.lifespan_context = lifespan

    @app.middleware("http")
    async def country_openings(request: Request, call_next: Any) -> Any:
        """Keep the countries an operator has opened (CR-127) current in this process, on every route: a gate that asks "is this feature open
        here?" must see the recorded decision, not only the registry file. It reads at most every few seconds; a database that cannot be read
        leaves the last known decisions (never opens anything new)."""
        engine = getattr(request.app.state, "engine", None)
        if engine is not None:
            from .modules.verification.ports import country_opening_store

            await country_opening_store.refresh(engine)
        return await call_next(request)

    @app.middleware("http")
    async def mcp_resource_challenge(request: Request, call_next: Any) -> Any:
        """RFC 9728: a refused call to the agent tool gateway points the client at the resource metadata, so an MCP client knows where to learn who issues the tokens it accepts."""
        response = await call_next(request)
        if response.status_code == 401 and request.url.path == "/v1/machine/mcp":
            settings = getattr(request.app.state, "token_settings", None)
            base = str(getattr(settings, "public_base_url", "") or str(request.base_url)).rstrip(
                "/"
            )
            hint = f'resource_metadata="{base}/.well-known/oauth-protected-resource/v1/machine/mcp"'
            current = response.headers.get("www-authenticate", "")
            if "resource_metadata" not in current:
                response.headers["www-authenticate"] = (
                    f"{current}, {hint}" if current else f"Bearer {hint}"
                )
        return response

    @app.middleware("http")
    async def verify_api_request_log(request: Request, call_next: Any) -> Any:
        """One row for each authenticated call a partner's server made to the Verify API: the route template, status, error code and trace id, nothing from the body (CR-173)."""
        response = await call_next(request)
        seen = getattr(request.state, "verify_api_log", None)
        engine = getattr(request.app.state, "engine", None)
        if seen is not None and engine is not None:
            import time

            from .modules.verify_api.application import request_log

            who, trace_id, started = seen
            route = request.scope.get("route")
            await request_log.record(
                engine, who, method=request.method, template=getattr(route, "path", None), path=request.url.path, status=response.status_code, error_code=getattr(request.state, "error_code", None),
                trace_id=trace_id, session_id=getattr(request.state, "log_session_id", None) or request.path_params.get("session_id"), duration_ms=int((time.monotonic() - started) * 1000),
            )  # fmt: skip
        return response

    @app.middleware("http")
    async def usage_meter(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        metered = getattr(request.state, "metered", None)
        if metered and metered[0]:
            recorder.record(metered[0], metered[1], request.url.path, response.status_code)
        return response


def _configure_global_plane(app: FastAPI, settings: Settings) -> None:
    """Locator + discovery (DEC-G3): pinned cell publication keys, the discovery signing key."""
    from zid_crypto import LocalSigner

    from .crypto_runtime import placement_signer
    from .modules.discovery.api.discovery import router as discovery_router

    keys: dict[str, list[dict[str, Any]]] = {
        k: list(v) for k, v in settings.locator_cell_keys.items()
    }
    pem = settings.dev_signing_keys.get("federation")
    if not keys and settings.environment in ("local", "test") and pem:
        # one machine: every local cell process shares the development federation key
        keys = {"*": [LocalSigner.from_pem("dev-federation-local", pem).public_jwk()]}
        for cell in settings.cell_endpoints:
            keys["*"].append(LocalSigner.from_pem(f"dev-federation-{cell}", pem).public_jwk())
    app.state.locator_cell_keys = keys
    signer = placement_signer(
        settings,
        dev_purpose="discovery",
        kid="discovery-global",
        kms_key_id=settings.discovery_kms_key_id,
        plane="global",  # its key lives in global_directory, also when this cell serves the plane
    )
    app.state.discovery_ring = KeyRing(signer) if signer else None
    app.state.discovery_ttl_s = settings.discovery_ttl_s
    app.include_router(discovery_router)


def _configure_simulators(app: FastAPI) -> None:
    """Development provider and register simulators (VERIFY-001 VERIFY-W2; local/test only)."""
    from .modules.organization.infrastructure.registry_simulator import (
        SIMULATOR_KEY,
        RegistrySimulator,
    )
    from .modules.verification.api.simulator import router as simulator_router
    from .modules.verification.infrastructure.simulator import ProviderSimulator, SimulatorTransport

    app.state.dev_simulator = ProviderSimulator()
    app.state.verification_transport = SimulatorTransport(app)
    app.state.registry_transport = RegistrySimulator()
    app.state.kyb_transport = (
        app.state.registry_transport
    )  # company claims read the same synthetic register
    app.state.kyb_key_override = SIMULATOR_KEY
    app.state.registry_simulator = True
    app.state.registry_credential_override = SIMULATOR_KEY
    app.include_router(simulator_router)


def build_refund_port(settings: Settings) -> Any:
    """The processor behind refunds and the authoritative dispute reads. Local and test cells record what they were asked; a deployed cell without a processor key has
    none (its refunds stay queued and are said to be waiting, never sent to nothing)."""
    from pathlib import Path

    from .modules.commercial.ports import RecordingRefunds, StripeRefunds

    if settings.stripe_secret_key_file:
        key = Path(settings.stripe_secret_key_file).read_text(encoding="utf-8").strip()
        if key:
            return StripeRefunds(key, base_url=settings.stripe_api_base)
    return RecordingRefunds() if settings.environment in ("local", "test") else None


def _secret_broker(settings: Settings) -> object | None:
    """The cell's secrets broker (DEC-F5), or None where none is configured (adapters then report
    their source unavailable rather than falling back to anything)."""
    from .crypto_runtime import secret_broker_from_settings

    try:
        return secret_broker_from_settings(settings)
    except Exception:  # noqa: BLE001 - misconfiguration surfaces as an unavailable dependency
        return None


def _plan_aware_sharing(settings: Settings) -> None:
    """Shared identity leaves a cell only if the residency rules allow it and the plan includes the feature that sends it."""
    from .modules.commercial.ports import plan_aware_transfer_gate
    from .modules.tenant.ports import authorize_transfer
    from .modules.verification.application.service import configure_transfer_gate

    configure_transfer_gate(
        plan_aware_transfer_gate(
            authorize_transfer,
            feature_id="standard.credential-verification-validation",
            enforcement=settings.commercial_enforcement,
        )
    )


def _configure_phase_f(app: FastAPI, settings: Settings) -> None:
    """Evidence keys (DEC-F1/F2/F5) and the operator plane (DEC-F4) for a cell process."""
    from .modules.decision.ports import register_evidence_replayer
    from .modules.evidence.ports import configure_runtime, current_runtime, runtime_from_settings
    from .modules.operator_access.ports import OperatorSettings
    from .protected import catalog

    if (
        settings.environment in ("local", "test")
        and not settings.local_keystore
        and settings.key_provider != "db"
    ):
        current_runtime()  # one process-wide development runtime (in-memory keys)
    else:
        configure_runtime(runtime_from_settings(settings))
    register_evidence_replayer(catalog())
    register_privacy_stores(settings.registration_tenant_id)
    _plan_aware_sharing(settings)
    _register_invitation_hooks()
    _billing_access()
    _approval_standing_check(settings)
    _approval_forms()
    _readiness_policy()
    from .modules.privacy.ports import configure_enforcement

    configure_enforcement(settings.privacy_enforcement)
    from .workload_identity import trust_policy_from_settings

    app.state.trust_policy = trust_policy_from_settings(settings)
    import logging

    from .modules.operator_access.ports import configure_direct_mode, require_direct_mode_ack

    require_direct_mode_ack(
        settings.operator_mode, settings.environment, settings.operator_direct_ack
    )
    configure_direct_mode(settings.operator_mode == "direct")
    if settings.operator_mode == "direct":
        logging.getLogger("zid.operator").warning(
            "ZID_OPERATOR_MODE=direct: operators hold every non-customer-data capability without access requests or "
            "a second approver (environment=%s)",
            settings.environment,
        )
    app.state.idv_face_route_ack = settings.idv_face_route_ack
    app.state.idv_global_policy_ack = settings.idv_global_policy_ack
    if settings.idv_open_to_all_countries:
        from .modules.verification.api.idv_cases import FACE_ROUTE_ACK as _FACE_ROUTE_ACK
        from .modules.verification.infrastructure.idv_policy_file import (
            GLOBAL_POLICY_ACK as _GLOBAL_POLICY_ACK,
        )
        from .modules.verification.infrastructure.idv_policy_file import configure_open_country

        configure_open_country(
            True
        )  # the global policy also carries a basis label, so the face route's lawful-basis check passes
        app.state.idv_face_route_ack = _FACE_ROUTE_ACK
        app.state.idv_global_policy_ack = _GLOBAL_POLICY_ACK
        app.state.idv_open_to_all_countries = (
            True  # country_gate.enforce no longer holds identity checks by country
        )
        logging.getLogger("zid.idv").warning(
            "ZID_IDV_OPEN_TO_ALL_COUNTRIES is on: identity checks are open in every country with no country opening, no policy of its own and no acknowledgement; the global policy "
            "serves them and the face-match route runs with no liveness test, no evaluated engine and no check that the document is genuine (environment=%s)",
            settings.environment,
        )
    if settings.idv_global_policy_ack:
        logging.getLogger("zid.idv").warning(
            "ZID_IDV_GLOBAL_POLICY_ACK is set: a country with no policy of its own is served by the global identity policy (config/idv/global-policy.yaml), "
            "on the owners' written acceptance of its stated values (environment=%s)",
            settings.environment,
        )
    if settings.idv_face_route_ack:
        logging.getLogger("zid.idv").warning(
            "ZID_IDV_FACE_ROUTE_ACK is set: a market's face-match route approves on a live face against an uploaded document photograph, with no liveness test and no check that the document is genuine (environment=%s)",
            settings.environment,
        )
    app.state.operator_settings = OperatorSettings(
        tenant_id=settings.operator_tenant_id,
        client_ids=tuple(settings.operator_client_ids),
        max_minutes=dict(settings.operator_max_minutes),
        break_glass_max=settings.operator_break_glass_max,
        environment=settings.environment,
        cell_id=settings.cell_id,
    )
    from .modules.resilience.application.service import ResilienceSettings

    uuids = dict(settings.cell_uuids)
    if settings.cell_uuid:
        uuids.setdefault(settings.cell_id, settings.cell_uuid)
    app.state.resilience_settings = ResilienceSettings(
        operator_tenant_id=settings.operator_tenant_id or "",
        cell_id=settings.cell_id,
        cell_uuids=uuids,
    )


def _eligibility_org_state() -> None:
    """The organization's readiness state belongs to onboarding; the commercial eligibility check is handed it (it must not import onboarding)."""
    from datetime import UTC, datetime

    from .modules.commercial.ports import configure_org_state
    from .modules.onboarding.application.claims import organization_state

    async def state(conn, tenant_id):  # type: ignore[no-untyped-def]
        return await organization_state(conn, tenant_id, datetime.now(UTC))

    configure_org_state(state)


def _readiness_policy() -> None:
    """What an organization's readiness refuses is checked against the approval registry at start-up, and the onboarding module's view of the
    state is handed to the approval service (which must not import it)."""
    from datetime import UTC, datetime

    from .modules.onboarding.application.claims import organization_state
    from .modules.tenant.infrastructure.readiness_policy import readiness_policy
    from .modules.tenant.ports import configure_readiness_check

    readiness_policy()

    async def state(conn, tenant_id):  # type: ignore[no-untyped-def]
        return await organization_state(conn, tenant_id, datetime.now(UTC))

    configure_readiness_check(state)


def _approval_forms() -> None:
    """The forms behind "New request" are checked against the approval registry at start-up: a file that offers something a customer cannot
    request, or leaves a requestable action with no way to ask, stops the process."""
    from .modules.tenant.infrastructure.approval_forms import request_forms

    request_forms()


def _approval_standing_check(settings: Settings) -> None:
    """An approved change is applied only if the organization's country still allows it when it runs (Decision 11, Decision 12 test 07)."""
    from .country_gate import tenant_standing_check
    from .modules.tenant.ports import configure_standing_check

    configure_standing_check(tenant_standing_check(settings.environment))


def _billing_access() -> None:
    """Billing authority belongs to the tenant module (roles and grants); commercial is handed its decision, never the module."""
    from .modules.commercial.ports import configure_billing_gate, configure_commitment_authority
    from .modules.tenant.ports import billing_permission_checker, owner_checker

    configure_billing_gate(billing_permission_checker())
    configure_commitment_authority(owner_checker())
    _eligibility_org_state()


def _register_invitation_hooks() -> None:
    """An Administrator who accepts an invitation receives the owner's delegated tenant administration, which is
    what lets a company have a second approver (OVERLAY §4)."""
    from .modules.onboarding.ports import seed_administrator_grant
    from .modules.tenant.ports import Role, register_acceptance_hook
    from .protected import catalog

    async def administrator_joined(conn, tenant_id, assignment):  # type: ignore[no-untyped-def]
        if assignment.role is Role.ADMIN and assignment.granted_by:
            await seed_administrator_grant(
                conn,
                tenant_id,
                admin_principal_id=assignment.principal_id,
                inviter_principal_id=assignment.granted_by,
                catalog=catalog(),
            )

    register_acceptance_hook("administrator-grant", administrator_joined)

    # A person the company's own directory sync provisioned attaches to that account at first sign-in (federation never imports provisioning).
    from .modules.federation.ports import register_provisioned_lookup
    from .modules.provisioning.ports import directory_person

    register_provisioned_lookup(directory_person)

    # Quarantining a sign-in connector stops the federation connection behind it (integration never imports federation: federation already calls integration).
    from .modules.federation.ports import set_connection_status
    from .modules.integration.ports import register_connection_status_setter

    register_connection_status_setter(set_connection_status)

    # A relying system may name a person by the id its own directory gave them (decision must not import provisioning).
    from .modules.decision.ports import register_subject_resolver
    from .modules.provisioning.ports import directory_principal

    register_subject_resolver(directory_principal)

    # Reporting by organization unit: decision asks which people a unit holds without importing tenant.
    from .modules.decision.ports import register_unit_lookup
    from .modules.tenant.ports import unit_principals

    register_unit_lookup(unit_principals)


def register_privacy_stores(registration_tenant: str | None = None) -> None:
    """Every module that keeps personal data plugs its locator/exporter/disposer into the privacy
    data map (PRIV-001 §10, §32.1 "all production personal-data stores registered")."""
    from . import evidence_privacy_adapter as evidence_uploads
    from .modules.authentication.infrastructure import privacy_adapter as authentication
    from .modules.credential.infrastructure import privacy_adapter as credential
    from .modules.principal.infrastructure import privacy_adapter as principal
    from .modules.screening.infrastructure import privacy_adapter as screening
    from .modules.session.infrastructure import privacy_adapter as session
    from .modules.verification.infrastructure import privacy_adapter as verification

    for adapter in (
        principal,
        authentication,
        session,
        verification,
        credential,
        evidence_uploads,
    ):
        adapter.register()
    from .modules.privacy.ports import register_store as _register_store

    screening.register(_register_store)
    if (
        registration_tenant
    ):  # sign-up records follow an erased account (PRIV-001 §17); a cell with sign-up off has none
        from .modules.onboarding.infrastructure import privacy_adapter as registration

        registration.register(registration_tenant)

    # An erased subject leaves nothing cached behind (PRIV-10).
    from .modules.credential.ports import resolve_and_bind
    from .modules.evidence.ports import register_issuer_binding
    from .modules.privacy.ports import register_erasure_hook
    from .modules.signals.ports import forget_subject

    register_erasure_hook("security-state-cache", forget_subject)
    # CR-110: a reversed decision revokes the verification claims that rested on it (privacy never imports verification)
    from .modules.privacy.ports import register_decision_hook
    from .modules.verification.ports import invalidate_for_decision

    register_decision_hook("verification-assertions", invalidate_for_decision)
    register_issuer_binding(
        resolve_and_bind
    )  # the evidence module asks the credential module's registry without importing it

    # A person disputing a fact the company register supplied has it re-read from the register, not edited (PRIV-16).
    from .modules.organization.ports import organization_exists, request_registry_refresh
    from .modules.privacy.ports import PrivacyError, register_rectification_source

    async def recheck_registry_fact(conn, tenant_id, fact, *, reason_ref, now):  # type: ignore[no-untyped-def]
        org = str(fact.get("organization_id") or "")
        if not org or not await organization_exists(conn, org):
            raise PrivacyError(
                "PRIVACY_SCHEMA_INVALID", "name the organization: scope.fact.organization_id"
            )
        return await request_registry_refresh(conn, tenant_id, org, reason_ref=reason_ref, now=now)

    register_rectification_source("REGISTRY", recheck_registry_fact)

    # Modules that must not import each other are joined here (no dependency cycles).
    from .modules.integration.application.service import configure_client_gateway
    from .modules.session.ports import OAuthClientGateway
    from .modules.tenant.ports import authorize_transfer
    from .modules.verification.application.service import configure_transfer_gate

    configure_client_gateway(
        OAuthClientGateway()
    )  # self-service OAuth clients (integration <- session)
    configure_transfer_gate(
        authorize_transfer
    )  # shared identity leaves a cell only through the gateway (TEN-24)


def _tenant_deletion() -> SimpleNamespace:
    from .modules.onboarding.ports import DeletionError, deletion_status, request_deletion

    return SimpleNamespace(request=request_deletion, status=deletion_status, error=DeletionError)


def _install_crypto_degradation(app: FastAPI) -> None:
    """A key service that is throttled or unreachable degrades safely (OPS-17): the request is refused with a retryable
    503 and a Retry-After. There is no path on which the work carries on unsigned or unencrypted."""
    from fastapi.responses import JSONResponse
    from zid_context import current_context
    from zid_crypto import CryptoError

    transient = {"CRYPTO_THROTTLED", "CRYPTO_MODULE_UNAVAILABLE", "CRYPTO_KEY_NOT_ACTIVE"}

    @app.exception_handler(CryptoError)
    async def crypto_degraded(_: Request, exc: CryptoError) -> JSONResponse:
        ctx = current_context()
        retryable = exc.code in transient
        return JSONResponse(
            {
                "success": False,
                "request_id": ctx.request_id if ctx else "req_unknown",
                "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
                "error": {
                    "code": "CRYPTO_UNAVAILABLE" if retryable else "CRYPTO_ERROR",
                    "message": "A required cryptographic service is busy or unavailable. Nothing was changed; try again shortly."
                    if retryable
                    else "A cryptographic operation was refused.",
                    "details": [],
                    "retryable": retryable,
                    "retry_after_ms": 2000 if retryable else None,
                    "docs_ref": None,
                },
            },
            status_code=503 if retryable else 500,
            headers={"Retry-After": "2"} if retryable else None,
        )

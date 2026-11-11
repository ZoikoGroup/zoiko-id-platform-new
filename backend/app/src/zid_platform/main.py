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
    app.state.environment = settings.environment
    if settings.agent_builds == "off" and settings.environment not in ("local", "test", "dev"):
        raise RuntimeError(
            "ZID_AGENT_BUILDS=off would let any typed digest back a blueprint release; outside local, test and dev "
            "builds must be approved (set it to auto or enforce)"
        )
    app.state.agent_builds = settings.agent_builds
    if settings.regulatory_packs == "off" and settings.environment not in ("local", "test", "dev"):
        raise RuntimeError(
            "ZID_REGULATORY_PACKS=off would let an enabled country run a regulated capability with no approved policy pack; "
            "outside local, test and dev use shadow or enforce"
        )
    app.state.regulatory_packs = settings.regulatory_packs
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
        from .modules.authentication.api.recovery import router as recovery_router
        from .modules.authentication.api.step_up import router as step_up_router
        from .modules.authentication.api.webauthn import router as webauthn_router
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
        from .modules.credential.api.trust import router as credential_trust_router
        from .modules.credential.api.v2 import router as credential_v2_router
        from .modules.credential.api.v2_cases import router as credential_v2_cases_router
        from .modules.credential.api.v2_issuer import router as credential_v2_issuer_router
        from .modules.credential.api.v2_presentations import router as credential_v2_pres_router
        from .modules.decision.api.authzen import router as authzen_router
        from .modules.decision.api.insights import router as insights_router
        from .modules.evidence.api.evidence import router as evidence_router
        from .modules.evidence.api.v2 import router as evidence_v2_router
        from .modules.evidence.api.v2_documents import router as evidence_v2_documents_router
        from .modules.evidence.api.v2_uploads import router as evidence_v2_uploads_router
        from .modules.federation.api.federation import router as federation_router
        from .modules.integration.api.integration import router as integration_router
        from .modules.machine.api.agents import router as agents_router
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
        from .modules.onboarding.api.v2_organizations import router as v2_organizations_router
        from .modules.verification.api.v2_age import router as v2_age_router
        from .modules.onboarding.api.readiness import router as readiness_router
        from .modules.onboarding.api.registration import router as registration_router
        from .modules.onboarding.api.signup import router as signup_router
        from .modules.operations.api.ops import router as operations_router
        from .modules.operator_access.api.commercial_admin import router as commercial_admin_router
        from .modules.operator_access.api.commercial_finance import (
            router as commercial_finance_router,
        )
        from .modules.operator_access.api.commercial_payment_ops import (
            router as commercial_payment_ops_router,
        )
        from .modules.operator_access.api.lockbox import router as lockbox_router
        from .modules.operator_access.api.operator import router as operator_router
        from .modules.operator_access.api.regulatory import router as regulatory_router
        from .modules.policy_authoring.api.policies import router as policy_router
        from .modules.principal.api.principals import router as principals_router
        from .modules.privacy.api.privacy import router as privacy_router
        from .modules.provisioning.api.directory import router as directory_sync_router
        from .modules.provisioning.api.scim import router as scim_router
        from .modules.resilience.api.platform import router as resilience_router
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
        from .modules.verification.api.countries import router as countries_router
        from .modules.verification.api.tiers import router as verification_tiers_router
        from .modules.verification.api.verification import router as verification_router
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
            recovery_router,
            credentials_router,
            claim_checks_router,
            credential_trust_router,
            credential_v2_router,
            credential_v2_issuer_router,
            credential_v2_pres_router,
            credential_v2_cases_router,
            principals_router,
            evidence_router,
            evidence_v2_router,
            evidence_v2_documents_router,
            evidence_v2_uploads_router,
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
            commitments_router,
            onboarding_router,
            org_claims_router,
            v2_organizations_router,
            v2_age_router,
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
            tool_calls_router,
            screening_signals_router,
            settings_router,
            usage_router,
            commercial_router,
            billing_workspace_router,
            stripe_webhook_router,
            commercial_admin_router,
            commercial_finance_router,
            commercial_payment_ops_router,
            regulatory_router,
            communication_router,
        ):
            app.include_router(router)
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
    if settings.stripe_secret_key_file:
        from pathlib import Path

        from .modules.commercial.ports import StripePayments, StripeTax

        # The processor and the tax engine (CR-027): the key is read once from its file and never logged.
        stripe_key = Path(settings.stripe_secret_key_file).read_text(encoding="utf-8").strip()
        if not stripe_key:
            raise RuntimeError("ZID_STRIPE_SECRET_KEY_FILE is empty")
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
    if settings.stripe_webhook_secret_file:
        from pathlib import Path

        # Signing secrets for Stripe's webhook (CR-040): one per line, so a secret can be rolled without a gap.
        app.state.stripe_webhook_secrets = [
            ln.strip()
            for ln in Path(settings.stripe_webhook_secret_file)
            .read_text(encoding="utf-8")
            .splitlines()
            if ln.strip()
        ]
        if not app.state.stripe_webhook_secrets:
            raise RuntimeError("ZID_STRIPE_WEBHOOK_SECRET_FILE is empty")
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
    from .modules.session.infrastructure import privacy_adapter as session
    from .modules.verification.infrastructure import privacy_adapter as verification

    for adapter in (principal, authentication, session, verification, credential, evidence_uploads):
        adapter.register()
    if (
        registration_tenant
    ):  # sign-up records follow an erased account (PRIV-001 §17); a cell with sign-up off has none
        from .modules.onboarding.infrastructure import privacy_adapter as registration

        registration.register(registration_tenant)

    # An erased subject leaves nothing cached behind (PRIV-10).
    from .modules.privacy.ports import register_erasure_hook
    from .modules.signals.ports import forget_subject

    register_erasure_hook("security-state-cache", forget_subject)

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

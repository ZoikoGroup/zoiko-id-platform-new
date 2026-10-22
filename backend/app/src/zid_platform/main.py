"""App factory for the Zoiko iD modular monolith (DEC-D6).

Composes the shared service shell (zid-app) with the modules of the configured role.
Module routers are mounted here as each module's api/ layer is implemented; modules talk to
each other only through their `ports.py` interfaces.
"""

from typing import Any

from fastapi import FastAPI
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
    spec = ServiceSpec(
        name=f"zoiko-id-{role.value}",
        plane="global" if role is Role.GLOBAL else "cell",
        modules=modules_for(role),
        required_schemas=schemas_for(role),
        journeys=[
            Journey(n, prefix, latency) for n, prefix, latency in catalogue_journeys(ops_catalogue)
        ],
    )
    app = create_service_app(spec, settings)
    app.state.ops_catalogue = ops_catalogue
    app.state.cell_uuid = settings.cell_uuid
    app.state.public_base_url = settings.public_base_url
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
    app.state.tenant_signup = settings.tenant_signup_enabled
    app.state.tenant_signup_dev_verify = settings.tenant_signup_dev_verify
    app.state.environment = settings.environment
    app.state.federation_authority_hints = settings.federation_authority_hints
    app.state.verification_transport = None  # real network; tests inject a provider simulator
    app.state.credential_transport = None  # status-list / federation fetches (simulated in tests)
    app.state.registry_transport = None  # Companies House / GLEIF (simulated in tests)
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
        if keyrings is None:
            if settings.environment not in ("local", "test"):
                raise RuntimeError(
                    "token issuer keys must come from the key broker outside local/test (DEC-F5)"
                )
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
    if role is Role.GLOBAL:
        _configure_global_plane(app, settings)
    if role is Role.CELL:
        _configure_phase_f(app, settings)
        from .modules.authentication.api.recovery import router as recovery_router
        from .modules.authentication.api.webauthn import router as webauthn_router
        from .modules.authority.api.approvals import router as approvals_router
        from .modules.authority.api.grants import router as grants_router
        from .modules.credential.api.credentials import router as credentials_router
        from .modules.decision.api.authzen import router as authzen_router
        from .modules.evidence.api.evidence import router as evidence_router
        from .modules.federation.api.federation import router as federation_router
        from .modules.integration.api.integration import router as integration_router
        from .modules.machine.api.workloads import router as workloads_router
        from .modules.mandate.api.mandates import router as mandates_router
        from .modules.onboarding.api.onboarding import router as onboarding_router
        from .modules.onboarding.api.signup import router as signup_router
        from .modules.operations.api.ops import router as operations_router
        from .modules.operator_access.api.lockbox import router as lockbox_router
        from .modules.operator_access.api.operator import router as operator_router
        from .modules.principal.api.principals import router as principals_router
        from .modules.privacy.api.privacy import router as privacy_router
        from .modules.provisioning.api.scim import router as scim_router
        from .modules.resilience.api.platform import router as resilience_router
        from .modules.session.api.oauth import router as oauth_router
        from .modules.session.api.sessions import router as sessions_router
        from .modules.signals.api.signals import router as signals_router
        from .modules.tenant.api.domains import router as domains_router
        from .modules.tenant.api.residency import router as residency_router
        from .modules.tenant.api.tenants import router as tenants_router
        from .modules.verification.api.verification import router as verification_router

        for router in (
            authzen_router,
            grants_router,
            mandates_router,
            approvals_router,
            oauth_router,
            sessions_router,
            webauthn_router,
            signals_router,
            federation_router,
            scim_router,
            verification_router,
            recovery_router,
            credentials_router,
            principals_router,
            evidence_router,
            operator_router,
            lockbox_router,
            privacy_router,
            residency_router,
            tenants_router,
            onboarding_router,
            signup_router,
            domains_router,
            integration_router,
            resilience_router,
            operations_router,
            workloads_router,
        ):
            app.include_router(router)
    return app


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
    )
    app.state.discovery_ring = KeyRing(signer) if signer else None
    app.state.discovery_ttl_s = settings.discovery_ttl_s
    app.include_router(discovery_router)


def _secret_broker(settings: Settings) -> object | None:
    """The cell's secrets broker (DEC-F5), or None where none is configured (adapters then report
    their source unavailable rather than falling back to anything)."""
    from .crypto_runtime import secret_broker_from_settings

    try:
        return secret_broker_from_settings(settings)
    except Exception:  # noqa: BLE001 - misconfiguration surfaces as an unavailable dependency
        return None


def _configure_phase_f(app: FastAPI, settings: Settings) -> None:
    """Evidence keys (DEC-F1/F2/F5) and the operator plane (DEC-F4) for a cell process."""
    from .modules.decision.ports import register_evidence_replayer
    from .modules.evidence.ports import configure_runtime, current_runtime, runtime_from_settings
    from .modules.operator_access.ports import OperatorSettings
    from .protected import catalog

    if settings.environment in ("local", "test") and not settings.local_keystore:
        current_runtime()  # one process-wide development runtime (in-memory keys)
    else:
        configure_runtime(runtime_from_settings(settings))
    register_evidence_replayer(catalog())
    register_privacy_stores()
    from .modules.privacy.ports import configure_enforcement

    configure_enforcement(settings.privacy_enforcement)
    from .workload_identity import trust_policy_from_settings

    app.state.trust_policy = trust_policy_from_settings(settings)
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


def register_privacy_stores() -> None:
    """Every module that keeps personal data plugs its locator/exporter/disposer into the privacy
    data map (PRIV-001 §10, §32.1 "all production personal-data stores registered")."""
    from .modules.authentication.infrastructure import privacy_adapter as authentication
    from .modules.credential.infrastructure import privacy_adapter as credential
    from .modules.principal.infrastructure import privacy_adapter as principal
    from .modules.session.infrastructure import privacy_adapter as session
    from .modules.verification.infrastructure import privacy_adapter as verification

    for adapter in (principal, authentication, session, verification, credential):
        adapter.register()

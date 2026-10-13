"""App factory for the Zoiko iD modular monolith (DEC-D6).

Composes the shared service shell (zid-app) with the modules of the configured role.
Module routers are mounted here as each module's api/ layer is implemented; modules talk to
each other only through their `ports.py` interfaces.
"""

from fastapi import FastAPI
from zid_app import ServiceSpec, create_service_app
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
    spec = ServiceSpec(
        name=f"zoiko-id-{role.value}",
        plane="global" if role is Role.GLOBAL else "cell",
        modules=modules_for(role),
        required_schemas=schemas_for(role),
    )
    app = create_service_app(spec, settings)
    app.state.cell_uuid = settings.cell_uuid
    app.state.public_base_url = settings.public_base_url
    app.state.mtls_cert_header = settings.mtls_client_cert_header
    app.state.home_cell = settings.cell_id
    app.state.federation_authority_hints = settings.federation_authority_hints
    app.state.verification_transport = None  # real network; tests inject a provider simulator
    app.state.credential_transport = None  # status-list / federation fetches (simulated in tests)
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
    if role is Role.CELL:
        from .modules.authentication.api.recovery import router as recovery_router
        from .modules.authentication.api.webauthn import router as webauthn_router
        from .modules.authority.api.approvals import router as approvals_router
        from .modules.authority.api.grants import router as grants_router
        from .modules.credential.api.credentials import router as credentials_router
        from .modules.decision.api.authzen import router as authzen_router
        from .modules.federation.api.federation import router as federation_router
        from .modules.mandate.api.mandates import router as mandates_router
        from .modules.provisioning.api.scim import router as scim_router
        from .modules.session.api.oauth import router as oauth_router
        from .modules.session.api.sessions import router as sessions_router
        from .modules.signals.api.signals import router as signals_router
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
        ):
            app.include_router(router)
    return app

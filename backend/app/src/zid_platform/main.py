"""App factory for the Zoiko iD modular monolith (DEC-D6).

Composes the shared service shell (zid-app) with the modules of the configured role.
Module routers are mounted here as each module's api/ layer is implemented; modules talk to
each other only through their `ports.py` interfaces.
"""

from fastapi import FastAPI
from zid_app import ServiceSpec, create_service_app
from zid_crypto import KeyRing, LocalSigner

from .modules.session.ports import TokenSettings
from .roles import Role, modules_for, schemas_for
from .settings import Settings


def create_app(settings: Settings | None = None, *, keyring: KeyRing | None = None) -> FastAPI:
    """`keyring` holds the token issuer's keys (KMS/HSM-backed in deployed environments, DEC-F5).

    Locally, with ZID_TOKEN_ISSUER set and no keyring given, an in-memory development key is created;
    any other environment refuses to start without real keys rather than minting its own."""
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
    app.state.token_settings = None
    if settings.token_issuer:
        if keyring is None:
            if settings.environment not in ("local", "test"):
                raise RuntimeError(
                    "token issuer keys must come from the key broker outside local/test (DEC-F5)"
                )
            keyring = KeyRing(LocalSigner(f"dev-{settings.cell_id}"))
        app.state.token_settings = TokenSettings(
            settings.token_issuer, settings.token_audience, keyring
        )
    if role is Role.CELL:
        from .modules.authority.api.approvals import router as approvals_router
        from .modules.authority.api.grants import router as grants_router
        from .modules.decision.api.authzen import router as authzen_router
        from .modules.mandate.api.mandates import router as mandates_router

        for router in (authzen_router, grants_router, mandates_router, approvals_router):
            app.include_router(router)
    return app

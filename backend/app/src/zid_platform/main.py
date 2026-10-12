"""App factory for the Zoiko iD modular monolith (DEC-D6).

Composes the shared service shell (zid-app) with the modules of the configured role.
Module routers are mounted here as each module's api/ layer is implemented; modules talk to
each other only through their `ports.py` interfaces.
"""

from fastapi import FastAPI
from zid_app import ServiceSpec, create_service_app

from .roles import Role, modules_for, schemas_for
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
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
    if role is Role.CELL:
        from .modules.authority.api.approvals import router as approvals_router
        from .modules.authority.api.grants import router as grants_router
        from .modules.decision.api.authzen import router as authzen_router
        from .modules.mandate.api.mandates import router as mandates_router

        for router in (authzen_router, grants_router, mandates_router, approvals_router):
            app.include_router(router)
    return app

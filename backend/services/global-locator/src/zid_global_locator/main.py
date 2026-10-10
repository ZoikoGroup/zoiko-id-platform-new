"""App factory for the global-locator deployable unit.

Composes the shared service shell (zid-app) with this unit's modules. Module routers are
mounted here as each module's api/ layer is implemented.
"""

from fastapi import FastAPI
from zid_app import ServiceSpec, create_service_app

from .settings import Settings

SPEC = ServiceSpec(
    name="global-locator",
    plane="global",
    modules=['discovery', 'locator'],
    # Schemas holding this unit's tables (backend/database/table-ownership.yaml) + migration metadata.
    required_schemas=['global_directory', 'zid_migrations'],
)


def create_app(settings: Settings | None = None) -> FastAPI:
    return create_service_app(SPEC, settings or Settings())

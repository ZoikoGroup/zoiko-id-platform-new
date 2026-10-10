"""App factory for the authorization-runtime deployable unit.

Composes the shared service shell (zid-app) with this unit's modules. Module routers are
mounted here as each module's api/ layer is implemented.
"""

from fastapi import FastAPI
from zid_app import ServiceSpec, create_service_app

from .settings import Settings

SPEC = ServiceSpec(
    name="authorization-runtime",
    plane="runtime",
    modules=['decision', 'policy_runtime', 'relationship'],
    # Schemas holding this unit's tables (backend/database/table-ownership.yaml) + migration metadata.
    required_schemas=['authz', 'org', 'zid_migrations'],
)


def create_app(settings: Settings | None = None) -> FastAPI:
    return create_service_app(SPEC, settings or Settings())

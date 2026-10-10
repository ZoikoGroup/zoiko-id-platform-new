"""HTTP entrypoint: `uvicorn zid_enterprise_integration.entrypoints.api:app`."""

from zid_enterprise_integration.main import create_app

app = create_app()

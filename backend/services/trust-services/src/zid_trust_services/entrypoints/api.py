"""HTTP entrypoint: `uvicorn zid_trust_services.entrypoints.api:app`."""

from zid_trust_services.main import create_app

app = create_app()

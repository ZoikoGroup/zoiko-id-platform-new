"""HTTP entrypoint: `uvicorn zid_authorization_runtime.entrypoints.api:app`."""

from zid_authorization_runtime.main import create_app

app = create_app()

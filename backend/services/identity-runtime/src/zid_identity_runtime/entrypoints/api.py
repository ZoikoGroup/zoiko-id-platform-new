"""HTTP entrypoint: `uvicorn zid_identity_runtime.entrypoints.api:app`."""

from zid_identity_runtime.main import create_app

app = create_app()

"""HTTP entrypoint: `uvicorn zid_authority_runtime.entrypoints.api:app`."""

from zid_authority_runtime.main import create_app

app = create_app()

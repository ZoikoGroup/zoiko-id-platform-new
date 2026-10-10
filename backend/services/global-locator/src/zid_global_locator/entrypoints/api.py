"""HTTP entrypoint: `uvicorn zid_global_locator.entrypoints.api:app`."""

from zid_global_locator.main import create_app

app = create_app()

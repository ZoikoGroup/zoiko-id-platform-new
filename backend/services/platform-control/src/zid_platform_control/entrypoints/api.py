"""HTTP entrypoint: `uvicorn zid_platform_control.entrypoints.api:app`."""

from zid_platform_control.main import create_app

app = create_app()

"""HTTP entrypoint: `uvicorn zid_platform.entrypoints.api:app` (role from ZID_ROLE: cell | global)."""

from zid_platform.main import create_app

app = create_app()

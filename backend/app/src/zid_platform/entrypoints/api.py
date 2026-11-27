"""HTTP entrypoint: `uvicorn zid_platform.entrypoints.api:app` (role from ZID_ROLE: cell | global)."""

import os

from zid_platform.cell_identity import resolve_cell_id
from zid_platform.main import create_app
from zid_platform.settings import Settings

_settings = Settings()
resolve_cell_id(
    {
        "ZID_ENVIRONMENT": _settings.environment,
        "ZID_CELL_ID": os.environ.get("ZID_CELL_ID", ""),
    }
)
app = create_app(_settings)

"""HTTP entrypoint: `uvicorn zid_security_evidence.entrypoints.api:app`."""

from zid_security_evidence.main import create_app

app = create_app()

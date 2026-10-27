"""Mint a short-lived access token for the LOCAL cell, for load and smoke tests (development only).

    backend/.venv/Scripts/python tools/scripts/load_token.py <tenant_id> <principal_id>

Signs with the development key in backend/.env (ZID_DEV_SIGNING_KEYS), exactly as the local cell does, so the
cell accepts it as a real token. Refuses to run unless ZID_ENVIRONMENT is local or test, so it can never mint
a token for a deployed cell (deployed cells hold their keys in a KMS and never in a file).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    tenant, principal = sys.argv[1:3]
    env = load_env(ROOT / "backend" / ".env")
    if env.get("ZID_ENVIRONMENT", "local") not in ("local", "test"):
        print("refusing: this mints tokens for local/test cells only", file=sys.stderr)
        return 1
    sys.path.insert(0, str(ROOT / "backend" / "app" / "src"))
    from zid_platform.keys import KeyRings
    from zid_platform.modules.session.ports import issue_access_token

    rings = KeyRings.development(
        env.get("ZID_CELL_ID", "local-1"), json.loads(env["ZID_DEV_SIGNING_KEYS"])
    )
    print(
        issue_access_token(
            rings.token,
            issuer=env["ZID_TOKEN_ISSUER"],
            audience=env["ZID_TOKEN_AUDIENCE"],
            subject=principal,
            tenant_id=tenant,
            client_id="load-test",
            now=datetime.now(UTC),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

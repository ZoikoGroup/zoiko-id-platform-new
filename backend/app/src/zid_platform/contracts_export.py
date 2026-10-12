"""Export published API contracts from code (ZID-API-001 §4; DEC-D4: one OpenAPI toolchain).

    python -m zid_platform.contracts_export        # rewrites contracts/openapi/authzen/evaluation.json

CI (`test_published_contracts_match_code`) fails if code and the committed contract drift, so a
contract change is always a reviewed diff in contracts/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
AUTHZEN_FILE = REPO / "contracts" / "openapi" / "authzen" / "evaluation.json"


def authzen_contract() -> dict[str, Any]:
    from zid_platform.main import create_app
    from zid_platform.settings import Settings

    spec = create_app(Settings(database_url=None)).openapi()
    paths = {p: v for p, v in spec["paths"].items() if p.startswith("/access/v1/")}
    return {
        "openapi": spec["openapi"],
        "info": {
            "title": "Zoiko iD AuthZEN Authorization API (OpenID AuthZEN 1.0 profile)",
            "version": "1.0.0",
        },
        "paths": paths,
        "components": spec.get("components", {}),
    }


def render(contract: dict[str, Any]) -> str:
    return json.dumps(contract, indent=2, sort_keys=True) + "\n"


def main() -> None:
    AUTHZEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTHZEN_FILE.write_text(render(authzen_contract()), encoding="utf-8")
    sys.stdout.write(f"wrote {AUTHZEN_FILE}\n")


if __name__ == "__main__":
    main()

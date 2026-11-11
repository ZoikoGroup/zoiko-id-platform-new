"""The service-owned API contract (backend/assistant/contract/assistant-v1.openapi.json), generated from the code, never hand-written.

    python -m zid_assistant.contract_tool export     write the contract
    python -m zid_assistant.contract_tool check      exit 1 when the committed contract differs from the code

It lives beside the service, not in the shared contracts/ folder, because that folder drives the generated platform client and the
assistant is deliberately isolated from the platform (ADR-02).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from zid_assistant import API_PREFIX
from zid_assistant.main import create_app
from zid_assistant.settings import AssistantSettings

CONTRACT = Path(__file__).resolve().parents[2] / "contract" / "assistant-v1.openapi.json"


def build() -> str:
    app = create_app(AssistantSettings(_env_file=None, environment="test"))  # type: ignore[call-arg]
    spec = app.openapi()
    spec["paths"] = {p: v for p, v in spec["paths"].items() if p.startswith(API_PREFIX)}
    spec["info"] = {
        "title": "Zoiko iD Public Assistant API",
        "version": "v1",
        "description": "Anonymous, public, information-only. See backend/assistant/API.md.",
    }
    spec.pop("components", None)
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "export":
        CONTRACT.parent.mkdir(parents=True, exist_ok=True)
        CONTRACT.write_text(build(), encoding="utf-8")
        print(f"wrote {CONTRACT}")
        return 0
    if cmd == "check":
        if not CONTRACT.exists() or CONTRACT.read_text(encoding="utf-8") != build():
            print(
                "the committed contract is out of date: run `python -m zid_assistant.contract_tool export`",
                file=sys.stderr,
            )
            return 1
        print("contract is current")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())

"""The committed OpenAPI contract and API.md both match the code (ADR-06: no route is 'deployed' unless contract, code and tests agree)."""

from __future__ import annotations

import re
from pathlib import Path

from zid_assistant import contract_tool

ROOT = Path(__file__).resolve().parents[2]


def test_the_committed_openapi_contract_is_current() -> None:
    assert contract_tool.CONTRACT.exists(), "run: python -m zid_assistant.contract_tool export"
    assert contract_tool.CONTRACT.read_text(encoding="utf-8") == contract_tool.build(), (
        "the contract drifted from the code: re-export it"
    )


def test_api_md_documents_exactly_the_routes_the_service_serves() -> None:
    import json

    served = {
        (m.upper(), p.replace("/api/public/chat/v1", ""))
        for p, ops in json.loads(contract_tool.build())["paths"].items()
        for m in ops
    }
    doc = (ROOT / "API.md").read_text(encoding="utf-8")
    documented: set[tuple[str, str]] = set()
    for m in re.finditer(
        r"^### (GET|POST|DELETE)(?: and (GET|POST|DELETE))? `(/[^`]*)`(?: and (?:POST|GET|DELETE) `(/[^`]*)`)?",
        doc,
        re.MULTILINE,
    ):
        for verb in (m.group(1), m.group(2)):
            if verb:
                documented.add((verb, m.group(3)))
    for m in re.finditer(
        r"^### (GET|POST|DELETE) `(/[^`]*)` and (GET|POST|DELETE) `(/[^`]*)`", doc, re.MULTILINE
    ):
        documented |= {(m.group(1), m.group(2)), (m.group(3), m.group(4))}
    documented |= (
        {("POST", "/intake/sales"), ("POST", "/intake/support")}
        if "### POST `/intake/sales` and POST `/intake/support`" in doc
        else set()
    )
    normalized = {(v, re.sub(r"\{link_id\}", "{link_id}", p)) for v, p in documented}
    assert served <= normalized, f"served but undocumented: {sorted(served - normalized)}"
    assert normalized <= served, f"documented but not served: {sorted(normalized - served)}"

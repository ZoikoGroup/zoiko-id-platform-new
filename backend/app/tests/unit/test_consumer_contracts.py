"""Consumer contract tests for Zoikorum and Zoiko Rooms (ZID-INT-002 sections 13 and 16.1).

Each consumer states in `contracts/consumers/*.yaml` what it relies on. These tests fail if the PUBLISHED OpenAPI or AsyncAPI stops providing
any of it, so a change that would break a marketplace is caught before release, not by the marketplace. The Rooms response shape is also
checked against a real answer in `tests/component/test_v2_events_pg.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
CONSUMERS = sorted((ROOT / "contracts" / "consumers").glob("*.yaml"))
OPENAPI = ROOT / "contracts" / "openapi" / "public"
ASYNCAPI = json.loads(
    (ROOT / "contracts" / "asyncapi" / "channels" / "zoiko-id.asyncapi.json").read_text(
        encoding="utf-8"
    )
)


def operations() -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for f in OPENAPI.glob("*.json"):
        spec = json.loads(f.read_text(encoding="utf-8"))
        for path, item in spec["paths"].items():
            for method, op in item.items():
                if isinstance(op, dict):
                    out[(method, path)] = {"op": op, "spec": spec}
    return out


def request_fields(entry: dict) -> set[str]:
    body = entry["op"].get("requestBody")
    if body is None:
        return set()
    schema = body["content"]["application/json"]["schema"]
    while "$ref" in schema:
        schema = entry["spec"]["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return set(schema.get("properties", {}))


def test_there_are_consumer_contracts_for_both_marketplaces() -> None:
    assert {p.stem for p in CONSUMERS} == {"zoikorum", "zoiko-rooms"}


@pytest.mark.parametrize("path", CONSUMERS, ids=lambda p: p.stem)
def test_every_route_a_consumer_relies_on_is_published_with_the_fields_it_sends(path: Path) -> None:
    ops = operations()
    for r in yaml.safe_load(path.read_text(encoding="utf-8"))["requests"]:
        entry = ops.get((r["method"], r["path"]))
        assert entry is not None, (
            f"{path.stem} relies on {r['method']} {r['path']}, which is not published"
        )
        missing = set(r.get("required_request_fields", [])) - request_fields(entry)
        assert not missing, f"{path.stem}: {r['path']} no longer accepts {sorted(missing)}"
        if r.get("idempotency_key_required"):
            params = {p["name"].lower(): p for p in entry["op"].get("parameters", [])}
            assert params.get("idempotency-key", {}).get("required") is True, (
                f"{path.stem}: {r['path']} must require an Idempotency-Key"
            )


@pytest.mark.parametrize("path", CONSUMERS, ids=lambda p: p.stem)
def test_every_event_a_consumer_subscribes_to_is_in_the_published_catalogue(path: Path) -> None:
    published = set(ASYNCAPI["components"]["messages"])
    for event in yaml.safe_load(path.read_text(encoding="utf-8"))["events"]:
        assert event.replace(".", "_") in published, (
            f"{path.stem} subscribes to {event}, which is not published"
        )


def test_rooms_vocabulary_never_includes_a_stronger_word_than_it_may_get() -> None:
    rooms = yaml.safe_load(
        (ROOT / "contracts" / "consumers" / "zoiko-rooms.yaml").read_text(encoding="utf-8")
    )
    check = rooms["requests"][0]
    assert not set(check["never_returned"]) & set(check["listing_authority_values"])

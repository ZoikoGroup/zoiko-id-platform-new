"""Export published API contracts from code (ZID-API-001 §4; DEC-D4: one OpenAPI toolchain).

    python -m zid_platform.contracts_export        # rewrites every contract in CONTRACTS + ASYNCAPI_FILE

CI (`test_published_contracts_match_code`) fails if code and the committed contract drift, so a
contract change is always a reviewed diff in contracts/.

The AsyncAPI document is derived from the event catalogues and the relay's own routing rules
(`zid_outbox.lane_for`, topic `zid.<env>.<region>.<domain>.<lane>`), so it describes the channels
events are actually published on (ZID-EVENTS-001 §3-6).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml
from zid_outbox import lane_for

REPO = Path(__file__).resolve().parents[4]
OPENAPI = REPO / "contracts" / "openapi"
# published file -> (path prefixes it covers, title)
CONTRACTS: dict[Path, tuple[tuple[str, ...], str]] = {
    OPENAPI / "authzen" / "evaluation.json": (
        ("/access/v1/",),
        "Zoiko iD AuthZEN Authorization API (OpenID AuthZEN 1.0 profile)",
    ),
    OPENAPI / "public" / "authority-mandate.json": (
        (
            "/v1/authority-grants",
            "/v1/approval-requests",
            "/v1/mandates",
            "/v1/mandate-reservations",
        ),
        "Zoiko iD Authority, Approval and Mandate API",
    ),
}
AUTHZEN_FILE = OPENAPI / "authzen" / "evaluation.json"


def _refs(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            out.add(ref.rsplit("/", 1)[1])
        for v in node.values():
            _refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _refs(v, out)


def contract(prefixes: tuple[str, ...], title: str) -> dict[str, Any]:
    from zid_platform.main import create_app
    from zid_platform.settings import Settings

    spec = create_app(Settings(database_url=None)).openapi()
    paths = {p: v for p, v in spec["paths"].items() if p.startswith(prefixes)}
    schemas = spec.get("components", {}).get("schemas", {})
    used: set[str] = set()
    _refs(paths, used)
    pending = set(used)
    while pending:  # close over schemas referenced by schemas
        found: set[str] = set()
        _refs([schemas[n] for n in pending], found)
        pending = found - used
        used |= found
    return {
        "openapi": spec["openapi"],
        "info": {"title": title, "version": "1.0.0"},
        "paths": paths,
        "components": {"schemas": {n: schemas[n] for n in sorted(used)}},
    }


def authzen_contract() -> dict[str, Any]:
    return contract(*CONTRACTS[AUTHZEN_FILE])


def render(contract: dict[str, Any]) -> str:
    return json.dumps(contract, indent=2, sort_keys=True) + "\n"


ASYNCAPI = REPO / "contracts" / "asyncapi"
ASYNCAPI_FILE = ASYNCAPI / "channels" / "zoiko-id.asyncapi.json"
CLOUDEVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "CloudEvents 1.0 structured envelope with Zoiko iD extensions (ZID-EVENTS-001 §4).",
    "required": [
        "specversion",
        "id",
        "type",
        "source",
        "subject",
        "time",
        "tenantid",
        "cellid",
        "aggregatetype",
        "aggregateid",
        "aggregateversion",
        "correlationid",
        "data",
    ],
    "properties": {
        "specversion": {"const": "1.0"},
        "id": {"type": "string", "description": "UUIDv7; stable across retries and replays"},
        "type": {"type": "string", "pattern": r"^com\.zoiko\.id\.[a-z0-9_.]+\.v[0-9]+$"},
        "source": {"type": "string"},
        "subject": {"type": "string"},
        "time": {"type": "string", "format": "date-time"},
        "datacontenttype": {"const": "application/json"},
        "dataschema": {"type": "string"},
        "tenantid": {"type": "string", "format": "uuid"},
        "cellid": {"type": "string"},
        "aggregatetype": {"type": "string"},
        "aggregateid": {"type": "string"},
        "aggregateversion": {
            "type": "integer",
            "minimum": 1,
            "description": "ordering key per aggregate",
        },
        "correlationid": {"type": "string"},
        "causationid": {"type": ["string", "null"]},
        "classification": {"type": "string"},
        "data": {
            "type": "object",
            "description": "identifiers, versions and amounts only; no PII payloads",
        },
    },
}


def asyncapi_contract() -> dict[str, Any]:
    events: dict[str, dict[str, Any]] = {}
    for file, status in (
        ("event-catalogue.yaml", "catalogued"),
        ("proposed-events.yaml", "proposed"),
    ):
        for name, e in (yaml.safe_load((ASYNCAPI / file).read_text(encoding="utf-8")) or {})[
            "events"
        ].items():
            events[name] = {**e, "_status": status}
    channels: dict[str, Any] = {}
    messages: dict[str, Any] = {}
    for name in sorted(events):
        e = events[name]
        domain, lane = name.split(".")[0], lane_for(name)
        key = f"{domain}.{lane}"
        ch = channels.setdefault(
            key,
            {
                "address": f"zid.{{env}}.{{region}}.{domain}.{lane}",
                "parameters": {
                    "env": {"description": "deployment environment"},
                    "region": {"description": "home region of the publishing cell"},
                },
                "messages": {},
            },
        )
        mid = name.replace(".", "_")
        ch["messages"][mid] = {"$ref": f"#/components/messages/{mid}"}
        messages[mid] = {
            "name": e["cloudevents_type"],
            "title": name,
            "summary": e.get("meaning", ""),
            "contentType": "application/cloudevents+json",
            "payload": {"$ref": "#/components/schemas/CloudEvent"},
            "x-zid-source": e.get("source", ""),
            "x-zid-status": e["_status"],
        }
    operations = {
        f"publish_{k.replace('.', '_')}": {
            "action": "send",
            "channel": {"$ref": f"#/channels/{k}"},
            "summary": "Outbox relay; Kafka key = aggregatetype:aggregateid",
        }
        for k in sorted(channels)
    }
    return {
        "asyncapi": "3.0.0",
        "info": {
            "title": "Zoiko iD integration events",
            "version": "0.1.0",
            "description": "At-least-once delivery from the transactional outbox; consumers dedupe by id.",
        },
        "channels": channels,
        "operations": operations,
        "components": {"messages": messages, "schemas": {"CloudEvent": CLOUDEVENT_SCHEMA}},
    }


def main() -> None:
    ASYNCAPI_FILE.parent.mkdir(parents=True, exist_ok=True)
    ASYNCAPI_FILE.write_text(render(asyncapi_contract()), encoding="utf-8", newline="\n")
    sys.stdout.write(f"wrote {ASYNCAPI_FILE}\n")
    for path, (prefixes, title) in CONTRACTS.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(contract(prefixes, title)), encoding="utf-8", newline="\n")
        sys.stdout.write(f"wrote {path}\n")


if __name__ == "__main__":
    main()

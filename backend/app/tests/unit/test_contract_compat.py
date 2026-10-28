"""Contract compatibility gate (ZID-EVENTS-001 EVT-07, EVT-09; ZID-SVC-001 SVC-22, SVC-23).

A breaking field or type change, an unsafe enum expansion and a changed event type are each refused; an additive
change is not; a deliberate break needs a change id and a named approver.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
_spec = importlib.util.spec_from_file_location(
    "zid_tool_contract_compat", REPO / "tools/scripts/contract_compat.py"
)
assert _spec and _spec.loader
tool = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = tool
_spec.loader.exec_module(tool)


def openapi() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "paths": {
            "/v1/things": {
                "post": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "dry_run",
                            "required": False,
                            "schema": {"type": "boolean"},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/ThingIn"}}
                        }
                    },
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ThingOut"}
                                }
                            }
                        }
                    },
                },
                "get": {
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "object"}}}}
                    }
                },
            }
        },
        "components": {
            "schemas": {
                "ThingIn": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": ["A", "B"]},
                    },
                },
                "ThingOut": {
                    "type": "object",
                    "required": ["id", "status"],
                    "properties": {
                        "id": {"type": "string"},
                        "status": {"type": "string", "enum": ["OPEN", "CLOSED"]},
                        "note": {"type": "string"},
                    },
                },
            }
        },
    }


def breaks(change: Any) -> list[str]:
    new = openapi()
    change(new)
    return tool.breaking_openapi(openapi(), new)


def test_an_additive_change_is_compatible() -> None:
    """EVT-07: new optional request fields, new response fields, a new operation and a new enum in a request pass."""

    def add(new: dict[str, Any]) -> None:
        schemas = new["components"]["schemas"]
        schemas["ThingIn"]["properties"]["tag"] = {"type": "string"}
        schemas["ThingIn"]["properties"]["kind"]["enum"].append("C")  # a request that accepts more
        schemas["ThingOut"]["properties"]["created"] = {"type": "string"}
        new["paths"]["/v1/other"] = {"get": {"responses": {"200": {}}}}

    assert breaks(add) == []
    assert tool.breaking_openapi(openapi(), openapi()) == []


@pytest.mark.parametrize(
    ("change", "finding"),
    [
        (lambda n: n["paths"]["/v1/things"].pop("get"), "REMOVED_OPERATION GET /v1/things"),
        (lambda n: n["paths"].pop("/v1/things"), "REMOVED_OPERATION POST /v1/things"),
        (
            lambda n: n["components"]["schemas"]["ThingOut"]["properties"].pop("note"),
            "PROPERTY_REMOVED response.note",
        ),
        (
            lambda n: n["components"]["schemas"]["ThingOut"]["properties"]["id"].update(
                type="integer"
            ),
            "TYPE_CHANGED response.id",
        ),
        (
            lambda n: n["components"]["schemas"]["ThingOut"].update(required=["id"]),
            "REQUIRED_PROPERTY_BECAME_OPTIONAL response.status",
        ),
        (
            lambda n: n["components"]["schemas"]["ThingIn"]["required"].append("kind"),
            "REQUIRED_PROPERTY_ADDED body.kind",
        ),
        (
            lambda n: n["components"]["schemas"]["ThingIn"]["properties"].pop("name"),
            "PROPERTY_REMOVED body.name",
        ),
        (
            lambda n: n["components"]["schemas"]["ThingIn"]["properties"]["kind"].update(
                enum=["A"]
            ),
            "ENUM_VALUE_REMOVED body.kind",
        ),
        (
            lambda n: n["paths"]["/v1/things"]["post"]["parameters"].append(
                {
                    "in": "header",
                    "name": "x-tenant-hint",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ),
            "REQUIRED_PARAMETER_ADDED POST /v1/things header:x-tenant-hint",
        ),
        (
            lambda n: n["paths"]["/v1/things"]["post"]["responses"].pop("201"),
            "SUCCESS_RESPONSE_REMOVED POST /v1/things 201",
        ),
    ],
)
def test_a_breaking_change_is_found(change: Any, finding: str) -> None:
    """EVT-07 / SVC-22: removal, retyping, a new required field and a lost enum value cannot ride a minor release."""
    assert any(finding in f for f in breaks(change)), breaks(change)


def test_a_response_enum_gains_a_value_only_when_the_schema_says_unknown_values_are_safe() -> None:
    """EVT-09 / SVC-23: an expanded response enum is flagged unless it is marked open, where clients must treat an
    unknown value as unknown; a security-sensitive enum is simply never marked open, so it fails the gate."""

    def expand(new: dict[str, Any]) -> None:
        new["components"]["schemas"]["ThingOut"]["properties"]["status"]["enum"].append("ARCHIVED")

    found = breaks(expand)
    assert any("UNSAFE_ENUM_EXPANSION" in f and "ARCHIVED" in f for f in found)

    def expand_open(new: dict[str, Any]) -> None:
        expand(new)
        new["components"]["schemas"]["ThingOut"]["properties"]["status"]["x-open-enum"] = True

    assert breaks(expand_open) == []


def asyncapi() -> dict[str, Any]:
    return {
        "channels": {
            "agent.priority": {
                "messages": {"agent_revoked": {"$ref": "#/components/messages/agent_revoked"}}
            },
            "agent.domain": {
                "messages": {"agent_registered": {"$ref": "#/components/messages/agent_registered"}}
            },
        },
        "components": {
            "messages": {
                "agent_revoked": {
                    "name": "com.zoiko.id.agent.revoked.v1",
                    "title": "agent.revoked",
                },
                "agent_registered": {
                    "name": "com.zoiko.id.agent.registered.v1",
                    "title": "agent.registered",
                },
            },
            "schemas": {
                "CloudEvent": {
                    "type": "object",
                    "required": ["id", "type"],
                    "properties": {"id": {"type": "string"}, "type": {"type": "string"}},
                }
            },
        },
    }


def test_an_event_cannot_be_removed_retyped_or_moved_off_its_lane() -> None:
    """EVT-07: a changed event type or major version, a removed event and a priority event moved to the domain
    lane are breaking; a new event is not."""
    new = asyncapi()
    new["components"]["messages"]["agent_suspended"] = {
        "name": "com.zoiko.id.agent.suspended.v1",
        "title": "agent.suspended",
    }
    new["channels"]["agent.domain"]["messages"]["agent_suspended"] = {}
    assert tool.breaking_asyncapi(asyncapi(), new) == []

    retyped = copy.deepcopy(asyncapi())
    retyped["components"]["messages"]["agent_revoked"]["name"] = "com.zoiko.id.agent.revoked.v2"
    assert any(
        "EVENT_TYPE_CHANGED agent.revoked" in f for f in tool.breaking_asyncapi(asyncapi(), retyped)
    )

    removed = copy.deepcopy(asyncapi())
    del removed["components"]["messages"]["agent_registered"]
    assert any(
        "EVENT_REMOVED agent.registered" in f for f in tool.breaking_asyncapi(asyncapi(), removed)
    )

    moved = copy.deepcopy(asyncapi())
    moved["channels"]["agent.domain"]["messages"]["agent_revoked"] = moved["channels"][
        "agent.priority"
    ]["messages"].pop("agent_revoked")
    assert any(
        "EVENT_LANE_CHANGED agent.revoked: priority -> domain" in f
        for f in tool.breaking_asyncapi(asyncapi(), moved)
    )

    envelope = copy.deepcopy(asyncapi())
    envelope["components"]["schemas"]["CloudEvent"]["required"].append("region")
    envelope["components"]["schemas"]["CloudEvent"]["properties"]["region"] = {"type": "string"}
    assert any(
        "ENVELOPE_REQUIRED_FIELD_ADDED region" in f
        for f in tool.breaking_asyncapi(asyncapi(), envelope)
    )


def test_a_deliberate_break_needs_a_change_id_and_a_named_approver() -> None:
    """SVC-22: an accepted break names the finding, a change id and an approver; anything less does not count."""
    findings = [
        "x.json: REMOVED_OPERATION GET /v1/things",
        "y.json: PROPERTY_REMOVED response.note",
    ]
    full = {"finding": findings[0], "change_id": "CHG-1", "approved_by": "arb-chair"}
    assert tool.unaccepted(findings, [full]) == [findings[1]]
    for partial in (
        {"finding": findings[1], "change_id": "CHG-2"},
        {"finding": findings[1], "approved_by": "x"},
    ):
        assert tool.unaccepted(findings, [full, partial]) == [findings[1]]


def test_the_repository_contracts_are_compatible_with_themselves_and_ci_runs_the_gate() -> None:
    """SVC-21: the gate reads the real contract tree without error, and CI compares every change with the base
    branch (adjacent versions N and N-1) before it can merge."""
    assert tool.compare_dirs(REPO / "contracts", REPO / "contracts") == []
    accepted = json.loads(
        (REPO / "contracts/baseline/accepted-contract-changes.json").read_text(encoding="utf-8")
    )
    assert isinstance(accepted, list)
    workflow = yaml.safe_load((REPO / ".github/workflows/backend.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["static"]["steps"]
    gate = next(s for s in steps if "contract_compat.py" in s.get("run", ""))
    assert (
        gate["if"] == "github.event_name == 'pull_request'"
        and "git archive FETCH_HEAD contracts" in gate["run"]
    )
    assert (
        "github.base_ref" not in gate["run"]
    )  # the branch name arrives through env, never spliced into the script

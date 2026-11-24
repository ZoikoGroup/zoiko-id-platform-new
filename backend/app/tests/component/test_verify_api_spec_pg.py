"""The OpenAPI a partner downloads (CR-157): exactly the operations its server calls, with every reference resolving."""

from __future__ import annotations

from typing import Any

from test_verify_api_go_live_pg import Company
from zid_platform.modules.verify_api.application import partner_spec


def refs(node: Any, out: set[str]) -> set[str]:
    if isinstance(node, dict):
        r = node.get("$ref")
        if isinstance(r, str):
            out.add(r)
        for v in node.values():
            refs(v, out)
    elif isinstance(node, list):
        for v in node:
            refs(v, out)
    return out


def test_the_published_spec_is_exactly_the_partner_operations_and_is_self_contained(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        r = co.http.get("/v1/verify-api/openapi.json")  # no sign-in
        assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
        spec = r.json()
        listed = {(m, p) for p, ops in spec["paths"].items() for m in ops}
        assert listed == set(
            partner_spec.PARTNER_OPERATIONS
        )  # every operation is present, nothing else
        # no console, go-live, usage or hosted-page route is published to partners
        console_only = ("applications", "go-live", "usage", "hosted", "sessions", "openapi.json")
        for path in spec["paths"]:
            assert not path.startswith(tuple(f"/v1/verify-api/{c}" for c in console_only)), path
        schemas = spec["components"]["schemas"]
        for ref in refs(spec, set()):
            assert ref.startswith("#/components/schemas/") and ref.rsplit("/", 1)[-1] in schemas, (
                ref
            )
        assert spec["info"]["title"] == "Zoiko iD Verify API" and spec["openapi"].startswith("3.")
        # it describes the interface and nothing about anyone's data
        assert "tenant" not in str(spec["info"]).lower()
        # a developer's token request and session request are described with their fields
        create = spec["paths"]["/v1/verify-api/verification-sessions"]["post"]
        names = {p["name"] for p in create.get("parameters", [])}
        assert "Idempotency-Key" in names or any("idempotency" in n.lower() for n in names)
    finally:
        co.close()


def test_a_missing_operation_is_skipped_not_a_crash_and_unused_schemas_are_dropped() -> None:
    full = {
        "openapi": "3.1.0",
        "paths": {
            "/v1/verify-api/whoami": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/Who"}}
                            }
                        }
                    }
                }
            },
            "/v1/verify-api/applications": {
                "get": {"responses": {"200": {"$ref": "#/components/schemas/Secret"}}}
            },
        },
        "components": {
            "schemas": {
                "Who": {"properties": {"x": {"$ref": "#/components/schemas/Inner"}}},
                "Inner": {"type": "string"},
                "Secret": {"type": "object"},
                "Unused": {"type": "object"},
            }
        },
    }
    out = partner_spec.build(full)
    assert list(out["paths"]) == ["/v1/verify-api/whoami"]
    assert set(out["components"]["schemas"]) == {
        "Who",
        "Inner",
    }  # reached through a reference; the console's and the unused ones are gone

"""The published OpenAPI for a partner's server (CR-157; ZID-EXT-IDV-001 epic E13, QA Q48).

The platform's contract for the Verify API holds every route under `/v1/verify-api/`, including the ones a signed-in administrator uses in the console. A developer integrating a server needs only the routes
their server calls. This keeps exactly those operations, and only the schemas they refer to, so the file a partner downloads is the contract and nothing else. The list is explicit: a new route is not
published to partners until it is added here (and documented), never by accident.
"""

from __future__ import annotations

import copy
from typing import Any

PARTNER_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("post", "/v1/verify-api/oauth2/token"),
    ("get", "/v1/verify-api/whoami"),
    ("get", "/v1/verify-api/workflows"),
    ("get", "/v1/verify-api/products"),
    ("post", "/v1/verify-api/verification-sessions"),
    ("get", "/v1/verify-api/verification-sessions"),
    ("get", "/v1/verify-api/verification-sessions/{session_id}"),
    ("get", "/v1/verify-api/verification-sessions/{session_id}/decision"),
    ("post", "/v1/verify-api/verification-sessions/{session_id}:cancel"),
    ("post", "/v1/verify-api/verification-sessions/{session_id}/resume-token"),
    ("post", "/v1/verify-api/verification-sessions/{session_id}:recheck"),
    ("post", "/v1/verify-api/sandbox/fixtures/{outcome}/sessions"),
    ("post", "/v1/verify-api/sandbox/sessions/{session_id}:simulate"),
)


def _refs(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            out.add(ref.rsplit("/", 1)[-1])
        for v in node.values():
            _refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _refs(v, out)


def build(full: dict[str, Any]) -> dict[str, Any]:
    """The partner-facing document from the whole application's OpenAPI. Unknown operations in the list are skipped, so a missing route is caught by the test, not by a crash."""
    paths: dict[str, dict[str, Any]] = {}
    for method, path in PARTNER_OPERATIONS:
        op = full.get("paths", {}).get(path, {}).get(method)
        if op is not None:
            paths.setdefault(path, {})[method] = copy.deepcopy(op)
    used: set[str] = set()
    _refs(paths, used)
    schemas = full.get("components", {}).get("schemas", {})
    keep: dict[str, Any] = {}
    pending = sorted(used)
    while pending:
        name = pending.pop()
        if name in keep or name not in schemas:
            continue
        keep[name] = copy.deepcopy(schemas[name])
        more: set[str] = set()
        _refs(keep[name], more)
        pending.extend(sorted(more - set(keep)))
    return {
        "openapi": full.get("openapi", "3.1.0"),
        "info": {
            "title": "Zoiko iD Verify API",
            "version": "1",
            "description": (
                "Verify your own customers from your own server. Exchange your client credentials for a short-lived bearer token, create a verification session with an Idempotency-Key, send your "
                "customer to the launch link, and read the minimized decision. Everything here runs in the sandbox until your company is approved to go live."
            ),
        },
        "paths": dict(sorted(paths.items())),
        "components": {"schemas": dict(sorted(keep.items()))},
    }

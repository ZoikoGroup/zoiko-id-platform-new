"""ZID-INT-002 section 07: "no runtime rule edit without dual approval".

Service packs are versioned configuration reviewed with the code, and there is NO route that creates, edits, approves or retires one at
runtime. This test pins that: every route that touches packs only reads, so a future route that mutates a pack fails here and has to bring
its own maker/checker approval first.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zid_platform.main import create_app
from zid_platform.settings import Settings

READ_ONLY = {"GET", "HEAD", "OPTIONS"}


def _contexts(app: Any) -> Iterator[Any]:
    for r in app.routes:
        if type(r).__name__ == "_IncludedRouter":
            yield from r.effective_route_contexts()
        elif getattr(r, "methods", None):
            yield r


def test_no_route_can_change_a_service_pack_at_runtime() -> None:
    app = create_app(Settings(database_url=None))
    service_pack_paths = ("credential-packs", "service-packs", "/v2/coverage", "/v2/capabilities")
    pack_routes = [
        (r.path, sorted(r.methods or ()))
        for r in _contexts(app)
        if any(marker in r.path.lower() for marker in service_pack_paths)
    ]
    assert any("credential-packs" in p for p, _ in pack_routes), (
        "the pack read routes must exist for this guard to mean anything"
    )
    mutating = [(p, m) for p, m in pack_routes if set(m) - READ_ONLY]
    assert mutating == [], f"a route mutates packs without a maker/checker approval: {mutating}"

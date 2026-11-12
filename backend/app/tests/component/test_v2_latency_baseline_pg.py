"""A LOCAL latency baseline for the intake routes (ZID-INT-002 section 14.2: p95 <= 400 ms excluding provider calls).

This runs the real app against real Postgres inside one process, so it measures this code and this database on this machine, with no
network and no load from other callers. It proves the routes are not accidentally slow; it is NOT the document's release evidence, which
needs load at expected traffic and synthetic probes from two regions against a deployed environment (`python -m zid_platform.slo_probe`).
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.settings import Settings
from zid_platform.slo_probe import Sample, summarise

SAMPLES = 40
PROBES = [
    ("POST", "/v2/age-checks", {"threshold": 18, "estimate": {"estimated_age": 40, "margin": 3}}),
    (
        "POST",
        "/v2/rooms/property-checks",
        {
            "property_ref": "p",
            "host_ref": "h",
            "relationship": "owner",
            "jurisdiction": {"country": "GB", "subdivision": "GB-ENG"},
            "requested_dimensions": ["tenant_legal_route"],
        },
    ),
    ("GET", "/v2/coverage?country=GB", None),
]


def test_intake_routes_answer_within_the_p95_target_locally(rt_url: str, capsys) -> None:
    o = onboard(
        rt_url,
        extra_actions=("credential.claim.verify",),
        extra_resources=("age-check:new", "credential-claim:*"),
    )
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1",
        ),
        keyring=o.ring,
    )  # fmt: skip
    mine = o.bearer(o.owner)
    samples: list[Sample] = []
    with TestClient(app, base_url=ISS) as http:
        for method, path, body in PROBES:  # one warm-up each: pool and imports are not what is measured
            http.request(method, path, headers=mine, json=body)
        for _ in range(SAMPLES):
            for method, path, body in PROBES:
                start = time.perf_counter()
                r = http.request(method, path, headers=mine, json=body)
                ms = (time.perf_counter() - start) * 1000
                samples.append(Sample(f"{method} {path}", r.status_code, ms))
    report = summarise(samples)
    with capsys.disabled():
        routes = {k: v["p95_ms"] for k, v in report["routes"].items()}  # type: ignore[attr-defined]
        print("\nLOCAL BASELINE", routes, report["measured"])
    assert all(s.status == 200 for s in samples), [s for s in samples if s.status != 200][:3]
    assert report["met"] is True, report["breaches"]

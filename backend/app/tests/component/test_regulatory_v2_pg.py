"""The ZID-RCG-003 section 09.2 regulatory API at its own paths (CR-085), through real operator sessions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from operator_fixture import ISS
from test_conformance_pg import _body as run_body
from test_regulatory_api_pg import _body as pack_body
from test_regulatory_releases_api_pg import _app, _attestation, _audit, _grant, _people


def test_the_section_09_2_routes_reach_the_same_rules_as_the_operator_api(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    provider = f"PARTNER-{uuid.uuid4().hex[:8]}"
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, path: str, person: str, **kw: Any) -> Any:
            return api.request(method, path, headers=o.bearer(who[person]), **kw)

        for person, cap in (
            ("recorder", "regulatory.attestation.record"),
            ("launcher", "regulatory.suspension.apply"),
            ("author", "regulatory.conformance.record"),
            ("vendor", "regulatory.pack.author"),
        ):
            _grant(api, o, who, person, cap)

        # provider-attestations/refresh records the official register's answer; providers/{id}/services shows only what it proves
        scope = {"entity": provider, "scheme": "UK_DIATF", "country": "GB", "service_code": "RTR"}
        r = call(
            "POST",
            "/v2/regulatory/provider-attestations/refresh",
            "recorder",
            json=_attestation(scope),
        )
        assert r.status_code == 201, r.text
        services = call("GET", f"/v2/regulatory/providers/{provider}/services", "recorder").json()[
            "services"
        ]
        assert [(s["service_code"], s["certified"]) for s in services] == [("RTR", True)]
        assert (
            call("GET", f"/v2/regulatory/providers/{provider}-other/services", "recorder").json()[
                "services"
            ]
            == []
        )  # nothing inferred

        # holds: the kill switch at the document's path; the provider's service then shows as held
        hold = {
            "suspension_id": f"hold-{uuid.uuid4().hex[:8]}",
            "scope": {"provider": provider, "service_id": "RTR", "country": "GB"},
            "trigger": "CERTIFICATION_WITHDRAWN",
            "reason": "the register shows the RTR certificate withdrawn",
            "evidence_ref": "INC-99",
        }
        assert call("POST", "/v2/regulatory/holds", "launcher", json=hold).status_code == 201
        services = call("GET", f"/v2/regulatory/providers/{provider}/services", "recorder").json()[
            "services"
        ]
        assert services[0]["certified"] is False and services[0]["reason"].startswith("suspended:")

        # pack-revisions: the same lifecycle (a draft is open, so a second revision is refused by the same rule)
        draft = pack_body({"product": f"P{uuid.uuid4().hex[:8].upper()}", "purpose": "ENGAGEMENT"})
        assert call("POST", "/v1/regulatory/policy-packs", "vendor", json=draft).status_code == 201
        r = call(
            "POST",
            "/v2/regulatory/pack-revisions",
            "vendor",
            json={"pack_id": draft["pack_id"], "rules": {"evidence_minimum": ["X"]}},
        )
        assert r.status_code == 409, (r.status_code, r.text)
        assert _code(r) == "PACK_DRAFT_OPEN"
        r = call(
            "POST",
            f"/v2/regulatory/pack-revisions/{draft['pack_id']}:v1/approve",
            "vendor",
            json={"role": "legal", "signed_hash": "0" * 64},
        )
        assert r.status_code == 403  # the author holds no approver capability

        # evidence-bundles: a stated purpose and legal basis, or nothing (and the refusal is audited)
        assert (
            call(
                "POST", "/v1/regulatory/conformance-runs", "author", json=run_body(run_id)
            ).status_code
            == 201
        )
        r = call("GET", f"/v2/regulatory/evidence-bundles/run:{run_id}", "recorder")
        assert r.status_code == 422 and _code(r) == "EXPORT_PURPOSE_REQUIRED"
        r = call(
            "GET",
            f"/v2/regulatory/evidence-bundles/run:{run_id}",
            "recorder",
            params={"purpose": "REGULATOR_REQUEST", "legal_basis_ref": "LEGAL-REQ-2026-7"},
        )
        assert r.status_code == 200, r.text
        bundle = r.json()
        assert bundle["content"]["run_id"] == run_id and len(bundle["digest"]) == 64
        assert bundle["signature_status"] in ("SIGNED", "NO_SIGNING_KEY")
        assert (
            call(
                "GET",
                "/v2/regulatory/evidence-bundles/run:nobody",
                "recorder",
                params={"purpose": "AUDIT", "legal_basis_ref": "LB-1"},
            ).status_code
            == 404
        )

    records = _audit(owner_url, f"run:{run_id}")
    events = [(r["event_type"], r["reason_code"]) for r in records]
    assert ("regulatory.evidence_bundle.refused", "EXPORT_PURPOSE_REQUIRED") in events
    assert ("regulatory.evidence_bundle.exported", None) in events
    assert datetime.now(UTC) - timedelta(minutes=30) < datetime.fromisoformat(bundle["exported_at"])


def _code(r: Any) -> str:
    """/v1 answers with the platform envelope, /v2 with RFC 7807 problem details (lowercase code)."""
    body = r.json()
    return str(body["error"]["code"]) if "error" in body else str(body.get("code", "")).upper()

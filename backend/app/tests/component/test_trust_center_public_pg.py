"""The public Trust Center over HTTP (ZID-CP-WF12 WF-12.01 to 12.08; CR-174), against PostgreSQL: only approved, current facts are public; an empty history is never 100%;
security.txt is served only when true; a vulnerability report returns a reference and never its text; an advisory is public only once a second person approved it."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from conftest import CELL, engine
from fastapi.testclient import TestClient
from operator_fixture import AUD, ISS, Operators, activate_grant, operators
from sqlalchemy import text
from zid_platform.main import create_app
from zid_platform.modules.operator_access.domain.model import CAPABILITIES, OperatorRole
from zid_platform.settings import Settings

SCOPE = "regulatory:*"
TODAY = date.today()
NAMES = ("author", "evidence_mgr", "evidence_checker", "sec", "legal", "publisher", "recorder", "validator", "drafter", "approver", "triager")


def uid() -> str:
    return str(uuid.uuid4())


def _people(rt_url: str) -> tuple[Operators, dict[str, str]]:
    who = {n: uid() for n in NAMES} | {"grant_1": uid(), "grant_2": uid()}
    roles: dict[str, set[OperatorRole]] = {who[n]: {OperatorRole.REQUESTER} for n in NAMES}
    roles[who["grant_1"]] = {OperatorRole.APPROVER}
    roles[who["grant_2"]] = {OperatorRole.APPROVER}
    return operators(rt_url, roles), who


def _app(rt_url: str, o: Operators, **extra: Any) -> Any:
    return create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1", cell_uuid=CELL, operator_tenant_id=o.tenant, operator_client_ids=(o.client,), **extra),
        keyring=o.ring,
    )  # fmt: skip


def _grant(api: TestClient, o: Operators, who: dict[str, str], person: str, capability: str) -> None:
    approvers = (who["grant_1"], who["grant_2"]) if CAPABILITIES[capability].two_person else (who["grant_1"],)
    activate_grant(api, o, who[person], approvers, capability=capability, resource_scope=SCOPE)


def _sql(owner_url: str, stmt: str, **params: Any) -> None:
    async def go() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text(stmt), params)

    asyncio.run(go())


def test_the_public_reads_show_only_what_is_approved_current_and_confirmed(rt_url: str, owner_url: str) -> None:
    o, who = _people(rt_url)
    digest = hashlib.sha256(b"public-soc2").hexdigest()
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        for person, cap in (
            ("author", "trust.claim.author"), ("evidence_mgr", "trust.evidence.manage"), ("evidence_checker", "trust.evidence.manage"),
            ("sec", "trust.claim.review_security"), ("legal", "trust.claim.review_legal"), ("publisher", "trust.claim.publish"),
        ):  # fmt: skip
            _grant(api, o, who, person, cap)

        # nothing is public yet, and every public read works with no sign-in
        assert api.get("/public/trust/v1/claims").json()["items"] == []
        empty = api.get("/public/trust/v1/certifications").json()
        assert empty["current"] == [] and empty["history"] == []

        ev = call("POST", "/v1/trust-center/evidence", "evidence_mgr", json={
            "digest": digest, "evidence_type": "report", "scheme": "SOC 2", "issuer": "Auditor LLP", "assessor": "Auditor LLP", "legal_entity": "entity_demo",
            "products": ["verify_api"], "regions": ["US"], "period_start": str(TODAY - timedelta(days=30)), "period_end": str(TODAY + timedelta(days=300)), "holder": "zoiko", "restricted": True,
        }).json()  # fmt: skip
        assert api.get("/public/trust/v1/certifications").json()["current"] == []  # not confirmed by the issuer: not shown at all
        call("POST", f"/v1/trust-center/evidence/{ev['evidence_id']}:confirm", "evidence_checker")
        certs = api.get("/public/trust/v1/certifications").json()
        assert len(certs["current"]) == 1
        row = certs["current"][0]
        assert row["kind"] == "Independent audit report" and "certif" not in row["kind"].lower() and row["full_report"] == "Available on request under NDA." and row["is_zoiko"] is True

        claim = call("POST", "/v1/trust-center/claims", "author", json={
            "exact_text": "An independent SOC 2 report is available on request", "locale": "en-US", "claim_type": "third_party_assessment", "entity_id": "entity_demo",
            "service": "verify_api", "markets": ["US"], "channels": ["trust_center"], "evidence_digests": [digest], "valid_until": str(TODAY + timedelta(days=200)),
        }).json()  # fmt: skip
        cid, h = claim["claim_id"], claim["content_hash"]
        assert api.get("/public/trust/v1/claims").json()["items"] == []  # drafted is not public
        for person, role in (("sec", "security"), ("legal", "legal")):
            call("POST", f"/v1/trust-center/claims/{cid}/revisions/1:approve", person, json={"role": role, "evidence_digests": [digest]})
        assert call("POST", f"/v1/trust-center/claims/{cid}/revisions/1:publish", "publisher", json={"expected_hash": h}).status_code == 200
        shown = api.get("/public/trust/v1/claims").json()
        assert [i["claim_id"] for i in shown["items"]] == [cid]
        item = shown["items"][0]
        assert item["effective_as_of"] and item["evidence"][0]["scheme"] == "SOC 2" and "author" not in item and "approvals" not in item and digest not in str(item)

        # evidence that lapses takes the claim off the public page at once, with no sweep needed
        call("POST", f"/v1/trust-center/evidence/{ev['evidence_id']}:lifecycle", "evidence_mgr", json={"lifecycle": "SUSPENDED"})
        assert api.get("/public/trust/v1/claims").json()["items"] == []
        hist = api.get("/public/trust/v1/certifications").json()
        assert hist["current"] == [] and len(hist["history"]) == 1 and hist["history"][0]["state"] == "SUSPENDED"


def test_availability_is_never_one_hundred_percent_without_a_validated_figure(rt_url: str) -> None:
    o, who = _people(rt_url)
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        first = api.get("/public/status/v1/availability").json()
        assert first["services"] and all(s["state"] == "METRIC_UNAVAILABLE" and s["percent"] is None for s in first["services"])
        assert "target" in first["sla"].lower() and "not measured" in first["sla"].lower()
        svc_name = first["services"][0]["service"]

        _grant(api, o, who, "recorder", "trust.status.record")
        _grant(api, o, who, "validator", "trust.status.validate")
        body = {"service": svc_name, "region": "uk-1", "window_label": "2026-10", "numerator": 9990, "denominator": 10000, "exclusions": "Planned maintenance excluded.", "source_ref": "telemetry:window-2026-10"}
        bad = call("POST", "/v1/trust-center/availability/snapshots", "recorder", json=body | {"raw_good": 9000, "raw_total": 10000})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "FIGURES_DO_NOT_RECONCILE"
        rec = call("POST", "/v1/trust-center/availability/snapshots", "recorder", json=body | {"raw_good": 9990, "raw_total": 10000})
        assert rec.status_code == 201, rec.text
        sid = rec.json()["snapshot_id"]
        mid = api.get("/public/status/v1/availability").json()["services"]
        assert all(s["percent"] is None for s in mid)  # recorded but not validated: still not shown
        assert call("POST", f"/v1/trust-center/availability/snapshots/{sid}:validate", "recorder").status_code == 403  # holds no validate capability
        assert call("POST", f"/v1/trust-center/availability/snapshots/{sid}:validate", "validator").status_code == 200
        done = {s["service"]: s for s in api.get("/public/status/v1/availability").json()["services"]}
        assert done[svc_name]["percent"] == 99.9 and done[svc_name]["state"] == "MEASURED" and "maintenance" in done[svc_name]["note"].lower()
        assert call("POST", f"/v1/trust-center/availability/snapshots/{sid}:validate", "validator").status_code == 404  # already validated


def test_security_txt_is_served_only_when_it_is_true(rt_url: str) -> None:
    o, _ = _people(rt_url)
    with TestClient(_app(rt_url, o), base_url=ISS) as api:
        assert api.get("/.well-known/security.txt").status_code == 404  # no contact configured: no file
    soon = datetime.now(UTC) + timedelta(days=120)
    with TestClient(_app(rt_url, o, trust_security_contact="mailto:security@example.test", trust_security_expires=soon, trust_security_policy_url="https://example.test/security"), base_url=ISS) as api:
        r = api.get("/.well-known/security.txt")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain") and "utf-8" in r.headers["content-type"].lower()
        from zid_platform.modules.trust_center.domain import security_txt as stx

        assert stx.check(r.text, content_type=r.headers["content-type"], served_url=f"{AUD}/.well-known/security.txt", now=datetime.now(UTC)) == []
    with TestClient(_app(rt_url, o, trust_security_contact="mailto:security@example.test", trust_security_expires=datetime.now(UTC) - timedelta(days=1)), base_url=ISS) as api:
        assert api.get("/.well-known/security.txt").status_code == 503  # a lapsed file is reported, never served


def test_a_vulnerability_report_returns_a_reference_and_never_its_text_and_staff_triage_it(rt_url: str, owner_url: str) -> None:
    o, who = _people(rt_url)
    secret_detail = "The sign-in page reveals whether an address is registered, see the third step in the flow."
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        assert api.post("/public/trust/v1/vulnerability-reports", json={"summary": "too short"}).status_code == 422
        r = api.post("/public/trust/v1/vulnerability-reports", json={"summary": secret_detail, "severity": "high", "contact": "researcher@example.test"})
        assert r.status_code == 201, r.text
        receipt = r.json()
        assert receipt["reference"].startswith("VR-") and "target" in receipt["note"].lower() and secret_detail not in r.text and "researcher@example.test" not in r.text
        for _ in range(4):
            assert api.post("/public/trust/v1/vulnerability-reports", json={"summary": secret_detail}).status_code == 201
        assert api.post("/public/trust/v1/vulnerability-reports", json={"summary": secret_detail}).status_code == 429  # five an hour from one source

        assert call("GET", "/v1/trust-center/reports", "triager").status_code == 403
        _grant(api, o, who, "triager", "trust.report.triage")
        listed = call("GET", "/v1/trust-center/reports?open=true", "triager").json()["items"]
        assert len(listed) == 5 and "summary" not in listed[0]
        rid = next(i["report_id"] for i in listed if i["reference"] == receipt["reference"])
        full = call("GET", f"/v1/trust-center/reports/{rid}", "triager").json()
        assert full["summary"] == secret_detail and full["contact"] == "researcher@example.test" and full["timeline"][0]["kind"] == "RECEIVED"
        skip = call("POST", f"/v1/trust-center/reports/{rid}:triage", "triager", json={"state": "FIXED"})
        assert skip.status_code == 422 and skip.json()["error"]["code"] == "REPORT_STATE_INVALID"
        ack = call("POST", f"/v1/trust-center/reports/{rid}:triage", "triager", json={"state": "ACKNOWLEDGED", "note": "Thanks, looking now"})
        assert ack.status_code == 200 and ack.json()["state"] == "ACKNOWLEDGED" and ack.json()["acknowledged_at"]
    _sql(owner_url, "SELECT 1")


def test_an_advisory_is_public_only_after_a_second_person_approves_and_a_correction_never_erases(rt_url: str, owner_url: str) -> None:
    o, who = _people(rt_url)
    incident = uuid.uuid4()
    _sql(owner_url, "INSERT INTO platform.incident (incident_id, title, severity, security, declared_by) VALUES (:i, 'Delayed sign-in', 'SEV2', false, :by)", i=incident, by=uuid.uuid4())
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        _grant(api, o, who, "drafter", "trust.advisory.draft")
        _grant(api, o, who, "approver", "trust.advisory.approve")
        none = call("POST", "/v1/trust-center/advisories", "drafter", json={"incident_ref": str(uuid.uuid4()), "title": "Delayed sign-in", "body": "Some sign-ins were delayed."})
        assert none.status_code == 404  # no such incident in the operators' registry
        leak = call("POST", "/v1/trust-center/advisories", "drafter", json={"incident_ref": str(incident), "title": "Delayed sign-in", "body": "Cause: host 10.2.3.4 ran out of memory."})
        assert leak.status_code == 422 and leak.json()["error"]["code"] == "ADVISORY_REFUSED"
        d = call("POST", "/v1/trust-center/advisories", "drafter", json={"incident_ref": str(incident), "title": "Delayed sign-in", "body": "Some sign-ins were delayed between 10:00 and 10:40 UTC."})
        assert d.status_code == 201, d.text
        aid = d.json()["advisory_id"]
        assert api.get("/public/trust/v1/advisories").json()["items"] == []  # drafted is not public
        assert call("POST", f"/v1/trust-center/advisories/{aid}/versions/1:approve", "drafter").status_code == 403
        assert call("POST", f"/v1/trust-center/advisories/{aid}/versions/1:approve", "approver").status_code == 200
        pub = api.get("/public/trust/v1/advisories").json()["items"]
        assert len(pub) == 1 and pub[0]["versions"][0]["kind"] == "PUBLISH"
        no_reason = call("POST", "/v1/trust-center/advisories", "drafter", json={"incident_ref": str(incident), "kind": "CORRECTION", "title": "Delayed sign-in", "body": "Delayed between 10:05 and 10:40 UTC."})
        assert no_reason.status_code == 422
        fix = call("POST", "/v1/trust-center/advisories", "drafter", json={"incident_ref": str(incident), "kind": "CORRECTION", "title": "Delayed sign-in", "body": "Delayed between 10:05 and 10:40 UTC.", "reason": "The first start time was wrong."})
        assert fix.status_code == 201
        assert len(api.get("/public/trust/v1/advisories").json()["items"][0]["versions"]) == 1  # the correction waits for its own approval
        assert call("POST", f"/v1/trust-center/advisories/{aid}/versions/2:approve", "approver").status_code == 200
        versions = api.get("/public/trust/v1/advisories").json()["items"][0]["versions"]
        assert [v["seq"] for v in versions] == [1, 2] and versions[1]["correction_reason"] == "The first start time was wrong." and "10:00" in versions[0]["body"]


def test_monitoring_closes_the_release_gate_when_a_live_claim_loses_its_support_and_the_scan_finds_drift(rt_url: str) -> None:
    o, who = _people(rt_url)
    digest = hashlib.sha256(b"monitor-soc2").hexdigest()
    sentence = "An independent SOC 2 report is available on request"
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, url: str, person: str, **kw: Any) -> Any:
            return api.request(method, url, headers=o.bearer(who[person]), **kw)

        for person, cap in (
            ("author", "trust.claim.author"), ("evidence_mgr", "trust.evidence.manage"), ("evidence_checker", "trust.evidence.manage"),
            ("sec", "trust.claim.review_security"), ("legal", "trust.claim.review_legal"), ("publisher", "trust.claim.publish"),
        ):  # fmt: skip
            _grant(api, o, who, person, cap)
        assert call("GET", "/v1/trust-center/monitoring", "triager").status_code == 403
        ev = call("POST", "/v1/trust-center/evidence", "evidence_mgr", json={
            "digest": digest, "evidence_type": "report", "scheme": "SOC 2", "issuer": "Auditor LLP", "legal_entity": "entity_demo", "products": ["verify_api"], "regions": ["US"],
            "period_start": str(TODAY - timedelta(days=30)), "period_end": str(TODAY + timedelta(days=10)), "holder": "zoiko",
        }).json()  # fmt: skip
        call("POST", f"/v1/trust-center/evidence/{ev['evidence_id']}:confirm", "evidence_checker")
        c = call("POST", "/v1/trust-center/claims", "author", json={
            "exact_text": sentence, "locale": "en-US", "claim_type": "third_party_assessment", "entity_id": "entity_demo", "service": "verify_api", "markets": ["US"],
            "channels": ["trust_center"], "evidence_digests": [digest], "valid_until": str(TODAY + timedelta(days=200)),
        }).json()  # fmt: skip
        for person, role in (("sec", "security"), ("legal", "legal")):
            call("POST", f"/v1/trust-center/claims/{c['claim_id']}/revisions/1:approve", person, json={"role": role, "evidence_digests": [digest]})
        call("POST", f"/v1/trust-center/claims/{c['claim_id']}/revisions/1:publish", "publisher", json={"expected_hash": c["content_hash"]})

        m = call("GET", "/v1/trust-center/monitoring", "author").json()
        assert m["counts"]["live_claims"] == 1 and m["unsupported_live_claims"] == 0 and m["release_gate"]["open"] is True
        assert m["expiring"] and m["expiring"][0]["days_left"] == 10  # the evidence ends in ten days: on the watch list

        scan = call("POST", "/v1/trust-center/scans", "author", json={"pages": [{"url": "https://www.example.test/", "content": f"<p>{sentence}.</p><p>We are 99.99% available.</p>"}]})
        assert scan.status_code == 200 and scan.json()["clean"] is False
        assert [f["code"] for f in scan.json()["findings"]] == ["UNREGISTERED_CLAIM"] and "99.99%" in scan.json()["findings"][0]["sentence"]

        # the evidence is suspended: the claim is unsupported at once, the gate closes, and the public page has already dropped it
        call("POST", f"/v1/trust-center/evidence/{ev['evidence_id']}:lifecycle", "evidence_mgr", json={"lifecycle": "SUSPENDED"})
        m2 = call("GET", "/v1/trust-center/monitoring", "author").json()
        assert m2["unsupported_live_claims"] == 1 and m2["release_gate"]["open"] is False and "no current support" in m2["release_gate"]["reasons"][0]
        assert api.get("/public/trust/v1/claims").json()["items"] == []

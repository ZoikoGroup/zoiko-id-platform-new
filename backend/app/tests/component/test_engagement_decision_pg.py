"""ZID-RCG-003 section 10.1 over HTTP: the Zoikorum engagement decision on a real /v2 case keeps qualification and practice eligibility apart,
cites only positive evidence and leaves acceptance to the marketplace. WF-05: the trust-list view an operator relies on."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from operator_fixture import ISS
from test_regulatory_releases_api_pg import _app, _grant, _people
from test_v2_cases_pg import CASE, TASK, key
from test_v2_facade_pg import world

ENGAGEMENT = {
    "professional_ref": "pro-77",
    "role": "advisor",
    "services": ["legal.advice"],
    "country": "US",
    "subdivision": "US-CA",
    "profession": "lawyer",
    "audience": "zoikorum",
}


def test_a_verified_license_without_an_approved_pack_is_not_practice_eligibility(
    rt_url: str,
) -> None:
    now = datetime.now(UTC)
    with world(rt_url) as (http, mine, _tenant, _admin):
        c = http.post(
            "/v2/verifications",
            headers={**mine, **key("eng-case-0001")},
            json={**CASE, "requested_checks": ["license"]},
        ).json()
        base = f"/v2/verifications/{c['case_id']}"
        done = http.post(
            f"{base}/tasks/license", headers={**mine, **key("eng-task-0001")}, json=TASK
        )
        assert done.status_code == 200, done.text

        body = ENGAGEMENT | {
            "period_from": now.isoformat(),
            "period_to": (now + timedelta(days=30)).isoformat(),
        }
        r = http.post(f"{base}/engagement-decision", headers=mine, json=body)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["credential_truth"] == {"license": "VERIFIED"}
        # no pack is approved for US / US-CA / lawyer in the shipped configuration: never practice eligibility
        assert d["practice_eligibility"] == "PRACTICE_ELIGIBILITY_NOT_ESTABLISHED"
        assert d["qualification"] == "QUALIFICATION_NOT_ESTABLISHED"  # no degree was checked
        assert any(x.startswith("NO_APPROVED_PACK:") for x in d["reasons"])
        assert "VERIFIED" not in (d["qualification"], d["practice_eligibility"])
        assert d["assertion"]["audience"] == "zoikorum" and d["assertion"]["revocable"] is True
        assert d["assertion"]["evidence_refs"] and d["assertion"]["evidence_refs"][0].startswith(
            "cred_"
        )
        assert d["engagement_acceptance"] == "DECIDED_BY_MARKETPLACE"

        missing = http.post(
            "/v2/verifications/case_00000000-0000-0000-0000-000000000000/engagement-decision",
            headers=mine,
            json=body,
        )
        assert missing.status_code == 404


def test_the_trust_list_view_reports_whether_qualified_status_can_be_assessed(rt_url: str) -> None:
    o, who = _people(rt_url)
    with TestClient(_app(rt_url, o), base_url=ISS) as api:
        h = o.bearer(who["recorder"])
        assert api.get("/v1/regulatory/trust-lists", headers=h).status_code == 403
        _grant(api, o, who, "recorder", "regulatory.attestation.record")
        r = api.get("/v1/regulatory/trust-lists", headers=h)
        assert r.status_code == 200, r.text
        v = r.json()
        assert v["qualified_status_assessable"] == any(x["authenticated"] for x in v["lists"])
        if not v["qualified_status_assessable"]:
            assert v["reasons"] == ["NO_AUTHENTICATED_TRUST_LIST"]
        assert "not lawful authority" in v["interpretation"]

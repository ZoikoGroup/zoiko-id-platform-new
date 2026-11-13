"""Service offerings over HTTP (CR-088): the operator view says why each offering is or is not available in each country, and the public
catalog lists only what the publish-coverage rule allows, with nothing but the approved wording."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from operator_fixture import ISS
from test_conformance_pg import _body as run_body
from test_regulatory_releases_api_pg import _app, _grant, _people

API_ONLY = "GLOBAL_APIS_INTEGRATIONS"  # no personal data and no country capability: needs only accepted tests


def test_offerings_are_published_only_where_every_check_holds(rt_url: str) -> None:
    o, who = _people(rt_url)
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, path: str, person: str, **kw: Any) -> Any:
            return api.request(method, path, headers=o.bearer(who[person]), **kw)

        assert call("GET", "/v1/regulatory/offerings", "recorder").status_code == 403
        _grant(api, o, who, "recorder", "regulatory.attestation.record")
        _grant(api, o, who, "author", "regulatory.conformance.record")
        _grant(api, o, who, "vendor", "regulatory.conformance.review")

        # an independently accepted run where every case passes: the newest accepted run is the one that counts
        r = call("POST", "/v1/regulatory/conformance-runs", "author", json=run_body(run_id))
        assert r.status_code == 201, r.text
        review = {"decision": "ACCEPTED", "signed_hash": r.json()["run_hash"], "note_ref": "QA-1"}
        r = call("POST", f"/v1/regulatory/conformance-runs/{run_id}:review", "vendor", json=review)
        assert r.status_code == 200, r.text

        view = call("GET", "/v1/regulatory/offerings", "recorder", params={"country": "GB"})
        assert view.status_code == 200, view.text
        items = {i["offering_id"]: i for i in view.json()["items"]}
        assert len(items) == 12
        gb = {k: i["countries"][0] for k, i in items.items()}
        assert gb[API_ONLY] == {"country": "GB", "status": "AVAILABLE", "reasons": []}
        # no privacy overlay is approved in the shipped configuration: nothing that handles personal data may be shown
        idv = gb["IDENTITY_DOCUMENTS"]
        assert idv["status"] == "NOT_AVAILABLE" and "PRIVACY_OVERLAY_MISSING" in idv["reasons"]

        public = api.get("/v1/offerings", params={"country": "GB"})
        assert public.status_code == 200  # no credentials needed
        listed = {i["offering_id"]: i for i in public.json()["items"]}
        available = {k for k, c in gb.items() if c["status"] == "AVAILABLE"}
        assert set(listed) == available  # the public catalog and the operator view never disagree
        assert set(listed[API_ONLY]) == {"offering_id", "title", "wording", "countries"}
        assert listed[API_ONLY]["countries"] == ["GB"]

        # a country the registry does not list has no coverage at all
        assert api.get("/v1/offerings", params={"country": "ZZ"}).json() == {"items": []}
        assert api.get("/v1/offerings", params={"country": "gb"}).status_code == 422

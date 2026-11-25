"""Per-application workflow entitlement and hosted-page settings (CR-156), against the real database and the real HTTP endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from test_verify_api_go_live_pg import Company, uid
from test_verify_api_results_pg import code, hdr, server


def app_id(h: dict[str, str]) -> str:
    return h["X-App"]


def put(co: Company, aid: str, body: dict[str, Any]) -> Any:
    return co.http.put(f"/v1/verify-api/applications/{aid}/settings", headers=co.h, json=body)


def create(co: Company, h: dict[str, str], **over: Any) -> Any:
    body = {
        "customer_reference": "c-1",
        "country": "US",
        "purpose_code": "customer_onboarding",
        **over,
    }
    return co.http.post(
        "/v1/verify-api/verification-sessions",
        headers={**hdr(h), "Idempotency-Key": uid()},
        json=body,
    )


def test_an_application_can_be_limited_to_some_workflows_with_a_default(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        h = server(co, "Checkout")
        aid = app_id(h)
        # by default every approved workflow is allowed, and the platform default is used
        cat = co.http.get("/v1/verify-api/workflows", headers=hdr(h)).json()
        assert {w["workflow_id"]: (w["entitled"], w["default"]) for w in cat["workflows"]} == {
            "wf_document_selfie": (True, True),
            "wf_document_only": (True, False),
        }
        assert create(co, h).json()["workflow_id"] == "wf_document_selfie"

        # limit to document-only: it becomes the default, and the other is refused
        r = put(co, aid, {"allowed_workflows": ["wf_document_only"]})
        assert r.status_code == 200, r.text
        v = r.json()
        assert v["allowed_workflows"] == ["wf_document_only"] and v["default_workflow_id"] is None
        assert (
            create(co, h).json()["workflow_id"] == "wf_document_only"
        )  # none named: the only allowed one
        refused = create(co, h, workflow_id="wf_document_selfie")
        assert refused.status_code == 403 and code(refused) == "WORKFLOW_NOT_ENTITLED"
        assert create(co, h, workflow_id="wf_document_only").status_code == 201
        fx = co.http.post(
            "/v1/verify-api/sandbox/fixtures/approved/sessions",
            headers={**hdr(h), "Idempotency-Key": uid()},
            json={"workflow_id": "wf_document_selfie"},
        )
        assert fx.status_code == 403  # a test fixture cannot step round it either
        cat = co.http.get("/v1/verify-api/workflows", headers=hdr(h)).json()
        assert {w["workflow_id"]: (w["entitled"], w["default"]) for w in cat["workflows"]} == {
            "wf_document_selfie": (False, False),
            "wf_document_only": (True, True),
        }

        # allow both again with a chosen default; clearing the list allows every workflow
        r = put(
            co,
            aid,
            {
                "allowed_workflows": ["wf_document_only", "wf_document_selfie"],
                "default_workflow_id": "wf_document_only",
            },
        )
        assert (
            r.json()["default_workflow_id"] == "wf_document_only"
            and create(co, h).json()["workflow_id"] == "wf_document_only"
        )
        assert create(co, h, workflow_id="wf_document_selfie").status_code == 201
        assert put(co, aid, {"allowed_workflows": []}).json()["allowed_workflows"] == []
        assert create(co, h, workflow_id="wf_document_selfie").status_code == 201

        # a recheck cannot use a workflow the application is no longer allowed
        made = co.http.post(
            "/v1/verify-api/sandbox/fixtures/approved/sessions",
            headers={**hdr(h), "Idempotency-Key": uid()},
            json={"customer_reference": "p-1", "workflow_id": "wf_document_selfie"},
        )
        assert made.status_code == 201, made.text
        done = made.json()["session"]["session_id"]
        put(
            co,
            aid,
            {"allowed_workflows": ["wf_document_only"], "default_workflow_id": "wf_document_only"},
        )
        again = co.http.post(
            f"/v1/verify-api/verification-sessions/{done}:recheck",
            headers={**hdr(h), "Idempotency-Key": uid()},
        )
        assert (
            again.status_code == 403 and code(again) == "WORKFLOW_NOT_ENTITLED"
        )  # the original ran the selfie workflow
    finally:
        co.close()


def test_a_bad_setting_is_refused_and_a_partial_change_keeps_the_rest(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        h = server(co, "Checkout", origins=["https://app.example.com"])
        aid = app_id(h)
        for body in (
            {"allowed_workflows": ["wf_nope"]},
            {"default_workflow_id": "wf_nope"},
            {
                "allowed_workflows": ["wf_document_only"],
                "default_workflow_id": "wf_document_selfie",
            },
            {"branding": {"theme": "red"}},
            {"branding": {"display_name": "<script>"}},
            {"branding": {"support_url": "http://acme.example"}},
            {"branding": {"css": "x"}},
            {"redirect_origins": ["http://evil.example"]},
            {"redirect_origins": ["https://a.example/path"]},
        ):
            assert put(co, aid, body).status_code == 422, body
        assert put(co, aid, {"bogus": 1}).status_code == 422  # unknown setting
        assert (
            put(co, aid, {"branding": {"display_name": "Acme Bank", "theme": "violet"}}).status_code
            == 200
        )
        v = put(
            co, aid, {"redirect_origins": ["https://app.example.com", "https://m.example.com"]}
        ).json()
        assert v["redirect_origins"] == ["https://app.example.com", "https://m.example.com"]
        assert v["branding"] == {
            "display_name": "Acme Bank",
            "theme": "violet",
        }  # the earlier setting stayed
        assert put(co, aid, {"branding": {}}).json()["branding"] == {}  # an empty object clears it
        # unknown application, no sign-in, another company
        assert put(co, str(uuid.uuid4()), {"branding": {}}).status_code == 404
        assert (
            co.http.put(f"/v1/verify-api/applications/{aid}/settings", json={}).status_code == 401
        )
        other = Company(rt_url)
        try:
            assert put(other, aid, {"branding": {"theme": "green"}}).status_code == 404
        finally:
            other.close()
        # a retired application cannot be changed
        co.http.post(f"/v1/verify-api/applications/{aid}:retire", headers=co.h)
        r = put(co, aid, {"branding": {"theme": "green"}})
        assert r.status_code == 409 and code(r) == "APPLICATION_STATE_INVALID"
    finally:
        co.close()


def test_the_hosted_page_shows_the_chosen_name_theme_and_support_link_and_only_a_registered_return_address(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        h = server(co, "Checkout", origins=["https://app.example.com"])
        aid = app_id(h)
        plain = create(co, h, return_origin="https://app.example.com").json()
        page = co.http.get(f"/v1/verify-api/hosted/{plain['launch_url'].rsplit('/', 1)[-1]}").json()
        assert (
            page["requested_by"] == "Checkout"
            and page["theme"] == "blue"
            and page["support_url"] is None
        )

        put(
            co,
            aid,
            {
                "branding": {
                    "display_name": "Acme Bank",
                    "theme": "green",
                    "support_url": "https://acme.example/help",
                    "locale": "fr",
                }
            },
        )
        branded = create(
            co, h, return_origin="https://app.example.com", customer_reference="c-2"
        ).json()
        token = branded["launch_url"].rsplit("/", 1)[-1]
        page = co.http.get(f"/v1/verify-api/hosted/{token}").json()
        assert (
            page["requested_by"] == "Acme Bank"
            and page["theme"] == "green"
            and page["support_url"] == "https://acme.example/help"
            and page["locale"] == "fr"
        )
        assert "Checkout" not in str(page) and "c-2" not in str(
            page
        )  # not the internal name, not the reference

        # the return address is offered while it is registered, and not after it is removed
        done = co.http.post(
            f"/v1/verify-api/hosted/{token}:simulate", json={"outcome": "approved"}
        ).json()
        assert done["return_origin"] == "https://app.example.com"
        second = create(
            co, h, return_origin="https://app.example.com", customer_reference="c-3"
        ).json()
        put(co, aid, {"redirect_origins": ["https://other.example.com"]})
        gone = co.http.post(
            f"/v1/verify-api/hosted/{second['launch_url'].rsplit('/', 1)[-1]}:simulate",
            json={"outcome": "approved"},
        ).json()
        assert gone["return_origin"] is None
        # a new session with the removed address is refused
        assert (
            code(create(co, h, return_origin="https://app.example.com", customer_reference="c-4"))
            == "RETURN_ORIGIN_NOT_REGISTERED"
        )
    finally:
        co.close()

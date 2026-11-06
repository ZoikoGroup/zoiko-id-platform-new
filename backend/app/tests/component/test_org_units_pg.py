"""The organization hierarchy on a real database: a bounded tree, no orphans, and people placed in units."""

from __future__ import annotations

from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.settings import Settings

BASE = "/v1/tenant/org-units"


def test_a_group_tree_is_bounded_and_members_are_placed(rt_url: str) -> None:
    o = onboard(rt_url)
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"), keyring=o.ring
    )
    h = o.bearer(o.owner)
    with TestClient(app) as api:
        top = api.post(BASE, json={"name": "Acme Group", "kind": "GROUP"}, headers=h)
        assert top.status_code == 201, top.text
        gid = top.json()["unit_id"]
        assert api.post(BASE, json={"name": "acme group", "kind": "GROUP"}, headers=h).status_code == 409
        sub = api.post(BASE, json={"name": "Acme UK", "kind": "SUBSIDIARY", "parent_unit_id": gid}, headers=h)
        assert sub.status_code == 201 and sub.json()["depth"] == 1
        uk = sub.json()["unit_id"]
        assert api.post(BASE, json={"name": "Ghost", "kind": "DEPARTMENT", "parent_unit_id": "00000000-0000-4000-8000-000000000000"}, headers=h).status_code == 404

        # six levels at most
        parent = uk
        for depth, kind in enumerate(["DIVISION", "DEPARTMENT", "DEPARTMENT", "DEPARTMENT"], start=2):
            r = api.post(BASE, json={"name": f"Level {depth}", "kind": kind, "parent_unit_id": parent}, headers=h)
            assert r.status_code == 201 and r.json()["depth"] == depth, r.text
            parent = r.json()["unit_id"]
        too_deep = api.post(BASE, json={"name": "Level 6", "kind": "DEPARTMENT", "parent_unit_id": parent}, headers=h)
        assert too_deep.status_code == 422 and too_deep.json()["error"]["code"] == "ORG_UNIT_TOO_DEEP"

        # people
        assert api.post(f"{BASE}/{uk}/members", json={"principal_id": o.second, "title": "Finance lead"}, headers=h).status_code == 201
        assert api.post(f"{BASE}/{uk}/members", json={"principal_id": "00000000-0000-4000-8000-000000000001"}, headers=h).status_code == 404
        items = {i["name"]: i for i in api.get(BASE, headers=h).json()["items"]}
        assert items["Acme UK"]["members"] == 1 and items["Acme Group"]["parent_unit_id"] is None
        assert items["Acme UK"]["parent_unit_id"] == gid

        # a unit with live sub-units cannot be retired; a leaf can, and its people leave with it
        assert api.post(f"{BASE}/{gid}:retire", headers=h).status_code == 409
        assert api.post(f"{BASE}/{parent}:retire", headers=h).status_code == 200
        assert api.post(f"{BASE}/{uk}/members/{o.second}:remove", headers=h).status_code == 200
        assert api.post(f"{BASE}/{uk}/members/{o.second}:remove", headers=h).status_code == 404

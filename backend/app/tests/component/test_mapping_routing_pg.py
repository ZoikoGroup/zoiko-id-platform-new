"""Mapping releases and multi-IdP routing end to end (G-W3; OVERLAY-001 §8, §11, §20; OVL-10, OVL-11,
OVL-27).

A mapping can never target authority; a release must be simulated, conflict-free and approved by
someone other than its author before it becomes ACTIVE; rules are immutable in the database;
rollback is the governed re-activation of an earlier version. Routing is deterministic and a second
route for the same match is refused.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, approval, give_role, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.integration.ports import map_record
from zid_platform.settings import Settings

GOOD = [
    {"source": "claims.department", "target": "profile.department", "transform": [{"op": "lower"}]},
    {
        "source": "groups",
        "target": "relation.member_of",
        "transform": [{"op": "prefix_strip", "prefix": "grp-"}],
    },
]
CLASHING = [
    *GOOD,
    {
        "source": "claims.dept_code",
        "target": "profile.department",
        "transform": [{"op": "map", "table": {"FIN": "finance"}}],
    },
]


def test_mapping_releases_and_routing(rt_url: str, owner_url: str) -> None:
    o = onboard(rt_url)
    give_role(
        rt_url, o, o.second, "ADMIN"
    )  # the second administrator holds the role an accepted invitation gives
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )

    def code(r: Any) -> str:
        return str(r.json()["error"]["code"])

    with TestClient(app) as api:
        r = api.post(
            "/v1/integration/connectors",
            json={
                "connector_type": "DIRECTORY",
                "display_name": "Entra groups",
                "capabilities": ["GROUPS"],
            },
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 201, r.text
        cid = r.json()["connector_id"]
        base = f"/v1/integration/connectors/{cid}/mapping-releases"

        r = api.post(
            base,
            json={"rules": [{"source": "groups", "target": "authority.grant"}]},
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 422 and code(r) == "MAPPING_TARGET_FORBIDDEN"  # OVL-10

        def create(rules: list[dict[str, Any]], author: str) -> dict[str, Any]:
            r = api.post(base, json={"rules": rules}, headers=o.bearer(author))
            assert r.status_code == 201, r.text
            return dict(r.json())

        def activate(rel: dict[str, Any], by: str, approver: str) -> Any:
            rid = approval(
                api,
                o,
                owner_url,
                action="integration.mapping.manage",
                resource=f"mapping_release:{rel['release_id']}",
                change=f"ACTIVATE:{rel['content_hash']}",
                requester=by,
                approver=approver,
            )
            return api.post(
                f"/v1/integration/mapping-releases/{rel['release_id']}:activate",
                json={"approval_request_id": rid},
                headers=o.bearer(by),
            )

        def simulate(rel: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
            r = api.post(
                f"/v1/integration/mapping-releases/{rel['release_id']}:simulate",
                json={"records": records},
                headers=o.bearer(o.owner),
            )
            assert r.status_code == 200, r.text
            return dict(r.json())

        v1 = create(CLASHING, o.owner)
        r = activate(v1, o.owner, o.second)
        assert r.status_code == 409 and code(r) == "MAPPING_SIMULATION_REQUIRED"
        sim = simulate(v1, [{"id": "u1", "claims": {"department": "Sales", "dept_code": "FIN"}}])
        assert sim["status"] == "REVIEW" and sim["simulation_report"]["conflicts"] == 1
        r = activate(v1, o.owner, o.second)
        assert (
            r.status_code == 409 and code(r) == "MAPPING_CONFLICT"
        )  # OVL-11: quarantined, not applied

        v2 = create(GOOD, o.owner)
        simulate(v2, [{"id": "u1", "claims": {"department": "Sales"}, "groups": ["grp-pay"]}])
        r = activate(v2, o.owner, o.second)
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE", r.text
        assert (
            api.get("/v1/tenant", headers=o.bearer(o.owner)).json()["mapping_release_id"]
            == v2["release_id"]
        )

        v3 = create(GOOD[:1], o.second)  # authored by the second administrator
        simulate(v3, [{"id": "u2", "claims": {"department": "Ops"}}])
        r = activate(v3, o.owner, o.second)  # ... who cannot also be its approver
        assert r.status_code == 409 and code(r) == "APPROVER_IS_AUTHOR"
        v4 = create(GOOD[:1], o.owner)
        simulate(v4, [{"id": "u2", "claims": {"department": "Ops"}}])
        assert activate(v4, o.owner, o.second).json()["status"] == "ACTIVE"
        # rollback = the governed re-activation of the earlier immutable version
        r = activate(v2, o.owner, o.second)
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
        statuses = {
            x["version"]: x["status"]
            for x in api.get(base, headers=o.bearer(o.owner)).json()["items"]
        }
        assert statuses == {1: "REVIEW", 2: "ACTIVE", 3: "REVIEW", 4: "RETIRED"}

        # --- routing ----------------------------------------------------------------------------
        for kind, match, ref, want in (
            ("DOMAIN", "acme.com", "federation_connection:entra", 201),
            ("DOMAIN", "eu.acme.com", "federation_connection:okta", 201),
            ("APPLICATION", "payroll", "federation_connection:ping", 201),
            ("DOMAIN", "ACME.com", "federation_connection:other", 409),  # same match: refused
        ):
            r = api.post(
                "/v1/integration/routes",
                json={"kind": kind, "match": match, "connection_ref": ref},
                headers=o.bearer(o.owner),
            )
            assert r.status_code == want, r.text

        def route(**q: Any) -> str | None:
            return api.post(
                "/v1/integration/routes:resolve", json=q, headers=o.bearer(o.owner)
            ).json()["connection_ref"]

        assert route(login_domain="eu.acme.com") == "federation_connection:okta"
        assert route(login_domain="uk.acme.com") == "federation_connection:entra"
        assert (
            route(application_id="payroll", login_domain="eu.acme.com")
            == "federation_connection:ping"
        )
        assert route(login_domain="acme.com.evil.example") is None

    async def runtime_and_immutability() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(o.tenant)) as c:
                res = await map_record(
                    c, cid, {"claims": {"department": "FINANCE"}, "groups": ["grp-a", "b"]}
                )
                assert res is not None and res.facts == {
                    "profile.department": "finance",
                    "relation.member_of": ["a", "b"],
                }
            with pytest.raises(DBAPIError):
                async with tenant_transaction(eng, ctx(o.tenant)) as c:
                    await c.execute(text("UPDATE platform.mapping_release SET rules = '[]'"))

    asyncio.run(runtime_and_immutability())

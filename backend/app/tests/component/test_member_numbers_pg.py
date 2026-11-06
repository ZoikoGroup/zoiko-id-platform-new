"""Names and permanent numbers for people (CR-061), through the real API and the real database.

A person is known by a short number inside their company (M-0001, M-0002, ...), assigned once and never reused, and by a name someone gave them.
Every place that used to show a UUID shows "Name (M-0007)": the member list, the invitation, the approvals and the reviewers.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.tenant.api.org_approvals import _name_people
from zid_platform.modules.tenant.application import lifecycle as service
from zid_platform.modules.tenant.application import people
from zid_platform.modules.tenant.domain.lifecycle import Role
from zid_platform.modules.tenant.infrastructure.route_map import route_matrix_enforced
from zid_platform.settings import Settings


def client(rt_url: str, o: Any) -> TestClient:
    return TestClient(
        create_app(
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
    )


def member(api: TestClient, o: Any, principal: str) -> dict[str, Any]:
    items = api.get("/v1/tenant/members", headers=o.bearer(o.owner)).json()["items"]
    return next(m for m in items if m["principal_id"] == principal)


def test_everyone_gets_a_permanent_number_and_the_name_they_were_given(rt_url: str) -> None:
    o = onboard(rt_url)
    with client(rt_url, o) as api:
        owner = member(api, o, o.owner)["person"]
        assert owner["member_no"] == 1  # the first person in a company is number 1
        assert (
            owner["name"] is None and owner["label"] == owner["member_code"]
        )  # no name yet: the number alone, never a blank

        r = api.post(
            "/v1/tenant/members",
            json={"role": "MEMBER", "display_name": "  Priya   Nair "},
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 201, r.text
        priya = r.json()
        assert priya["person"]["name"] == "Priya Nair"  # trimmed and single-spaced
        assert (
            priya["person"]["member_no"] == 2 and priya["person"]["label"] == "Priya Nair (M-0002)"
        )

        r = api.post("/v1/tenant/members", json={"role": "MEMBER"}, headers=o.bearer(o.owner))
        nameless = r.json()
        assert nameless["person"]["member_no"] == 3 and nameless["person"]["label"] == "M-0003"

        # the member list carries the same person, so the Team page can show it
        assert member(api, o, priya["principal_id"])["person"]["label"] == "Priya Nair (M-0002)"

        # an owner or admin renames; the number never moves
        r = api.post(
            f"/v1/tenant/members/{priya['principal_id']}:rename",
            json={"name": "Priya N. Nair"},
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 200 and r.json()["label"] == "Priya N. Nair (M-0002)"
        r = api.post(
            f"/v1/tenant/members/{priya['principal_id']}:rename",
            json={"name": ""},
            headers=o.bearer(o.owner),
        )
        assert r.json()["name"] is None and r.json()["member_no"] == 2  # blank clears the name

        # a person names themselves
        r = api.post(
            "/v1/tenant/members/me:name", json={"name": "Anil Kumar"}, headers=o.bearer(o.owner)
        )
        assert r.status_code == 200 and r.json()["label"].startswith("Anil Kumar (M-")


def test_unusable_names_and_unknown_people_are_refused_cleanly(rt_url: str) -> None:
    o = onboard(rt_url)
    with client(rt_url, o) as api:
        for bad in ("bad\x00name", "bell\x07 inside", "x" * 121):
            r = api.post(
                "/v1/tenant/members",
                json={"role": "MEMBER", "display_name": bad},
                headers=o.bearer(o.owner),
            )
            assert r.status_code in (422, 400), (bad, r.status_code, r.text)
        r = api.post(
            "/v1/tenant/members",
            json={"role": "MEMBER", "display_name": "two\nlines"},
            headers=o.bearer(o.owner),
        )
        assert (
            r.status_code == 201 and r.json()["person"]["name"] == "two lines"
        )  # a pasted newline becomes a space
        before = len(api.get("/v1/tenant/members", headers=o.bearer(o.owner)).json()["items"])
        api.post(
            "/v1/tenant/members",
            json={"role": "MEMBER", "display_name": "bad\x00name"},
            headers=o.bearer(o.owner),
        )
        assert (
            len(api.get("/v1/tenant/members", headers=o.bearer(o.owner)).json()["items"]) == before
        )  # nothing half-created
        r = api.post(
            f"/v1/tenant/members/{uuid.uuid4()}:rename",
            json={"name": "Ghost"},
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 404


def test_numbers_are_per_company_and_the_permissions_answer_reports_real_enforcement(
    rt_url: str,
) -> None:
    a, b = onboard(rt_url), onboard(rt_url)
    with client(rt_url, a) as api_a, client(rt_url, b) as api_b:
        assert {
            m["person"]["member_no"]
            for m in api_a.get("/v1/tenant/members", headers=a.bearer(a.owner)).json()["items"]
        } == {1}
        assert {
            m["person"]["member_no"]
            for m in api_b.get("/v1/tenant/members", headers=b.bearer(b.owner)).json()["items"]
        } == {1}  # each company starts at 1
        # a company cannot name or see a person in another company
        r = api_b.post(
            f"/v1/tenant/members/{a.owner}:rename",
            json={"name": "Intruder"},
            headers=b.bearer(b.owner),
        )
        assert r.status_code == 404
        perms = api_a.get("/v1/tenant/me/permissions", headers=a.bearer(a.owner)).json()
        assert (
            perms["enforcement"] is route_matrix_enforced()
        )  # it was hard-coded False whatever the owners had ruled


def test_numbers_stay_unique_when_people_join_at_the_same_moment(rt_url: str) -> None:
    o = onboard(rt_url)

    async def join(i: int) -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            inv = await service.invite(c, o.tenant, role=Role.MEMBER, invited_by=o.owner)
        return inv.assignment.principal_id

    async def run() -> list[int]:
        joined = await asyncio.gather(*(join(i) for i in range(8)))
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            refs = await people.person_refs(c, joined)
        return sorted(r.member_no or 0 for r in refs.values())

    assert asyncio.run(run()) == list(
        range(2, 10)
    )  # eight new people after the owner (number 1): no gaps, no duplicates


def test_a_number_can_never_be_changed_reused_or_removed(rt_url: str, owner_url: str) -> None:
    o = onboard(rt_url)

    async def run() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            for statement in (
                "UPDATE platform.tenant_member_no SET member_no = 99 WHERE tenant_id = :t",
                "DELETE FROM platform.tenant_member_no WHERE tenant_id = :t",
            ):
                with pytest.raises(DBAPIError, match="permanent"):
                    async with c.begin_nested():
                        await c.execute(text(statement), {"t": uuid.UUID(o.tenant)})

    asyncio.run(run())


def test_approvals_name_the_requester_the_beneficiary_and_every_voter(rt_url: str) -> None:
    o = onboard(rt_url)
    item: dict[str, Any] = {
        "requester_principal_id": o.second,
        "beneficiary_principal_id": o.owner,
        "votes": [{"approver_principal_id": o.owner, "vote": "APPROVE"}],
    }

    async def run() -> dict[str, Any]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await people.rename(c, o.owner, "Anil Kumar")
            return await _name_people(c, item)

    got = asyncio.run(run())
    assert got["beneficiary"]["name"] == "Anil Kumar" and got["beneficiary"][
        "member_code"
    ].startswith("M-")
    assert (
        got["votes"][0]["approver"]["label"] == got["beneficiary"]["label"]
    )  # the voter and the beneficiary are the same person: one label
    # a person with no number or name yet (here the fixture's second administrator has authority but no role assignment) is a short id, never blank
    assert got["requester"]["member_no"] is None and got["requester"]["label"] == o.second[:8]

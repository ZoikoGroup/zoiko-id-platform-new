"""Directory sync on a real database: a company's Auth0 users are brought into its SCIM source, safely.

The database, the sealed connector secret, the SCIM code and the roles are real. Only Auth0's HTTP is a stand-in (an automated test cannot call a real
tenant); what it answers is shaped like Auth0's Management API.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from conftest import ctx, engine
from scim_fixture import make_scim_client
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform import egress
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.provisioning.application import directory_sync as ds
from zid_platform.modules.provisioning.domain.directory import DirectoryError
from zid_platform.modules.provisioning.infrastructure import directory_store, scim_store
from zid_platform.modules.provisioning.infrastructure.scim_store import Source

DOMAIN = "acme-test.us.auth0.com"
SECRET = "cli-" + "x" * 32  # a stand-in value: the tests check it is never stored in clear
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def uid() -> str:
    return str(uuid.uuid4())


class FakeAuth0:
    """Answers like Auth0's token and users endpoints for one tenant."""

    def __init__(self) -> None:
        self.users: list[dict[str, Any]] = []
        self.scope = "read:users"
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if request.url.path == "/oauth/token":
            ok = b'"client_secret": "' + SECRET.encode() + b'"' in request.content or SECRET.encode() in request.content
            if not ok:
                return httpx.Response(401, json={"error": "access_denied"})
            return httpx.Response(200, json={"access_token": "t0k3n", "scope": self.scope, "token_type": "Bearer"})
        if request.url.path == "/api/v2/users":
            page, per = int(request.url.params["page"]), int(request.url.params["per_page"])
            return httpx.Response(200, json={"users": self.users[page * per : (page + 1) * per], "total": len(self.users)})
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def person(n: int, **extra: Any) -> dict[str, Any]:
    return {"user_id": f"auth0|u{n}", "email": f"person{n}@acme.example", "name": f"Person {n}", "given_name": "Person", "family_name": str(n), **extra}


@pytest.fixture(autouse=True)
def runtime(monkeypatch: pytest.MonkeyPatch) -> Any:
    configure_runtime(development_runtime("uk-dir"))

    async def public(url: str, *, resolve: bool = True) -> str:  # no DNS in a test
        return url

    monkeypatch.setattr(egress, "guard", public)
    yield
    configure_runtime(None)


class World:
    def __init__(self, rt_url: str) -> None:
        self.rt_url, self.tenant, self.source_id, self.auth0 = rt_url, uid(), uid(), FakeAuth0()
        self.sync_id = ""

    def run(self, work: Any) -> Any:
        async def go() -> Any:
            async with engine(self.rt_url) as eng, tenant_transaction(eng, ctx(self.tenant)) as c:
                return await work(c)

        return asyncio.run(go())

    def seed(self) -> None:
        async def work(c: Any) -> None:
            await c.execute(  # a real tenant record: people who join become its members
                text(
                    "INSERT INTO platform.tenant (tenant_id, display_name, tenant_slug, status, deployment_mode, home_cell_id, creator_tenant_id, "
                    "creator_principal_id, created_at, state_changed_at) VALUES (:t, 'Acme', :s, 'ACTIVE', 'O0', 'uk-1', :t, :p, now(), now())"
                ),
                {"t": uuid.UUID(self.tenant), "s": f"acme-{self.tenant[:8]}", "p": uuid.uuid4()},
            )
            await make_scim_client(c, self.tenant, f"hr-{self.tenant[:8]}")
            await scim_store.create_source(c, Source(self.source_id, self.tenant, f"hr-{self.tenant[:8]}", "HR", "ACTIVE"))
            sync = await ds.create_sync(
                c, tenant_id=self.tenant, source_id=self.source_id, provider="AUTH0", display_name="Acme Auth0",
                settings={"domain": DOMAIN, "client_id": "test-client-id-0001"}, secret=SECRET, owner=uid(), actor=uid(), now=NOW,
            )  # fmt: skip
            self.sync_id = sync.sync_id

        self.run(work)

    def pass_at(self, when: datetime) -> dict[str, int]:
        async def work(c: Any) -> dict[str, int]:
            return await ds.sync_pass(c, when, http=self.auth0.client(), home_cell="uk-1")

        return self.run(work)  # type: ignore[no-any-return]

    def sync(self) -> Any:
        async def work(c: Any) -> Any:
            return await directory_store.get(c, self.sync_id)

        return self.run(work)

    def users(self) -> dict[str, Any]:
        async def work(c: Any) -> dict[str, Any]:
            rows = (await c.execute(text("SELECT external_id, user_name, display_name, active, principal_id FROM platform.scim_resource WHERE resource_type = 'User'"))).all()
            return {r.external_id: r for r in rows}

        return self.run(work)  # type: ignore[no-any-return]

    def roles(self) -> dict[str, list[str]]:
        async def work(c: Any) -> dict[str, list[str]]:
            rows = (await c.execute(text("SELECT principal_id, role FROM platform.tenant_role_assignment WHERE status = 'ACTIVE'"))).all()
            out: dict[str, list[str]] = {}
            for r in rows:
                out.setdefault(str(r.principal_id), []).append(r.role)
            return out

        return self.run(work)  # type: ignore[no-any-return]


@pytest.fixture
def world(rt_url: str) -> World:
    w = World(rt_url)
    w.seed()
    return w


def test_the_first_run_brings_in_the_directory_and_joins_active_people_as_members(world: World) -> None:
    world.auth0.users = [person(1), person(2), person(3, blocked=True)]
    assert world.pass_at(NOW) == {"ran": 1, "failed": 0}
    s = world.sync()
    assert (s.last_outcome, s.last_error, s.consecutive_failures) == ("OK", None, 0)
    assert s.last_counts == {"created": 3, "updated": 0, "unchanged": 0, "deactivated": 0, "failed": 0}
    assert s.next_run_at == NOW + timedelta(seconds=s.interval_seconds)
    people = world.users()
    assert set(people) == {"auth0|u1", "auth0|u2", "auth0|u3"}  # the provider's id is the externalId (it is also the sign-in `sub`)
    assert people["auth0|u1"].user_name == "person1@acme.example" and people["auth0|u1"].active is True
    assert people["auth0|u3"].active is False
    roles = world.roles()
    assert roles[str(people["auth0|u1"].principal_id)] == ["MEMBER"] and roles[str(people["auth0|u2"].principal_id)] == ["MEMBER"]
    assert str(people["auth0|u3"].principal_id) not in roles  # a blocked person is provisioned but joins nothing


def test_a_second_run_changes_nothing_and_only_real_differences_are_applied(world: World) -> None:
    world.auth0.users = [person(1), person(2), person(3)]
    world.pass_at(NOW)
    world.pass_at(NOW + timedelta(hours=2))
    assert world.sync().last_counts == {"created": 0, "updated": 0, "unchanged": 3, "deactivated": 0, "failed": 0}
    world.auth0.users = [person(1, name="Person One Renamed"), person(2), person(3, blocked=True)]
    world.pass_at(NOW + timedelta(hours=4))
    assert world.sync().last_counts == {"created": 0, "updated": 2, "unchanged": 1, "deactivated": 0, "failed": 0}
    people = world.users()
    assert people["auth0|u1"].display_name == "Person One Renamed" and people["auth0|u3"].active is False
    world.auth0.users = [person(1, name="Person One Renamed"), person(2), person(3)]
    world.pass_at(NOW + timedelta(hours=6))
    assert world.users()["auth0|u3"].active is True  # unblocked in the directory: switched back on


def test_people_who_leave_the_directory_are_deactivated_never_deleted(world: World) -> None:
    world.auth0.users = [person(n) for n in range(1, 21)]
    world.pass_at(NOW)
    world.auth0.users = [person(n) for n in range(1, 18)]  # three leave: within the safety limit
    world.pass_at(NOW + timedelta(hours=2))
    assert world.sync().last_counts["deactivated"] == 3
    people = world.users()
    assert len(people) == 20 and sorted(e for e, r in people.items() if not r.active) == ["auth0|u18", "auth0|u19", "auth0|u20"]


def test_a_directory_that_suddenly_lists_nobody_or_most_people_is_not_acted_on(world: World) -> None:
    world.auth0.users = [person(n) for n in range(1, 31)]
    world.pass_at(NOW)
    world.auth0.users = []
    assert world.pass_at(NOW + timedelta(hours=2)) == {"ran": 1, "failed": 1}
    s = world.sync()
    assert (s.last_outcome, s.last_error, s.consecutive_failures) == ("FAILED", "EMPTY_DIRECTORY", 1)
    assert all(r.active for r in world.users().values())
    assert s.next_run_at == NOW + timedelta(hours=2) + timedelta(seconds=s.interval_seconds * 2)  # backs off
    world.auth0.users = [person(n) for n in range(1, 6)]  # 25 of 30 gone at once
    world.pass_at(s.next_run_at)
    assert world.sync().last_error == "MASS_DEACTIVATION" and all(r.active for r in world.users().values())


def test_a_credential_wider_than_read_users_is_refused_and_the_secret_is_never_stored_in_clear(world: World) -> None:
    world.auth0.users = [person(1)]
    world.auth0.scope = "read:users update:users"
    world.pass_at(NOW)
    assert world.sync().last_error == "CREDENTIAL_TOO_BROAD" and world.users() == {}

    async def stored(c: Any) -> list[bytes]:
        return [bytes(r.envelope) for r in (await c.execute(text("SELECT envelope FROM platform.connector_credential"))).all()]

    envelopes = world.run(stored)
    assert envelopes and all(SECRET.encode() not in e for e in envelopes)
    assert SECRET not in str(world.sync().settings) and SECRET not in str(world.sync().last_error)


def test_a_directory_the_reader_cannot_see_completely_is_never_acted_on(world: World) -> None:
    world.auth0.users = [person(n) for n in range(1, 1002)]
    world.pass_at(NOW)
    assert world.sync().last_error == "DIRECTORY_TOO_LARGE" and world.users() == {}


def test_a_provisioned_person_is_found_by_the_providers_id_on_the_same_tenant_only(world: World) -> None:
    from zid_platform.modules.provisioning.ports import directory_person

    world.auth0.users = [person(1), person(2), person(3, blocked=True)]
    world.pass_at(NOW)
    people = world.users()

    def found(issuer: str, subject: str) -> str | None:
        async def work(c: Any) -> str | None:
            return await directory_person(c, issuer, subject)

        return world.run(work)  # type: ignore[no-any-return]

    assert found(f"https://{DOMAIN}/", "auth0|u1") == str(people["auth0|u1"].principal_id)
    assert found(f"https://{DOMAIN.upper()}", "auth0|u2") == str(people["auth0|u2"].principal_id)
    assert found(f"https://{DOMAIN}/", "auth0|u3") is None  # blocked in the directory: nothing to attach to
    assert found(f"https://{DOMAIN}/", "auth0|nobody") is None
    assert found("https://other-tenant.us.auth0.com/", "auth0|u1") is None  # another Auth0 tenant is not this company's directory
    assert found(f"https://{DOMAIN}/", "person1@acme.example") is None  # an e-mail address is never an identity


def test_only_an_auth0_tenant_address_is_accepted(world: World) -> None:
    async def work(c: Any) -> None:
        for bad in ("169.254.169.254", "evil.example.com", "acme.auth0.com.evil.example", "http://acme.us.auth0.com", ""):
            with pytest.raises(DirectoryError) as e:
                ds._provider("AUTH0").check_settings({"domain": bad, "client_id": "test-client-id-0001"})
            assert e.value.code == "SETTINGS_INVALID"

    world.run(work)

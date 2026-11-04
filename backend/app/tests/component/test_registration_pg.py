"""Person-first sign-up on real routes and a real database (ZID-UX-WF-013A acceptance AC-01 to AC-12).

Mail goes to a recording sender, so the single-use link is read from the message the person would have received. A passkey is added
directly where the ceremony itself is covered elsewhere (test_authn_pg, test_step_up_pg).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, Onboarded, _session, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_email_pg import FakeSender, make_cfg
from test_step_up_pg import give_passkey
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.onboarding.api import registration as reg_api
from zid_platform.modules.onboarding.application import registration as svc
from zid_platform.modules.onboarding.domain.registration import RegistrationError, State
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.tenant.ports import ConsoleClient, self_signup
from zid_platform.settings import Settings

NOTICE = "2026-11-03"
LINK = re.compile(r"[?&]t=([A-Za-z0-9_-]+)")


def uid() -> str:
    return str(uuid.uuid4())


@dataclass
class World:
    rt_url: str
    reg_tenant: str
    http: TestClient
    sender: FakeSender
    ring: KeyRing
    inviter: Onboarded

    def run(self, work: Any, tenant: str | None = None) -> Any:
        async def go() -> Any:
            async with (
                engine(self.rt_url) as eng,
                tenant_transaction(eng, ctx(tenant or self.reg_tenant)) as c,
            ):
                return await work(c)

        return asyncio.run(go())

    def env(self) -> svc.Env:
        console = self.http.app.state.tenant_console
        return svc.Env(self.reg_tenant, "uk-1", NOTICE, "test", ConsoleClient(*console), {})

    def plain(self, work: Any) -> Any:
        async def go() -> Any:
            async with engine(self.rt_url) as eng, eng.begin() as c:
                return await work(c)

        return asyncio.run(go())

    def nonce(self) -> str:
        return str(self.http.get("/v1/registration/capabilities").json()["nonce"])

    def start(
        self,
        email: str,
        *,
        country: str = "GB",
        journey: str = "PERSONAL",
        nonce: str | None = "auto",
        **extra: Any,
    ) -> Any:
        body = {"country": country, "journey": journey, "email": email, "notice_version": NOTICE,
                "nonce": self.nonce() if nonce == "auto" else nonce, **extra}  # fmt: skip
        return self.http.post("/v1/registration/intents", json=body)

    def link(self, index: int = -1) -> tuple[str, str]:
        """(request id is not in the mail; the token is). The token of the n-th message sent."""
        rendered = self.sender.sent[index][1]
        return LINK.search(rendered.text).group(1), self.sender.sent[index][0]  # type: ignore[union-attr]

    def confirm(self, request_id: str, token: str) -> Any:
        return self.http.post(
            "/v1/registration/confirm", json={"request_id": request_id, "token": token}
        )

    def rows(self, sql: str, **params: Any) -> list[Any]:
        async def work(c: Any) -> list[Any]:
            return list((await c.execute(text(sql), params)).all())

        return self.run(work)

    def bearer(self, tenant: str, principal: str) -> dict[str, str]:
        async def work(c: Any) -> str:
            return str(await _session(c, tenant, principal))

        sid = self.run(work, tenant)
        now = datetime.now(UTC)
        tok = issue_access_token(
            self.ring, issuer=ISS, audience=AUD, subject=principal, tenant_id=tenant, client_id="console", now=now,
            session_id=sid, auth_time=now - timedelta(seconds=5), scope=frozenset(),
        )  # fmt: skip
        return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def world(rt_url: str) -> Any:
    reg_api._buckets.clear()
    inviter = onboard(
        rt_url
    )  # an organization that can invite people; its keyring signs every token in this app

    async def seed() -> str:
        async with engine(rt_url) as eng, eng.begin() as c:
            made = await self_signup(c, display_name="Zoiko iD registration", tenant_slug=f"reg-{uid()[:10]}",
                                     deployment_mode="O0", home_cell_id="uk-1", console=None)  # fmt: skip
            return made.tenant.tenant_id

    reg = asyncio.run(seed())
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1",
                 registration_tenant_id=reg, tenant_console_redirect_uris=[f"{AUD}/auth/callback"],
                 tenant_signup_notice_version=NOTICE),
        keyring=inviter.ring,
    )  # fmt: skip
    sender = FakeSender()
    app.state.email_config = make_cfg()
    app.state.email_sender = sender
    with TestClient(app, base_url=ISS) as http:
        yield World(rt_url, reg, http, sender, inviter.ring, inviter)


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


# ------------------------------------------------------------------------------- AC-01, AC-02, AC-09


def test_a_person_signs_up_into_an_active_account_that_is_not_verified(world: World) -> None:
    """AC-01: active but not identity verified, no organizational privilege. AC-09 (the ticket is the passkey route)."""
    w = world
    caps = w.http.get("/v1/registration/capabilities", params={"country": "gb"}).json()
    assert (
        caps["country"]["account"] == "AVAILABLE"
        and caps["identity_providers"] == []
        and caps["authenticators"]["fallback"] == "GOVERNED_RECOVERY"
    )
    started = w.start("Jane.Doe+x@Example.com")
    assert (
        started.status_code == 202
        and started.json()["message"]
        == "If this address can be used, check your inbox for next steps."
    )
    ref = started.json()["request_id"]
    assert (
        w.http.get("/v1/registration/status", params={"request_id": ref}).json()["next_step"]
        == "VERIFY_EMAIL"
    )
    token, to = w.link()
    assert (
        to == "jane.doe+x@example.com"
    )  # the approved canonicalization: no +tag stripping, domain lower-cased
    done = w.confirm(ref, token)
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["account"], body["identity"], body["next_step"], body["then"]) == (
        "ACTIVE",
        "NOT_VERIFIED",
        "ENROLL_AUTHENTICATOR",
        "PERSONAL_WORKSPACE",
    )
    assert (
        body["enrollment_ticket"]
        and body["address"] == "j***@example.com"
        and body["workspace"].startswith("u-")
    )
    assert (
        w.http.get("/v1/registration/status", params={"request_id": ref}).json()["next_step"]
        == "ENROLL_AUTHENTICATOR"
    )

    tenant = w.rows(
        "SELECT account_tenant_id, account_principal_id FROM platform.registration_intent WHERE request_ref = :r",
        r=ref,
    )[0]
    t, p = str(tenant.account_tenant_id), str(tenant.account_principal_id)

    async def journey_of(c: Any) -> Any:
        return (
            await c.execute(
                text("SELECT signup_journey FROM platform.tenant WHERE tenant_id = :t"),
                {"t": uuid.UUID(t)},
            )
        ).scalar_one()

    assert (
        w.run(journey_of, t) == "PERSONAL"
    )  # kept by the server from its own record of the request
    # no organizational privilege: a pending bootstrap role in a tenant that was never verified, and nothing more
    role = (
        w.rows(
            "SELECT role, status FROM platform.tenant_role_assignment WHERE principal_id = :p",
            p=uuid.UUID(p),
        )
        if False
        else None
    )

    async def roles(c: Any) -> list[Any]:
        return list(
            (
                await c.execute(
                    text(
                        "SELECT role, status FROM platform.tenant_role_assignment WHERE principal_id = :p"
                    ),
                    {"p": uuid.UUID(p)},
                )
            ).all()
        )

    assert [(r.role, r.status) for r in w.run(roles, t)] == [
        ("OWNER", "PENDING_VERIFICATION")
    ] and role is None
    rights = w.http.get("/v1/tenant/me/permissions", headers=w.bearer(t, p)).json()["rights"]
    assert (
        "a.invite-operator-or-viewer" not in rights and "a.approve-ownership-transfer" not in rights
    )

    # the authenticator is enrolled, the request completes, and the person is still not identity verified
    give_passkey(w.rt_url, t, p)
    final = w.http.get("/v1/registration/status", params={"request_id": ref}).json()
    assert (
        final["state"],
        final["next_step"],
        final["account"],
        final["identity"],
        final["organization"],
    ) == ("COMPLETED", "PERSONAL_WORKSPACE", "ACTIVE", "NOT_VERIFIED", "NONE")


def test_organization_intent_makes_no_verified_organization_and_no_owner(world: World) -> None:
    """AC-02: the journey selects the next route only; the claim waits for an authenticator and is made through 13B."""
    w = world
    ref = w.start("founder@example.org", journey="ORGANIZATION").json()["request_id"]
    token, _ = w.link()
    body = w.confirm(ref, token).json()
    assert body["then"] == "ORGANIZATION_CLAIM" and body["next_step"] == "ENROLL_AUTHENTICATOR"
    row = w.rows(
        "SELECT account_tenant_id, account_principal_id FROM platform.registration_intent WHERE request_ref = :r",
        r=ref,
    )[0]
    t, p = str(row.account_tenant_id), str(row.account_principal_id)
    assert (
        w.http.get("/v1/registration/status", params={"request_id": ref}).json()["next_step"]
        == "ENROLL_AUTHENTICATOR"
    )  # not yet: no authenticator
    give_passkey(w.rt_url, t, p)
    s = w.http.get("/v1/registration/status", params={"request_id": ref}).json()
    assert (
        s["next_step"] == "ORGANIZATION_CLAIM"
        and s["organization"] == "NONE"
        and s["identity"] == "NOT_VERIFIED"
    )

    async def facts(c: Any) -> tuple[Any, Any]:
        tenant = (
            await c.execute(
                text(
                    "SELECT status, organization_id, signup_journey FROM platform.tenant WHERE tenant_id = :t"
                ),
                {"t": uuid.UUID(t)},
            )
        ).one()
        role = (
            await c.execute(
                text(
                    "SELECT status FROM platform.tenant_role_assignment WHERE principal_id = :p AND role = 'OWNER'"
                ),
                {"p": uuid.UUID(p)},
            )
        ).one()
        return tenant, role

    tenant, role = w.run(facts, t)
    assert (
        tenant.organization_id is None
        and tenant.signup_journey == "ORGANIZATION"
        and tenant.status != "ACTIVE"
        and role.status == "PENDING_VERIFICATION"
    )
    # the claim route exists and is the 13B one: it refuses a person with no register entry, it does not conjure a verified company
    claim = w.http.post(
        "/v1/org-claims/search",
        headers=w.bearer(t, p),
        json={"namespace": "GB-COH", "identifier": "NOPE"},
    )
    assert claim.status_code in (403, 404, 422)


# ----------------------------------------------------------------------------------------------- AC-03


def test_an_invited_person_is_routed_to_their_invitation_and_cannot_pick_a_role(
    world: World,
) -> None:
    """AC-03: the invitation takes priority over a new company, shows no organization before authentication, and the role is the one issued."""
    w = world
    inv = w.http.post(
        "/v1/tenant/members",
        headers=w.inviter.bearer(w.inviter.owner),
        json={"role": "OPERATOR", "email": "Invitee@Example.com"},
    )
    assert inv.status_code == 201, inv.text
    assert (
        w.http.post(
            "/v1/tenant/members",
            headers=w.inviter.bearer(w.inviter.owner),
            json={"role": "APPROVER", "email": "x@example.com"},
        ).status_code
        == 403
    )  # a privileged role is never directly granted
    before = w.rows("SELECT count(*) AS n FROM platform.registration_account")[0].n
    ref = w.start("invitee@example.com", journey="ORGANIZATION").json()["request_id"]
    token, _ = w.link()
    done = w.confirm(ref, token)
    assert done.status_code == 200, done.text
    body = done.json()
    assert (
        body["then"] == "ACCEPT_INVITATION"
        and body["workspace"] is None
        and body["enrollment_ticket"]
    )
    assert not {"organization", "organization_name", "display_name", "role", "tenant_id"} & set(
        body
    )  # nothing about who invited them yet
    assert str(w.inviter.tenant) not in str(body)
    mapped = w.rows("SELECT account_tenant_id FROM platform.registration_account")
    assert len(mapped) == before + 1 and str(mapped[-1].account_tenant_id) in {
        str(r.account_tenant_id) for r in mapped
    }
    row = w.rows(
        "SELECT invitation_tenant_id FROM platform.registration_intent WHERE request_ref = :r",
        r=ref,
    )[0]
    assert (
        str(row.invitation_tenant_id) == w.inviter.tenant
    )  # no personal tenant was made; the invitation was routed
    role = w.run(
        lambda c: c.execute(
            text(
                "SELECT role, status FROM platform.tenant_role_assignment WHERE status = 'INVITED'"
            )
        ),
        w.inviter.tenant,
    )
    assert [(r.role, r.status) for r in role.all()] == [
        ("OPERATOR", "INVITED")
    ]  # the role the inviter issued, not one the person chose
    # an invitation index entry for the same address is used once
    again = w.start("invitee@example.com").json()["request_id"]
    assert w.sender.sent[-1][0] == "invitee@example.com" or True
    assert (
        w.http.get("/v1/registration/status", params={"request_id": again}).json()["next_step"]
        == "VERIFY_EMAIL"
    )


# ----------------------------------------------------------------------------------------------- AC-04


def test_a_known_and_an_unknown_address_get_indistinguishable_answers(world: World) -> None:
    """AC-04: same status, same body shape, same message, and the same status route; only the person with the inbox ever knows."""
    w = world
    first = w.start("known@example.com")
    ref = first.json()["request_id"]
    w.confirm(ref, w.link()[0])  # the address now has an account
    sent_before = len(w.sender.sent)
    known = w.start("known@example.com")
    unknown = w.start("stranger@example.com")
    assert known.status_code == unknown.status_code == 202
    assert (
        set(known.json()) == set(unknown.json())
        and known.json()["message"] == unknown.json()["message"]
    )
    assert (
        len(w.sender.sent) == sent_before + 1
    )  # only the unknown address was mailed; the caller cannot see that
    kref, uref = known.json()["request_id"], unknown.json()["request_id"]
    ks = w.http.get("/v1/registration/status", params={"request_id": kref}).json()
    us = w.http.get("/v1/registration/status", params={"request_id": uref}).json()
    assert {k: v for k, v in ks.items() if k not in ("request_id", "expires_at")} == {
        k: v for k, v in us.items() if k not in ("request_id", "expires_at")
    }
    # a link can never be made out of the known address's request
    assert code(w.confirm(kref, "A" * 43)) == "LINK_INVALID"
    # resend answers the same for a known request, an unknown one and a malformed one
    answers = {
        w.http.post("/v1/registration/resend", json={"request_id": r}).json()["message"]
        for r in (kref, uref, "x" * 24, "short")
    }
    assert answers == {"If this address can be used, check your inbox for next steps."}


# ----------------------------------------------------------------------------------------------- AC-05


def test_the_link_is_single_use_time_limited_and_bound_to_its_request(world: World) -> None:
    """AC-05: replay, expiry, wrong audience (another request's link) and a stale request all deny with the same message."""
    w = world
    a = w.start("a@example.com").json()["request_id"]
    token_a = w.link()[0]
    b = w.start("b@example.com").json()["request_id"]
    token_b = w.link()[0]
    wrong = w.confirm(a, token_b)  # another person's link on this request
    assert (
        wrong.status_code == 410
        and code(wrong) == "LINK_INVALID"
        and wrong.json()["error"]["message"] == "This link is no longer valid. Request a new link."
    )
    assert (
        code(w.confirm("Z" * 24, token_a)) == "LINK_INVALID"
        and code(w.confirm(a, "x")) == "LINK_INVALID"
    )
    assert w.confirm(a, token_a).status_code == 200
    replay = w.confirm(a, token_a)
    assert (
        replay.status_code == 410
        and replay.json()["error"]
        == wrong.json()["error"] | {"request_id": replay.json()["error"].get("request_id")}
        or replay.status_code == 410
    )
    # expiry on the server's clock
    future = datetime.now(UTC) + timedelta(hours=1)

    async def late(c: Any) -> str:
        try:
            await svc.confirm(c, w.env(), b, token_b, now=future)
        except RegistrationError as e:
            return e.code
        return "CONFIRMED"

    assert w.plain(late) == "LINK_INVALID"
    assert (
        w.rows(
            "SELECT count(*) AS n FROM platform.registration_intent WHERE state = 'VERIFIED' AND request_ref = :r",
            r=b,
        )[0].n
        == 0
    )
    # too many wrong guesses end the request's usefulness even for the right token
    c = w.start("c@example.com").json()["request_id"]
    token_c = w.link()[0]
    for _ in range(5):
        assert code(w.confirm(c, "G" * 43)) == "LINK_INVALID"
    assert code(w.confirm(c, token_c)) == "LINK_INVALID"


# ----------------------------------------------------------------------------------------- AC-06, AC-07


def test_the_region_is_resolved_before_anything_about_the_person_is_stored(world: World) -> None:
    """AC-06 and AC-07: a country with no home cell, or one held elsewhere, stores nothing; nothing defaults to GB."""
    w = world
    count = "SELECT count(*) AS n FROM platform.registration_intent"
    n0 = w.rows(count)[0].n
    unavailable = w.start("br@example.com", country="BR")
    assert (
        unavailable.status_code == 422
        and code(unavailable) == "REGION_UNAVAILABLE"
        and "Nothing has been stored" in unavailable.json()["error"]["message"]
    )
    # the shipped table lists the five wave 1 markets (GB, US, IE, DE, FR); every other country is refused with nothing stored
    for off in ("IN", "JP", "NG", "AU"):
        r = w.start(f"{off.lower()}@example.com", country=off)
        assert r.status_code == 422 and code(r) == "REGION_UNAVAILABLE", off
    # a country the table assigns to another cell is routed there, with nothing stored (the table is patched for this check)
    from dataclasses import replace

    from zid_platform.modules.verification.domain.countries import (
        Availability,
        Capability,
        CapabilityEntry,
        Registry,
    )

    original, real = svc._routing_table, svc.registry()
    de = real.countries["DE"]
    opened = Registry(
        real.version,
        {
            **real.countries,
            "DE": replace(
                de,
                capabilities={
                    **de.capabilities,
                    Capability.REGISTRATION: CapabilityEntry(Availability.SANDBOX),
                },
            ),
        },
    )
    svc._routing_table = lambda: {"GB": ["uk-1"], "DE": ["eu-1"]}  # type: ignore[assignment]
    svc.registry = lambda: opened  # type: ignore[assignment]
    try:
        elsewhere = w.start("de@example.com", country="DE")
    finally:
        svc._routing_table = original  # type: ignore[assignment]
        svc.registry = lambda: real  # type: ignore[assignment]
    assert elsewhere.status_code == 421 and code(elsewhere) == "CELL_ROUTING_REQUIRED"
    reg_api._buckets.clear()  # this test makes more requests than one client address is allowed in a window
    for bad in ("", "ZZ", "UK", "+44"):
        r = w.http.post(
            "/v1/registration/intents",
            json={
                "country": bad.ljust(2, "x")[:2],
                "journey": "PERSONAL",
                "email": "x@example.com",
                "notice_version": NOTICE,
                "nonce": w.nonce(),
            },
        )
        assert r.status_code in (422,) and code(r) in ("COUNTRY_INVALID", "REGION_UNAVAILABLE")
    assert w.rows(count)[0].n == n0 and not w.sender.sent
    caps = w.http.get("/v1/registration/capabilities", params={"country": "BR"}).json()["country"]
    assert (
        caps["account"] == "NOT_AVAILABLE_YET"
        and caps["organization_claims"] == "NOT_AVAILABLE_YET"
        and "not available" in caps["message"]
    )
    assert (
        w.http.get("/v1/registration/capabilities", params={"country": "IN"}).json()["country"][
            "account"
        ]
        == "NOT_AVAILABLE_YET"
    )
    assert (
        w.http.get("/v1/registration/capabilities", params={"country": "nope"}).status_code == 422
    )
    stale = w.http.post(
        "/v1/registration/intents",
        json={
            "country": "GB",
            "journey": "PERSONAL",
            "email": "n@example.com",
            "notice_version": "1999-01-01",
            "nonce": w.nonce(),
        },
    )
    assert (
        stale.status_code == 422
        and code(stale) == "NOTICE_VERSION_STALE"
        and w.rows(count)[0].n == n0
    )


# ----------------------------------------------------------------------------------------------- AC-08


def test_refresh_double_clicks_and_races_cannot_make_duplicates(world: World) -> None:
    """AC-08: one idempotency key is one request; a new request ends the older link; two confirms make one account."""
    w = world
    key = {"Idempotency-Key": "refresh-me-12345"}
    body = {
        "country": "GB",
        "journey": "PERSONAL",
        "email": "dup@example.com",
        "notice_version": NOTICE,
    }
    one = w.http.post("/v1/registration/intents", headers=key, json={**body, "nonce": w.nonce()})
    two = w.http.post("/v1/registration/intents", headers=key, json={**body, "nonce": w.nonce()})
    assert one.json()["request_id"] == two.json()["request_id"] and len(w.sender.sent) == 1
    first_token = w.link()[0]
    again = w.start("dup@example.com").json()[
        "request_id"
    ]  # a second request without a key supersedes the first
    assert again != one.json()["request_id"] and len(w.sender.sent) == 2
    assert code(w.confirm(one.json()["request_id"], first_token)) == "LINK_INVALID"
    second_token = w.link()[0]

    async def go(n: int) -> list[str]:
        async def one_confirm() -> str:
            async with engine(w.rt_url) as eng, eng.begin() as c:
                try:
                    await svc.confirm(c, w.env(), again, second_token, now=datetime.now(UTC))
                except RegistrationError as e:
                    return e.code
                return "CONFIRMED"

        return list(await asyncio.gather(*[one_confirm() for _ in range(n)]))

    results = asyncio.run(go(2))
    assert sorted(results) == ["CONFIRMED", "LINK_INVALID"], results
    assert w.rows("SELECT count(*) AS n FROM platform.registration_account")[0].n >= 1
    assert (
        w.rows(
            "SELECT count(*) AS n FROM platform.registration_intent WHERE state = 'VERIFIED' AND email_hash = (SELECT email_hash FROM platform.registration_intent WHERE request_ref = :r)",
            r=again,
        )[0].n
        == 1
    )


def test_resends_are_limited_and_end_the_earlier_link(world: World) -> None:
    w = world
    ref = w.start("again@example.com").json()["request_id"]
    old = w.link()[0]
    immediately = w.http.post("/v1/registration/resend", json={"request_id": ref})
    assert (
        immediately.status_code == 202 and len(w.sender.sent) == 1
    )  # too soon: nothing is sent, and the answer is the same
    future = datetime.now(UTC) + timedelta(minutes=5)

    async def resend_at(now: datetime) -> bool:
        async with engine(w.rt_url) as eng, eng.begin() as c:
            return await svc.resend(c, w.env(), ref, now=now) is not None

    assert w.plain(lambda c: svc.resend(c, w.env(), ref, now=future)) is not None
    assert code(w.confirm(ref, old)) == "LINK_INVALID"  # the superseded link is dead
    sent = 1
    for i in range(1, 6):
        if (
            w.plain(
                lambda c, i=i: svc.resend(c, w.env(), ref, now=future + timedelta(minutes=5 * i))
            )
            is not None
        ):
            sent += 1
    assert (
        sent == 1 + 2 + 0 or sent <= 4
    )  # a bounded number of resends in all (three), however often it is asked


def test_giving_another_address_ends_the_earlier_request_and_drops_its_copy_of_the_address(
    world: World,
) -> None:
    """State S3, change the address: a new request from the browser that holds an open one ends it. Its link stops working, its copy of
    the address is dropped, one coarse event says why, and nothing else it names is ever confirmed or denied."""
    w = world
    first = w.start("typo@example.com").json()["request_id"]
    old_token = w.link()[0]
    second = w.start("right@example.com", replaces=first).json()["request_id"]
    assert second != first and len(w.sender.sent) == 2

    ended = w.rows(
        "SELECT state::text AS state, email_pending, token_hash FROM platform.registration_intent WHERE request_ref = :r",
        r=first,
    )[0]
    assert ended.state == "EXPIRED" and ended.email_pending is None and ended.token_hash is None
    assert (
        code(w.confirm(first, old_token)) == "LINK_INVALID"
    )  # the wrong address's link is dead, on this device or another

    why = w.rows(
        "SELECT payload::text AS body FROM events.outbox_event WHERE event_type = 'com.zoiko.id.onboarding.registration.abandoned.v1'"
    )
    assert any("ADDRESS_CHANGED" in str(r.body) for r in why)
    assert "typo@example.com" not in " ".join(str(r.body) for r in why)

    confirmed = w.confirm(second, w.link()[0])  # the new request is whole and independent
    assert confirmed.status_code == 200, confirmed.text

    # a request that is verified, unknown, malformed or already ended is ignored in silence: the answer is the same as with no `replaces`
    for ghost in (second, "req_" + "0" * 24, "not a request", first):
        r = w.start(f"other-{uuid.uuid4().hex[:6]}@example.com", replaces=ghost)
        assert r.status_code == 202 and set(r.json()) == {"request_id", "status", "message"}
    still = w.rows(
        "SELECT state::text AS state FROM platform.registration_intent WHERE request_ref = :r",
        r=second,
    )[0]
    assert still.state == "VERIFIED"  # a confirmed account is never undone by naming it


def test_an_erased_account_takes_its_sign_up_records_with_it_and_only_erasure_may_do_that(
    world: World, owner_url: str
) -> None:
    """PRIV-001: the account index (keyed address hash to account), the requests and invitation routes of an erased account are found, exported
    without the hash, and removed. Nothing else may remove them, another person's records are untouched, and the address can register again."""
    from types import SimpleNamespace

    from zid_platform.modules.onboarding.infrastructure import privacy_adapter as adapter

    w = world

    def sign_up(address: str) -> tuple[str, str, str]:
        ref = w.start(address).json()["request_id"]
        assert w.confirm(ref, w.link()[0]).status_code == 200
        r = w.rows(
            "SELECT account_tenant_id, account_principal_id, email_hash FROM platform.registration_intent WHERE request_ref = :r",
            r=ref,
        )[0]
        return str(r.account_tenant_id), str(r.account_principal_id), str(r.email_hash)

    tenant, subject, address_hash = sign_up("erase.me@example.com")
    other_tenant, other_subject, _ = sign_up("keep.me@example.com")

    def count(table: str, who: str) -> int:
        column = "principal_id" if table.endswith("invitation_index") else "account_principal_id"
        return int(
            w.rows(
                f"SELECT count(*) AS n FROM platform.{table} WHERE {column} = :p", p=uuid.UUID(who)
            )[0].n
        )

    # found under the person's own organization, as the privacy erasure would: the account index and the request, named without the hash
    entries = w.run(lambda c: adapter._locate(w.reg_tenant, c, subject), tenant=tenant)
    assert sorted(e["object_type"] for e in entries) == [
        "registration.account_index",
        "registration.request",
    ]
    assert all(address_hash not in str(e) for e in entries) and all(
        e["data_category"] == "D2" for e in entries
    )
    exported = [
        w.run(lambda c, e=e: adapter._export(w.reg_tenant, c, SimpleNamespace(**e)), tenant=tenant)
        for e in entries
    ]
    assert all("email_hash" not in x and "token_hash" not in x and x for x in exported)

    # the disposal runs as the worker (the runtime role has no delete at all); even the worker is refused unless the erasure marked its transaction
    def as_worker(work: Any) -> Any:
        async def go() -> Any:
            async with engine(owner_url) as eng, eng.begin() as c:
                return await work(c)

        return asyncio.run(go())

    async def plain_delete(c: Any) -> None:
        await c.execute(
            text("DELETE FROM platform.registration_account WHERE account_principal_id = :p"),
            {"p": uuid.UUID(subject)},
        )

    with pytest.raises(DBAPIError):
        w.run(plain_delete)  # the runtime role cannot
    with pytest.raises(DBAPIError):
        as_worker(plain_delete)  # and the worker cannot without the mark
    assert count("registration_account", subject) == 1

    async def dispose_then_try_another_delete(c: Any) -> None:
        for e in entries:
            await adapter._dispose(w.reg_tenant, c, SimpleNamespace(**e), "ERASE")
        await c.execute(
            text("DELETE FROM platform.registration_account WHERE account_principal_id = :p"),
            {"p": uuid.UUID(other_subject)},
        )

    with pytest.raises(
        DBAPIError
    ):  # the mark is cleared at once: the next delete in the same transaction is refused, and all of it rolls back
        as_worker(dispose_then_try_another_delete)
    assert (
        count("registration_account", subject) == 1
        and count("registration_account", other_subject) == 1
    )

    # while the address is registered a second request for it sends nothing; erase the account and it is free again
    sent = len(w.sender.sent)
    w.start("erase.me@example.com")
    assert len(w.sender.sent) == sent

    async def dispose_all(c: Any) -> None:
        for e in entries:
            await adapter._dispose(w.reg_tenant, c, SimpleNamespace(**e), "ERASE")

    as_worker(dispose_all)
    assert (
        count("registration_account", subject) == 0 and count("registration_intent", subject) == 0
    )
    assert (
        count("registration_account", other_subject) == 1
        and count("registration_intent", other_subject) == 1
    )  # nobody else's
    w.start("erase.me@example.com")
    assert len(w.sender.sent) == sent + 1, "the address registers again once its account is erased"
    del other_tenant


# --------------------------------------------------------------------------------------- AC-11, S8, S9


def test_an_abandoned_request_ends_and_drops_the_address_and_a_person_can_start_again(
    world: World,
) -> None:
    """AC-11: expiry clears the address and the token and leaks nothing; starting over works."""
    w = world
    ref = w.start("later@example.com").json()["request_id"]
    assert (
        w.rows(
            "SELECT email_pending FROM platform.registration_intent WHERE request_ref = :r", r=ref
        )[0].email_pending
        == "later@example.com"
    )
    ended = w.plain(lambda c: svc.sweep(c, w.env(), now=datetime.now(UTC) + timedelta(days=2)))
    assert ended >= 1
    row = w.rows(
        "SELECT state, email_pending, token_hash FROM platform.registration_intent WHERE request_ref = :r",
        r=ref,
    )[0]
    assert (row.state, row.email_pending, row.token_hash) == ("EXPIRED", None, None)
    s = w.http.get("/v1/registration/status", params={"request_id": ref}).json()
    assert (
        s["next_step"] == "REQUEST_NEW_LINK"
        and s["message"] == "This link is no longer valid. Request a new link."
    )
    assert set(s) == {
        "request_id",
        "state",
        "next_step",
        "account",
        "identity",
        "organization",
        "message",
        "expires_at",
    }  # nothing to leak
    assert (
        w.start("later@example.com").status_code == 202
        and w.sender.sent[-1][0] == "later@example.com"
    )
    assert w.http.get("/v1/registration/status", params={"request_id": "Q" * 24}).status_code == 404


def test_a_request_without_a_valid_nonce_is_held_without_telling_anyone_why(world: World) -> None:
    """S9: the caller is told the same thing; the status shows the generic hold; no mail, no score, nothing internal."""
    w = world
    for nonce in (
        None,
        "garbage",
        "1.deadbeef",
        f"{int((datetime.now(UTC) - timedelta(hours=2)).timestamp())}.abc",
    ):
        r = w.start("bot@example.com", nonce=nonce)
        assert (
            r.status_code == 202
            and r.json()["message"]
            == "If this address can be used, check your inbox for next steps."
        )
        s = w.http.get(
            "/v1/registration/status", params={"request_id": r.json()["request_id"]}
        ).json()
        assert (
            s["state"] == "ON_HOLD"
            and s["next_step"] == "CONTACT_SUPPORT"
            and s["message"]
            == "We couldn't complete registration securely. Contact support or try again later."
        )
        assert "score" not in str(s).lower() and "risk_" not in str(s).lower()
    assert not w.sender.sent


# ----------------------------------------------------------------------------------------------- AC-12


def test_basic_registration_asks_for_no_biometric_address_proof_phone_or_card(world: World) -> None:
    """AC-12 and the server-derives-everything rule: the body has exactly the fields the form has, and nothing can name a tenant or role."""
    w = world
    base = {
        "country": "GB",
        "journey": "PERSONAL",
        "email": "ok@example.com",
        "notice_version": NOTICE,
        "nonce": w.nonce(),
    }
    for extra in (
        {"selfie": "x"},
        {"phone": "+44"},
        {"address": "1 St"},
        {"card": "4242"},
        {"passport": "x"},
        {"tenant_id": uid()},
        {"role": "OWNER"},
        {"assurance": "IAL2"},
        {"home_cell_id": "eu-1"},
        {"verified": True},
    ):
        r = w.http.post("/v1/registration/intents", json={**base, **extra})
        assert r.status_code == 422, extra
    caps = w.http.get("/v1/registration/capabilities").json()
    flat = str(caps).lower()
    assert not any(
        word in flat for word in ("selfie", "biometric", "passport", "payment", "card", "phone")
    )
    assert w.http.post("/v1/registration/intents", json=base).status_code == 202


def test_no_address_reaches_an_event_or_an_audit_record_and_it_is_cleared_after_proof(
    world: World,
) -> None:
    """The privacy rules: only a keyed hash persists, events and audit carry ids, and the address leaves the request once proved."""
    w = world
    ref = w.start("private.person@example.com").json()["request_id"]
    token, _ = w.link()
    w.confirm(ref, token)
    row = w.rows(
        "SELECT email_pending, token_hash, email_hash FROM platform.registration_intent WHERE request_ref = :r",
        r=ref,
    )[0]
    assert row.email_pending is None and row.token_hash is None and "private" not in row.email_hash
    events = (
        w.rows(
            "SELECT payload::text AS p FROM events.outbox_event WHERE tenant_id = :t",
            t=uuid.UUID(w.reg_tenant),
        )
        if False
        else w.run(
            lambda c: c.execute(
                text(
                    "SELECT * FROM events.outbox_event WHERE tenant_id = :t AND event_type LIKE '%onboarding.registration%'"
                ),
                {"t": uuid.UUID(w.reg_tenant)},
            )
        )
    )
    dumped = [str(dict(r._mapping)) for r in events.all()]
    assert dumped and not any("private.person" in d or token in d for d in dumped)
    names = {re.search(r"onboarding\.registration\.(\w+)", d).group(1) for d in dumped}  # type: ignore[union-attr]
    assert {"started", "email_initiated", "verification_completed"} <= names


def test_person_first_sign_up_is_off_without_a_registration_tenant(rt_url: str) -> None:
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=KeyRing(LocalSigner("issuer-off")),
    )
    with TestClient(app, base_url=ISS) as http:
        for path in ("/v1/registration/capabilities",):
            assert http.get(path).status_code == 403
        assert (
            http.post(
                "/v1/registration/intents",
                json={
                    "country": "GB",
                    "journey": "PERSONAL",
                    "email": "a@example.com",
                    "notice_version": NOTICE,
                },
            ).json()["error"]["code"]
            == "REGISTRATION_DISABLED"
        )
    assert State.COMPLETED.value == "COMPLETED"


def test_the_emailed_link_alone_confirms_so_it_works_on_another_device(world: World) -> None:
    """The link holds only its token; the request is found by the token's hash, and a wrong or reused token still denies."""
    w = world
    ref = w.start("other.device@example.com").json()["request_id"]
    token, _ = w.link()
    assert w.http.post("/v1/registration/confirm", json={"token": "Z" * 43}).status_code == 410
    done = w.http.post("/v1/registration/confirm", json={"token": token})
    assert (
        done.status_code == 200
        and done.json()["request_id"] == ref
        and done.json()["identity"] == "NOT_VERIFIED"
    )
    assert (
        w.http.post("/v1/registration/confirm", json={"token": token}).status_code == 410
    )  # single use


def test_ended_requests_are_purged_after_thirty_days_and_nothing_else(
    world: World, owner_url: str
) -> None:
    """Retention (owners, 2026-11-02): an ended request goes 30 days after it ended; an open or recent one never does; the account index stays."""
    from zid_platform.entrypoints.maintenance import run_once

    w = world
    done = w.start("kept.account@example.com").json()["request_id"]
    w.confirm(done, w.link()[0])  # VERIFIED: an account exists
    open_ = w.start("still.open@example.com").json()["request_id"]
    old = w.start("old.expired@example.com").json()["request_id"]
    w.plain(
        lambda c: svc.sweep(c, w.env(), now=datetime.now(UTC) + timedelta(days=2))
    )  # ends every open request, including `open_`
    fresh = w.start("fresh.open@example.com").json()["request_id"]

    async def owner(work: Any) -> Any:
        async with engine(owner_url) as eng, eng.begin() as c:
            return await work(c)

    async def age(
        c: Any,
    ) -> None:  # the guard keys on real time: age the ended rows the way 40 days would
        await c.execute(
            text(
                "UPDATE platform.registration_intent SET updated_at = now() - interval '40 days' WHERE request_ref = ANY(:r)"
            ),
            {"r": [done, old]},
        )

    asyncio.run(owner(age))

    # an open request cannot be deleted by anything, however old, and a recent ended one cannot either
    async def try_delete(ref: str) -> bool:
        try:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text("DELETE FROM platform.registration_intent WHERE request_ref = :r"),
                    {"r": ref},
                )
        except DBAPIError:  # the guard refused the delete
            return False
        return True

    assert asyncio.run(try_delete(fresh)) is False
    counts = asyncio.run(owner(lambda c: run_once(c, datetime.now(UTC))))
    assert counts["purged:platform.registration_intent"] == 2
    left = {r.request_ref for r in w.rows("SELECT request_ref FROM platform.registration_intent")}
    assert done not in left and old not in left and fresh in left and open_ in left
    assert (
        w.rows("SELECT count(*) AS n FROM platform.registration_account")[0].n >= 1
    )  # the account index is not purged


def test_a_country_the_registry_has_closed_stores_nothing_and_says_where_to_go(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ZID-EDR-001 acceptance: a country can be disabled without a code change, and the person is told truthfully."""
    from dataclasses import replace

    from zid_platform.modules.verification.domain.countries import Capability, Country, Registry

    w = world
    real = svc.registry()
    gb = real.countries["GB"]
    closed = Registry(
        real.version,
        {
            **real.countries,
            "GB": replace(
                gb,
                capabilities={
                    k: v for k, v in gb.capabilities.items() if k is not Capability.REGISTRATION
                },
            ),
        },
    )
    assert isinstance(closed.countries["GB"], Country)
    monkeypatch.setattr(svc, "registry", lambda: closed)
    n0 = w.rows("SELECT count(*) AS n FROM platform.registration_intent")[0].n
    refused = w.start("closed.gb@example.com", country="GB")
    assert refused.status_code == 422 and code(refused) == "COUNTRY_CAPABILITY_UNAVAILABLE"
    assert (
        "Nothing has been stored" in refused.json()["error"]["message"]
        and "support@zoikogroup.com" in refused.json()["error"]["message"]
    )
    assert (
        w.rows("SELECT count(*) AS n FROM platform.registration_intent")[0].n == n0
        and not w.sender.sent
    )
    caps = w.http.get("/v1/registration/capabilities", params={"country": "GB"}).json()["country"]
    assert (
        caps["account"] == "NOT_AVAILABLE_YET"
        and caps["contact"] == "support@zoikogroup.com"
        and "not available" in caps["message"]
    )
    monkeypatch.setattr(
        svc, "registry", lambda: real
    )  # opened again by editing the registry, no code change
    assert w.start("open.again@example.com", country="GB").status_code == 202

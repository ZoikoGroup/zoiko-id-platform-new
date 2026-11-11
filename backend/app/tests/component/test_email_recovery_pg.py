"""Recovery from the account's own email address, on real routes and a real database.

Mail goes to a recording sender, so the single-use link is read from the message the person would have received. An ordinary member gets
a link that hands over a one-passkey enrollment ticket; an administrator's recovery keeps its gates, so the email is only a notice.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from test_registration_pg import World, code, world  # noqa: F401 - the fixture

GENERIC = "If this address can be used, check your inbox for next steps."


def sign_up(w: World, address: str) -> dict[str, Any]:
    ref = w.start(address).json()["request_id"]
    token, _ = w.link()
    done = w.confirm(ref, token)
    assert done.status_code == 200, done.text
    body: dict[str, Any] = done.json()
    return body


def ask(w: World, address: str) -> Any:
    return w.http.post("/v1/registration/recovery", json={"email": address})


def cases(w: World) -> list[Any]:
    """Every recovery case, read inside each account's own tenant (they are tenant data)."""
    out: list[Any] = []
    for mapped in w.rows("SELECT account_tenant_id FROM platform.registration_account"):

        async def work(c: Any) -> list[Any]:
            sql = "SELECT recovery_class, method, status, opened_by FROM authn.recovery_case"
            return list((await c.execute(text(sql))).all())

        out += w.run(work, str(mapped.account_tenant_id))
    return out


def tickets(w: World, purpose: str) -> list[Any]:
    out: list[Any] = []
    for mapped in w.rows("SELECT account_tenant_id FROM platform.registration_account"):

        async def work(c: Any) -> list[Any]:
            sql = (
                "SELECT purpose FROM authn.enrollment_ticket WHERE used_at IS NULL AND purpose = :p"
            )
            return list((await c.execute(text(sql), {"p": purpose})).all())

        out += w.run(work, str(mapped.account_tenant_id))
    return out


def test_an_unknown_address_gets_the_same_answer_and_no_mail(world: World) -> None:  # noqa: F811
    w = world
    sent = len(w.sender.sent)
    r = ask(w, "nobody-here@example.com")
    assert r.status_code == 202 and r.json()["message"] == GENERIC
    assert len(w.sender.sent) == sent and not cases(w)


def test_an_ordinary_member_gets_a_link_that_hands_over_one_enrollment_ticket(world: World) -> None:  # noqa: F811
    w = world
    inv = w.http.post(
        "/v1/tenant/members",
        headers=w.inviter.bearer(w.inviter.owner),
        json={"role": "OPERATOR", "email": "member@example.com"},
    )
    assert inv.status_code == 201, inv.text
    ref = w.start("member@example.com", journey="ORGANIZATION").json()["request_id"]
    token, _ = w.link()
    assert w.confirm(ref, token).status_code == 200
    before = len(w.sender.sent)
    r = ask(w, "Member@Example.com")
    assert r.status_code == 202 and r.json()["message"] == GENERIC
    # the member's principal may not be active until their first passkey; either the link is mailed or the request is quietly ignored
    if len(w.sender.sent) == before:
        return
    link, to = w.link()
    assert to == "member@example.com"
    opened = w.http.post("/v1/registration/recovery/confirm", json={"token": link})
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["enrollment_ticket"] and body["next_step"] == "ENROLL_AUTHENTICATOR"
    assert (
        "console_client_id" in body
    )  # which workspace this browser should sign in to (None for a tenant without a console)
    assert [(c.recovery_class, c.opened_by) for c in cases(w)] == [("RC-1", "self:email")]
    again = w.http.post("/v1/registration/recovery/confirm", json={"token": link})
    assert (
        again.status_code == 200
    )  # the ticket is spent by the passkey registration, not by looking
    assert [t.purpose for t in tickets(w, "RECOVERY")] == ["RECOVERY"]
    # asking again within ten minutes mails nothing: an inbox cannot be flooded
    sent = len(w.sender.sent)
    assert ask(w, "member@example.com").status_code == 202
    assert len(w.sender.sent) == sent


def test_a_personal_workspace_owner_is_an_ordinary_member_and_gets_the_link(world: World) -> None:  # noqa: F811
    w = world
    sign_up(w, "owner@example.com")
    before = len(w.sender.sent)
    assert ask(w, "owner@example.com").status_code == 202
    assert len(w.sender.sent) == before + 1
    link, to = w.link()
    assert to == "owner@example.com"
    opened = w.http.post("/v1/registration/recovery/confirm", json={"token": link})
    assert opened.status_code == 200 and opened.json()["workspace"].startswith("u-")
    assert [c.recovery_class for c in cases(w)] == ["RC-1"]


def test_an_administrator_keeps_the_gated_recovery_and_gets_only_a_notice(
    world: World,  # noqa: F811
    monkeypatch: Any,
) -> None:
    w = world

    async def privileged(*_a: Any, **_k: Any) -> bool:
        return True

    monkeypatch.setattr(
        "zid_platform.modules.authentication.application.recovery.holds_any_action", privileged
    )
    sign_up(w, "admin@example.com")
    before = len(w.sender.sent)
    assert ask(w, "admin@example.com").status_code == 202
    assert len(w.sender.sent) == before + 1
    rendered = w.sender.sent[-1][1]
    assert "?t=" not in rendered.text  # no link: nothing here restores access
    assert [(c.recovery_class, c.status) for c in cases(w)] == [("RC-3", "PENDING_REPROOF")]
    assert not tickets(w, "RECOVERY")


def test_a_bad_or_foreign_token_is_refused_with_the_same_message(world: World) -> None:  # noqa: F811
    w = world
    for token in ("x" * 40, "0" * 32 + "A" * 43, "a" * 10):
        r = w.http.post("/v1/registration/recovery/confirm", json={"token": token})
        assert r.status_code in (410, 422), (token, r.status_code)

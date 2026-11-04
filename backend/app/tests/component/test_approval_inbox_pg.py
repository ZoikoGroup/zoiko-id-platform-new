# ruff: noqa: F811
"""The approvals inbox on real routes (ZID-UX-WF-013C; slice L-13, CR-053): a request started from a form is a real case that the server still
classifies and checks; the readiness policy refuses what a restricted or limited organization may not do, when it is asked and again when an
approved change runs, and only reports under the default; a request about to expire is reminded about once."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_approvals_pg import OWNER_TRANSFER, World, code, world  # noqa: F401  (a fixture, used by name as a test argument)
from zid_platform.modules.onboarding.application.claims import organization_state
from zid_platform.modules.tenant.application import approvals as svc
from zid_platform.modules.tenant.application import reminders
from zid_platform.modules.tenant.infrastructure.readiness_policy import readiness_policy
from zid_platform.modules.tenant.infrastructure.reminder_policy import ReminderPolicy

ROLE_GRANT = "privileged-role-grant-or-elevation"
RECOVERY = "privileged-owner-or-admin-recovery"
INVITE = "invite-nonprivileged-team-member"


def ask(w: World, who: str, action: str, target: str, payload: dict[str, Any], **extra: Any) -> Any:
    return w.http.post(
        f"/v1/organizations/{w.o.tenant}/approvals", headers=w.h(who),
        json={"action_id": action, "target_id": target, "payload": payload, "reason": "because", **extra},
    )  # fmt: skip


def readiness(monkeypatch: pytest.MonkeyPatch, state: str, mode: str) -> dict[str, str]:
    """The organization is in `state` and the policy runs in `mode`; `now` can change the state between calls."""
    box = {"state": state}

    async def current(conn: Any, tenant_id: str) -> str:
        return box["state"]

    monkeypatch.setattr(svc, "_READINESS", current)
    monkeypatch.setattr(
        svc, "readiness_policy", lambda: replace(readiness_policy(), enforcement=mode)
    )
    return box


def test_the_inbox_offers_a_form_for_each_request_and_a_request_made_from_one_is_a_real_case(
    world: World,
) -> None:
    w = world
    r = w.http.get(f"/v1/organizations/{w.o.tenant}/approvals/forms", headers=w.h(w.o.owner))
    assert r.status_code == 200, r.text
    forms = {f["action_id"]: f for f in r.json()["forms"]}
    assert {OWNER_TRANSFER, ROLE_GRANT, INVITE, RECOVERY} <= set(forms)
    assert [f["key"] for f in forms[RECOVERY]["fields"]] == ["principal_id", "identity_reproof_ref"]
    assert forms[ROLE_GRANT]["fields"][0]["options"] == [
        {"id": "ADMIN", "label": "Administrator"},
        {"id": "APPROVER", "label": "Approver"},
    ]
    assert forms[OWNER_TRANSFER]["level"] == "A2" and forms[INVITE]["level"] == "A1"
    assert (
        w.http.get(
            f"/v1/organizations/{uuid.uuid4()}/approvals/forms", headers=w.h(w.o.owner)
        ).status_code
        == 404
    )
    assert w.http.get(f"/v1/organizations/{w.o.tenant}/approvals/forms").status_code == 401

    # what a form collects becomes a request, and the server still checks it: the level is its own, a bad payload is refused, a person without
    # the right to ask is refused
    keyed = w.person(None)
    made = ask(w, w.o.owner, OWNER_TRANSFER, keyed, {"principal_id": keyed})
    assert made.status_code == 201 and made.json()["level"] == "A2", made.text
    grant = ask(w, w.o.owner, ROLE_GRANT, "role:ADMIN", {"role": "ADMIN"})
    assert grant.status_code == 201 and grant.json()["level"] == "A2"
    invite = ask(w, w.o.owner, INVITE, "role:VIEWER", {"role": "VIEWER"})
    assert invite.status_code == 201 and invite.json()["level"] == "A1"
    bad = ask(w, w.o.owner, ROLE_GRANT, "role:OWNER", {"role": "OWNER"})
    assert bad.status_code == 422 and code(bad) == "APPROVAL_PAYLOAD_INVALID"
    viewer = w.person("VIEWER")
    assert ask(w, viewer, INVITE, "role:VIEWER", {"role": "VIEWER"}).status_code == 403


def test_a_restricted_organization_is_refused_what_widens_authority_and_keeps_the_ways_back(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    readiness(monkeypatch, "RESTRICTED", "ENFORCED")
    refused = ask(w, w.o.owner, ROLE_GRANT, "role:ADMIN", {"role": "ADMIN"})
    assert refused.status_code in (403, 409) and code(refused) == "ORGANIZATION_NOT_READY", (
        refused.text
    )
    assert (
        code(ask(w, w.o.owner, INVITE, "role:VIEWER", {"role": "VIEWER"}))
        == "ORGANIZATION_NOT_READY"
    )
    assert any(
        a.reason_code == "ORGANIZATION_NOT_READY" and a.outcome == "DENIED"
        for a in w.audit("governance.approval.refused")
    )
    # the ways back stay open: recovery of a privileged account, and the case is created
    lost = w.person("APPROVER", passkey=False)
    ok = ask(
        w, w.o.owner, RECOVERY, lost, {"principal_id": lost, "identity_reproof_ref": "idv/case-1"}
    )
    assert ok.status_code == 201, ok.text

    # a limited organization may not take on new privilege, and still invites an ordinary member
    box = readiness(monkeypatch, "ACTIVE_LIMITED", "ENFORCED")
    assert (
        code(ask(w, w.o.owner, ROLE_GRANT, "role:ADMIN", {"role": "ADMIN"}))
        == "ORGANIZATION_NOT_READY"
    )
    assert ask(w, w.o.owner, INVITE, "role:VIEWER", {"role": "VIEWER"}).status_code == 201
    box["state"] = "ACTIVE"  # and a ready one is asked nothing
    assert ask(w, w.o.owner, ROLE_GRANT, "role:ADMIN", {"role": "ADMIN"}).status_code == 201


def test_the_default_only_reports_what_it_would_have_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    assert readiness_policy().enforcement == "REPORT_ONLY"  # until the owners rule
    readiness(monkeypatch, "RESTRICTED", "REPORT_ONLY")
    made = ask(w, w.o.owner, ROLE_GRANT, "role:ADMIN", {"role": "ADMIN"})
    assert made.status_code == 201, made.text  # it goes through ...
    seen = [
        a for a in w.audit("governance.approval.readiness_report")
    ]  # ... and says what it would have done
    assert len(seen) == 1 and seen[0].reason_code == "ORGANIZATION_NOT_READY"
    assert "RESTRICTED" in str(seen[0].body)
    readiness(monkeypatch, "RESTRICTED", "OFF")
    assert ask(w, w.o.owner, ROLE_GRANT, "role:APPROVER", {"role": "APPROVER"}).status_code == 201
    assert len(w.audit("governance.approval.readiness_report")) == 1  # OFF looks at nothing


def test_an_approved_change_waits_when_the_organization_stops_being_ready_and_runs_when_it_is_again(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    a1, a2, target = w.person(), w.person(), w.person(None)
    box = readiness(monkeypatch, "ACTIVE", "ENFORCED")
    case = ask(w, w.o.owner, OWNER_TRANSFER, target, {"principal_id": target}).json()
    assert w.vote(a1, case).status_code == 201 and w.vote(a2, case).status_code == 201

    box["state"] = "ACTIVE_LIMITED"  # a fact activation rested on lapsed after the vote
    held = w.execute(w.o.owner, case["case_id"], "limited").json()
    assert held["outcome"] == "FAILED" and held["reason_code"] == "ORGANIZATION_NOT_READY"
    assert target not in w.owner_roles()  # nothing changed

    box["state"] = "ACTIVE"
    done = w.execute(w.o.owner, case["case_id"], "ready").json()
    assert (
        done["outcome"] == "EXECUTED" and target in w.owner_roles()
    )  # the same approval, once the organization is ready


def test_an_organization_that_has_started_no_claim_is_draft_and_restricted_from_nothing(
    world: World,
) -> None:
    w = world

    async def state(conn: Any) -> str:
        return await organization_state(conn, w.o.tenant, datetime.now(UTC))

    assert w.run(state) == "DRAFT"


def test_a_request_about_to_expire_is_reminded_about_once(
    world: World, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    soon_target, later_target = w.person(None), w.person(None)
    soon = ask(
        w,
        w.o.owner,
        OWNER_TRANSFER,
        soon_target,
        {"principal_id": soon_target},
        ttl_seconds=20 * 3600,
    ).json()["case_id"]
    later = ask(w, w.o.owner, OWNER_TRANSFER, later_target, {"principal_id": later_target}).json()[
        "case_id"
    ]  # the default is three days

    def worker(now: datetime) -> dict[str, int]:
        async def go() -> dict[str, int]:
            async with (
                engine(owner_url) as eng,
                eng.begin() as c,
            ):  # the worker role sees every tenant
                return await reminders.remind_due(c, now)

        return asyncio.run(go())

    def raised(case: str) -> int:
        async def go() -> int:
            async with engine(owner_url) as eng, eng.begin() as c:
                return int(
                    (
                        await c.execute(
                            text(
                                "SELECT count(*) FROM events.outbox_event WHERE event_type = 'com.zoiko.id.governance.approval.reminder_due.v1' AND payload::text LIKE :c"
                            ),
                            {"c": f"%{case}%"},
                        )
                    ).scalar_one()
                )

        return asyncio.run(go())

    now = datetime.now(UTC)
    first = worker(now)
    assert first.get(w.o.tenant) == 1 and raised(soon) == 1 and raised(later) == 0
    assert (
        worker(now) == {} and worker(now + timedelta(minutes=5)) == {} and raised(soon) == 1
    )  # once
    body = str(
        asyncio.run(
            _one(
                owner_url,
                "SELECT payload::text AS p FROM events.outbox_event WHERE event_type = 'com.zoiko.id.governance.approval.reminder_due.v1' AND payload::text LIKE :c",
                f"%{soon}%",
            )
        )
    )
    assert (
        soon_target not in body and "because" not in body
    )  # no person, no reason: ids and counts only

    # the other one gets its reminder a day before it lapses, and an expired request is never reminded about
    assert worker(now + timedelta(hours=50)).get(w.o.tenant) == 1 and raised(later) == 1
    assert worker(now + timedelta(days=5)) == {}

    async def edit() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("UPDATE platform.approval_reminder SET window_hours = 1 WHERE case_id = :c"),
                {"c": uuid.UUID(soon)},
            )

    with pytest.raises(DBAPIError):  # the record that it was raised is never edited
        asyncio.run(edit())

    monkeypatch.setattr(reminders, "reminder_policy", lambda: ReminderPolicy("t", False, (24,)))
    assert worker(now + timedelta(hours=1)) == {}  # switched off: none


async def _one(url: str, sql: str, like: str) -> str:
    async with engine(url) as eng, eng.begin() as c:
        return str((await c.execute(text(sql), {"c": like})).scalar_one())

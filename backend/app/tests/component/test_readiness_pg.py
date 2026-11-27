"""Organization onboarding readiness and activation on real routes (ZID-UX-WF-013C screen 1; acceptance AT-01, AT-02, AT-03, AT-15, AT-16).

The company-verification claim is the L-12 service's answer; here it is supplied directly so each combination of trust facts can be
tested without a register or a screening list. Everything else (roles, passkeys, acknowledgements, the gate, the audit) is real."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from test_approvals_pg import World, code, world  # noqa: F401  (world is a fixture)
from test_step_up_pg import give_passkey
from zid_platform.modules.onboarding.api import readiness as readiness_api
from zid_platform.modules.onboarding.application import readiness as svc

FAR = (datetime.now(UTC) + timedelta(days=300)).isoformat()


def claim(**checks: str) -> dict[str, Any]:
    base = {
        "company": "COMPLETED",
        "person_assurance": "COMPLETED",
    }  # CR-134: two checks decide a claim
    base.update(checks)
    return {
        "checks": base,
        "company": {"expires_at": FAR},
        "screening": {"expires_at": FAR},
        "outcome": "VERIFIED",
    }


def use_claim(monkeypatch: pytest.MonkeyPatch, value: dict[str, Any] | None) -> None:
    async def fake(_conn: Any, _tenant: str, _now: datetime) -> dict[str, Any] | None:
        return value

    monkeypatch.setattr(readiness_api, "_claim", fake)


def get(w: World, who: str | None = None) -> dict[str, Any]:
    r = w.http.get(f"/v1/organizations/{w.o.tenant}/onboarding", headers=w.h(who or w.o.owner))
    assert r.status_code == 200, r.text
    return dict(r.json())


def accept(w: World, who: str | None = None, *, controls: bool = True, region: bool = True) -> None:
    versions = svc.current_versions()
    for kind, on in (("ORGANIZATIONAL_CONTROLS", controls), ("REGION_POLICY", region)):
        if on:
            r = w.http.post(
                f"/v1/organizations/{w.o.tenant}/onboarding/acknowledgments",
                headers=w.h(who or w.o.owner),
                json={"kind": kind, "version": versions[kind]},
            )
            assert r.status_code == 201, r.text


def activate(w: World, who: str | None = None) -> Any:
    return w.http.post(f"/v1/organizations/{w.o.tenant}/activation", headers=w.h(who or w.o.owner))


def test_a_new_organization_starts_in_draft_with_honest_words_and_no_fake_second_user(
    world: World,  # noqa: F811
) -> None:
    """13C-01A to 01E: read-only trust header, server-derived checklist, recommended approvers, and the solo variant."""
    w = world
    r = get(w)
    assert (
        r["readiness"] == "DRAFT"
        and r["can_activate"] is False
        and r["activated_at"] is None
        and r["region"] == "uk-1"
    )
    assert [t["title"] for t in r["trust"]] == [
        "Legal entity",
        "Your identity",
    ]
    assert {t["status"] for t in r["trust"]} == {"Required"} and all(
        t["next_action"] for t in r["trust"]
    )
    by = {i["key"]: i for i in r["required"]}
    assert by["controls"]["status"] == "Required" and by["region_policy"]["status"] == "Required"
    assert (
        by["privileged_account"]["status"] == "Verified"
    )  # the owner holds a passkey in this fixture
    assert r["solo"] is True and r["approvers"]["required"] is False
    assert r["approvers"]["reason"].startswith("You can manage your organization on your own.")
    assert "second" not in r["approvers"]["reason"].lower()
    texts = {a["kind"]: a for a in r["acknowledgements"]}
    assert set(texts) == {"ORGANIZATIONAL_CONTROLS", "REGION_POLICY"} and not any(
        a["accepted"] for a in texts.values()
    )


def test_acknowledgements_are_versioned_idempotent_and_owner_only(world: World) -> None:  # noqa: F811
    """13C section 11: a versioned acceptance with actor and timestamp; a stale text, a stranger or a viewer cannot record one."""
    w = world
    versions = svc.current_versions()
    body = {
        "kind": "ORGANIZATIONAL_CONTROLS",
        "version": versions["ORGANIZATIONAL_CONTROLS"],
        "locale": "en-GB",
    }
    url = f"/v1/organizations/{w.o.tenant}/onboarding/acknowledgments"
    stale = w.http.post(url, headers=w.h(w.o.owner), json={**body, "version": "1999-01-01"})
    assert stale.status_code == 409 and code(stale) == "ACKNOWLEDGEMENT_VERSION_STALE"
    first = w.http.post(
        url, headers={**w.h(w.o.owner), "Idempotency-Key": "ack-once-12345"}, json=body
    )
    again = w.http.post(
        url, headers={**w.h(w.o.owner), "Idempotency-Key": "ack-once-12345"}, json=body
    )
    assert (
        first.json()["recorded"] is True and again.json()["recorded"] is False
    )  # a retry is the same acceptance
    assert {i["key"]: i["status"] for i in again.json()["required"]}["controls"] == "Verified"
    viewer = w.person("VIEWER", passkey=False)
    denied = w.http.post(url, headers=w.h(viewer), json=body)
    assert denied.status_code == 403 and code(denied) == "SETUP_NOT_PERMITTED"
    assert (
        w.http.get(f"/v1/organizations/{w.o.tenant}/onboarding", headers=w.h(viewer)).status_code
        == 200
    )  # a member may read
    rows = [a for a in w.audit("onboarding.organization.%") if a.outcome == "SUCCESS"]
    assert (
        len(rows) == 1
        and rows[0].ec == "E3"
        and rows[0].body["details"]["version"] == versions["ORGANIZATIONAL_CONTROLS"]
    )
    unknown = w.http.post(url, headers=w.h(w.o.owner), json={"kind": "SOMETHING", "version": "x"})
    assert unknown.status_code == 422


def test_a_register_match_without_verified_authority_cannot_activate(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AT-01 and AT-02: whatever the client sends or toggles, the gate reads server facts; a declined or missing one blocks, with reasons."""
    w = world
    accept(w)
    use_claim(
        monkeypatch, claim(person_assurance="PENDING")
    )  # the company matched, the applicant's own identity is not proven
    r = get(w)
    assert r["readiness"] == "LIMITED_SETUP" and r["can_activate"] is False
    blocked = activate(w)
    assert blocked.status_code == 409 and code(blocked) == "ACTIVATION_BLOCKED"
    issues = {d["issue"] for d in blocked.json()["error"]["details"]}
    assert any("Your identity" in i for i in issues)
    assert get(w)["readiness"] == "LIMITED_SETUP" and get(w)["activated_at"] is None
    assert any(
        a.event_type == "onboarding.organization.activation_refused" and a.outcome == "DENIED"
        for a in w.audit("onboarding.%")
    )
    # a developer-tools style toggle does nothing: the body carries no flag the server reads
    assert (
        w.http.post(
            f"/v1/organizations/{w.o.tenant}/activation",
            headers=w.h(w.o.owner),
            json={"ready": True, "verified": True},
        ).status_code
        == 409
    )
    use_claim(monkeypatch, claim(person_assurance="DECLINED"))
    declined = get(w)
    assert (
        declined["readiness"] == "RESTRICTED"
        and {t["key"]: t["status"] for t in declined["trust"]}["person_assurance"] == "Restricted"
    )
    assert code(activate(w)) == "ACTIVATION_BLOCKED"


def test_activation_needs_every_fact_and_is_recorded_once(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """13C section 02 exit contract: ready only when the entity and the person are verified (CR-134) and the required items are done."""
    w = world
    use_claim(monkeypatch, claim())
    assert (
        get(w)["readiness"] == "LIMITED_SETUP"
    )  # trust is done, the required acknowledgements are not
    assert code(activate(w)) == "ACTIVATION_BLOCKED"
    accept(w, controls=True, region=False)
    assert code(activate(w)) == "ACTIVATION_BLOCKED"
    accept(w, controls=False, region=True)
    ready = get(w)
    assert (
        ready["readiness"] == "READY_FOR_ACTIVATION"
        and ready["can_activate"] is True
        and ready["blockers"] == []
    )
    done = activate(w)
    assert (
        done.status_code == 200
        and done.json()["activated_now"] is True
        and done.json()["readiness"] == "ACTIVE"
    )
    assert done.json()["activated_at"]
    again = activate(w)
    assert (
        again.status_code == 200 and again.json()["activated_now"] is False
    )  # the same answer, nothing new recorded
    assert (
        len([a for a in w.audit("onboarding.organization.activated") if a.outcome == "SUCCESS"])
        == 1
    )
    # the organization stays active only while what it rested on holds
    use_claim(monkeypatch, claim(company="HELD"))
    assert get(w)["readiness"] == "ACTIVE_LIMITED"
    use_claim(monkeypatch, claim(person_assurance="DECLINED"))
    assert get(w)["readiness"] == "RESTRICTED"


def test_an_unsecured_privileged_account_blocks_activation(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """13C-01B: every Owner and Administrator must hold a passkey; this is derived, so it cannot be suppressed from the client."""
    w = world
    use_claim(monkeypatch, claim())
    accept(w)
    admin = w.person("ADMIN", passkey=False)
    r = get(w)
    item = {i["key"]: i for i in r["required"]}["privileged_account"]
    assert (
        item["status"] == "Needs action"
        and "1 privileged account(s)" in item["reason"]
        and r["can_activate"] is False
    )
    assert code(activate(w)) == "ACTIVATION_BLOCKED"
    give_passkey(w.rt_url, w.o.tenant, admin)
    assert get(w)["can_activate"] is True


def test_approver_readiness_is_recommended_and_counted_by_independent_people(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """13C-01C and 01E: no approvers never blocks activation; two independent people are what an A2 change needs."""
    w = world
    use_claim(monkeypatch, claim())
    accept(w)
    solo = get(w)
    assert (
        solo["solo"] is True and solo["can_activate"] is True and solo["approvers"]["done"] is False
    )
    w.person()
    one = get(w)
    assert (
        one["solo"] is False
        and one["approvers"]["done"] is False
        and "two independent approvers" in one["approvers"]["reason"]
    )
    w.person()
    assert get(w)["approvers"]["done"] is True and get(w)["can_activate"] is True


def test_a_new_policy_version_asks_again_and_limits_an_active_organization(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AT-09 for setup: when the controls text changes, acceptance is Required again; an active organization is limited until accepted."""
    w = world
    use_claim(monkeypatch, claim())
    accept(w)
    assert activate(w).json()["readiness"] == "ACTIVE"
    real = svc.current_versions()
    monkeypatch.setattr(
        svc, "current_versions", lambda: {**real, "ORGANIZATIONAL_CONTROLS": "2027-01-01"}
    )
    r = get(w)
    assert (
        r["readiness"] == "ACTIVE_LIMITED"
        and {i["key"]: i["status"] for i in r["required"]}["controls"] == "Required"
    )
    assert accept(w, region=False) is None
    assert get(w)["readiness"] == "ACTIVE"


def test_no_other_organization_can_read_or_change_the_setup(world: World, rt_url: str) -> None:  # noqa: F811
    """AT-10 for this screen: another organization's id is 'no such organization', for reads and for writes."""
    from fastapi.testclient import TestClient
    from onboarding_fixture import onboard
    from test_step_up_pg import app_for

    w = world
    other = onboard(rt_url)
    with TestClient(app_for(rt_url, other, "shadow"), base_url="https://id.zoiko.test") as foreign:
        h = other.bearer(other.owner)
        for path, method in (
            ("onboarding", "GET"),
            ("onboarding/acknowledgments", "POST"),
            ("../activation", "POST"),
        ):
            url = (
                f"/v1/organizations/{w.o.tenant}/{path}"
                if "../" not in path
                else f"/v1/organizations/{w.o.tenant}/activation"
            )
            r = foreign.request(
                method,
                url,
                headers=h,
                json={"kind": "REGION_POLICY", "version": "x"} if method == "POST" else None,
            )
            assert r.status_code == 404, (path, r.status_code)
        assert (
            foreign.get(f"/v1/organizations/{other.tenant}/onboarding", headers=h).status_code
            == 200
        )
    stranger = w.person(None, passkey=False)  # an account with no role here
    assert (
        w.http.get(f"/v1/organizations/{w.o.tenant}/onboarding", headers=w.h(stranger)).status_code
        == 404
    )

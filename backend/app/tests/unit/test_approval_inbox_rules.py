"""The rules behind the approvals inbox (ZID-UX-WF-013C; slice L-13): the forms for "New request" cannot drift from the approval registry, and
the readiness policy cannot name an action or a state that does not exist. A bad file stops the process, so a typo never quietly removes a
protection."""

from __future__ import annotations

from typing import Any

import pytest
from zid_platform.modules.tenant.infrastructure import approvals as approvals_store
from zid_platform.modules.tenant.infrastructure.approval_forms import FormsError, request_forms
from zid_platform.modules.tenant.infrastructure.approval_forms import parse as parse_forms
from zid_platform.modules.tenant.infrastructure.readiness_policy import (
    ReadinessPolicyError,
    readiness_policy,
)
from zid_platform.modules.tenant.infrastructure.readiness_policy import (
    parse as parse_policy,
)
from zid_platform.modules.tenant.infrastructure.reminder_policy import reminder_policy

REGISTRY = approvals_store.registry()


def forms_doc(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "action_id": "ownership-transfer-or-replacement",
        "label": "Add an Owner",
        "summary": "x",
        "target": {"from": "principal_id"},
        "fields": [{"key": "principal_id", "label": "Who", "kind": "member", "required": True}],
    }
    return {**base, **over}


def test_the_shipped_forms_cover_every_action_a_customer_can_request() -> None:
    forms = request_forms()  # loading checks it all against the registry
    assert {f.action_id for f in forms} >= {
        "ownership-transfer-or-replacement",
        "privileged-role-grant-or-elevation",
        "reinstate-suspended-tenant-access",
    }
    assert all(f.tier in ("A1", "A2") for f in forms)


def test_a_form_names_its_target_from_what_was_typed() -> None:
    by = {f.action_id: f for f in request_forms()}
    assert by["ownership-transfer-or-replacement"].target_for({"principal_id": "p-1"}) == "p-1"
    assert by["privileged-role-grant-or-elevation"].target_for({"role": "ADMIN"}) == "role:ADMIN"
    assert (
        by["reinstate-suspended-tenant-access"].target_for({"reason_code": "x"}) == "organization"
    )


@pytest.mark.parametrize(
    "doc, why",
    [
        ({"forms": [forms_doc(action_id="no-such-action")]}, "not an action"),
        (
            {"forms": [forms_doc(action_id="root-super-admin-or-global-break-glass-grant")]},
            "cannot request",
        ),  # a platform action
        (
            {"forms": [forms_doc(action_id="update-organization-presentation-details")]},
            "cannot request",
        ),  # a routine one
        ({"forms": [forms_doc(), forms_doc()]}, "two forms"),
        (
            {
                "forms": [
                    forms_doc(fields=[{"key": "principal_id", "label": "Who", "kind": "wizard"}])
                ]
            },
            "unknown field kind",
        ),
        (
            {
                "forms": [
                    forms_doc(
                        fields=[{"key": "role", "label": "R", "kind": "select"}],
                        target={"from": "role"},
                    )
                ]
            },
            "needs options",
        ),
        ({"forms": [forms_doc(target={"from": "elsewhere"})]}, "not on the form"),
        ({"forms": [forms_doc(target={})]}, "literal or a field"),
        ({"forms": [forms_doc(fields=[], target={"literal": "organization"})]}, "asks for nothing"),
        ({"forms": [forms_doc()], "handled_elsewhere": {"no-such-action": "x"}}, "not an action"),
        (
            {"forms": [forms_doc()]},
            "no form, and no reason",
        ),  # every other requestable action is left without a way to ask
    ],
)
def test_a_forms_file_that_drifts_from_the_registry_does_not_load(
    doc: dict[str, Any], why: str
) -> None:
    with pytest.raises(FormsError, match=why):
        parse_forms(doc, REGISTRY)


def test_the_readiness_policy_loads_and_a_restricted_organization_keeps_the_ways_back() -> None:
    policy = readiness_policy()
    assert policy.enforcement == "REPORT_ONLY"  # until the owners rule
    # restricted: nothing widens or moves authority, but recovery, adjudication, more independence and removing authority stay open
    for allowed in (
        "privileged-owner-or-admin-recovery",
        "manual-reversal-of-high-assurance-trust-decision",
        "appoint-independent-reviewer",
        "removal-of-last-owner-or-control-chain",
    ):
        assert not policy.refuses("RESTRICTED", allowed)
    for refused in (
        "ownership-transfer-or-replacement",
        "privileged-role-grant-or-elevation",
        "invite-nonprivileged-team-member",
        "powerful-agent-or-integration-authority",
    ):
        assert policy.refuses("RESTRICTED", refused)
    # limited: no new privilege, and everything else goes on
    assert policy.refuses("ACTIVE_LIMITED", "privileged-role-grant-or-elevation")
    assert not policy.refuses("ACTIVE_LIMITED", "invite-nonprivileged-team-member")
    # a state nobody listed refuses nothing, and an unknown one never refuses by accident
    for state in ("DRAFT", "LIMITED_SETUP", "READY_FOR_ACTIVATION", "ACTIVE", "SOMETHING_NEW"):
        assert not policy.refuses(state, "ownership-transfer-or-replacement")


@pytest.mark.parametrize(
    "doc, why",
    [
        ({"enforcement": "ENFORCE", "states": {}}, "enforcement"),
        ({"states": {"SLEEPING": {"refuses": []}}}, "not a readiness state"),
        ({"states": {"RESTRICTED": {"refuses": ["no-such-action"]}}}, "not actions"),
        ({"states": {"RESTRICTED": {"refuses": ["a"], "refuses_all_except": ["b"]}}}, "together"),
    ],
)
def test_a_readiness_policy_with_a_typo_does_not_load(doc: dict[str, Any], why: str) -> None:
    with pytest.raises(ReadinessPolicyError, match=why):
        parse_policy(doc, REGISTRY)


def test_the_reminder_policy_is_one_reminder_a_day_ahead() -> None:
    p = reminder_policy()
    assert p.enabled and p.windows_hours == (24,)


def test_a_reminder_and_an_expiry_each_have_an_alert_that_names_nobody() -> None:
    from zid_platform.modules.notification.domain.alerts import TEMPLATES

    reminder, expired = (
        TEMPLATES["governance.approval.reminder_due"],
        TEMPLATES["governance.approval.expired"],
    )
    assert (
        reminder.task and not expired.task
    )  # one asks someone to act in time; the other says it lapsed and nothing ran
    for t in (reminder, expired):
        assert t.href == "/admin/approvals" and "@" not in t.title + t.action

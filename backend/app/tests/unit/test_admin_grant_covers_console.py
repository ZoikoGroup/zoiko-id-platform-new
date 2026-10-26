"""The owner's seeded grant covers the company console, so no page is 'not connected' by a missing permission.

A page that reads or changes the company's own data names an action; if the owner's grant lacks it, the page answers
403 and looks broken. This keeps the grant and the pages in step, and keeps business authority out of the grant.
"""

from __future__ import annotations

from zid_platform.modules.onboarding.domain.gates import TENANT_ADMIN_ACTIONS
from zid_platform.protected import catalog

# What the Company and Account pages in the console call (billing, subscription, usage, settings, alerts, contacts,
# developers) and the administration pages that only read.
CONSOLE_READS = {
    "commercial.read",  # billing, subscription, usage detail, invoices, budgets
    "metering.read",  # usage and limits
    "configuration.read",  # settings, cryptography overview
    "notification.alert.read",  # alerts and the shell's bell
    "communication.read",  # email contacts
    "webhook.endpoint.read",  # developers
    "credential.read",  # credentials and trust (read only)
    "machine.agent.read",  # agents and workloads (read only)
    "mandate.read",  # mandates (read only)
}
CONSOLE_CHANGES = {
    "commercial.plan.request",  # ask for a different plan (billing and subscription pages)
    "configuration.manage",
    "commercial.budget.manage",
    "communication.contact.manage",
    "notification.alert.manage",
    "webhook.endpoint.manage",
}
# Acts that commit the company to something stay out of the administration grant.
BUSINESS_AUTHORITY = {
    "mandate.create",
    "credential.issue",
    "credential.status.change",
    "credential.trust.manage",
    "machine.agent.manage",
    "machine.agent.activate",
    "payment.release",
    "purchase.create",
    "authority.grant",
    "authority.grant.delegate",
    "evidence.export",
}


def test_every_console_page_action_is_in_the_owner_grant() -> None:
    missing = (CONSOLE_READS | CONSOLE_CHANGES) - set(TENANT_ADMIN_ACTIONS)
    assert not missing, f"pages would answer 403 for the owner: {sorted(missing)}"


def test_every_granted_action_is_a_real_catalogued_action() -> None:
    unknown = set(TENANT_ADMIN_ACTIONS) - set(catalog())
    assert not unknown, f"not in the action catalogue: {sorted(unknown)}"


def test_the_administration_grant_never_carries_business_authority() -> None:
    assert not (BUSINESS_AUTHORITY & set(TENANT_ADMIN_ACTIONS))


def test_no_action_is_listed_twice() -> None:
    assert len(TENANT_ADMIN_ACTIONS) == len(set(TENANT_ADMIN_ACTIONS))

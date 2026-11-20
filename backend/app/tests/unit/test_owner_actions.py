"""CR-137: what a company's owner is granted by default."""

from __future__ import annotations

from zid_platform.modules.onboarding.domain.gates import STAFF_ONLY_ACTIONS, TENANT_ADMIN_ACTIONS, owner_actions
from zid_platform.protected import catalog


def test_the_owner_holds_every_customer_side_action_and_nothing_that_belongs_to_zoiko_staff() -> None:
    c = catalog()
    got = set(owner_actions(c))
    assert {"authority.grant", "authority.grant.delegate"} <= got  # so the owner can issue and delegate grants
    assert all(a in got for a in TENANT_ADMIN_ACTIONS if a in c), "the standing administrator list is never taken away"
    assert not (got & STAFF_ONLY_ACTIONS)
    assert not any(a.startswith(("assurance.", "policy.release.")) for a in got)
    assert got <= set(c) and len(got) > len([a for a in TENANT_ADMIN_ACTIONS if a in c])


def test_the_same_catalogue_gives_the_same_grant() -> None:
    c = catalog()
    assert owner_actions(c) == owner_actions(dict(reversed(list(c.items())))) == tuple(sorted(owner_actions(c)))

"""CR-137: what a company's owner is granted by default."""

from __future__ import annotations

from zid_platform.modules.onboarding.domain.gates import TENANT_ADMIN_ACTIONS, owner_actions
from zid_platform.protected import catalog


def test_the_owner_holds_every_action_in_the_catalogue() -> None:
    c = catalog()
    got = set(owner_actions(c))
    assert got == set(c)
    assert {"authority.grant", "authority.grant.delegate", "organization.kyb.review", "operator.jit.elevate"} <= got
    assert all(a in got for a in TENANT_ADMIN_ACTIONS if a in c), "the standing administrator list is a subset"


def test_the_same_catalogue_gives_the_same_grant() -> None:
    c = catalog()
    assert owner_actions(c) == owner_actions(dict(reversed(list(c.items())))) == tuple(sorted(owner_actions(c)))

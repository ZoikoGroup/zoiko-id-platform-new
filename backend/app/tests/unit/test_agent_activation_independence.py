"""ZID-AG-WF-002 section 04 (four-eyes): the people who answer for an agent cannot be the ones who switch it on (CR-142)."""

from __future__ import annotations

import pytest
from zid_platform.modules.machine.domain.agents import (
    AgentProfile,
    AgentStatus,
    activator_blockers,
    attestor_blockers,
)


def agent(**over: object) -> AgentProfile:
    base: dict[str, object] = {
        "agent_principal_id": "a1",
        "tenant_id": "t1",
        "agent_name": "bot",
        "blueprint_id": "bp",
        "blueprint_version": "1",
        "publisher_principal_id": "pub",
        "operator_principal_id": "op",
        "sponsor_principal_id": "sp",
        "owning_organization_id": "org",
        "model_provider": "",
        "allowed_tools": frozenset({"t"}),
        "max_subagent_depth": 0,
        "status": AgentStatus.PENDING_APPROVAL,
    }
    return AgentProfile(**{**base, **over})  # type: ignore[arg-type]


@pytest.mark.parametrize("who", ["sp", "op", "pub"])
def test_the_sponsor_the_operator_and_the_publisher_cannot_activate_their_own_agent(
    who: str,
) -> None:
    assert activator_blockers(who, agent()) == ["AGENT_REVIEW_NOT_INDEPENDENT"]


def test_the_person_who_registered_the_agent_cannot_activate_or_review_it_even_if_they_named_others() -> None:
    """The registrant may have named three other people as sponsor, operator and publisher and still built the agent themselves."""
    a = agent(registered_by="builder")
    assert activator_blockers("builder", a) == ["AGENT_REVIEW_NOT_INDEPENDENT"]
    assert attestor_blockers("SECURITY_REVIEW", "builder", a) == ["AGENT_REVIEW_NOT_INDEPENDENT"]


def test_an_unknown_registrant_bars_nobody() -> None:
    """An agent from before the column has no registrant on its record. None must never match a real actor."""
    a = agent(registered_by=None)
    assert activator_blockers("someone-else", a) == []
    assert attestor_blockers("SECURITY_REVIEW", "someone-else", a) == []


def test_a_person_outside_the_chain_of_accountability_may_activate() -> None:
    assert activator_blockers("someone-else", agent()) == []


def test_it_is_the_same_set_of_people_the_security_review_already_bars() -> None:
    """One rule for the chain: whoever cannot attest the review cannot activate either, so neither step can be done alone."""
    a = agent(registered_by="builder")
    for who in ("sp", "op", "pub", "builder", "someone-else"):
        barred_review = bool(attestor_blockers("SECURITY_REVIEW", who, a))
        barred_activation = bool(activator_blockers(who, a))
        assert barred_review == barred_activation


def test_an_agent_with_no_sponsor_does_not_let_an_anonymous_actor_through() -> None:
    """A missing sponsor is never 'equal' to a real actor, and activation still needs a sponsor elsewhere (AGENT_SPONSOR_REQUIRED)."""
    assert activator_blockers("someone-else", agent(sponsor_principal_id=None)) == []

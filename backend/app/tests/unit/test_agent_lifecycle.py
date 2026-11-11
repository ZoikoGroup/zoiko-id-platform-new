"""WP-10 (ZID-GAP-001): an agent whose sponsor is gone, or whose expiry has passed, must stop acting.

Pure domain: `lifecycle_breach` decides, and it fails closed (a sponsor we cannot confirm active counts as gone).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.machine.domain.agents import (
    AGENT_EXPIRED,
    SPONSOR_GONE,
    AgentProfile,
    AgentStatus,
    lifecycle_breach,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


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
        "status": AgentStatus.ACTIVE,
    }
    return AgentProfile(**{**base, **over})  # type: ignore[arg-type]


def test_healthy_agent_is_left_alone() -> None:
    assert lifecycle_breach(agent(), sponsor_active=True, now=NOW) is None


def test_sponsor_who_left_suspends_the_agent() -> None:
    assert lifecycle_breach(agent(), sponsor_active=False, now=NOW) == SPONSOR_GONE


def test_an_agent_with_no_sponsor_at_all_is_orphaned() -> None:
    assert (
        lifecycle_breach(agent(sponsor_principal_id=None), sponsor_active=True, now=NOW)
        == SPONSOR_GONE
    )


def test_expiry_suspends_the_agent_at_the_instant() -> None:
    assert (
        lifecycle_breach(agent(expires_at=NOW - timedelta(seconds=1)), sponsor_active=True, now=NOW)
        == AGENT_EXPIRED
    )
    assert lifecycle_breach(agent(expires_at=NOW), sponsor_active=True, now=NOW) == AGENT_EXPIRED
    assert (
        lifecycle_breach(agent(expires_at=NOW + timedelta(seconds=1)), sponsor_active=True, now=NOW)
        is None
    )


def test_a_missing_sponsor_wins_over_expiry_in_the_reason() -> None:
    both = agent(expires_at=NOW - timedelta(days=1))
    assert lifecycle_breach(both, sponsor_active=False, now=NOW) == SPONSOR_GONE


def test_only_active_agents_are_reviewed() -> None:
    for status in (
        AgentStatus.DRAFT,
        AgentStatus.PENDING_APPROVAL,
        AgentStatus.SUSPENDED,
        AgentStatus.REVOKED,
        AgentStatus.RETIRED,
    ):
        assert lifecycle_breach(agent(status=status), sponsor_active=False, now=NOW) is None

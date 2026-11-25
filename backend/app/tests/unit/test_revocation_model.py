"""Stopping agents by scope (ZID-CP-WF07 v2.0 .10; CR-169): the shape of a stop, what it says about where it holds, and who may close it or reinstate."""

from __future__ import annotations

from typing import Any

import pytest
from zid_platform.modules.machine.domain import revocation as rv
from zid_platform.modules.machine.domain.agents import MachineError


def code(fn: Any, *a: Any, **k: Any) -> str:
    with pytest.raises(MachineError) as e:
        fn(*a, **k)
    return e.value.code


def test_every_scope_names_exactly_what_it_stops() -> None:
    assert rv.check_shape("TENANT", None) == ("TENANT", None, None)
    assert rv.check_shape("AGENT", " a1 ") == ("AGENT", "a1", None)
    assert rv.check_shape("BLUEPRINT", "daara") == ("BLUEPRINT", "daara", None)
    assert rv.check_shape("BLUEPRINT", "daara@2") == ("BLUEPRINT", "daara", "2")
    for scope, subject in (("TENANT", "x"), ("AGENT", None), ("AGENT", " "), ("SPONSOR", ""), ("BLUEPRINT", "@2"), ("BLUEPRINT", "a@"), ("GLOBAL", None), ("AGENT", "x" * 201)):
        assert code(rv.check_shape, scope, subject) == "REVOCATION_INVALID", (scope, subject)
    assert rv.clean_reason("  Sponsor   left. ") == "Sponsor left." and code(rv.clean_reason, "x") == "REVOCATION_INVALID" and code(rv.clean_reason, "x" * 301) == "REVOCATION_INVALID"


def test_a_stop_is_never_called_complete_while_some_place_is_not_told() -> None:
    plain = rv.enforcement_report([], 4)
    assert plain["enforced_everywhere"] is True and plain["epoch"] == 4 and [a["point"] for a in plain["acknowledged"]] == ["tool_gateway"] and plain["unsupported"] == []
    with_servers = rv.enforcement_report(["srv-b", "srv-a", "srv-a"], 5)
    assert with_servers["enforced_everywhere"] is False  # a tool server is not told to stop work it began
    assert [u["point"] for u in with_servers["unsupported"]] == ["tool_server:srv-a", "tool_server:srv-b"] and all("not told" in u["reason"] for u in with_servers["unsupported"])
    assert with_servers["pending"] == [] and with_servers["failed"] == []


def test_a_stop_is_closed_once_by_someone_else_and_its_maker_never_reinstates() -> None:
    ev = {"actor": "maker", "closed_at": None}
    assert rv.closing_problems(actor="other", event=ev) == []
    assert rv.closing_problems(actor="maker", event=ev) == ["REVOCATION_CLOSE_NOT_INDEPENDENT"]
    assert rv.closing_problems(actor="other", event={**ev, "closed_at": 1}) == ["REVOCATION_ALREADY_CLOSED"]
    assert rv.reinstate_problems(actor="other", open_events=[], initiators=["maker"]) == []
    assert rv.reinstate_problems(actor="other", open_events=[ev], initiators=["maker"]) == ["REVOCATION_OPEN"]
    assert rv.reinstate_problems(actor="maker", open_events=[], initiators=["maker"]) == ["REVOCATION_REINSTATE_NOT_INDEPENDENT"]
    assert rv.reinstate_problems(actor="maker", open_events=[ev], initiators=["maker"]) == ["REVOCATION_OPEN", "REVOCATION_REINSTATE_NOT_INDEPENDENT"]

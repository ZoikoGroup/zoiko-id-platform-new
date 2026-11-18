"""A company's verification changes reach customers as webhooks (WF02 K08, K10; acceptance T26): the existing delivery is signed, idempotent, versioned and tenant-scoped; these tests pin that the two
events are subscribable and carry no company data."""

from __future__ import annotations

import re
from pathlib import Path

from zid_platform.modules.webhook.domain.webhooks import SUBSCRIBABLE, check_event_types

ISSUED = "com.zoiko.id.organization.kyb.assertion_issued.v1"
REVOKED = "com.zoiko.id.organization.kyb.assertion_revoked.v1"
EVENTS = Path(__file__).resolve().parents[4] / "contracts" / "asyncapi" / "proposed-events.yaml"


def test_the_two_company_grade_events_are_subscribable_and_versioned() -> None:
    assert ISSUED in SUBSCRIBABLE and REVOKED in SUBSCRIBABLE
    assert check_event_types([ISSUED, REVOKED]) == [ISSUED, REVOKED]
    assert all(re.fullmatch(r"com\.zoiko\.id\.[a-z_.]+\.v\d+", t) for t in (ISSUED, REVOKED))


def test_an_unlisted_internal_event_is_still_refused() -> None:
    import pytest

    with pytest.raises(Exception, match="(?i)event|type|subscri"):
        check_event_types(
            ["com.zoiko.id.organization.kyb.policy_activated.v1"]
        )  # operator-side governance, never delivered to a customer


def test_the_events_are_declared_and_say_they_carry_no_company_data() -> None:
    text = EVENTS.read_text(encoding="utf-8")
    for name in ("organization.kyb.assertion_issued", "organization.kyb.assertion_revoked"):
        block = text[text.index(f"  {name}:") : text.index(f"  {name}:") + 900]
        assert "no company data" in block, name

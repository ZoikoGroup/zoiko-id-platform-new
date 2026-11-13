"""ZID-RCG-003 T45: an outage/incident notification deadline simulation: routing, legal escalation, the clock and the evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.operations.domain import notifications as nt
from zid_platform.modules.operations.domain.incidents import Severity

AWARE = datetime(2026, 11, 13, 8, tzinfo=UTC)
RULES = nt.parse(
    {
        "rules": [
            {
                "rule_id": "uk-gdpr-art33",
                "recipient": "SUPERVISORY_AUTHORITY",
                "deadline_hours": 72,
                "legal_source": "UK GDPR Art. 33",
                "personal_data": True,
                "countries": ["GB"],
            },
            {
                "rule_id": "customer-sla-outage",
                "recipient": "AFFECTED_CUSTOMERS",
                "deadline_hours": 4,
                "legal_source": "Customer agreement, incident notice clause",
                "min_severity": "SEV1",
                "escalate_at": 0.25,
                "escalate_to": "CUSTOMER_SUCCESS_LEAD",
            },
            {
                "rule_id": "trust-service-breach",
                "recipient": "TRUST_SERVICE_REGULATOR",
                "deadline_hours": 24,
                "legal_source": "eIDAS Art. 19(2)",
                "security": True,
            },
        ]
    }
)


def _incident(**kw: object) -> nt.Incident:
    base: dict[str, object] = {
        "incident_id": "inc-1",
        "severity": Severity.SEV1,
        "security": False,
        "personal_data": True,
        "countries": frozenset({"GB"}),
        "aware_at": AWARE,
    }
    return nt.Incident(**(base | kw))  # type: ignore[arg-type]


def test_t45_routing_only_the_rules_the_incident_meets() -> None:
    got = {o.rule_id: o for o in nt.obligations(_incident(), RULES, [], now=AWARE)}
    assert set(got) == {
        "uk-gdpr-art33",
        "customer-sla-outage",
    }  # not a security incident: no trust-service report
    assert got["uk-gdpr-art33"].recipient == "SUPERVISORY_AUTHORITY"
    assert got["uk-gdpr-art33"].due_at == AWARE + timedelta(hours=72)
    minor = nt.obligations(
        _incident(severity=Severity.SEV3, personal_data=False), RULES, [], now=AWARE
    )
    assert minor == []  # nothing is inferred


def test_t45_the_clock_escalates_to_legal_then_goes_overdue() -> None:
    def state(hours: float) -> nt.State:
        o = {
            x.rule_id: x
            for x in nt.obligations(_incident(), RULES, [], now=AWARE + timedelta(hours=hours))
        }
        return o["uk-gdpr-art33"].state

    assert state(1) is nt.State.DUE
    assert state(36) is nt.State.ESCALATED  # half the window: Legal is called in
    assert state(73) is nt.State.OVERDUE
    early = {
        x.rule_id: x
        for x in nt.obligations(_incident(), RULES, [], now=AWARE + timedelta(hours=1, minutes=1))
    }
    assert (
        early["customer-sla-outage"].state is nt.State.ESCALATED
    )  # its own escalation share and escalation owner
    assert early["customer-sla-outage"].escalate_to == "CUSTOMER_SUCCESS_LEAD"


def test_t45_met_needs_evidence_and_a_send_inside_the_window() -> None:
    on_time = nt.Sent("uk-gdpr-art33", AWARE + timedelta(hours=70), "ICO-REF-123")
    late = nt.Sent("customer-sla-outage", AWARE + timedelta(hours=5), "MAIL-77")
    no_proof = nt.Sent("trust-service-breach", AWARE + timedelta(hours=2), "  ")
    got = {
        o.rule_id: o
        for o in nt.obligations(
            _incident(security=True),
            RULES,
            [on_time, late, no_proof],
            now=AWARE + timedelta(hours=80),
        )
    }
    assert (
        got["uk-gdpr-art33"].state is nt.State.MET
        and got["uk-gdpr-art33"].evidence_ref == "ICO-REF-123"
    )
    assert got["customer-sla-outage"].state is nt.State.LATE
    assert (
        got["trust-service-breach"].state is nt.State.OVERDUE
    )  # a send without evidence is not a send


@pytest.mark.parametrize(
    "rule",
    [
        {"rule_id": "x", "recipient": "R", "deadline_hours": 0, "legal_source": "L"},
        {"rule_id": "x", "recipient": "R", "deadline_hours": 24, "legal_source": ""},
        {
            "rule_id": "x",
            "recipient": "R",
            "deadline_hours": 24,
            "legal_source": "L",
            "escalate_at": 1.5,
        },
    ],
)
def test_a_rule_that_cannot_be_applied_honestly_is_refused(rule: dict[str, object]) -> None:
    with pytest.raises(nt.NotificationError, match="NOTIFICATION_RULE_INVALID"):
        nt.parse({"rules": [rule]})

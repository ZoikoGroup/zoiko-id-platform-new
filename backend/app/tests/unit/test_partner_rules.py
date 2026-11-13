"""Partner channel rules (ZID-COM-WF-2026-11 WF-I06; INV-09; CAT-34, CAT-35): a partner acts only for a permitted child, in its territory, in its term, with the child's
consent on file, and every operation has exactly one bill-to."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.commercial.domain import partner as p

NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)


def partner(**over: object) -> p.Partner:
    base: dict[str, object] = {
        "partner_id": "p1", "parent_tenant_id": "T-PARTNER", "bill_to": "PARTNER", "territory": ("DE", "FR"), "status": "ACTIVE",
        "effective_from": NOW - timedelta(days=30), "effective_until": NOW + timedelta(days=30),
    }  # fmt: skip
    return p.Partner(**{**base, **over})  # type: ignore[arg-type]


def child(**over: object) -> p.Child:
    base: dict[str, object] = {
        "child_tenant_id": "T-CHILD",
        "revoked": False,
        "downstream_consent_ref": "consent:2026-10-01",
    }
    return p.Child(**{**base, **over})  # type: ignore[arg-type]


def test_a_partner_acts_for_a_permitted_child_in_its_territory_and_term() -> None:
    assert p.refusals(partner(), child(), country="DE", now=NOW) == []
    p.check(partner(), child(), country="FR", now=NOW)


@pytest.mark.parametrize(
    ("pt", "ch", "country", "expect"),
    [
        (None, child(), "DE", "PARTNER_UNKNOWN"),
        (partner(status="SUSPENDED"), child(), "DE", "PARTNER_NOT_ACTIVE"),
        (partner(status="TERMINATED"), child(), "DE", "PARTNER_NOT_ACTIVE"),
        (partner(effective_until=NOW), child(), "DE", "PARTNER_OUTSIDE_TERM"),
        (partner(effective_from=NOW + timedelta(seconds=1)), child(), "DE", "PARTNER_OUTSIDE_TERM"),
        (partner(effective_from=None), child(), "DE", "PARTNER_OUTSIDE_TERM"),
        (partner(), None, "DE", "CHILD_NOT_PERMITTED"),
        (partner(), child(revoked=True), "DE", "CHILD_NOT_PERMITTED"),
        (partner(), child(downstream_consent_ref=None), "DE", "DOWNSTREAM_CONSENT_MISSING"),
        (partner(), child(downstream_consent_ref="  "), "DE", "DOWNSTREAM_CONSENT_MISSING"),
        (partner(), child(), "US", "CROSS_TERRITORY"),
        (partner(), child(), None, "COUNTRY_NOT_STATED"),
    ],
)
def test_every_unknown_or_outside_fact_is_a_refusal(pt, ch, country, expect) -> None:
    assert expect in p.refusals(pt, ch, country=country, now=NOW)
    with pytest.raises(p.PartnerError) as e:
        p.check(pt, ch, country=country, now=NOW)
    assert e.value.code in p.refusals(pt, ch, country=country, now=NOW)


def test_every_reason_is_named_not_only_the_first() -> None:
    reasons = p.refusals(
        partner(status="SUSPENDED"), child(downstream_consent_ref=None), country="US", now=NOW
    )
    assert {"PARTNER_NOT_ACTIVE", "DOWNSTREAM_CONSENT_MISSING", "CROSS_TERRITORY"} <= set(reasons)


def test_there_is_exactly_one_bill_to_by_the_agreement() -> None:
    assert p.legal_payer(partner(bill_to="PARTNER"), "T-CHILD") == "T-PARTNER"
    assert p.legal_payer(partner(bill_to="CHILD"), "T-CHILD") == "T-CHILD"
    with pytest.raises(p.PartnerError):
        p.legal_payer(partner(bill_to="BOTH"), "T-CHILD")


def ev(key: str, payer: str | None, tenant: str = "T-CHILD") -> dict[str, object]:
    return {"deduplication_key": key, "tenant_id": tenant, "legal_payer_id": payer}


def test_parent_and_child_usage_reconciles_once() -> None:
    ok = p.reconcile(
        [ev("a", "T-PARTNER"), ev("b", "T-PARTNER"), ev("a", "T-PARTNER", tenant="T-OTHER")],
        partner(),
    )
    assert ok == {"events": 3, "duplicates": [], "wrong_or_missing_payer": [], "reconciles": True}


def test_a_duplicate_or_wrongly_billed_event_is_found() -> None:
    dup = p.reconcile([ev("a", "T-PARTNER"), ev("a", "T-PARTNER")], partner())
    assert dup["duplicates"] == ["a"] and dup["reconciles"] is False
    wrong = p.reconcile([ev("a", "T-CHILD"), ev("b", None)], partner())
    assert wrong["wrong_or_missing_payer"] == ["a", "b"] and wrong["reconciles"] is False
    by_child = p.reconcile([ev("a", "T-CHILD")], partner(bill_to="CHILD"))
    assert by_child["reconciles"] is True

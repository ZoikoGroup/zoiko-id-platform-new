"""OpenID IDA `verified_claims` projection (ZID-VERIFY-001; VERIFY-25)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from zid_platform.modules.verification.domain.ida import project

NOW = datetime(2026, 10, 26, tzinfo=UTC)


def assertion(claim: str = "given_name", **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "assertion_id": uuid.uuid4(),
        "claim_type": claim,
        "status": "VALID",
        "assurance_level": "IAL-Z2",
        "performed_at": NOW - timedelta(days=1),
        "valid_until": NOW + timedelta(days=30),
        "evidence_types": ["DIGITAL_CREDENTIAL"],
        "purposes": ["onboarding"],
        "verification_method": "PROVIDER-ACME",  # internal: must never leave
        "jurisdiction": "GB",
    }
    return {**base, **over}


def test_only_requested_valid_unexpired_claims_for_the_purpose_are_returned() -> None:
    """VERIFY-25: anything not asked for, not valid, expired or not relied on for this purpose is left out."""
    rows = [
        assertion("given_name"),
        assertion("family_name"),  # valid but not requested
        assertion("given_name", status="REVOKED"),
        assertion("given_name", valid_until=NOW - timedelta(seconds=1)),
        assertion("given_name", purposes=["recovery"]),  # profile does not list this purpose
        assertion("given_name", assurance_level="IAL-Z0"),
    ]
    out = project(rows, purpose="onboarding", requested={"given_name"}, now=NOW)
    assert len(out) == 1
    assert out[0]["claims"] == {"given_name": True}


def test_each_entry_maps_to_a_recorded_assertion_and_exposes_no_internals() -> None:
    """VERIFY-25: the entry carries the internal assertion id; provider, method and jurisdiction never appear."""
    a = assertion("given_name")
    [entry] = project([a], purpose="onboarding", requested=["given_name"], now=NOW)
    v = entry["verification"]
    assert v["verification_process"] == str(a["assertion_id"])
    assert v["trust_framework"] == "zoiko_ial" and v["assurance_level"] == "IAL-Z2"
    assert v["evidence"] == [{"type": "DIGITAL_CREDENTIAL"}]
    rendered = str(entry)
    assert "PROVIDER-ACME" not in rendered and "GB" not in rendered


def test_minimum_level_filters_weaker_assertions() -> None:
    rows = [assertion("given_name", assurance_level="IAL-Z1")]
    assert (
        project(rows, purpose="onboarding", requested={"given_name"}, now=NOW, min_level="IAL-Z2")
        == []
    )


def test_no_request_means_nothing_is_returned() -> None:
    assert project([assertion()], purpose="onboarding", requested=set(), now=NOW) == []

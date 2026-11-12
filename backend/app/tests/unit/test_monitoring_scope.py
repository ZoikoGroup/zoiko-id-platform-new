"""Sector, transaction and crypto monitoring scope (ZID-RCG-003 RCG-G12; CR-081): no global background monitoring by default."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.screening.application import monitoring_gate as gate
from zid_platform.modules.screening.domain import monitoring_scope as ms

NOW = datetime(2027, 3, 1, 12, tzinfo=UTC)


def approval(**over: Any) -> ms.MonitoringApproval:
    a = ms.MonitoringApproval(
        approval_id="gb-sanctions",
        version=1,
        country="GB",
        kind=ms.Kind.SANCTIONS_ONGOING,
        sectors=frozenset({"ANY"}),
        legal_basis_ref="LEGAL-MLR2017",
        contract_ref="CONTRACT-2027",
        valid_from=NOW - timedelta(days=10),
    )
    a = replace(a, **over)
    if "approvals" not in over:
        digest = ms.approval_hash(a)
        a = replace(a, approvals={r: ms.Approval(f"REF-{r}", digest) for r in ms.APPROVER_ROLES})
    return a


def decide(approvals: list[ms.MonitoringApproval], **over: Any) -> ms.MonitoringDecision:
    args: dict[str, Any] = {
        "country": "GB",
        "kind": ms.Kind.SANCTIONS_ONGOING,
        "sector": "ANY",
        "at": NOW,
    } | over
    return ms.decide(approvals, **args)


def test_an_approved_kind_country_and_sector_may_be_monitored() -> None:
    d = decide([approval()])
    assert d.allowed and d.approval_id == "gb-sanctions"


def test_no_approval_is_no_monitoring_never_a_default_yes() -> None:
    assert decide([]).reasons == ("MONITORING_NOT_APPROVED",)
    assert decide([approval()], country="FR").reasons == ("MONITORING_NOT_APPROVED",)
    assert decide([approval()], kind=ms.Kind.PEP).reasons == (
        "MONITORING_NOT_APPROVED",
    )  # an approval for sanctions is not one for PEP


def test_a_sector_must_be_covered_any_must_be_written() -> None:
    fin = approval(sectors=frozenset({"FINANCIAL"}))
    assert decide([fin], sector="FINANCIAL").allowed
    assert decide([fin], sector="MARKETPLACE").reasons == ("SECTOR_NOT_COVERED",)
    assert decide([approval()], sector="MARKETPLACE").allowed  # ANY covers it


def test_legal_and_commercial_entitlement_are_both_needed() -> None:
    assert "LEGAL_BASIS_MISSING" in decide([approval(legal_basis_ref="")]).reasons
    assert "CONTRACT_MISSING" in decide([approval(contract_ref="x")]).reasons


def test_a_crypto_kind_needs_its_own_authorization_and_a_general_approval_never_stretches_to_crypto() -> (
    None
):
    travel = approval(approval_id="gb-travel", kind=ms.Kind.CRYPTO_TRAVEL_RULE)
    assert decide([travel], kind=ms.Kind.CRYPTO_TRAVEL_RULE).reasons == (
        "CRYPTO_AUTHORIZATION_MISSING",
    )
    authorized = approval(
        approval_id="gb-travel",
        kind=ms.Kind.CRYPTO_TRAVEL_RULE,
        crypto_authorization_ref="FCA-CRYPTO-REG-123",
    )
    assert decide([authorized], kind=ms.Kind.CRYPTO_TRAVEL_RULE).allowed
    assert decide([approval()], kind=ms.Kind.CRYPTO_WALLET_SCREENING).reasons == (
        "MONITORING_NOT_APPROVED",
    )


def test_dates_and_both_signatures_of_the_exact_hash_count() -> None:
    assert (
        "APPROVAL_NOT_YET_VALID" in decide([approval(valid_from=NOW + timedelta(days=1))]).reasons
    )
    assert "APPROVAL_EXPIRED" in decide([approval(valid_until=NOW)]).reasons
    a = approval()
    assert (
        "approval_missing:compliance"
        in decide([replace(a, approvals={"legal": a.approvals["legal"]})]).reasons
    )
    widened = replace(a, sectors=frozenset({"ANY", "CRYPTO"}))  # edited after both signed
    assert {"approval_hash_mismatch:legal", "approval_hash_mismatch:compliance"} <= set(
        decide([widened]).reasons
    )


def test_the_newest_usable_version_is_used_and_an_unusable_newer_one_does_not_hide_a_good_older_one() -> (
    None
):
    old = approval()
    new_unsigned = approval(version=2, approvals={})
    d = decide([old, new_unsigned])
    assert d.allowed and d.approval_id == "gb-sanctions"


# ---- the gate


@pytest.fixture
def mode() -> Any:
    before = gate.mode()
    yield gate.configure
    gate.configure(before)


def test_shadow_only_reports_and_enforce_holds(mode: Any) -> None:
    mode("shadow")
    gate.require_monitoring(country="GB", kind=ms.Kind.PEP, at=NOW, approvals=[])  # no error
    mode("enforce")
    with pytest.raises(gate.MonitoringNotApproved) as e:
        gate.require_monitoring(country="GB", kind=ms.Kind.PEP, at=NOW, approvals=[])
    assert e.value.decision.reasons == ("MONITORING_NOT_APPROVED",)
    assert isinstance(e.value, ValueError)  # the re-screen pass holds on ValueError
    gate.require_monitoring(
        country="GB", kind=ms.Kind.SANCTIONS_ONGOING, at=NOW, approvals=[approval()]
    )  # approved: no error
    mode("off")
    gate.require_monitoring(country="GB", kind=ms.Kind.PEP, at=NOW, approvals=[])
    with pytest.raises(ValueError):
        gate.configure("sometimes")


# ---- configuration


def test_parse_and_refuse_a_malformed_file() -> None:
    row = {
        "approval_id": "gb-sanctions",
        "version": 1,
        "country": "gb",
        "kind": "sanctions_ongoing",
        "sectors": ["any"],
        "legal_basis_ref": "LEGAL-1",
        "contract_ref": "CONTRACT-1",
        "valid_from": "2027-01-01T00:00:00+00:00",
    }
    (a,) = ms.parse({"approvals": [row]})
    assert (a.country, a.kind, a.sectors) == ("GB", ms.Kind.SANCTIONS_ONGOING, frozenset({"ANY"}))
    for bad in (
        row | {"country": "Britain"},
        row | {"kind": "EVERYTHING"},
        row | {"sectors": []},
        row | {"valid_from": "2027-01-01T00:00:00"},
        {k: v for k, v in row.items() if k != "valid_from"},
    ):
        with pytest.raises(ms.MonitoringError) as e:
            ms.parse({"approvals": [bad]})
        assert e.value.code == "MONITORING_APPROVALS_INVALID"
    with pytest.raises(ms.MonitoringError):
        ms.parse({"approvals": [row, row]})  # duplicate version


def test_the_shipped_file_lists_no_approval() -> None:
    from zid_platform.modules.screening.infrastructure.monitoring_approvals import (
        monitoring_approvals,
    )

    assert monitoring_approvals() == ()

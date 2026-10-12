"""DEC-B1 risk-class enforcement contract (ZID-REQ-001 §4; ZID-ARCH-002 §11; P0-05)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from zid_platform.modules.decision.domain.risk import (
    CONTRACT,
    EXCEPTION_APPROVERS,
    LOWEST_CLASS_FOR_R3_ACTION,
    MAX_LEVELS_LOWERED,
    EvidenceClass,
    FailurePosture,
    RiskClass,
    RiskContractError,
    WeakeningException,
    effective_risk_class,
    evidence_floor,
    failure_posture,
    revocation_slo,
)

REPO = Path(__file__).resolve().parents[4]
MODULES = REPO / "backend" / "app" / "src" / "zid_platform" / "modules"
PUBLISHED = yaml.safe_load((REPO / "contracts/baseline/risk-classes.yaml").read_text("utf-8"))
CATALOG = yaml.safe_load((REPO / "contracts/action-catalog/actions.yaml").read_text("utf-8"))
NOW = datetime(2026, 10, 12, tzinfo=UTC)


def _exception(lowered_to: RiskClass, **kw: object) -> WeakeningException:
    fields: dict[str, object] = {
        "action": "invoice.approve",
        "tenant_id": "ten_1",
        "lowered_to": lowered_to,
        "approved_by": frozenset({"Product", "Security"}),
        "compensating_control": "second approver in the customer ERP",
        "expires_at": NOW + timedelta(days=30),
        "approval_ref": "EXC-0001",
    } | kw
    return WeakeningException(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------- code == published contract


def test_code_matches_published_contract() -> None:
    for name, spec in PUBLISHED["classes"].items():
        c = CONTRACT[RiskClass[name]]
        assert c.revocation_slo == timedelta(seconds=spec["revocation_slo_seconds"]), name
        cross = spec.get("revocation_slo_cross_party_seconds")
        assert c.revocation_slo_cross_party == (timedelta(seconds=cross) if cross else None), name
        assert c.failure_posture == FailurePosture(spec["failure_posture"]), name
        assert c.evidence_floor == EvidenceClass[spec["evidence_floor"]], name
        assert (c.online_decision, c.mandate_reservation, c.commit_recheck) == (
            spec["online_decision"],
            spec["mandate_reservation"],
            spec["commit_recheck"],
        ), name
    rules = PUBLISHED["weakening_exception"]
    assert set(rules["approvers"]) == EXCEPTION_APPROVERS
    assert rules["max_levels_lowered"] == MAX_LEVELS_LOWERED
    assert RiskClass[rules["lowest_class_for_r3_action"]] == LOWEST_CLASS_FOR_R3_ACTION
    assert "R3 fail-closed" in PUBLISHED["platform_floors"]


def test_revocation_slos_tighten_as_risk_rises() -> None:
    slos = [revocation_slo(r) for r in RiskClass]
    assert slos == sorted(slos, reverse=True)
    assert revocation_slo(RiskClass.R3, cross_party=True) == timedelta(seconds=5)
    assert revocation_slo(RiskClass.R1, cross_party=True) == timedelta(seconds=60)


# ---------------------------------------------------------------- tighten, never silently weaken


def test_callers_and_tenants_can_only_raise_the_floor() -> None:
    f = RiskClass.R1
    kw = {"now": NOW}
    assert effective_risk_class("a", "t", f, requested=RiskClass.R0, **kw) is RiskClass.R1
    assert effective_risk_class("a", "t", f, tenant_override=RiskClass.R0, **kw) is RiskClass.R1
    assert effective_risk_class("a", "t", f, requested=RiskClass.R3, **kw) is RiskClass.R3
    assert effective_risk_class("a", "t", f, tenant_override=RiskClass.R2, **kw) is RiskClass.R2


def test_valid_exception_lowers_r2_by_one_level() -> None:
    got = effective_risk_class(
        "invoice.approve", "ten_1", RiskClass.R2, exception=_exception(RiskClass.R1), now=NOW
    )
    assert got is RiskClass.R1


@pytest.mark.parametrize(
    "bad",
    [
        {"expires_at": NOW},  # expired: re-blocks automatically
        {"approved_by": frozenset({"Product"})},  # Security missing
        {"approved_by": frozenset({"Security"})},  # Product missing
        {"tenant_id": "ten_other"},  # scoped to another tenant
        {"action": "payment.release"},  # scoped to another action
        {"compensating_control": " "},
        {"approval_ref": ""},
    ],
)
def test_invalid_exception_is_ignored(bad: dict[str, object]) -> None:
    exc = _exception(RiskClass.R1, **bad)
    got = effective_risk_class("invoice.approve", "ten_1", RiskClass.R2, exception=exc, now=NOW)
    assert got is RiskClass.R2


def test_exception_never_breaks_r3_fail_closed() -> None:
    with pytest.raises(RiskContractError):  # two levels
        effective_risk_class(
            "invoice.approve", "ten_1", RiskClass.R3, exception=_exception(RiskClass.R1), now=NOW
        )
    lowered = effective_risk_class(
        "invoice.approve", "ten_1", RiskClass.R3, exception=_exception(RiskClass.R2), now=NOW
    )
    assert failure_posture(lowered) is FailurePosture.FAIL_CLOSED


def test_failure_posture() -> None:
    assert failure_posture(RiskClass.R3, bounded_cache_approved=True) is FailurePosture.FAIL_CLOSED
    assert failure_posture(RiskClass.R2) is FailurePosture.FAIL_CLOSED
    assert (
        failure_posture(RiskClass.R2, bounded_cache_approved=True)
        is FailurePosture.USE_BOUNDED_CACHE
    )
    for r in (RiskClass.R0, RiskClass.R1):
        assert failure_posture(r) is FailurePosture.DEGRADE_PER_TENANT_POLICY


# ---------------------------------------------------------------- the action catalog obeys the contract


def test_catalog_evidence_floor_matches_contract() -> None:
    assert {
        RiskClass[r]: EvidenceClass[e] for r, e in CATALOG["evidence_floor_by_risk"].items()
    } == {r: evidence_floor(r) for r in RiskClass}


def test_every_action_meets_its_evidence_floor() -> None:
    """R3 actions are E3 unless a documented decision permits E2 (DEC-B1 §2)."""
    problems = []
    for action, spec in CATALOG["actions"].items():
        risk, ev = RiskClass[spec["min_risk_class"]], EvidenceClass[spec["min_evidence_class"]]
        if ev < evidence_floor(risk) and not spec.get("evidence_exception_ref"):
            problems.append(
                f"{action}: {ev.name} is below the {risk.name} floor {evidence_floor(risk).name}"
            )
    assert not problems, "\n".join(problems)


def test_every_catalog_owner_is_a_module_or_a_customer_pep() -> None:
    modules = {p.name for p in MODULES.iterdir() if p.is_dir() and not p.name.startswith("__")}
    bad = {
        a: s["owner"]
        for a, s in CATALOG["actions"].items()
        if s["owner"] not in modules and s["owner"] != "customer-application (PEP)"
    }
    assert not bad, bad


def test_catalog_covers_every_r3_example_category() -> None:
    """ZID-REQ-001 §4 names the R3 examples; each needs at least one catalogued action."""
    categories = " ".join(
        s["category"].lower() for s in CATALOG["actions"].values() if s["min_risk_class"] == "R3"
    )
    for example in (
        "idp change",
        "mfa change",
        "bulk export",
        "authority delegation",
        "credential signing",
    ):
        assert example in categories, example

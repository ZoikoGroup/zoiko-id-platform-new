"""The DEC-B3 k6 harness stays bound to the published SLO contract (NFR-PERF-001, NFR-SCL-002)."""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
SCRIPT = (REPO / "tests/load/decision-gate/decision-gate.js").read_text(encoding="utf-8")
SLO = yaml.safe_load((REPO / "contracts/baseline/slo.yaml").read_text(encoding="utf-8"))
CATALOG = yaml.safe_load((REPO / "contracts/action-catalog/actions.yaml").read_text("utf-8"))


def _gate_constant(name: str) -> float:
    m = re.search(rf"^\s*{name}:\s*([\d.]+),", SCRIPT, re.MULTILINE)
    assert m, f"GATE.{name} missing from decision-gate.js"
    return float(m.group(1))


def test_k6_gate_matches_slo_contract() -> None:
    gate = SLO["decision_service_gate"]
    budgets = SLO["latency_budgets_ms"]
    assert _gate_constant("ratePerSecond") == gate["load_per_cell_per_s"]
    assert _gate_constant("soakMinutes") == gate["soak_minutes"]
    assert _gate_constant("maxErrorRate") == gate["max_error_rate"]
    assert _gate_constant("p95MsR0R1") == budgets["R0_R1"]["total"]["p95_lt"]
    assert _gate_constant("p95MsR2R3") == budgets["R2_R3"]["total"]["p95_lt"]


def test_k6_traffic_uses_catalogued_actions_on_the_right_path() -> None:
    requests = re.findall(r'path: "(R0_R1|R2_R3)", action: "([\w.]+)"', SCRIPT)
    assert {p for p, _ in requests} == {"R0_R1", "R2_R3"}, "both latency paths must be exercised"
    for path, action in requests:
        risk = CATALOG["actions"][action]["min_risk_class"]
        assert (risk in {"R0", "R1"}) == (path == "R0_R1"), f"{action} ({risk}) tagged {path}"


def test_k6_reports_percentiles_not_averages() -> None:
    stats = re.search(r"summaryTrendStats:\s*\[([^\]]*)\]", SCRIPT)
    assert stats, "summaryTrendStats missing"
    assert all(p in stats.group(1) for p in ('"p(50)"', '"p(95)"', '"p(99)"'))
    assert '"avg"' not in stats.group(1)


def test_security_state_cache_fits_the_propagation_slo() -> None:
    """FR-SIG-006 inside FR-SIG-004: no risk class may be served a cached state for longer than
    its propagation budget (R2/R3 are never cached)."""
    from zid_platform.modules.signals.domain.rules import STATE_CACHE_TTL

    budget = SLO["revocation_propagation_seconds"]
    assert budget["requirement"] == "FR-SIG-004"
    limits = {0: "R0", 1: "R1", 2: "R2", 3: "R3_in_cell"}
    for rc, name in limits.items():
        assert STATE_CACHE_TTL[rc].total_seconds() < budget[name]["p99_le"], name
    assert STATE_CACHE_TTL[2].total_seconds() == STATE_CACHE_TTL[3].total_seconds() == 0


MACHINE_SCRIPT = (REPO / "tests/load/machine-token/machine-token.js").read_text(encoding="utf-8")
CAPACITY = yaml.safe_load((REPO / "contracts/baseline/capacity.yaml").read_text(encoding="utf-8"))


def _machine_constant(name: str) -> float:
    m = re.search(rf"^\s*{name}:\s*([\d.]+),", MACHINE_SCRIPT, re.MULTILINE)
    assert m, f"GATE.{name} missing from machine-token.js"
    return float(m.group(1))


def test_machine_token_gate_matches_the_slo_and_twice_stage_a() -> None:
    """MACH-26: 2x the Stage A token-issuance rate, with the published exchange budget and error limit."""
    gate = SLO["machine_identity_gate"]
    stage_a = CAPACITY["stages"]["A"]["token_issuance_per_s"]
    assert gate["token_exchanges_per_s"] == 2 * stage_a
    assert _machine_constant("ratePerSecond") == gate["token_exchanges_per_s"]
    assert _machine_constant("soakMinutes") == gate["soak_minutes"]
    assert _machine_constant("maxErrorRate") == gate["max_error_rate"]
    assert _machine_constant("p95MsTokenExchange") == gate["workload_token_exchange"]["p95_lt_ms"]


def test_machine_token_harness_identifies_the_workload_by_certificate_and_reports_percentiles() -> (
    None
):
    assert "tlsAuth" in MACHINE_SCRIPT, (
        "the workload must be identified by its SVID, not by a header"
    )
    assert "/v1/machine/workload-token" in MACHINE_SCRIPT
    stats = re.search(r"summaryTrendStats:\s*\[([^\]]*)\]", MACHINE_SCRIPT)
    assert stats and all(p in stats.group(1) for p in ('"p(50)"', '"p(95)"', '"p(99)"'))
    assert '"avg"' not in stats.group(1)


SCREENING_SCRIPT = (REPO / "tests/load/screening-gate/screening-gate.js").read_text(
    encoding="utf-8"
)


def _screening_constant(name: str) -> float:
    m = re.search(rf"^\s*{name}:\s*([\d.]+),", SCRIPT_SCREENING_GATE, re.MULTILINE)
    assert m, f"GATE.{name} missing from screening-gate.js"
    return float(m.group(1))


SCRIPT_SCREENING_GATE = SCREENING_SCRIPT


def test_screening_gate_matches_the_slo_proposal_and_says_it_is_only_proposed() -> None:
    gate = SLO["screening_gate"]
    assert (
        gate["status"] == "PROPOSED"
    )  # no requirement sets these numbers: they are not a gate until the owners ratify them
    assert _screening_constant("eligibilityPerSecond") == gate["eligibility_reads_per_s"]
    assert _screening_constant("caseOpensPerSecond") == gate["case_opens_per_s"]
    assert _screening_constant("soakMinutes") == gate["soak_minutes"]
    assert _screening_constant("maxErrorRate") == gate["max_error_rate"]
    assert _screening_constant("p95MsEligibility") == gate["p95_ms_eligibility"]
    assert _screening_constant("p95MsCaseOpen") == gate["p95_ms_case_open"]
    assert not any("requirement" in k for k in gate)


def test_screening_harness_counts_a_leaked_list_name_as_a_failure_and_reports_percentiles() -> None:
    assert 'customer_response_leaks: ["count==0"]' in SCREENING_SCRIPT
    for forbidden in ("OFAC", "matched_name", '"score"'):
        assert forbidden in SCREENING_SCRIPT
    assert (
        "/v1/screening/eligibility" in SCREENING_SCRIPT
        and "/v1/screening/cases" in SCREENING_SCRIPT
    )
    stats = re.search(r"summaryTrendStats:\s*\[([^\]]*)\]", SCREENING_SCRIPT)
    assert stats and all(p in stats.group(1) for p in ('"p(50)"', '"p(95)"', '"p(99)"'))
    assert '"avg"' not in stats.group(1)

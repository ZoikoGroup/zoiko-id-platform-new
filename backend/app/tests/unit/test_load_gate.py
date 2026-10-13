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

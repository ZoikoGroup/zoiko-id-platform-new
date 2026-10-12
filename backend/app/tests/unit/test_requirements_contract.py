"""Phase B: the requirements contract (ZID-REQ-001) stays complete, traceable and self-consistent.

- requirements.csv is well-formed, complete and every row traces to real decisions
- every P0 gap closes through requirement IDs that exist (§9)
- every §10 prohibition is covered by an invariant or names its enforcing phase
- SLO, latency and capacity numbers in contracts/baseline/ match the requirement text they cite
- DEC-B2 capacity admission rules
"""

import csv
import re
from pathlib import Path

import pytest
import yaml
from zid_platform.modules.tenant.domain.placement import (
    MIN_COMPUTE_HEADROOM,
    AdmissionRefusal,
    CellCapacity,
    admission_refusals,
)

REPO = Path(__file__).resolve().parents[4]
BASELINE = REPO / "contracts" / "baseline"
REQ_CSV = REPO / "docs" / "traceability" / "requirements.csv"


def _load(name: str) -> dict:  # type: ignore[type-arg]
    return yaml.safe_load((BASELINE / name).read_text(encoding="utf-8"))


ROWS = list(csv.reader(REQ_CSV.open(encoding="utf-8", newline="")))
HEADER, BODY = ROWS[0], ROWS[1:]
REQS = {r[0]: dict(zip(HEADER, r, strict=True)) for r in BODY}
DECISIONS = set(
    re.findall(
        r"^### (DEC-[A-I]\d+):",
        "\n".join(p.read_text("utf-8") for p in (REPO / "docs/phases").glob("phase-*.md")),
        re.MULTILINE,
    )
)
SLO, CAPACITY, GATES, INVARIANTS = (
    _load("slo.yaml"),
    _load("capacity.yaml"),
    _load("gates.yaml"),
    _load("invariants.yaml"),
)


# ---------------------------------------------------------------- requirements.csv


def test_requirements_csv_is_well_formed() -> None:
    bad = [r[0] for r in BODY if len(r) != len(HEADER)]
    assert not bad, f"rows with the wrong number of columns (unquoted commas?): {bad}"
    assert len(REQS) == len(BODY), "duplicate requirement IDs"


def test_requirements_cover_every_area_of_zid_req_001() -> None:
    assert len(REQS) == 191
    prefixes = {rid.rsplit("-", 1)[0] for rid in REQS}
    assert "REG" in prefixes, "§6 regulatory requirements are missing"
    assert sum(1 for r in REQS.values() if r["priority"] == "P0") == 83


def test_every_requirement_is_traceable() -> None:
    problems = []
    for rid, r in REQS.items():
        if r["priority"] not in {"P0", "P1", "P2"}:
            problems.append(f"{rid}: priority {r['priority']!r}")
        if not r["verification"].strip():
            problems.append(f"{rid}: no verification method")
        if not r["owning_units_modules"].strip() or r["owning_units_modules"] == "UNMAPPED":
            problems.append(f"{rid}: no owner")
        decisions = [d.strip() for d in r["governing_decisions"].split(",") if d.strip()]
        if not decisions:
            problems.append(f"{rid}: no governing decision")
        problems += [f"{rid}: unknown decision {d}" for d in decisions if d not in DECISIONS]
        if r["priority"] == "P0" and not r["test_suites"].strip():
            problems.append(f"{rid}: P0 without a test suite")
    assert not problems, "\n".join(problems)


def test_every_p0_gap_closes_through_existing_requirements() -> None:
    problems = []
    for gid, g in GATES["p0"].items():
        if not g.get("requirements"):
            problems.append(f"{gid}: no requirement closure (ZID-REQ-001 §9)")
        problems += [
            f"{gid}: unknown requirement {r}" for r in g.get("requirements", []) if r not in REQS
        ]
    assert not problems, "\n".join(problems)


def test_every_prohibition_is_covered() -> None:
    invariants = INVARIANTS["invariants"]
    prohibitions = INVARIANTS["prohibitions"]
    assert sorted(prohibitions) == [f"PRO-{i:02d}" for i in range(1, 12)]
    for pid, p in prohibitions.items():
        if "covered_by" in p:
            assert p["covered_by"] in invariants, pid
        else:
            assert p["phase"] in set("ABCDEFGHI"), pid
            assert p.get("decision") is None or p["decision"] in DECISIONS, pid


# ---------------------------------------------------------------- SLO and latency budgets


def _cited_values(node: object) -> list[tuple[str, float]]:
    """(requirement, number) pairs for every leaf that cites a requirement."""
    out: list[tuple[str, float]] = []
    if isinstance(node, dict):
        if "requirement" in node:
            out += [(node["requirement"], v) for k, v in node.items() if k != "requirement"]
        else:
            for v in node.values():
                out += _cited_values(v)
    return out


def test_slo_values_match_the_requirement_text() -> None:
    problems = []
    for rid, value in _cited_values(SLO["availability"]) + _cited_values(SLO["latency_budgets_ms"]):
        text = REQS[rid]["requirement"]
        number = f"{value:g}"
        if not re.search(rf"(?<![\d.]){re.escape(number)}(?![\d])", text):
            problems.append(f"{rid}: {number} does not appear in {text!r}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("path", ["R0_R1", "R2_R3"])
def test_component_budgets_fit_inside_the_path_total(path: str) -> None:
    budget = SLO["latency_budgets_ms"][path]
    used = sum(c["p95_le"] for c in budget["components"].values())
    assert used < budget["total"]["p95_lt"], f"{path}: components use {used} ms"


def test_performance_gate_runs_at_twice_the_stage_a_cell_peak() -> None:
    a = CAPACITY["stages"]["A"]
    gate = SLO["decision_service_gate"]
    assert (
        gate["load_per_cell_per_s"]
        == CAPACITY["qualification"]["multiplier"] * a["authz_decisions_per_s_per_cell"]
    )


# ---------------------------------------------------------------- capacity design points


def _short(n: int) -> str:
    return f"{n // 1_000_000}M" if n >= 1_000_000 and n % 1_000_000 == 0 else f"{n // 1000}k"


def test_stage_a_matches_nfr_scl_001() -> None:
    text = REQS["NFR-SCL-001"]["requirement"]
    a = CAPACITY["stages"]["A"]
    for key in (
        "principals",
        "authz_decisions_per_s_global",
        "authz_decisions_per_s_per_cell",
        "token_issuance_per_s",
        "r2_r3_receipts_per_s",
    ):
        assert _short(a[key]) in text, f"{key}={_short(a[key])} not in NFR-SCL-001"


def test_stages_b_and_c_match_nfr_scl_003_and_stay_targets() -> None:
    text = REQS["NFR-SCL-003"]["requirement"]
    for stage in ("B", "C"):
        s = CAPACITY["stages"][stage]
        assert s["status"] == "target", f"Stage {stage} needs re-ratification before it is approved"
        assert _short(s["principals"]) in text
        assert _short(s["authz_decisions_per_s_global"]) in text


def test_stages_only_grow() -> None:
    stages = [CAPACITY["stages"][s] for s in "ABC"]
    for key in stages[0]:
        if isinstance(stages[0][key], int):
            assert stages[0][key] < stages[1][key] < stages[2][key], key


def test_cell_admission_config_matches_the_contract() -> None:
    cells = yaml.safe_load((REPO / "backend/config/cells/cells.yaml").read_text("utf-8"))
    cfg = cells["capacity_admission"]
    assert (
        float(cfg["min_compute_headroom"].rstrip("%")) / 100
        == CAPACITY["admission"]["min_compute_headroom"]
    )
    assert CAPACITY["admission"]["min_compute_headroom"] == MIN_COMPUTE_HEADROOM
    assert cfg["survive_one_az_loss"] and cfg["stop_new_placement_before_saturation"]


CELL = CellCapacity(capacity_per_s=10_000, availability_zones=3, saturation_threshold=0.9)


def test_placement_admits_within_headroom_and_az_loss() -> None:
    # one-AZ-loss capacity = 6,667/s; x 0.9 saturation = 6,000/s; headroom limit = 7,000/s
    assert admission_refusals(CELL, current_peak_per_s=5_000, tenant_peak_per_s=900) == []


def test_placement_stops_before_one_az_loss_saturates() -> None:
    assert admission_refusals(CELL, 5_000, 1_500) == [AdmissionRefusal.AZ_LOSS]


def test_placement_keeps_thirty_percent_headroom() -> None:
    roomy = CellCapacity(capacity_per_s=10_000, availability_zones=3, saturation_threshold=1.2)
    assert admission_refusals(roomy, 6_000, 1_500) == [AdmissionRefusal.HEADROOM]


def test_single_az_cell_never_admits() -> None:
    one = CellCapacity(capacity_per_s=10_000, availability_zones=1, saturation_threshold=0.9)
    assert AdmissionRefusal.SINGLE_AZ in admission_refusals(one, 0, 1)


# ---------------------------------------------------------------- progress tracking

STATUSES = ["NOT_STARTED", "IN_PROGRESS", "IMPLEMENTED", "VERIFIED", "ACCEPTED"]


def test_requirement_status_is_honest() -> None:
    """Progress needs evidence that exists; VERIFIED needs a passing verification (README vocabulary)."""
    problems = []
    for rid, r in REQS.items():
        impl, test, evidence = r["implementation_status"], r["test_status"], r["evidence_link"]
        if impl not in STATUSES or test not in STATUSES:
            problems.append(f"{rid}: unknown status {impl!r}/{test!r}")
            continue
        if impl != "NOT_STARTED" and not evidence:
            problems.append(f"{rid}: {impl} without an evidence link")
        if evidence and not evidence.startswith("http"):
            path = REPO / evidence.split("::")[0]
            if not path.exists():
                problems.append(f"{rid}: evidence {evidence} does not exist")
            elif "::" in evidence and f"def {evidence.split('::')[1]}(" not in path.read_text(
                "utf-8"
            ):
                problems.append(f"{rid}: evidence test {evidence} does not exist")
        if STATUSES.index(impl) >= STATUSES.index("VERIFIED") and test not in {
            "VERIFIED",
            "ACCEPTED",
        }:
            problems.append(f"{rid}: {impl} but test_status is {test}")
    assert not problems, "\n".join(problems)


def test_every_contract_is_independently_versioned() -> None:
    """NFR-MNT-001: each contract registry carries its own version and approval status."""
    problems = []
    for f in sorted((REPO / "contracts").rglob("*.yaml")):
        header = []
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.startswith("#"):
                break
            header.append(line)
        if not any(re.fullmatch(r"# version: \d+\.\d+\.\d+.*", h) for h in header):
            problems.append(f"{f.relative_to(REPO)}: no '# version: X.Y.Z' header")
        if not any(h.startswith("# status: ") for h in header):
            problems.append(f"{f.relative_to(REPO)}: no '# status:' header")
    assert not problems, "\n".join(problems)


def test_sli_contract_is_low_cardinality_and_alerts_resolve() -> None:
    """NFR-OBS-002/004: required SLIs exist, carry no per-tenant labels, and every alert has an SLI."""
    metrics = SLO["slis"]["metrics"]
    for name, m in metrics.items():
        assert name.startswith("zid_"), name
        assert m["type"] in {"counter", "gauge", "histogram"}, name
        assert not set(m["labels"]) & {"tenant_id", "principal_id", "subject", "request_id"}, name
    rules = SLO["alerting"]["rules"]
    assert all(r["sli"] in metrics for r in rules.values())
    for must in (
        "r2_r3_revocation_slo_breach",
        "authorization_latency_slo_breach",
        "mandate_integrity_failure",
    ):
        assert rules[must]["pages"], must
    assert rules["r2_r3_revocation_slo_breach"]["severity"].endswith("security")


def test_acceptance_test_status_is_honest() -> None:
    """Same rule as requirements: progress needs evidence that exists (acceptance-tests.csv)."""
    rows = list(
        csv.DictReader(
            (REPO / "docs/traceability/acceptance-tests.csv").open(encoding="utf-8", newline="")
        )
    )
    problems = []
    for r in rows:
        status, evidence = r["test_status"], r["evidence_link"]
        tag = f"{r['test_id']} ({r['source_spec']})"
        if status not in STATUSES:
            problems.append(f"{tag}: unknown status {status!r}")
        elif status != "NOT_STARTED" and not evidence:
            problems.append(f"{tag}: {status} without evidence")
        if evidence and not evidence.startswith("http"):
            path = REPO / evidence.split("::")[0]
            if not path.exists():
                problems.append(f"{tag}: evidence {evidence} does not exist")
            elif "::" in evidence and f"def {evidence.split('::')[1]}(" not in path.read_text(
                "utf-8"
            ):
                problems.append(f"{tag}: evidence test {evidence} does not exist")
    assert not problems, "\n".join(problems)

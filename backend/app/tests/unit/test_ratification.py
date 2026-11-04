"""Commercial ratification and go-live register (ZID-COM-RAT-011; Phase L slice L-15, CR-055).

The register is the owners' instrument and the default is NO-GO. These tests pin what the checker refuses: a GO the register does not support,
a conditional GO over an open P0, a gate passed without a reviewer or evidence, an unratified policy flag that is no longer disabled, and a money
flag that moved. They also show GO is reachable only when everything the document asks for is present, so the checker is not a wall."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[4]
TOOL = REPO / "tools" / "scripts" / "ratification.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("zid_tool_ratification", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rat = _load()


def _real() -> dict[str, Any]:
    return rat.load()


# ----------------------------------------------------------------------------------------------------------- the shipped register


def test_the_shipped_register_is_consistent_and_says_no_go() -> None:
    reg = _real()
    assert rat.validate(reg) == []
    assert reg["disposition"] == "NO-GO"
    state, why = rat.verdict(reg)
    assert state == "NO-GO" and why
    assert all(r["decision"] is None for r in reg["ratifications"]), "engineering never fills an owner's decision"
    assert all(g["status"] == "PENDING" and g["reviewer"] is None for g in reg["gates"])
    assert all(s["name"] is None and s["decision"] is None for s in reg["signatories"])


def test_every_no_go_of_the_document_is_present_and_open_today() -> None:
    reg = _real()
    assert [n["id"] for n in reg["no_go"]] == ["NG-01", "NG-02", "NG-03", "NG-04", "NG-05"]
    assert all(n["p0"] for n in reg["no_go"])
    for n in reg["no_go"]:
        assert rat._no_go(n, reg, REPO)[0] == "OPEN", n["id"]


def test_require_go_refuses_the_shipped_register() -> None:
    assert rat.main(["--require-go"]) == 1


def test_the_check_passes_on_the_shipped_register() -> None:
    assert rat.main(["--check"]) == 0


def test_the_report_names_every_open_item_and_decides_nothing() -> None:
    text = rat.report(_real())
    assert "Recorded disposition: **NO-GO**" in text
    for needle in ("CL-004", "G6", "NG-01", "E07", "Legal", "FLAG-commitment-enforcement"):
        assert needle in text
    assert "not a decision" in text


# ------------------------------------------------------------------------------------------------------------------- refusals


def test_a_go_the_register_does_not_support_is_refused() -> None:
    reg = _real()
    reg["disposition"] = "GO"
    problems = rat.validate(reg)
    assert any("says GO but the register does not support it" in p for p in problems)


def test_a_conditional_go_cannot_override_an_open_p0_no_go() -> None:
    reg = _real()
    reg["disposition"] = "CONDITIONAL_GO"
    assert any("cannot override an unresolved P0 no-go" in p for p in rat.validate(reg))


def test_unknown_disposition_decision_and_gate_status_are_refused() -> None:
    reg = _real()
    reg["disposition"] = "MAYBE"
    reg["ratifications"][0]["decision"] = "Nod"
    reg["gates"][0]["status"] = "GREEN"
    text = "\n".join(rat.validate(reg))
    assert "MAYBE" in text and "Nod" in text and "GREEN" in text


def test_an_amend_or_a_defer_needs_its_decision_record() -> None:
    reg = _real()
    reg["ratifications"][3]["decision"] = "Defer"
    text = "\n".join(rat.validate(reg))
    for f in ("decision_record", "policy_version", "owner", "effective_date"):
        assert f"CL-004: an Defer needs {f}" in text


def test_a_gate_passes_only_with_a_named_reviewer_and_evidence_that_resolves() -> None:
    reg = _real()
    reg["gates"][3].update(status="PASS")
    text = "\n".join(rat.validate(reg))
    assert "G3: PASS needs a named reviewer" in text and "G3: PASS needs an evidence reference that resolves" in text
    reg["gates"][3].update(reviewer="A. Reviewer", evidence_ref="docs/signoff/does-not-exist.md")
    assert any("G3: PASS needs an evidence reference" in p for p in rat.validate(reg))
    reg["gates"][3].update(evidence_ref="tools/scripts/release_gate.py")
    assert rat.validate(reg) == []


def test_the_founder_and_the_cfo_cannot_be_one_person() -> None:
    reg = _real()
    reg["signatories"][0]["name"] = "Sam Roe"
    reg["signatories"][1]["name"] = " sam roe "
    assert any("two different people" in p for p in rat.validate(reg))


def test_the_register_keeps_its_closures_gates_evidence_and_signatories() -> None:
    for path, bad in (("ratifications", 1), ("gates", 2), ("evidence", 3), ("signatories", 4)):
        reg = _real()
        del reg[path][bad]
        assert rat.validate(reg), path


# --------------------------------------------------------------------------------------------------------------- policy flags


def _tree(tmp: Path, *, commitment: str = "REPORT_ONLY", bands: str = "REPORT_ONLY", bill: str = "false", sandbox: str = '"local", "test"') -> Path:
    """A repository the checks can read, with every file the register names."""
    def put(rel: str, text: str) -> None:
        f = tmp / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")

    put("backend/config/commercial/finance-policy.yaml", f"commitment:\n  valid_days: 14\n  enforcement: {commitment}\n")
    put("backend/config/entitlements/capability-bands.yaml", f"# note\nband_matrix_enforcement: {bands}\n")
    put("backend/config/onboarding/readiness-policy.yaml", "enforcement: REPORT_ONLY\n")
    put("backend/config/roles/role-policy.yaml", "billing_access_enforcement: ENFORCED\n")
    put("backend/config/entitlements/meter-register.yaml", f"meters:\n  a:\n    bill_on_commit: {bill}\n  b:\n    bill_on_commit: {bill}\n")
    put("backend/app/src/zid_platform/modules/commercial/application/finance_service.py", f"SANDBOX = frozenset({{{sandbox}}})\n")
    put(".github/workflows/ci.yml", "jobs:\n  unit:\n    x: 1\n  component:\n    x: 1\n  sast:\n    x: 1\n  secret-scan:\n    x: 1\n  python-audit:\n    x: 1\n  checks:\n    needs:\n      - unit\n      - component\n      - sast\n      - secret-scan\n      - python-audit\n    runs-on: x\n")
    put("docs/traceability/acceptance-tests.csv", "test_id,test_status\nCL04-C14,VERIFIED\nWF09-OP-05,VERIFIED\nWF09-OP-09,VERIFIED\nWF09-OP-10,VERIFIED\nCL04-C12,VERIFIED\nWF09-OP-03,VERIFIED\nWF10-C00-11,VERIFIED\n")
    put("docs/evidence.md", "evidence\n")
    return tmp


def test_an_unratified_closure_keeps_its_policy_flag_disabled(tmp_path: Path) -> None:
    root = _tree(tmp_path, commitment="ENFORCED")
    problems = rat.validate(_real(), root=root)
    assert any("CL-004 is not Approved" in p and "must stay in" in p for p in problems)
    reg = _real()
    reg["ratifications"][3].update(decision="Approve", owner="CFO")
    assert not any("must stay in" in p for p in rat.validate(reg, root=root))


def test_a_flag_that_drifted_from_the_register_is_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path, bands="ENFORCED")
    assert any("band_matrix_enforcement" in p for p in rat.validate(_real(), root=root))


def test_a_money_flag_that_was_switched_on_is_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path, bill="true")
    assert any("money flag is not at its shipped value" in p for p in rat.validate(_real(), root=root))


def test_the_shipped_files_agree_with_every_flag_in_the_register() -> None:
    assert [p for p in rat.validate(_real()) if "FLAG-" in p] == []
    for fl in _real()["flags"] + _real()["money_flags"]:
        values = rat.flag_values((REPO / fl["file"]).read_text(encoding="utf-8"), fl["key"])
        assert values and all(v == fl["shipped"] for v in values), fl["id"]


# ---------------------------------------------------------------------------------------------------------- GO is reachable


def _complete(root: Path) -> dict[str, Any]:
    reg = copy.deepcopy(_real())
    reg["disposition"] = "GO"
    for r in reg["ratifications"]:
        r.update(decision="Approve", owner="Owner", decision_record="DR-1", policy_version="1", effective_date="2026-12-01")
    for g in reg["gates"]:
        g.update(status="PASS", reviewer="Reviewer", evidence_ref="docs/evidence.md")
    for e in reg["evidence"]:
        e["ref"] = "docs/evidence.md"
    reg["release"].update(release_id="R-1", policy_version="1", approved_countries=["GB"], approved_products=["Core"], approved_issuers=["Zoiko Ltd"], environment="production", date="2026-12-01")
    names = ["A", "B", "C", "D", "E", "F", "G"]
    for s, n in zip(reg["signatories"], names, strict=True):
        s.update(name=n, date="2026-12-01", decision="GO")
    return reg


def test_go_is_reachable_only_when_everything_the_document_asks_for_is_present(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    reg = _complete(root)
    assert rat.validate(reg, root=root) == []
    assert rat.verdict(reg, root=root) == ("GO", [])


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda r: r["ratifications"][2].update(decision="Defer", decision_record="DR", policy_version="2", owner="O", effective_date="2027-01-01"), "CL-003 is Defer"),
        (lambda r: r["gates"][4].update(status="FAIL"), "gate G4 is FAIL"),
        (lambda r: r["evidence"][6].update(ref=None), "evidence E07 is not referenced"),
        (lambda r: r["release"].update(approved_countries=[]), "release field approved_countries is blank"),
        (lambda r: r["signatories"][6].update(decision="NO-GO"), "Legal has not signed GO"),
        (lambda r: r["signatories"][3].update(name=None), "CTO has not signed GO"),
    ],
)
def test_one_missing_piece_returns_the_register_to_no_go(tmp_path: Path, mutate: Any, reason: str) -> None:
    root = _tree(tmp_path)
    reg = _complete(root)
    mutate(reg)
    state, why = rat.verdict(reg, root=root)
    assert state == "NO-GO" and any(reason in w for w in why), why


def test_a_no_go_stays_open_when_an_acceptance_row_is_only_in_progress(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "docs/traceability/acceptance-tests.csv").write_text("test_id,test_status\nCL04-C14,IN_PROGRESS\nWF09-OP-05,VERIFIED\nWF09-OP-09,VERIFIED\nWF09-OP-10,VERIFIED\nCL04-C12,VERIFIED\nWF09-OP-03,VERIFIED\nWF10-C00-11,VERIFIED\n", encoding="utf-8")
    state, why = rat.verdict(_complete(root), root=root)
    assert state == "NO-GO" and any("NG-04 is open" in w and "CL04-C14" in w for w in why)


def test_a_no_go_stays_open_when_a_required_ci_job_is_not_in_the_gate(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    ci = root / ".github/workflows/ci.yml"
    ci.write_text(ci.read_text(encoding="utf-8").replace("      - sast\n", ""), encoding="utf-8")
    state, why = rat.verdict(_complete(root), root=root)
    assert state == "NO-GO" and any("sast is not required by the checks gate" in w for w in why)


def test_the_finance_lock_that_skips_a_deployed_environment_reopens_the_monetary_no_go(tmp_path: Path) -> None:
    root = _tree(tmp_path, sandbox='"local", "test", "production"')
    state, why = rat.verdict(_complete(root), root=root)
    assert state == "NO-GO" and any("finance policy lock is no longer sandbox-only" in w for w in why)
    assert rat.finance_lock_is_sandbox_only((REPO / "backend/app/src/zid_platform/modules/commercial/application/finance_service.py").read_text(encoding="utf-8"))


def test_the_machine_signals_read_the_real_ci_and_acceptance_files() -> None:
    defined, needed = rat.required_ci_jobs((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert {"unit", "component", "sast", "secret-scan", "python-audit"} <= defined & needed
    assert {"CL04-C14", "WF09-OP-05", "CL04-C12"} <= set(rat.acceptance_status())


def test_a_register_written_to_another_path_is_read_from_there(tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    reg = _real()
    reg["disposition"] = "GO"
    p.write_text(json.dumps(reg), encoding="utf-8")
    assert rat.main(["--register", str(p), "--check"]) == 1


# ---------------------------------------------------------------------------------------------------------------------- wiring


def test_ci_runs_the_register_check_and_its_tests_in_the_devsecops_job() -> None:
    ci = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "tools/scripts/ratification.py --check" in ci
    assert "app/tests/unit/test_ratification.py" in ci


def test_production_promotion_needs_the_owners_go_before_the_supply_chain_gate() -> None:
    run = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "ratification.py --require-go" in run
    assert run.index("ratification.py --require-go") < run.index("python3 tools/scripts/release_gate.py")

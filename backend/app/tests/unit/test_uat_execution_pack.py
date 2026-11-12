"""The independent execution pack stays in step with the register and never claims a result (ZID-UAT-005)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


pack = load("uat_execution_pack")
register = load("uat_register")


def test_every_case_that_needs_an_outside_party_is_in_an_engagement() -> None:
    assert pack.uncovered(register.load()) == [], "someone must be told to run each of these"


def test_the_written_pack_is_current() -> None:
    assert pack.OUT.read_text(encoding="utf-8") == pack.render(register.load()), (
        "run tools/scripts/uat_execution_pack.py --write"
    )


def test_the_pack_claims_no_result_and_says_what_only_others_can_do() -> None:
    text = pack.render(register.load())
    assert "This is a brief, not a result" in text and "NOT_RUN" in text
    assert "the platform cannot supply any of the independent work" in text
    for needed in (
        "Penetration test and red team",
        "Accessibility audit",
        "fairness study",
        "Load, resilience and restore",
        "Redress",
        "Provider conformance",
        "Official registers",
    ):
        assert needed in text
    assert "ZID_UAT_TOKEN" in text and "must not be a commit author" in text


def test_the_named_cases_exist_in_the_register() -> None:
    ids = {r["case_id"] for r in register.load()}
    assert set(pack.PENTEST_CASES) <= ids
    for e in pack.ENGAGEMENTS:
        picked = [r for r in register.load() if e["pick"](r)]
        assert picked, f"{e['key']} names no case"


def test_a_new_case_needing_a_third_party_that_no_engagement_picks_up_is_caught() -> None:
    rows = [dict(r) for r in register.load()]
    rows.append({**rows[0], "case_id": "ZZ-99", "suite": "ZZ", "dependency": "THIRD_PARTY"})
    assert pack.uncovered(rows) == ["ZZ-99"]

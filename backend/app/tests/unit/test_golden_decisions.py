"""Golden authorization decisions (ZID-VERIFY-MASTER-001 MPR-05): data in tests/golden/decisions/*.yaml.

Every case goes through the real Cedar engine with the platform floor. A decision, reason code or determining
policy that changes makes the build fail; so does a case without a decision (a vector must say what is right).
Evaluation is deterministic: each case is evaluated three times and must give the identical outcome.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from zid_platform.modules.policy_runtime.domain.release import PolicyRelease, ReleaseStatus
from zid_platform.modules.policy_runtime.infrastructure.cedar_engine import build_schema, evaluate

REPO = Path(__file__).resolve().parents[4]
CATALOG = yaml.safe_load((REPO / "contracts/action-catalog/actions.yaml").read_text("utf-8"))[
    "actions"
]
SCHEMA = build_schema(CATALOG)


def load_cases() -> list[Any]:
    params = []
    for path in sorted((REPO / "tests" / "golden" / "decisions").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text("utf-8"))
        for suite in doc["suites"]:
            for case in suite["cases"]:
                params.append(
                    pytest.param(suite, case, id=f"{path.stem}:{suite['name']}:{case['name']}")
                )
    return params


CASES = load_cases()


def test_the_golden_set_is_substantial() -> None:
    assert len(CASES) >= 30, (
        "the golden decision set should cover R0 to R3, the floors and hostile input"
    )


@pytest.mark.parametrize(("suite", "case"), CASES)
def test_golden_decision(suite: dict[str, Any], case: dict[str, Any]) -> None:
    assert case["expect"] in ("ALLOW", "DENY"), "a golden case must state the decision it expects"
    release = PolicyRelease(
        release_id="golden",
        tenant_id="ten_golden",
        policies=suite["policy"],
        schema_version="1",
        status=ReleaseStatus.VALIDATING,
        cases=(),
        author="golden",
    )
    context = (
        case.get("context", {})
        if case.get("replace_context")
        else {**suite["base_context"], **case.get("context", {})}
    )
    outcomes = [
        evaluate(
            release,
            principal_id="p1",
            action=case["action"],
            resource_id="res1",
            context=context,
            schema=SCHEMA,
        )
        for _ in range(3)
    ]
    first = outcomes[0]
    assert all(
        (o.allowed, o.codes, o.determining) == (first.allowed, first.codes, first.determining)
        for o in outcomes
    ), "non-deterministic"
    assert first.allowed is (case["expect"] == "ALLOW"), (case["name"], first)
    for code in case.get("codes", []):
        assert code in first.codes, (case["name"], first.codes)
    for policy_id in case.get("determining", []):
        assert policy_id in first.determining, (case["name"], first.determining)

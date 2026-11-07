"""Plan features and the code that checks them: a ratchet, so a plan never promises something nothing enforces without saying so.

Every standard and advanced feature in `config/entitlements/capability-bands.yaml` is either checked in code (`require_feature("<id>")`) or listed below
with the reason it is not. A feature may only leave the list by gaining a gate, a new feature must be classified at once, and a gate that names a
feature the matrix does not have (a typo, a retired id) fails. The list can shrink; it cannot grow without a decision.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

APP = Path(__file__).resolve().parents[2]
SRC = APP / "src" / "zid_platform"
BANDS = APP.parent / "config" / "entitlements" / "capability-bands.yaml"
GATE = re.compile(
    r'(?:require_feature\(\s*(?:conn,\s*)?|feature_id=|gate\(conn,\s*)"((?:standard|advanced)\.[a-z0-9-]+)"'
)  # a direct gate, or a plan-aware gate

# Included in every plan (bands IIIII): no plan can lack it, so there is nothing for a plan gate to refuse. Any quota is the catalogue's.
INCLUDED_IN_EVERY_PLAN = {
    "standard.hosted-authentication-oidc-core",
    "standard.organization-onboarding-kyb-workflow",
    "standard.human-id-proofing-orchestration",
    "standard.authority-representative-verification",
    "standard.basic-verifiable-credential-issuance",
    "standard.api-access-and-signing-keys",
    "standard.developer-sandbox-test-identities",
    "standard.event-webhooks-replay-safety",
}
# Enforced by its own mechanism, not by a require_feature check: the allowance is part of how the feature works.
ENFORCED_BY_ITS_OWN_MECHANISM = {
    "standard.enhanced-api-throughput-allowances": "the request limiter takes each company's allowance from its plan (config/entitlements/plan-throughput.yaml)",
}
# Not checked anywhere yet. Each is either not built as a product feature, or built without a plan check; either way a plan that lists it promises
# something nothing enforces. Wire a gate and remove the line, or leave it here until the feature exists.
NO_GATE_YET = {}


def features() -> set[str]:
    doc = yaml.safe_load(BANDS.read_text(encoding="utf-8"))
    return {f["id"] for f in doc["features"] if f["section"] != "baseline"}


def gated() -> set[str]:
    found: set[str] = set()
    for py in SRC.rglob("*.py"):
        found |= set(GATE.findall(py.read_text(encoding="utf-8", errors="ignore")))
    return found


def test_every_gate_names_a_feature_the_matrix_has() -> None:
    assert gated() - features() == set(), (
        "a require_feature(...) names a feature that is not in capability-bands.yaml"
    )


def test_every_feature_is_gated_or_listed_with_a_reason() -> None:
    unclassified = (
        features()
        - gated()
        - INCLUDED_IN_EVERY_PLAN
        - set(NO_GATE_YET)
        - set(ENFORCED_BY_ITS_OWN_MECHANISM)
    )
    assert not unclassified, (
        f"classify these plan features (gate them, or list them with a reason): {sorted(unclassified)}"
    )


def test_a_feature_that_gained_a_gate_leaves_the_lists() -> None:
    stale = gated() & (INCLUDED_IN_EVERY_PLAN | set(NO_GATE_YET))
    assert not stale, f"these are gated now; remove them from the lists: {sorted(stale)}"


def test_the_lists_only_name_features_that_exist() -> None:
    assert (
        INCLUDED_IN_EVERY_PLAN | set(NO_GATE_YET) | set(ENFORCED_BY_ITS_OWN_MECHANISM)
    ) - features() == set()


def test_what_is_in_every_plan_really_is() -> None:
    doc = yaml.safe_load(BANDS.read_text(encoding="utf-8"))
    for f in doc["features"]:
        if f["id"] in INCLUDED_IN_EVERY_PLAN:
            assert set(f["bands"].values()) == {"I"}, f"{f['id']} is not in every plan"

"""The console guide's test fixture is the role matrix as the API publishes it (`GET /v1/roles/matrix`).

The guide (`frontend/packages/console-guide`) answers from that matrix, and its tests run against a JSON copy. This test fails the build
if the copy drifts from `backend/config/roles/role-matrix.yaml`, so the guide's tests can never pass against an out-of-date matrix.
Regenerate the fixture by projecting the YAML the way `read_role_matrix` does (see the fixture's header in the guide's README)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
YAML = REPO / "backend" / "config" / "roles" / "role-matrix.yaml"
FIXTURE = REPO / "frontend" / "packages" / "console-guide" / "test" / "fixtures" / "role-matrix.json"


def projected() -> dict[str, object]:
    d = yaml.safe_load(YAML.read_text(encoding="utf-8"))
    return {
        "version": d["version"],
        "roles": d["roles"],
        "matrices": [
            {
                "matrix": key,
                "title": m["title"],
                "actions": [
                    {"action": a["id"], "feature": a["feature"], "permissions": a["cells"], "rule": a["rule"]}
                    for a in m["actions"]
                ],
            }
            for key, m in d["matrices"].items()
        ],
    }


def test_the_guide_fixture_matches_the_published_role_matrix() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture.pop("policy_hash")  # the real hash is computed at runtime; the fixture carries a stand-in
    assert fixture == projected(), "regenerate frontend/packages/console-guide/test/fixtures/role-matrix.json from role-matrix.yaml"


def test_every_customer_role_in_the_matrix_is_one_the_guide_knows() -> None:
    roles = set(projected()["roles"])  # type: ignore[arg-type]
    assert roles == {"OWNER", "ADMIN", "APPROVER", "OPERATOR", "VIEWER"}

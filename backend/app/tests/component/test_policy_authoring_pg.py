"""Policy administration end to end (ZID-AUTHZ-001 §18-19).

A draft runs the §18.1 blocking checks and every allow/deny case: a failing case sends it back to
DRAFT with the problem; the author edits it; a clean validation moves it to REVIEW with a privilege
diff against the ACTIVE release. The author can never review their own release; an independent
reviewer can, and publishing then retires the previous ACTIVE release. Simulation evaluates one
request under the candidate and the ACTIVE release without changing anything.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.settings import Settings

OK = {
    "tenant_match": True,
    "authority_valid": True,
    "authority_stale": False,
    "security_state": "NORMAL",
    "risk_class": 1,
    "purpose": "",
}
POLICY = (
    '@id("permit-with-authority") permit (principal, action, resource) when { context.authority_valid };\n'
    '@id("no-blocked-purpose") forbid (principal, action == Action::"resource.write", resource) '
    'when { context.purpose == "blocked" };'
)


def cases(expect_blocked: bool) -> list[dict[str, object]]:
    return [
        {
            "name": "authority permits",
            "principal": "p",
            "action": "resource.write",
            "resource": "r",
            "context": OK,
            "expect_allow": True,
        },
        {
            "name": "no authority denies",
            "principal": "p",
            "action": "resource.write",
            "resource": "r",
            "context": {**OK, "authority_valid": False},
            "expect_allow": False,
        },
        {
            "name": "blocked purpose",
            "principal": "p",
            "action": "resource.write",
            "resource": "r",
            "context": {**OK, "purpose": "blocked"},
            "expect_allow": expect_blocked,
        },
    ]


def code(r: object) -> str:
    return str(r.json()["error"]["code"])  # type: ignore[attr-defined]


def test_policy_release_lifecycle(rt_url: str) -> None:
    o = onboard(
        rt_url,
        extra_actions=("policy.release.read", "policy.release.draft", "policy.release.publish"),
    )
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )
    author, reviewer = o.bearer(o.owner), o.bearer(o.second)
    with TestClient(app) as api:
        base = "/v1/policy-releases"
        # a case that expects the forbidden request to be allowed: validation fails, back to DRAFT
        r = api.post(
            base,
            headers=author,
            json={"name": "payments", "policies": POLICY, "cases": cases(True)},
        )
        assert r.status_code == 201 and r.json()["status"] == "DRAFT", r.text
        rid = r.json()["release_id"]
        r = api.post(f"{base}/{rid}:validate", headers=author)
        assert r.status_code == 200 and r.json()["status"] == "DRAFT", r.text
        assert any(
            "POLICY_TEST_FAILED" in p and "blocked purpose" in p for p in r.json()["problems"]
        )

        r = api.put(
            f"{base}/{rid}", headers=reviewer, json={"policies": POLICY, "cases": cases(False)}
        )
        assert r.status_code == 403 and code(r) == "POLICY_AUTHOR_ONLY"
        r = api.put(
            f"{base}/{rid}", headers=author, json={"policies": POLICY, "cases": cases(False)}
        )
        assert r.status_code == 200, r.text
        r = api.post(f"{base}/{rid}:validate", headers=author)
        assert (
            r.status_code == 200 and r.json()["status"] == "REVIEW" and r.json()["problems"] == []
        ), r.text
        assert "blocked purpose" not in r.json()["privilege_diff"]["newly_allowed"]

        # published source is immutable from here on
        r = api.put(
            f"{base}/{rid}", headers=author, json={"policies": POLICY, "cases": cases(False)}
        )
        assert r.status_code == 409 and code(r) == "POLICY_RELEASE_IMMUTABLE"

        r = api.post(
            f"{base}/{rid}:simulate",
            headers=author,
            json={
                "principal": "p",
                "action": "resource.write",
                "resource": "r",
                "context": {**OK, "purpose": "blocked"},
            },
        )
        assert r.status_code == 200 and r.json()["candidate"]["allowed"] is False, r.text

        r = api.post(f"{base}/{rid}:publish", headers=author)
        assert r.status_code == 409 and code(r) == "POLICY_REVIEW_REQUIRED"
        r = api.post(f"{base}/{rid}:review", headers=author)
        assert r.status_code == 403 and code(r) == "POLICY_REVIEWER_IS_AUTHOR"
        assert api.post(f"{base}/{rid}:review", headers=reviewer).status_code == 200
        r = api.post(f"{base}/{rid}:publish", headers=reviewer)
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE", r.text

        items = api.get(base, headers=author).json()["items"]
        mine = next(i for i in items if i["release_id"] == rid)
        assert (
            mine["status"] == "ACTIVE"
            and mine["policy_name"] == "payments"
            and mine["version"] == 1
        )
        assert sum(1 for i in items if i["status"] == "ACTIVE") == 1  # the previous release retired

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


def test_a_draft_is_compared_with_the_active_release_as_a_signed_analysis_that_never_touches_a_receipt(rt_url: str, owner_url: str) -> None:
    """CR-170 (ZID-CP-WF08 E12): who would gain and who would lose access is signed with the evidence key, kept append-only, and says it is analysis."""
    import asyncio

    import pytest
    from conftest import engine
    from sqlalchemy import text
    from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime, verify_evidence

    configure_runtime(development_runtime("uk-1"))
    o = onboard(rt_url, extra_actions=("policy.release.read", "policy.release.draft", "policy.release.publish"))
    app = create_app(Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"), keyring=o.ring)
    author, reviewer = o.bearer(o.owner), o.bearer(o.second)
    stricter = POLICY + '\n@id("no-marketing") forbid (principal, action == Action::"resource.write", resource) when { context.purpose == "marketing" };'
    scenarios = [
        {"principal": "p", "action": "resource.write", "resource": "r", "context": OK},
        {"principal": "p", "action": "resource.write", "resource": "r", "context": {**OK, "purpose": "marketing"}},
        {"principal": "p", "action": "resource.write", "resource": "r", "context": {**OK, "authority_valid": False}},
    ]
    try:
        with TestClient(app) as api:
            base = "/v1/policy-releases"
            a = api.post(base, headers=author, json={"name": "payments", "policies": POLICY, "cases": cases(False)}).json()["release_id"]
            assert api.post(f"{base}/{a}:validate", headers=author).json()["status"] == "REVIEW"
            assert api.post(f"{base}/{a}:review", headers=reviewer).status_code == 200
            assert api.post(f"{base}/{a}:publish", headers=reviewer).json()["status"] == "ACTIVE"
            b = api.post(base, headers=author, json={"name": "payments-v2", "policies": stricter, "cases": cases(False)}).json()["release_id"]

            r = api.post(f"{base}/{b}:compare", headers=author, json={"scenarios": scenarios})
            assert r.status_code == 201, r.text
            got = r.json()
            claims = got["claims"]
            assert claims["analysis"] is True and claims["affects_receipts"] is False and "never alters an existing decision receipt" in claims["statement"]
            assert claims["summary"]["counts"] == {"UNCHANGED": 2, "NEWLY_ALLOWED": 0, "NEWLY_DENIED": 1, "NOT_COMPARABLE": 0} and claims["summary"]["narrows_access"] is True
            assert claims["candidate"]["content_hash"].startswith("sha256:") and claims["active"]["release_id"] == a
            assert verify_evidence(got["signature"], got["typ"])["summary"] == claims["summary"]  # signed with the evidence key
            assert [c["index"] for c in claims["summary"]["changes"]] == [1]

            assert api.post(f"{base}/{b}:compare", headers=author, json={"scenarios": []}).status_code == 422
            assert api.post(f"{base}/{b}:compare", headers=author, json={"scenarios": [{**scenarios[0], "extra": 1}]}).status_code == 422
            assert api.post(f"{base}/{b}:compare", headers={}, json={"scenarios": scenarios}).status_code in (401, 403)

            # comparing changed nothing: both releases stand as they were
            assert {i["release_id"]: i["status"] for i in api.get(base, headers=author).json()["items"]}[a] == "ACTIVE"
    finally:
        configure_runtime(None)

    async def tamper() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text("UPDATE authz.policy_what_if SET scenarios = 1"))

    with pytest.raises(Exception, match="append-only"):
        asyncio.run(tamper())

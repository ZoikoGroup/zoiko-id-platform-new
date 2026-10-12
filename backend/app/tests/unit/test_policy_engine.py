"""Cedar policy engine and releases (DEC-C3; ZID-AUTHZ-001 AUTHZ-01/02/09/19/23, §18.1, §19)."""

from pathlib import Path

import pytest
import yaml
from zid_platform.modules.policy_runtime.domain.release import (
    PolicyCase,
    PolicyError,
    PolicyRelease,
    ReleaseStatus,
    advance,
)
from zid_platform.modules.policy_runtime.infrastructure.cedar_engine import (
    authority_invariant_violations,
    build_schema,
    evaluate,
    prepublication_problems,
    require_publishable,
)

REPO = Path(__file__).resolve().parents[4]
CATALOG = yaml.safe_load((REPO / "contracts/action-catalog/actions.yaml").read_text("utf-8"))[
    "actions"
]
SCHEMA = build_schema(CATALOG)

GOOD = """
@id("ap-approve")
permit (principal, action == Action::"invoice.approve", resource)
when { context.authority_valid && context.purpose == "accounts_payable" && context.value_minor <= 500000 };

@id("ap-block-large")
forbid (principal, action == Action::"invoice.approve", resource)
when { context.value_minor > 1000000 };
"""

OK_CTX = {
    "tenant_match": True,
    "authority_valid": True,
    "authority_stale": False,
    "security_state": "NORMAL",
    "risk_class": 2,
    "purpose": "accounts_payable",
    "value_minor": 10_000,
}

CASES = (
    PolicyCase("approve within limit", "p1", "invoice.approve", "inv1", OK_CTX, expect_allow=True),
    PolicyCase(
        "no authority", "p1", "invoice.approve", "inv1", {**OK_CTX, "authority_valid": False}
    ),
    PolicyCase(
        "wrong purpose", "p1", "invoice.approve", "inv1", {**OK_CTX, "purpose": "marketing"}
    ),
)


def release(
    policies: str = GOOD, cases: tuple[PolicyCase, ...] = CASES, **kw: object
) -> PolicyRelease:
    base: dict[str, object] = {
        "release_id": "rel_1",
        "tenant_id": "ten_1",
        "policies": policies,
        "schema_version": "1",
        "status": ReleaseStatus.VALIDATING,
        "cases": cases,
        "author": "prn_author",
    } | kw
    return PolicyRelease(**base)  # type: ignore[arg-type]


def run(ctx: dict[str, object], action: str = "invoice.approve", rel: PolicyRelease | None = None):  # type: ignore[no-untyped-def]
    return evaluate(
        rel or release(),
        principal_id="p1",
        action=action,
        resource_id="inv1",
        context=ctx,
        schema=SCHEMA,
    )


def test_permit_when_every_condition_holds() -> None:
    out = run(OK_CTX)
    assert out.allowed and out.determining == ("ap-approve",)


def test_default_deny_for_unmatched_actions() -> None:
    """AUTHZ-01."""
    out = run(OK_CTX, action="payment.release")
    assert not out.allowed and out.codes == ("POLICY_NO_PERMIT",)


def test_forbid_overrides_permit() -> None:
    """AUTHZ-02: a tenant permit for everything still loses to a matching forbid."""
    broad = release(
        '@id("all") permit (principal, action, resource) when { context.authority_valid };' + GOOD
    )
    out = run({**OK_CTX, "value_minor": 2_000_000}, rel=broad)
    assert (
        not out.allowed and out.codes == ("POLICY_FORBID",) and "ap-block-large" in out.determining
    )


@pytest.mark.parametrize(
    ("ctx", "floor"),
    [
        ({**OK_CTX, "tenant_match": False}, "floor-tenant-isolation"),
        ({**OK_CTX, "security_state": "COMPROMISED"}, "floor-security-compromised"),
        ({**OK_CTX, "security_state": "UNKNOWN"}, "floor-r2-r3-unknown-security-state"),
        ({**OK_CTX, "authority_stale": True}, "floor-authority-stale"),
    ],
)
def test_platform_floor_cannot_be_overridden(ctx: dict[str, object], floor: str) -> None:
    out = run(ctx)
    assert not out.allowed and floor in out.determining and out.codes == ("POLICY_FORBID",)


def test_missing_context_is_deny_safe() -> None:
    """INV-14 / AUTHZ-09: a caller that sends nothing gets the deny-safe defaults."""
    out = run({})
    assert not out.allowed


def test_schema_rejects_undeclared_attributes() -> None:
    """AUTHZ-19: policy analysis before publish."""
    bad = release(
        '@id("x") permit (principal, action == Action::"invoice.approve", resource) '
        "when { context.authority_valid && context.is_admin };"
    )
    problems = prepublication_problems(bad, CATALOG)
    assert problems and problems[0].startswith("POLICY_SCHEMA_INVALID")


def test_r2_r3_permit_must_require_authority() -> None:
    """Authority invariant validation (AUTHZ §18.1): Cedar never stands in for the authority chain."""
    lax = '@id("lax") permit (principal, action == Action::"invoice.approve", resource);'
    assert authority_invariant_violations(lax, CATALOG) == [
        "lax: permits an R2/R3 action without requiring context.authority_valid"
    ]
    low = '@id("low") permit (principal, action == Action::"preferences.update", resource);'
    assert authority_invariant_violations(low, CATALOG) == []  # R0 action: fine
    everything = '@id("all") permit (principal, action, resource);'
    assert authority_invariant_violations(everything, CATALOG)


def test_release_cases_must_exist_and_pass() -> None:
    assert prepublication_problems(release(), CATALOG) == []
    assert "POLICY_TESTS_REQUIRED" in prepublication_problems(release(cases=()), CATALOG)[0]
    wrong = (
        PolicyCase("expects deny", "p1", "invoice.approve", "inv1", OK_CTX, expect_allow=False),
    )
    with pytest.raises(PolicyError) as e:
        require_publishable(release(cases=wrong), CATALOG)
    assert e.value.code == "POLICY_TEST_FAILED"


def test_evaluation_errors_deny() -> None:
    """AUTHZ-23: a context of the wrong type is an error, and errors never allow."""
    out = run({**OK_CTX, "value_minor": "lots"})
    assert not out.allowed and out.codes[0] == "POLICY_EVALUATION_ERROR"


def test_publication_needs_independent_reviewers() -> None:
    """AUTHZ §19: separate reviewer for standard policy, two for R3."""
    rev = release(status=ReleaseStatus.REVIEW, reviewers=("prn_author",))
    with pytest.raises(PolicyError):
        advance(rev, ReleaseStatus.ACTIVE)
    one = release(status=ReleaseStatus.REVIEW, reviewers=("prn_r1",))
    assert advance(one, ReleaseStatus.ACTIVE).status is ReleaseStatus.ACTIVE
    with pytest.raises(PolicyError):
        advance(one, ReleaseStatus.ACTIVE, high_risk=True)
    active = advance(one, ReleaseStatus.ACTIVE)
    with pytest.raises(PolicyError):
        advance(active, ReleaseStatus.DRAFT)  # published releases are never edited


def test_content_hash_pins_the_exact_policy_text() -> None:
    assert release().content_hash == release().content_hash
    assert release().content_hash != release(GOOD + "\n// comment").content_hash

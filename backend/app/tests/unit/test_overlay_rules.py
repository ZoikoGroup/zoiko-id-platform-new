"""Pure Phase G overlay rules (G-W0..W3; ZID-OVERLAY-001 §2, §5, §11, §14-16; ZID-ROOT-001 §6).

Lifecycle moves, invitation limits, domain claimability, LEI checksums and officer name renderings,
mapping determinism (groups never become authority, conflicts quarantine), multi-IdP routing and
connector health/expiry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.integration.domain.mapping import (
    apply,
    check_activation,
    content_hash,
    parse_rules,
    simulate,
)
from zid_platform.modules.integration.domain.model import (
    Health,
    IntegrationError,
    expiry_alert,
    health_after,
)
from zid_platform.modules.integration.domain.routing import RouteKind, RoutingRule, resolve
from zid_platform.modules.organization.domain.registry import (
    RegistryError,
    check_identifier,
    lei_checksum_ok,
    name_renderings,
)
from zid_platform.modules.tenant.domain.domains import normalize_domain
from zid_platform.modules.tenant.domain.lifecycle import (
    Move,
    Role,
    TenantError,
    TenantStatus,
    check_invitation,
    check_owner_removal,
    plan_transition,
)

S = TenantStatus


def test_lifecycle_moves_are_explicit_and_returns_are_recorded() -> None:
    assert plan_transition(S.DRAFT, Move.ADVANCE).to_status is S.ORGANIZATION_VERIFYING
    t = plan_transition(S.CONFIGURING, Move.BLOCK)
    assert t.to_status is S.BLOCKED and t.status_before is S.CONFIGURING
    assert (
        plan_transition(S.BLOCKED, Move.UNBLOCK, status_before=S.CONFIGURING).to_status
        is S.CONFIGURING
    )
    assert plan_transition(S.PILOT, Move.ROLLBACK).to_status is S.SHADOW
    assert plan_transition(S.ACTIVE, Move.SUSPEND).status_before is S.ACTIVE
    for bad in ((S.DRAFT, Move.SUSPEND), (S.CLOSED, Move.ADVANCE), (S.ACTIVE, Move.CLOSE)):
        with pytest.raises(TenantError):
            plan_transition(*bad)
    assert plan_transition(S.INTEGRATING, Move.ADVANCE).to_status is S.SHADOW  # shadow first
    with pytest.raises(TenantError):
        plan_transition(S.SHADOW, Move.UNBLOCK)


def test_invitations_never_create_owner_and_the_last_owner_stays() -> None:
    assert check_invitation(Role.MEMBER) is Role.MEMBER
    with pytest.raises(TenantError):
        check_invitation(Role.OWNER)
    with pytest.raises(TenantError):
        check_owner_removal(1)
    check_owner_removal(2)


@pytest.mark.parametrize("bad", ["gmail.com", "Outlook.com", "192.168.1.1", "localhost", "a..b"])
def test_unclaimable_domains(bad: str) -> None:
    with pytest.raises(TenantError):
        normalize_domain(bad)


def test_domains_normalize_to_ascii_lower_case() -> None:
    assert normalize_domain("Example.COM.") == "example.com"
    assert normalize_domain("bücher.example") == "xn--bcher-kva.example"


def test_registry_identifiers_and_officer_renderings() -> None:
    assert check_identifier("GB-COH", "sc123456") == "SC123456"
    with pytest.raises(RegistryError):
        check_identifier("GB-COH", "1234")
    assert lei_checksum_ok("5493001KJTIIGC8Y1R12")
    assert not lei_checksum_ok("5493001KJTIIGC8Y1R13")
    forms = name_renderings("DOE, Jane Mary")
    assert "Jane Mary Doe" in forms and "JANE MARY DOE" in forms
    assert "Doe" not in forms and "Jane" not in forms  # never a partial match


RULES = [
    {
        "source": "claims.department",
        "target": "profile.department",
        "transform": [{"op": "trim"}, {"op": "lower"}],
    },
    {
        "source": "groups",
        "target": "relation.member_of",
        "transform": [{"op": "prefix_strip", "prefix": "grp-"}],
    },
    {
        "source": "claims.dept_code",
        "target": "profile.department",
        "transform": [{"op": "map", "table": {"FIN": "finance"}}],
    },
]


def test_mapping_is_deterministic_and_conflicts_quarantine() -> None:
    rules = parse_rules(RULES)
    assert content_hash(rules) == content_hash(parse_rules(list(RULES)))
    ok = apply(
        rules,
        {"claims": {"department": " Finance ", "dept_code": "FIN"}, "groups": ["grp-a", "grp-b"]},
    )
    assert ok.facts == {"profile.department": "finance", "relation.member_of": ["a", "b"]}
    assert not ok.quarantined
    clash = apply(rules, {"claims": {"department": "Sales", "dept_code": "FIN"}})
    assert clash.quarantined and clash.conflicts  # never silently overwritten (OVL-11)
    report = simulate(rules, [{"id": "u1", "claims": {"department": "x", "title": "CFO"}}])
    assert report["unknown_sources"] == ["claims.title"]
    check_activation(report)
    with pytest.raises(IntegrationError):
        check_activation(simulate(rules, [{"claims": {"department": "Sales", "dept_code": "FIN"}}]))


@pytest.mark.parametrize(
    "target", ["authority.grant", "grant.actions", "profile", "role", "relation.authority"]
)
def test_groups_never_map_to_authority(target: str) -> None:
    with pytest.raises(IntegrationError):
        parse_rules([{"source": "groups", "target": target}])


def test_multi_idp_routing_is_deterministic() -> None:
    rules = [
        RoutingRule("1", RouteKind.DOMAIN, "acme.com", "federation_connection:entra"),
        RoutingRule("2", RouteKind.DOMAIN, "eu.acme.com", "federation_connection:okta"),
        RoutingRule("3", RouteKind.APPLICATION, "app-1", "federation_connection:ping"),
    ]
    assert resolve(rules, application_id=None, login_domain="eu.acme.com").rule_id == "2"  # type: ignore[union-attr]
    assert resolve(rules, application_id=None, login_domain="x.uk.acme.com").rule_id == "1"  # type: ignore[union-attr]
    assert resolve(rules, application_id="APP-1", login_domain="eu.acme.com").rule_id == "3"  # type: ignore[union-attr]
    assert resolve(rules, application_id=None, login_domain="evil-acme.com") is None


def test_connector_health_and_expiry_alerts() -> None:
    assert health_after(Health.HEALTHY, ok=False, consecutive_failures=1) is Health.DEGRADED
    assert health_after(Health.DEGRADED, ok=False, consecutive_failures=5) is Health.FAILED
    assert health_after(Health.FAILED, ok=True, consecutive_failures=0) is Health.HEALTHY
    assert health_after(Health.QUARANTINED, ok=True, consecutive_failures=0) is Health.QUARANTINED
    now = datetime.now(UTC)
    assert expiry_alert(now + timedelta(days=6), now, frozenset()) == 7
    assert expiry_alert(now + timedelta(days=6), now, frozenset({7, 14, 30})) is None
    assert expiry_alert(now + timedelta(days=40), now, frozenset()) is None


def test_overlay_verdict_obligations_context_scope_and_suspension() -> None:
    from zid_platform.modules.onboarding.domain.plan import (
        Enforcement,
        ExitCriteria,
        overlay_verdict,
    )

    base = {
        "decision_allow": True,
        "risk": 2,
        "obligations": ["DECISION_RECEIPT", "COMMIT_RECHECK"],
        "supported_obligations": frozenset({"COMMIT_RECHECK"}),
        "required_context": frozenset({"purpose"}),
        "present_context": frozenset({"purpose"}),
        "in_enforcing_scope": True,
    }
    ok = overlay_verdict(tenant_status="ACTIVE", **base)  # type: ignore[arg-type]
    assert ok.allow and ok.enforcement is Enforcement.ENFORCE  # the receipt is the platform's
    v = overlay_verdict(tenant_status="ACTIVE", **{**base, "obligations": ["STEP_UP"]})  # type: ignore[arg-type]
    assert not v.allow and v.reason_codes == ("PEP_OBLIGATION_UNSUPPORTED",)  # OVL-17
    v = overlay_verdict(tenant_status="ACTIVE", **{**base, "present_context": frozenset()})  # type: ignore[arg-type]
    assert not v.allow and v.reason_codes == ("PEP_CONTEXT_INCOMPLETE",)
    v = overlay_verdict(tenant_status="PILOT", **{**base, "in_enforcing_scope": False})  # type: ignore[arg-type]
    assert v.enforcement is Enforcement.EVALUATE_ONLY
    v = overlay_verdict(tenant_status="SUSPENDED", **base)  # type: ignore[arg-type]
    assert not v.allow and v.enforcement is Enforcement.ENFORCE
    from zid_platform.modules.onboarding.domain.gates import OnboardingError

    with pytest.raises(OnboardingError):
        ExitCriteria.parse({"max_false_allows": 1})


def test_shadow_latency_gate_blocks_above_the_p95_budget() -> None:
    from zid_platform.modules.onboarding.domain.plan import ExitCriteria, ShadowStats, shadow_gates

    def latency(p95: float | None) -> str:
        stats = ShadowStats(200, 200, 10, 10, 200, 0, 0, p95)
        gates = {g.gate_id: g for g in shadow_gates(stats, ExitCriteria())}
        return str(gates["shadow.latency_p95"].status)

    assert latency(59.9) == "PASS"
    assert latency(60.0) == "PASS"
    assert latency(60.1) == "FAIL"  # OVL-20: a slower shadow path never reaches pilot
    assert latency(None) == "PASS"  # no measured decisions: coverage gates decide

"""Organizational root of trust (ZID-ROOT-001 acceptance ROOT-04..24; FR-ORG-001..008; P0-10)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from zid_platform.modules.organization.domain.roots import (
    Assurance,
    Fact,
    OrgEvent,
    RootAssertion,
    RootError,
    RootRequest,
    RootStatus,
    RootType,
    SourceEvent,
    apply_event,
    resolve_root,
    transition,
)

NOW = datetime(2026, 10, 12, tzinfo=UTC)
R3, R2, R1 = 3, 2, 1


def root(rid: str, root_type: RootType, facts: set[Fact], **kw: object) -> RootAssertion:
    base: dict[str, object] = {
        "root_id": rid,
        "organization_id": "org_acme",
        "holder_principal_id": "prn_cfo",
        "root_type": root_type,
        "facts": frozenset(facts),
        "assurance": Assurance.AR3,
        "status": RootStatus.ACTIVE,
        "jurisdiction": "GB",
        "valid_from": NOW - timedelta(days=100),
        "valid_to": None,
        "fresh_until": NOW + timedelta(hours=12),
    } | kw
    return RootAssertion(**base)  # type: ignore[arg-type]


REGISTRY = root("r1_registry", RootType.REGISTRY, {Fact.ENTITY_EXISTENCE, Fact.OFFICIAL_ROLE})
MATRIX = root(
    "r2_matrix",
    RootType.SIGNING_MATRIX,
    {Fact.SIGNING_POWER},
    assurance=Assurance.AR2,
    scope_actions=frozenset({"payment.release"}),
    value_limit=Decimal(250_000),
    currency="GBP",
)
ROOTS = [REGISTRY, MATRIX]


def req(
    action: str = "payment.release", value: str = "10000", risk: int = R2, **kw: object
) -> RootRequest:
    base: dict[str, object] = {
        "organization_id": "org_acme",
        "holder_principal_id": "prn_cfo",
        "action": action,
        "at": NOW,
        "risk_level": risk,
        "jurisdiction": "GB",
        "value": Decimal(value),
        "currency": "GBP",
    } | kw
    return RootRequest(**base)  # type: ignore[arg-type]


def test_three_part_root_test_with_an_explicit_composite() -> None:
    """Registry proves entity + role; the signing matrix proves the payment authority."""
    res = resolve_root(req(), ROOTS)
    assert res.valid and set(res.root_ids) == {"r1_registry", "r2_matrix"}
    assert res.valid_until == REGISTRY.fresh_until


def test_registry_alone_proves_the_role_but_not_signing_power() -> None:
    """ROOT-16 source competence: a register can't be misused to prove a payment limit."""
    claims_too_much = replace(
        REGISTRY,
        facts=frozenset({Fact.ENTITY_EXISTENCE, Fact.OFFICIAL_ROLE, Fact.SIGNING_POWER}),
        scope_actions=frozenset({"payment.release"}),
    )
    res = resolve_root(req(), [claims_too_much])
    assert not res.valid and "AUTHORITY_SCOPE_EXCEEDED" in res.codes


def test_out_of_scope_action_or_value_is_denied() -> None:
    """ROOT-09/11: a verified role still doesn't authorize actions or amounts outside the instrument."""
    assert "AUTHORITY_SCOPE_EXCEEDED" in resolve_root(req(action="contract.sign"), ROOTS).codes
    assert "AUTHORITY_SCOPE_EXCEEDED" in resolve_root(req(value="250000.01"), ROOTS).codes
    assert "AUTHORITY_SCOPE_EXCEEDED" in resolve_root(req(currency="EUR"), ROOTS).codes


def test_psc_control_never_becomes_signing_authority() -> None:
    """ROOT-07: beneficial owner with no signatory basis can't sign."""
    psc = root(
        "r_psc",
        RootType.REGISTRY,
        {Fact.ENTITY_EXISTENCE, Fact.CONTROL},
        holder_principal_id="prn_owner",
    )
    res = resolve_root(req(holder_principal_id="prn_owner"), [psc, MATRIX])
    assert not res.valid and {"ROOT_ROLE_UNVERIFIED", "AUTHORITY_SCOPE_EXCEEDED"} <= set(res.codes)


def test_parent_company_roots_cannot_bind_a_subsidiary() -> None:
    """ROOT-08 / ROOT-22: authority is entity-specific."""
    res = resolve_root(req(organization_id="org_acme_subsidiary"), ROOTS)
    assert not res.valid and "ROOT_ENTITY_UNVERIFIED" in res.codes


def test_stale_root_fails_r2_r3_but_not_r1() -> None:
    """ROOT-17 and §16.1: STALE is not REVOKED, but high risk fails closed."""
    stale = [replace(REGISTRY, fresh_until=NOW), MATRIX]
    assert resolve_root(req(risk=R3, r3_on_ar2_permitted=True), stale).codes[0] in (
        "ROOT_ENTITY_UNVERIFIED",
        "AUTHORITY_STALE",
    )
    assert "AUTHORITY_STALE" in resolve_root(req(risk=R2), stale).codes
    assert resolve_root(req(risk=R1), stale).valid


def test_conflicted_source_blocks_r3() -> None:
    """ROOT-18."""
    conflicted = [replace(REGISTRY, status=RootStatus.CONFLICTED), MATRIX]
    assert resolve_root(req(risk=R3, r3_on_ar2_permitted=True), conflicted).codes == (
        "ROOT_CONFLICTED",
    )


def test_ar2_roots_r3_only_where_policy_permits() -> None:
    assert "ASSURANCE_INSUFFICIENT" in resolve_root(req(risk=R3), ROOTS).codes
    assert resolve_root(req(risk=R3, r3_on_ar2_permitted=True), ROOTS).valid


def test_ar0_self_assertion_never_roots_material_authority() -> None:
    """FR-ORG-004."""
    selfie = [replace(REGISTRY, assurance=Assurance.AR0), replace(MATRIX, assurance=Assurance.AR0)]
    assert not resolve_root(req(risk=0), selfie).valid


def test_power_of_attorney_bound_to_jurisdiction_and_time() -> None:
    """ROOT-15."""
    poa = root(
        "r_poa",
        RootType.POWER_OF_ATTORNEY,
        {Fact.DELEGATION_BASIS},
        assurance=Assurance.AR2,
        holder_principal_id="prn_attorney",
        scope_actions=frozenset({"contract.sign"}),
        valid_to=NOW + timedelta(days=30),
    )
    reg = replace(REGISTRY, holder_principal_id="prn_attorney")
    ok = req(action="contract.sign", holder_principal_id="prn_attorney", value="0")
    assert resolve_root(ok, [reg, poa]).valid
    assert not resolve_root(replace(ok, jurisdiction="DE"), [reg, poa]).valid
    assert not resolve_root(replace(ok, at=NOW + timedelta(days=31)), [reg, poa]).valid


# ---------------------------------------------------------------- lifecycle


def by_id(*rs: RootAssertion) -> dict[str, RootAssertion]:
    return {r.root_id: r for r in rs}


def test_officer_cessation_revokes_that_holders_roots() -> None:
    """ROOT-06."""
    out = apply_event(
        SourceEvent("ev1", "org_acme", OrgEvent.OFFICER_CESSATION, "prn_cfo"),
        by_id(*ROOTS),
        processed_event_ids=frozenset(),
        organization_active=True,
    )
    assert {r.status for r in out.roots.values()} == {RootStatus.REVOKED}
    assert not resolve_root(req(), out.roots.values()).valid


def test_officer_appointment_only_creates_a_candidate() -> None:
    """ROOT-05: no automatic grant, no new root activated."""
    out = apply_event(
        SourceEvent("ev2", "org_acme", OrgEvent.OFFICER_APPOINTMENT, "prn_new"),
        by_id(*ROOTS),
        processed_event_ids=frozenset(),
        organization_active=True,
    )
    assert out.candidate_holders == ("prn_new",) and out.roots == by_id(*ROOTS)


def test_dissolution_blocks_authority_and_restoration_does_not_reactivate() -> None:
    """ROOT-04 / ROOT-21."""
    dissolved = apply_event(
        SourceEvent("ev3", "org_acme", OrgEvent.DISSOLUTION),
        by_id(*ROOTS),
        processed_event_ids=frozenset(),
        organization_active=True,
    )
    assert not dissolved.organization_can_originate_authority
    assert not resolve_root(req(), dissolved.roots.values()).valid
    restored = apply_event(
        SourceEvent("ev4", "org_acme", OrgEvent.RESTORATION),
        dissolved.roots,
        processed_event_ids=frozenset({"ev3"}),
        organization_active=False,
    )
    assert restored.organization_can_originate_authority
    assert all(r.status is RootStatus.SUSPENDED for r in restored.roots.values())
    assert not resolve_root(req(), restored.roots.values()).valid


def test_merger_forces_revalidation() -> None:
    """ROOT-20: old grants never silently transfer."""
    out = apply_event(
        SourceEvent("ev5", "org_acme", OrgEvent.MERGER),
        by_id(*ROOTS),
        processed_event_ids=frozenset(),
        organization_active=True,
    )
    assert all(r.status is RootStatus.STALE for r in out.roots.values())
    assert not resolve_root(req(risk=R2), out.roots.values()).valid


def test_duplicate_source_events_are_idempotent() -> None:
    """ROOT-24."""
    out = apply_event(
        SourceEvent("ev1", "org_acme", OrgEvent.OFFICER_CESSATION, "prn_cfo"),
        by_id(*ROOTS),
        processed_event_ids=frozenset({"ev1"}),
        organization_active=True,
    )
    assert not out.applied and out.roots == by_id(*ROOTS)


def test_ownership_change_never_changes_signatories() -> None:
    out = apply_event(
        SourceEvent("ev6", "org_acme", OrgEvent.OWNERSHIP_CHANGE),
        by_id(*ROOTS),
        processed_event_ids=frozenset(),
        organization_active=True,
    )
    assert out.roots == by_id(*ROOTS)


def test_conflict_state_machine() -> None:
    """§17.1: CONFLICTED resolves to ACTIVE, SUPERSEDED or REVOKED; terminal states never return."""
    assert transition(RootStatus.ACTIVE, RootStatus.CONFLICTED) is RootStatus.CONFLICTED
    for target in (RootStatus.ACTIVE, RootStatus.SUPERSEDED, RootStatus.REVOKED):
        assert transition(RootStatus.CONFLICTED, target) is target
    for terminal in (RootStatus.REVOKED, RootStatus.SUPERSEDED, RootStatus.EXPIRED):
        with pytest.raises(RootError):
            transition(terminal, RootStatus.ACTIVE)

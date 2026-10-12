"""Canonical principal, identifier and relationship rules (ZID-DOM-001 DM-AT-01/02/03/07/12/13/15)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from zid_platform.modules.authority.domain.algebra import Request
from zid_platform.modules.authority.domain.grants import authorize
from zid_platform.modules.principal.domain.principal import (
    DomainError,
    History,
    Identifier,
    Principal,
    PrincipalStatus,
    PrincipalType,
    UniquenessScope,
    change_status,
    may_link_same_human,
    normalize,
    uniqueness_key,
)
from zid_platform.modules.relationship.domain.relationship import (
    Relationship,
    RelationshipError,
    RelationshipStatus,
    RelationshipType,
    RevisionClock,
    mutate,
    projection_fresh_enough,
)

NOW = datetime(2026, 10, 12, tzinfo=UTC)
ALICE = Principal("prn_alice", "ten_1", PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")


def ident(
    tenant: str, raw: str, ns: str = "email", scope: UniquenessScope = UniquenessScope.TENANT
) -> Identifier:
    return Identifier(f"id_{tenant}_{raw}", f"prn_{tenant}", tenant, ns, normalize(ns, raw), scope)


def test_principal_id_survives_status_and_identifier_changes() -> None:
    """DM-AT-01: suspension, reactivation and new identifiers never change principal_id."""
    p = change_status(change_status(ALICE, PrincipalStatus.SUSPENDED), PrincipalStatus.ACTIVE)
    assert (
        p.principal_id == ALICE.principal_id and p.security_revision == ALICE.security_revision + 2
    )


def test_same_email_in_two_tenants_never_merges() -> None:
    """DM-AT-02."""
    a, b = ident("ten_1", "Alice@Example.com"), ident("ten_2", "Alice@Example.com")
    assert uniqueness_key(a) != uniqueness_key(b)
    assert not may_link_same_human(a, b)
    assert may_link_same_human(a, ident("ten_1", "Alice@EXAMPLE.com"))  # domain is case-insensitive


def test_email_local_part_case_is_preserved_and_bad_values_rejected() -> None:
    assert normalize("email", " Alice@Example.COM ") == "Alice@example.com"
    assert normalize("tel", "+44 20 7946 0000") == "+442079460000"
    for ns, raw in (("email", "no-at-sign"), ("tel", "020 7946 0000"), ("employee_id", "  ")):
        with pytest.raises(DomainError):
            normalize(ns, raw)


def test_lifecycle_is_a_state_machine_and_erasure_is_final() -> None:
    """DM-AT-15."""
    erased = change_status(ALICE, PrincipalStatus.ERASED)
    for target in PrincipalStatus:
        with pytest.raises(DomainError):
            change_status(erased, target)
    with pytest.raises(DomainError) as e:
        change_status(ALICE, PrincipalStatus.SUPERSEDED)
    assert e.value.code == "SUPERSEDED_BY_REQUIRED"


def test_type_rules() -> None:
    """§4.2: agents need a sponsor; organizations need a profile; tenant-owned principals need a tenant."""
    with pytest.raises(DomainError):
        Principal("a", "ten_1", PrincipalType.AI_AGENT, PrincipalStatus.ACTIVE, "uk-1")
    with pytest.raises(DomainError):
        Principal("o", "ten_1", PrincipalType.ORGANIZATION, PrincipalStatus.ACTIVE, "uk-1")
    with pytest.raises(DomainError):
        Principal("h", None, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")


@settings(max_examples=200, deadline=None)
@given(st.lists(st.sampled_from(list(PrincipalStatus)), max_size=20))
def test_security_revision_never_decreases(path: list[PrincipalStatus]) -> None:
    """INV-11 over any sequence of attempted transitions."""
    p = ALICE
    for target in path:
        before = p.security_revision
        try:
            p = change_status(p, target, superseded_by="prn_other")
        except DomainError:
            continue
        assert p.security_revision > before


def test_late_correction_does_not_rewrite_what_a_past_decision_saw() -> None:
    """DM-AT-07 bitemporal correction."""
    h: History[str] = History().record(
        "Acme Ltd",
        valid_from=NOW - timedelta(days=30),
        valid_to=None,
        recorded_at=NOW - timedelta(days=30),
    )
    decision_time = NOW - timedelta(days=5)
    h = h.record(
        "Acme Holdings Ltd", valid_from=NOW - timedelta(days=10), valid_to=None, recorded_at=NOW
    )
    assert (
        h.as_of(valid_at=decision_time, known_at=decision_time) == "Acme Ltd"
    )  # what we believed then
    assert h.as_of(valid_at=decision_time, known_at=NOW) == "Acme Holdings Ltd"  # what we know now
    assert len(h.versions) == 2  # nothing deleted
    with pytest.raises(DomainError):
        h.record("x", valid_from=NOW, valid_to=None, recorded_at=NOW - timedelta(days=1))


# ---------------------------------------------------------------- relationships

EDGE = Relationship(
    "rel_1",
    "ten_1",
    "prn_alice",
    "ten_1",
    RelationshipType.MEMBER_OF,
    "org_acme",
    "ten_1",
    "scim",
    assurance=2,
    status=RelationshipStatus.ACTIVE,
    valid_from=NOW - timedelta(days=1),
    valid_to=None,
    relation_revision=7,
)


def test_relationship_is_not_authority() -> None:
    """DM-AT-03 / R-1: MEMBER_OF + MANAGES with no grant still denies a protected action."""
    manages = replace(EDGE, relationship_id="rel_2", relationship_type=RelationshipType.MANAGES)
    assert EDGE.is_current(NOW) and manages.is_current(NOW)
    d = authorize(
        Request("payment.release", "payment:1", NOW),
        [],
        tenant_id="ten_1",
        grants={},
        roots={},
        risk_level=3,
    )
    assert not d.allowed and d.codes == ("AUTHORITY_MISSING",)


def test_cross_tenant_reference_fails_before_persistence() -> None:
    """DM-AT-12."""
    with pytest.raises(RelationshipError):
        replace(EDGE, object_tenant_id="ten_2")
    assert (
        replace(EDGE, object_tenant_id="ten_2", cross_tenant_trust_id="trust_9").object_tenant_id
        == "ten_2"
    )


def test_every_mutation_advances_the_tenant_revision() -> None:
    clock = RevisionClock("ten_1", current=7)
    rel, clock = mutate(EDGE, RelationshipStatus.REVOKED, clock)
    assert rel.relation_revision == clock.current == 8 and not rel.is_current(NOW)
    with pytest.raises(RelationshipError):
        mutate(rel, RelationshipStatus.ACTIVE, clock)  # REVOKED is terminal


def test_stale_projection_cannot_serve_r2_r3() -> None:
    """DM-AT-13 / AUTHZ-07."""
    assert not projection_fresh_enough(projection_revision=7, required_revision=8, risk_level=2)
    assert projection_fresh_enough(projection_revision=8, required_revision=8, risk_level=3)
    assert projection_fresh_enough(projection_revision=7, required_revision=8, risk_level=1)

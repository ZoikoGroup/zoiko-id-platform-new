"""Grant chains, roots, stitching and continuity (AUTHZ-03..06, MAND-01..05, AT-03, AT-07; DEC-C1, DEC-C2)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from zid_platform.modules.authority.domain.algebra import (
    ANY,
    Authority,
    Request,
    ValueLimit,
    Window,
    scope,
)
from zid_platform.modules.authority.domain.grants import (
    AuthorityError,
    AuthorityRoot,
    ContinuityEffect,
    ContinuityMode,
    Grant,
    GrantStatus,
    GrantType,
    RootAssurance,
    RootStatus,
    authorize,
    on_officeholder_change,
    on_personal_authority_ended,
    resolve_chain,
    transition,
    validate_child_grant,
)

NOW = datetime(2026, 10, 12, tzinfo=UTC)
YEAR = Window(NOW - timedelta(days=30), NOW + timedelta(days=335))
R3, R2, R1 = 3, 2, 1

ROOT = AuthorityRoot(
    root_id="root_cfo",
    tenant_id="ten_1",
    organization_id="org_1",
    holder_principal_id="prn_cfo",
    assurance=RootAssurance.AR3,
    status=RootStatus.ACTIVE,
    window=YEAR,
    fresh_until=NOW + timedelta(days=7),
)


def grant(gid: str, **kw: object) -> Grant:
    base: dict[str, object] = {
        "grant_id": gid,
        "tenant_id": "ten_1",
        "grant_type": GrantType.ROLE_ISSUED,
        "grantor_principal_id": "prn_cfo",
        "grantee_principal_id": "prn_controller",
        "represented_principal_id": "org_1",
        "authority": Authority(
            actions=scope("invoice.approve", "payment.release"),
            resources=scope("invoice:*", "payment:*"),
            purposes=scope("accounts_payable"),
            value=ValueLimit(Decimal(50_000), "GBP"),
            window=YEAR,
        ),
        "continuity_mode": ContinuityMode.ROLE_CONTINUOUS,
        "status": GrantStatus.ACTIVE,
        "issued_at": NOW - timedelta(days=1),
        "root_id": "root_cfo",
        "delegation_allowed": True,
        "max_delegation_depth": 2,
    } | kw
    return Grant(**base)  # type: ignore[arg-type]


TOP = grant("g_top")
CHILD = grant(
    "g_child",
    grantor_principal_id="prn_controller",
    grantee_principal_id="agent_ap",
    root_id=None,
    parent_grant_id="g_top",
    max_delegation_depth=1,
    grant_type=GrantType.AGENT_DELEGATION,
    authority=Authority(
        actions=scope("invoice.approve"),
        resources=scope("invoice:*"),
        purposes=scope("accounts_payable"),
        value=ValueLimit(Decimal(5_000), "GBP"),
        window=YEAR,
    ),
)
GRANTS = {g.grant_id: g for g in (TOP, CHILD)}
ROOTS = {ROOT.root_id: ROOT}


def req(
    action: str = "invoice.approve", resource: str = "invoice:7", amount: str = "100", **kw: object
) -> Request:
    return Request(
        action=action,
        resource=resource,
        at=NOW,
        purpose="accounts_payable",
        value=ValueLimit(Decimal(amount), "GBP"),
        **kw,
    )  # type: ignore[arg-type]


def test_valid_chain_resolves_to_its_root() -> None:
    res = resolve_chain(
        "g_child", tenant_id="ten_1", grants=GRANTS, roots=ROOTS, now=NOW, risk_level=R3
    )
    assert res.valid and res.chain_ids == ("g_child", "g_top") and res.root_id == "root_cfo"
    assert authorize(
        req(), ["g_child"], tenant_id="ten_1", grants=GRANTS, roots=ROOTS, risk_level=R3
    ).allowed


def test_default_deny_without_candidates() -> None:
    d = authorize(req(), [], tenant_id="ten_1", grants=GRANTS, roots=ROOTS, risk_level=R1)
    assert not d.allowed and d.codes == ("AUTHORITY_MISSING",)


def test_child_cannot_exceed_its_own_limits() -> None:
    d = authorize(
        req(amount="6000"),
        ["g_child"],
        tenant_id="ten_1",
        grants=GRANTS,
        roots=ROOTS,
        risk_level=R3,
    )
    assert not d.allowed and "AUTHORITY_SCOPE_EXCEEDED" in d.codes


def test_no_privilege_stitching() -> None:
    """AT-03 / AUTHZ-04 / MAND-04: action grant + resource grant never combine into A on B."""
    only_payment_action = grant(
        "g_a",
        authority=Authority(
            actions=scope("payment.release"), resources=scope("invoice:*"), window=YEAR
        ),
    )
    only_payment_resource = grant(
        "g_b",
        authority=Authority(
            actions=scope("invoice.read"), resources=scope("payment:*"), window=YEAR
        ),
    )
    gs = {g.grant_id: g for g in (only_payment_action, only_payment_resource)}
    d = authorize(
        Request("payment.release", "payment:9", NOW),
        ["g_a", "g_b"],
        tenant_id="ten_1",
        grants=gs,
        roots=ROOTS,
        risk_level=R3,
    )
    assert not d.allowed and "AUTHORITY_SCOPE_EXCEEDED" in d.codes


def test_cycle_is_rejected() -> None:
    """MAND-05."""
    a = replace(CHILD, grant_id="c1", parent_grant_id="c2")
    b = replace(CHILD, grant_id="c2", parent_grant_id="c1")
    res = resolve_chain(
        "c1", tenant_id="ten_1", grants={"c1": a, "c2": b}, roots=ROOTS, now=NOW, risk_level=R1
    )
    assert res.codes == ("AUTHORITY_CYCLE",)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"status": RootStatus.REVOKED}, "AUTHORITY_REVOKED"),
        ({"fresh_until": NOW}, "AUTHORITY_STALE"),
        ({"assurance": RootAssurance.AR2}, "ASSURANCE_INSUFFICIENT"),  # AR-2 cannot root R3
        ({"holder_principal_id": "prn_someone_else"}, "GRANTOR_AUTHORITY_MISSING"),
        ({"tenant_id": "ten_2"}, "AUTHORITY_CROSS_TENANT"),
    ],
)
def test_root_must_be_current_sufficient_and_held_by_the_grantor(
    change: dict[str, object], code: str
) -> None:
    """AUTHZ-05 root continuity; AR classes from AUTHZ §6."""
    roots = {"root_cfo": replace(ROOT, **change)}  # type: ignore[arg-type]
    res = resolve_chain(
        "g_child", tenant_id="ten_1", grants=GRANTS, roots=roots, now=NOW, risk_level=R3
    )
    assert res.codes == (code,)


def test_ar0_never_roots_material_authority() -> None:
    roots = {"root_cfo": replace(ROOT, assurance=RootAssurance.AR0)}
    res = resolve_chain(
        "g_top", tenant_id="ten_1", grants=GRANTS, roots=roots, now=NOW, risk_level=0
    )
    assert res.codes == ("ASSURANCE_INSUFFICIENT",)


def test_revoked_parent_invalidates_child() -> None:
    gs = GRANTS | {"g_top": replace(TOP, status=GrantStatus.REVOKED)}
    assert not resolve_chain(
        "g_child", tenant_id="ten_1", grants=gs, roots=ROOTS, now=NOW, risk_level=R1
    ).valid


def test_missing_parent_is_never_skipped() -> None:
    gs = {"g_child": CHILD}
    res = resolve_chain(
        "g_child", tenant_id="ten_1", grants=gs, roots=ROOTS, now=NOW, risk_level=R1
    )
    assert res.codes == ("AUTHORITY_MISSING",)


def test_revalidation_pending_denies_with_authority_stale() -> None:
    gs = GRANTS | {"g_top": replace(TOP, status=GrantStatus.REVALIDATION_PENDING)}
    res = resolve_chain(
        "g_child", tenant_id="ten_1", grants=gs, roots=ROOTS, now=NOW, risk_level=R2
    )
    assert res.codes == ("AUTHORITY_STALE",)


# ---------------------------------------------------------------- issuance gate


def test_valid_child_is_accepted() -> None:
    assert validate_child_grant(CHILD, grants=GRANTS, roots=ROOTS, now=NOW, risk_level=R3) == []


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (
            {"authority": replace(CHILD.authority, value=ValueLimit(Decimal(60_000), "GBP"))},
            "GRANT_SCOPE_WIDENING",
        ),
        ({"authority": replace(CHILD.authority, actions=ANY)}, "GRANT_SCOPE_WIDENING"),
        (
            {"authority": replace(CHILD.authority, window=Window(NOW, NOW + timedelta(days=999)))},
            "GRANT_SCOPE_WIDENING",
        ),
        ({"max_delegation_depth": 2}, "GRANT_DEPTH_EXCEEDED"),
        (
            {"grantor_principal_id": "prn_random"},
            "GRANTOR_AUTHORITY_MISSING",
        ),  # no self-certified authority
        ({"continuity_mode": ContinuityMode.ORGANIZATION_CONTINUOUS}, "GRANT_SCOPE_WIDENING"),
        ({"tenant_id": "ten_2"}, "AUTHORITY_CROSS_TENANT"),
    ],
)
def test_issuance_gate_rejects_widening(change: dict[str, object], code: str) -> None:
    child = replace(CHILD, **change)  # type: ignore[arg-type]
    assert code in validate_child_grant(child, grants=GRANTS, roots=ROOTS, now=NOW, risk_level=R3)


def test_parent_that_forbids_delegation_blocks_children() -> None:
    gs = GRANTS | {"g_top": replace(TOP, delegation_allowed=False)}
    assert "GRANT_DELEGATION_NOT_ALLOWED" in validate_child_grant(
        CHILD, grants=gs, roots=ROOTS, now=NOW, risk_level=R3
    )


@settings(max_examples=200, deadline=None)
@given(st.integers(1, 8))
def test_depth_strictly_decreases_until_delegation_stops(start_depth: int) -> None:
    """MAND-18: each generation lowers depth; at 0 no further child validates."""
    gs = {
        "g0": replace(
            TOP, grant_id="g0", max_delegation_depth=start_depth, grantee_principal_id="p0"
        )
    }
    parent_id, depth = "g0", start_depth
    for i in range(1, start_depth + 2):
        child = replace(
            CHILD,
            grant_id=f"g{i}",
            parent_grant_id=parent_id,
            grantor_principal_id=f"p{i - 1}",
            grantee_principal_id=f"p{i}",
            max_delegation_depth=depth - 1,
            delegation_allowed=depth - 1 > 0,
        )
        problems = validate_child_grant(child, grants=gs, roots=ROOTS, now=NOW, risk_level=R1)
        if depth == 0 or not gs[parent_id].delegation_allowed:
            assert problems
            break
        assert problems == [], problems
        gs[child.grant_id] = child
        parent_id, depth = child.grant_id, depth - 1
    assert depth == 0


# ---------------------------------------------------------------- lifecycle and continuity


def test_terminal_grant_states_never_return_to_active() -> None:
    for terminal in (GrantStatus.REVOKED, GrantStatus.EXPIRED, GrantStatus.SUPERSEDED):
        with pytest.raises(AuthorityError):
            transition(terminal, GrantStatus.ACTIVE)
    assert transition(GrantStatus.SUSPENDED, GrantStatus.ACTIVE) is GrantStatus.ACTIVE


def test_officer_removal_follows_each_continuity_mode() -> None:
    """MANDATE §5.1 / DEC-C2 §4."""
    person = replace(TOP, grant_id="t_person", continuity_mode=ContinuityMode.PERSON_BOUND)
    role = replace(TOP, grant_id="t_role")
    org = replace(TOP, grant_id="t_org", continuity_mode=ContinuityMode.ORGANIZATION_CONTINUOUS)
    under_person = replace(
        CHILD,
        grant_id="c_person",
        parent_grant_id="t_person",
        continuity_mode=ContinuityMode.PERSON_BOUND,
    )
    under_role = replace(CHILD, grant_id="c_role", parent_grant_id="t_role")
    gs = {g.grant_id: g for g in (person, role, org, under_person, under_role)}
    effects = on_officeholder_change("root_cfo", gs)
    assert effects["t_person"] is ContinuityEffect.INEFFECTIVE
    assert effects["c_person"] is ContinuityEffect.INEFFECTIVE
    assert effects["t_role"] is ContinuityEffect.REVALIDATE_ROLE
    assert effects["c_role"] is ContinuityEffect.REVALIDATE_ROLE
    assert effects["t_org"] is ContinuityEffect.REVALIDATE_ORGANIZATION


def test_no_descendant_survives_a_broken_person_bound_ancestor() -> None:
    """AT-07 / MAND-02: personal authority ends -> every dependent grant goes, whatever its mode."""
    top = replace(TOP, continuity_mode=ContinuityMode.PERSON_BOUND)
    child = replace(CHILD, continuity_mode=ContinuityMode.PERSON_BOUND)
    grandchild = replace(
        CHILD,
        grant_id="g_grand",
        parent_grant_id="g_child",
        grantor_principal_id="agent_ap",
        grantee_principal_id="agent_sub",
        max_delegation_depth=0,
        continuity_mode=ContinuityMode.PERSON_BOUND,
    )
    gs = {g.grant_id: g for g in (top, child, grandchild)}
    assert on_personal_authority_ended("prn_cfo", gs) == {"g_top", "g_child", "g_grand"}
    res = resolve_chain(
        "g_grand", tenant_id="ten_1", grants=gs, roots=ROOTS, now=NOW, risk_level=R1
    )
    assert res.valid and {"prn_cfo", "prn_controller", "agent_ap"} <= res.person_bindings


def test_role_continuous_child_cannot_hang_under_a_person_bound_parent() -> None:
    """DEC-C2 §3: surviving continuity needs every link to support it."""
    gs = GRANTS | {"g_top": replace(TOP, continuity_mode=ContinuityMode.PERSON_BOUND)}
    assert "GRANT_SCOPE_WIDENING" in validate_child_grant(
        CHILD, grants=gs, roots=ROOTS, now=NOW, risk_level=R3
    )

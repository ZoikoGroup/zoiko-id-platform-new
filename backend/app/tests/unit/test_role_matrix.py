"""Role by feature permissions (ZID-ENG-DEC-007 acceptance tests 01-10, policy level): deny by default, no role is enough alone, nothing
customer-side can touch what is reserved to Zoiko iD, and one unknown predicate is a HOLD, never an ALLOW."""

from __future__ import annotations

from dataclasses import replace

import pytest
from zid_platform.modules.tenant.domain.role_matrix import (
    RESERVED,
    ROLES,
    Facts,
    MatrixError,
    Mode,
    Outcome,
    Row,
    build,
    check_direct_role_grant,
    decide,
    effective_rights,
)
from zid_platform.modules.tenant.infrastructure.role_matrix import parse, role_matrix

M = role_matrix()


def good(*roles: str, **over: object) -> Facts:
    base = Facts(
        authenticated=True, membership_active=True, roles=roles, resource_in_scope=True, policy_current=True, region_ok=True,
        trust_ok=True, delegation_ok=True, session_ok=True, entitlement_ok=True, workflow_ok=True,
        actor_person="p-actor", initiator_person="p-initiator",
    )  # fmt: skip
    return replace(base, **over)  # type: ignore[arg-type]


def cells(row_id: str) -> dict[str, str]:
    return {r: m.value for r, m in M.rows[row_id].cells.items()}


# ---- the matrix is the document's -------------------------------------------------------------------------------------


def test_the_matrix_has_the_documents_six_matrices_and_sixty_eight_actions() -> None:
    assert M.version == "ZID-ENG-DEC-007-v1.0" and len(M.rows) == 68 and len(M.policy_hash) == 64
    counts = {k: sum(1 for r in M.rows.values() if r.matrix == k) for k in M.titles}
    assert counts == {"A": 11, "B": 12, "C": 11, "D": 11, "E": 11, "F": 12}
    assert tuple(sorted(M.rows[next(iter(M.rows))].cells)) == tuple(sorted(ROLES))


@pytest.mark.parametrize(
    ("row", "owner", "admin", "approver", "operator", "viewer"),
    [
        ("a.edit-ordinary-organization-profile", "X", "X", "-", "S", "-"),
        ("a.change-registered-entity-legal-jurisdiction", "S", "S", "A", "-", "-"),
        ("a.approve-privileged-role-grant", "A", "-", "A", "-", "-"),
        ("a.approve-ownership-transfer", "-", "-", "A", "-", "-"),
        ("b.upload-organization-kyb-evidence", "X", "X", "R", "X", "-"),
        ("b.approve-internal-representative-authority", "A", "-", "A", "-", "-"),
        ("c.revoke-ordinary-user-sessions", "X", "X", "-", "-", "-"),
        ("c.manage-own-passkeys-authenticators", "T", "T", "T", "T", "T"),
        ("d.revoke-authorized-ordinary-delegation", "X", "X", "-", "-", "-"),
        ("e.rotate-assigned-nonprivileged-api-credential", "X", "X", "-", "X", "-"),
        ("f.change-commercial-subscription-contract", "X", "S", "-", "-", "-"),
        ("f.approve-account-closure-request", "-", "-", "A", "-", "-"),
    ],
)
def test_rows_read_exactly_as_the_record_prints_them(
    row: str, owner: str, admin: str, approver: str, operator: str, viewer: str
) -> None:
    assert cells(row) == {
        "OWNER": owner,
        "ADMIN": admin,
        "APPROVER": approver,
        "OPERATOR": operator,
        "VIEWER": viewer,
    }


def test_exactly_the_reserved_rows_are_denied_to_every_role() -> None:
    denied = {i for i, r in M.rows.items() if all(m is Mode.DENIED for m in r.cells.values())}
    assert denied == RESERVED and len(RESERVED) == 9
    for row_id in (
        "b.issue-override-zoiko-id-verified-status",
        "b.clear-sanctions-pep-or-ubo-exceptions",
        "f.release-legal-hold-override-retention",
    ):
        assert row_id in denied


# ---- a matrix that breaks the record's invariants does not load --------------------------------------------------------


def row(i: str, **over: str) -> Row:
    base = {r: Mode.DENIED for r in ROLES} | {r: Mode(m) for r, m in over.items()}
    return Row(i, "A", i, base, "")


def test_a_matrix_that_widens_what_the_record_reserves_or_restricts_is_refused() -> None:
    reserved = [row(i) for i in RESERVED]
    ok = [*reserved, row("a.x", OWNER="X", ADMIN="X", APPROVER="A", OPERATOR="S", VIEWER="R")]
    assert build("v", {"A": "t"}, ok).rows["a.x"]
    bad_rows = [
        row("a.x", ADMIN="A"),  # an Admin approving
        row("a.x", OPERATOR="A"),
        row("a.x", VIEWER="X"),  # a Viewer mutating
        row("a.x", VIEWER="S"),
        row("a.x", OWNER="T"),  # an own-account right held by only one role
    ]
    for r in bad_rows:
        with pytest.raises(MatrixError):
            build("v", {"A": "t"}, [*reserved, r])
    widened = [
        row(i, OWNER="X") if i == "b.clear-sanctions-pep-or-ubo-exceptions" else row(i)
        for i in RESERVED
    ]
    with pytest.raises(MatrixError, match="reserved"):  # a customer self-clearing sanctions
        build("v", {"A": "t"}, widened)
    with pytest.raises(MatrixError):  # a reserved row quietly dropped
        build("v", {"A": "t"}, reserved[1:])
    with pytest.raises(MatrixError):  # a non-reserved row denied to all is a mistake, not a policy
        build("v", {"A": "t"}, [*reserved, row("a.dead")])
    with pytest.raises(MatrixError):
        build("v", {"A": "t"}, [*reserved, row("a.x", OWNER="X"), row("a.x", OWNER="X")])
    short = Row("a.y", "A", "y", {"OWNER": Mode.EXECUTE}, "")
    with pytest.raises(MatrixError, match="five roles"):
        build("v", {"A": "t"}, [*reserved, short])


def test_an_unknown_permission_key_in_the_file_is_refused() -> None:
    doc = {
        "version": "v",
        "matrices": {
            "A": {
                "title": "t",
                "actions": [{"id": "a.x", "feature": "x", "cells": {r: "Z" for r in ROLES}}],
            }
        },
    }
    with pytest.raises(MatrixError, match="unknown permission key"):
        parse(doc)


# ---- the decision expression --------------------------------------------------------------------------------------------


def test_every_role_mode_and_row_is_allowed_only_where_the_cell_says_so() -> None:
    """Exhaustive: with every other predicate true, ALLOW happens exactly when the role's cell is the requested mode."""
    for row_id, r in M.rows.items():
        for role in ROLES:
            for mode in (Mode.EXECUTE, Mode.PREPARE, Mode.APPROVE, Mode.READ, Mode.OWN):
                d = decide(M, row_id, mode, good(role, own_account=True))
                expected = r.cells[role] is mode and row_id not in RESERVED
                assert d.allowed is expected, (row_id, role, mode)
                if not expected:
                    assert d.outcome is Outcome.DENY and d.reasons


def test_deny_by_default_for_anything_unknown() -> None:
    assert decide(M, "z.not-a-row", Mode.EXECUTE, good("OWNER")).reasons == (
        "ACTION_NOT_IN_MATRIX",
    )
    assert (
        decide(M, "a.edit-ordinary-organization-profile", Mode.DENIED, good("OWNER")).outcome
        is Outcome.DENY
    )
    assert decide(M, "a.edit-ordinary-organization-profile", Mode.EXECUTE, good()).reasons == (
        "ROLE_DOES_NOT_HOLD_PERMISSION",
    )
    assert (
        decide(M, "a.edit-ordinary-organization-profile", Mode.EXECUTE, good("MEMBER")).outcome
        is Outcome.DENY
    )  # no matrix role


EDIT = "a.edit-ordinary-organization-profile"


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("authenticated", "UNAUTHENTICATED"),
        ("membership_active", "MEMBERSHIP_NOT_ACTIVE"),
        ("resource_in_scope", "OUT_OF_SCOPE"),
        ("policy_current", "POLICY_NOT_CURRENT"),
        ("region_ok", "REGION_NOT_PERMITTED"),
        ("trust_ok", "TRUST_STATE_BLOCKS"),
        ("delegation_ok", "DELEGATION_NOT_VALID"),
        ("session_ok", "SESSION_ASSURANCE_INSUFFICIENT"),
        ("entitlement_ok", "ENTITLEMENT_MISSING"),
        ("workflow_ok", "WORKFLOW_STATE_INVALID"),
    ],
)
def test_one_failed_predicate_denies_and_one_unknown_holds_whatever_the_role(
    field: str, code: str
) -> None:
    denied = decide(M, EDIT, Mode.EXECUTE, good("OWNER", **{field: False}))
    assert denied.outcome is Outcome.DENY and denied.reasons == (code,)
    held = decide(M, EDIT, Mode.EXECUTE, good("OWNER", **{field: None}))
    assert held.outcome is Outcome.HOLD and held.reasons == (code,) and held.granted_by_role is None
    other = "workflow_ok" if field != "workflow_ok" else "region_ok"
    mixed = decide(M, EDIT, Mode.EXECUTE, good("OWNER", **{field: False, other: None}))
    assert mixed.outcome is Outcome.DENY and mixed.reasons == (code,)  # a failure outranks a gap


def test_the_decision_names_the_role_and_the_policy_it_rested_on() -> None:
    d = decide(M, EDIT, Mode.EXECUTE, good("VIEWER", "ADMIN"))
    assert (
        d.allowed
        and d.granted_by_role == "ADMIN"
        and d.policy_hash == M.policy_hash
        and d.mode == "X"
    )


def test_acceptance_01_a_viewer_cannot_write_anything() -> None:
    for row_id in M.rows:
        for mode in (Mode.EXECUTE, Mode.PREPARE, Mode.APPROVE):
            assert not decide(M, row_id, mode, good("VIEWER")).allowed, row_id


def test_acceptance_02_an_admin_cannot_promote_approve_or_take_owner() -> None:
    assert not decide(M, "a.approve-privileged-role-grant", Mode.APPROVE, good("ADMIN")).allowed
    assert not decide(
        M, "a.initiate-organization-ownership-transfer", Mode.PREPARE, good("ADMIN")
    ).allowed
    assert (
        check_direct_role_grant("OWNER", actor="admin", target="someone")
        == "PRIVILEGED_ROLE_NEEDS_APPROVAL"
    )
    assert (
        check_direct_role_grant("ADMIN", actor="admin", target="admin") == "SELF_ELEVATION"
    )  # any role, to oneself
    assert check_direct_role_grant("VIEWER", actor="admin", target="admin") == "SELF_ELEVATION"
    assert (
        check_direct_role_grant("APPROVER", actor="owner", target="x")
        == "PRIVILEGED_ROLE_NEEDS_APPROVAL"
    )
    assert (
        check_direct_role_grant("MEMBER", actor="owner", target="x")
        == "ROLE_NOT_DIRECTLY_ASSIGNABLE"
    )
    assert check_direct_role_grant("OPERATOR", actor="owner", target="x") is None
    assert check_direct_role_grant("VIEWER", actor="admin", target="x") is None


def test_acceptance_03_an_approver_cannot_approve_what_the_same_person_started() -> None:
    row_id = "a.approve-privileged-role-grant"
    assert decide(
        M, row_id, Mode.APPROVE, good("APPROVER", actor_person="alice", initiator_person="bob")
    ).allowed
    same = decide(
        M, row_id, Mode.APPROVE, good("APPROVER", actor_person="alice", initiator_person="alice")
    )
    assert same.outcome is Outcome.DENY and same.reasons == (
        "SELF_APPROVAL",
    )  # a second account or a delegation resolves to one person
    unknown = decide(
        M, row_id, Mode.APPROVE, good("APPROVER", actor_person="alice", initiator_person=None)
    )
    assert unknown.outcome is Outcome.HOLD and unknown.reasons == ("SEPARATION_OF_DUTIES_UNKNOWN",)
    assert (
        decide(
            M, row_id, Mode.APPROVE, good("OWNER", actor_person=None, initiator_person="bob")
        ).outcome
        is Outcome.HOLD
    )


def test_acceptance_04_an_operator_outside_its_assignment_or_tenant_is_denied() -> None:
    for row_id in (
        "b.upload-organization-kyb-evidence",
        "b.draft-routine-credential-issuance",
        "e.rotate-assigned-nonprivileged-api-credential",
    ):
        assert decide(M, row_id, Mode.EXECUTE, good("OPERATOR")).allowed
        assert decide(
            M, row_id, Mode.EXECUTE, good("OPERATOR", resource_in_scope=False)
        ).reasons == ("OUT_OF_SCOPE",)
        assert (
            decide(M, row_id, Mode.EXECUTE, good("OPERATOR", membership_active=False)).outcome
            is Outcome.DENY
        )  # another tenant


def test_acceptance_05_and_06_nothing_a_customer_holds_reaches_what_is_reserved() -> None:
    for row_id in RESERVED:
        for role in ROLES:
            for mode in (Mode.EXECUTE, Mode.PREPARE, Mode.APPROVE, Mode.READ, Mode.OWN):
                d = decide(M, row_id, mode, good(role, own_account=True))
                assert d.outcome is Outcome.DENY and d.reasons == ("RESERVED_TO_ZOIKO_ID",)
    # and an Owner holding every other thing still cannot get past a trust hold, a residency restriction or a closed workflow
    for field in ("trust_ok", "region_ok", "workflow_ok"):
        assert not decide(
            M,
            "f.request-change-to-tenant-data-residency",
            Mode.PREPARE,
            good("OWNER", **{field: False}),
        ).allowed


def test_acceptance_07_an_expired_or_revoked_delegation_is_ineffective_at_once() -> None:
    row_id = "d.execute-ordinary-approved-operation"
    assert decide(M, row_id, Mode.EXECUTE, good("OPERATOR", delegation_ok=True)).allowed
    assert decide(M, row_id, Mode.EXECUTE, good("OPERATOR", delegation_ok=False)).reasons == (
        "DELEGATION_NOT_VALID",
    )
    assert (
        decide(M, row_id, Mode.EXECUTE, good("OPERATOR", delegation_ok=None)).outcome
        is Outcome.HOLD
    )  # unknown is not "still valid"


def test_acceptance_08_a_policy_change_changes_the_hash_the_decisions_record() -> None:
    reserved = [row(i) for i in RESERVED]
    one = build("v", {"A": "t"}, [*reserved, row("a.x", OWNER="X")])
    two = build("v", {"A": "t"}, [*reserved, row("a.x", OWNER="X", ADMIN="X")])
    same = build("v", {"A": "t"}, [*reserved, row("a.x", OWNER="X")])
    assert one.policy_hash == same.policy_hash != two.policy_hash
    assert decide(one, "a.x", Mode.EXECUTE, good("OWNER")).policy_hash == one.policy_hash
    assert decide(two, "a.x", Mode.EXECUTE, good("OWNER")).policy_hash != one.policy_hash


def test_a_right_to_ones_own_account_needs_the_account_to_be_ones_own() -> None:
    row_id = "c.manage-own-passkeys-authenticators"
    assert decide(M, row_id, Mode.OWN, good("VIEWER", own_account=True)).allowed
    assert decide(M, row_id, Mode.OWN, good("VIEWER", own_account=False)).reasons == (
        "OWN_ACCOUNT_ONLY",
    )
    assert not decide(
        M, row_id, Mode.OWN, good("VIEWER", own_account=True, membership_active=False)
    ).allowed


def test_the_navigation_projection_lists_rights_but_decides_nothing() -> None:
    viewer = effective_rights(M, ("VIEWER",))
    assert {m for modes in viewer.values() for m in modes} <= {"R", "T"}
    assert "a.view-organization-dashboard" in viewer and "a.invite-operator-or-viewer" not in viewer
    owner = effective_rights(M, ("OWNER",))
    assert "A" in owner["a.approve-privileged-role-grant"] and not any(i in owner for i in RESERVED)
    assert effective_rights(M, ("MEMBER",)) == {}

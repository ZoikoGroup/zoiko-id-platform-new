"""Role by feature permissions (ZID-ENG-DEC-007, proposed): five non-hierarchical roles, six matrices, one decision expression.

A role is never enough on its own. ALLOW needs every predicate of the decision expression to hold; one that is false is a DENY and
one that is unknown, expired or unavailable is a HOLD, each with a safe structured reason. Verified identity, corporate status,
sanctions decisions and trust badges are reserved to Zoiko iD: no role holds them, and the structural checks in `validate` fail the
load if a matrix row ever gives them to a customer role, gives approval to anything but Owner and Approver, or gives a Viewer more
than a read or its own account.

Pure functions only: no I/O, no clock. The server derives every fact; nothing the client says is a fact.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum

ROLES = ("OWNER", "ADMIN", "APPROVER", "OPERATOR", "VIEWER")


class Mode(StrEnum):
    EXECUTE = "X"  # execute within approved scope
    PREPARE = "S"  # prepare or request
    APPROVE = "A"  # independent approval
    READ = "R"  # scoped read
    OWN = "T"  # own account only
    DENIED = "-"


class Outcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    HOLD = "HOLD"


class MatrixError(ValueError):
    pass


# Rows that no customer role holds (the record marks them reserved to the Zoiko iD trust engine, governed compliance review, the
# platform's own just-in-time security workflow, the append-only ledger or the platform legal function, or as never exposed).
# `validate` demands that exactly these rows are all-denied: widening one by editing the matrix fails the load.
RESERVED = frozenset(
    {
        "b.issue-override-zoiko-id-verified-status",
        "b.clear-sanctions-pep-or-ubo-exceptions",
        "c.access-other-users-raw-passwords-secrets",
        "d.authorize-emergency-platform-access",
        "d.edit-delete-previous-approval-decision",
        "e.read-api-keys-private-keys-or-access-tokens",
        "e.authorize-cross-tenant-service-access",
        "f.modify-delete-identity-ledger-history",
        "f.release-legal-hold-override-retention",
    }
)


@dataclass(frozen=True, slots=True)
class Row:
    action_id: str
    matrix: str
    feature: str
    cells: dict[str, Mode]
    rule: str


@dataclass(frozen=True, slots=True)
class Matrix:
    version: str
    titles: dict[str, str]
    rows: dict[str, Row]
    policy_hash: str = field(default="")

    def row(self, action_id: str) -> Row | None:
        return self.rows.get(action_id)


def _digest(version: str, rows: dict[str, Row]) -> str:
    body = {i: {r: m.value for r, m in row.cells.items()} for i, row in sorted(rows.items())}
    return hashlib.sha256(
        json.dumps([version, body], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build(version: str, titles: dict[str, str], rows: list[Row]) -> Matrix:
    by_id: dict[str, Row] = {}
    for row in rows:
        if row.action_id in by_id:
            raise MatrixError(f"duplicate action {row.action_id}")
        by_id[row.action_id] = row
    result = Matrix(version, titles, by_id, _digest(version, by_id))
    validate(result)
    return result


def validate(m: Matrix) -> None:
    if not m.version or not m.rows:
        raise MatrixError("an empty matrix is not a policy")
    for row in m.rows.values():
        if set(row.cells) != set(ROLES):
            raise MatrixError(f"{row.action_id}: every row names exactly the five roles")
        denied_everywhere = all(c is Mode.DENIED for c in row.cells.values())
        if denied_everywhere != (row.action_id in RESERVED):
            raise MatrixError(
                f"{row.action_id}: a reserved row is denied to every role, and no other row is"
            )
        for role, mode in row.cells.items():
            if mode is Mode.APPROVE and role not in ("OWNER", "APPROVER"):
                raise MatrixError(f"{row.action_id}: only Owner and Approver approve")
            if role == "VIEWER" and mode not in (Mode.READ, Mode.OWN, Mode.DENIED):
                raise MatrixError(
                    f"{row.action_id}: a Viewer reads, and manages only its own account"
                )
            if mode is Mode.OWN and not all(c in (Mode.OWN,) for c in row.cells.values()):
                raise MatrixError(f"{row.action_id}: an own-account right is held by every role")
    missing = RESERVED - set(m.rows)
    if missing:
        raise MatrixError(f"reserved rows missing: {sorted(missing)}")


# ---------------------------------------------------------------------------------------- decision


@dataclass(frozen=True, slots=True)
class Facts:
    """Everything the decision expression asks. `True` holds, `False` fails, `None` is unknown or unavailable.

    `delegation_ok` and `entitlement_ok` are `True` when the action does not use a delegation or need an entitlement; `None`
    means one is needed and could not be established."""

    authenticated: bool | None
    membership_active: bool | None
    roles: tuple[str, ...]  # the actor's live roles in this tenant
    resource_in_scope: bool | None  # tenant match, assignment, scope
    policy_current: bool | None  # a policy version is in force and the grants are not stale
    region_ok: bool | None
    trust_ok: bool | None  # no suspension, hold, sanctions or trust block
    delegation_ok: bool | None
    session_ok: bool | None  # the assurance the action needs (Q9 sets the rules)
    entitlement_ok: bool | None
    workflow_ok: bool | None
    own_account: bool = False  # the target is the actor's own account
    actor_person: str | None = (
        None  # the natural person behind the actor, whatever account or delegation they use
    )
    initiator_person: str | None = (
        None  # for an approval: the natural person who started the request
    )


PREDICATES = (
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
)


@dataclass(frozen=True, slots=True)
class Decision:
    outcome: Outcome
    reasons: tuple[str, ...]
    action_id: str
    mode: str
    granted_by_role: str | None  # the role whose cell allowed it: the provenance for the evidence
    policy_hash: str

    @property
    def allowed(self) -> bool:
        return self.outcome is Outcome.ALLOW


def decide(m: Matrix, action_id: str, mode: Mode, facts: Facts) -> Decision:
    """The authoritative decision expression. Deny by default; a missing row, an unknown fact or any failed predicate is never ALLOW."""

    def done(outcome: Outcome, *reasons: str, role: str | None = None) -> Decision:
        return Decision(outcome, tuple(reasons), action_id, mode.value, role, m.policy_hash)

    row = m.row(action_id)
    if row is None or mode is Mode.DENIED:
        return done(Outcome.DENY, "ACTION_NOT_IN_MATRIX")
    held = [r for r in facts.roles if r in row.cells]
    holders = [r for r in held if row.cells[r] is mode]
    if action_id in RESERVED:
        return done(Outcome.DENY, "RESERVED_TO_ZOIKO_ID")
    failed = [code for attr, code in PREDICATES if getattr(facts, attr) is False]
    unknown = [code for attr, code in PREDICATES if getattr(facts, attr) is None]
    if failed:
        return done(Outcome.DENY, *failed)
    if not holders:
        return done(Outcome.DENY, "ROLE_DOES_NOT_HOLD_PERMISSION")
    if mode is Mode.OWN and not facts.own_account:
        return done(Outcome.DENY, "OWN_ACCOUNT_ONLY")
    if mode is Mode.APPROVE:
        # no self-approval: the same natural person behind two accounts, or through a delegation, is one person
        if facts.actor_person is None or facts.initiator_person is None:
            unknown.append("SEPARATION_OF_DUTIES_UNKNOWN")
        elif facts.actor_person == facts.initiator_person:
            return done(Outcome.DENY, "SELF_APPROVAL")
    if unknown:
        return done(Outcome.HOLD, *unknown)
    return done(Outcome.ALLOW, role=holders[0])


def effective_rights(m: Matrix, roles: tuple[str, ...]) -> dict[str, list[str]]:
    """What the roles could do if every other predicate held: a projection for navigation, never an enforcement control."""
    out: dict[str, list[str]] = {}
    for action_id, row in m.rows.items():
        modes = sorted(
            {
                row.cells[r].value
                for r in roles
                if r in row.cells and row.cells[r] is not Mode.DENIED
            }
        )
        if modes:
            out[action_id] = modes
    return out


# ---------------------------------------------------------------------------- role grants (invariant 2)

PRIVILEGED_ROLES = frozenset({"OWNER", "ADMIN", "APPROVER"})
INVITABLE_DIRECT = frozenset(
    {"OPERATOR", "VIEWER"}
)  # matrix A: Owner and Admin execute "Invite Operator or Viewer"


def check_direct_role_grant(role: str, *, actor: str, target: str) -> str | None:
    """None when a role may be assigned directly; else the safe reason. Admin, Approver and Owner are never assigned directly:
    Admin and Approver go through a request that an independent checker approves, and Owner only through the protected owner change."""
    if actor == target:
        return "SELF_ELEVATION"
    if role in PRIVILEGED_ROLES:
        return "PRIVILEGED_ROLE_NEEDS_APPROVAL"
    return None if role in INVITABLE_DIRECT else "ROLE_NOT_DIRECTLY_ASSIGNABLE"


# The bootstrap Owner holds the OWNER role as PENDING_VERIFICATION until the organization gate passes. Company verification is
# how that gate is passed, so these actions (matrix A and B, Owner cells) are open to that pending Owner and to nothing else. An
# engineering default, listed for the owners to confirm or narrow (docs/signoff/roles-owner-pack.md).
BOOTSTRAP_ACTIONS = frozenset(
    {
        "a.view-organization-profile",
        "b.view-verification-state-expiration",
        "b.upload-organization-kyb-evidence",
        "b.submit-refresh-organization-verification",
        "b.request-representative-authority-attestation",
    }
)

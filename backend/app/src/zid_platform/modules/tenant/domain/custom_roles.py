"""Custom organization roles (plan feature advanced.custom-organization-roles-permission-models).

A custom role is a company's own named change to what a standard role may do, in one of two kinds. The base role is always OPERATOR or VIEWER (never
OWNER, ADMIN or APPROVER, which carry approval and administration), and the person must hold it.
- NARROW keeps part of the base role: every action it keeps is one the base role holds, so it cannot name anything the base role lacks; the person is
  kept to the actions of their narrowing roles.
- EXTEND adds actions the base role lacks, but only reads and "prepare or request" actions (which still need an independent approval), never an
  approval, an administrative action or a reserved one. The matrix still decides every other condition, including separation of duties.
"""

from __future__ import annotations

from dataclasses import replace

from zid_platform.modules.tenant.domain.role_matrix import RESERVED, Matrix, Mode

BASE_ROLES = ("OPERATOR", "VIEWER")
PRIVILEGED = frozenset({"OWNER", "ADMIN", "APPROVER"})
MAX_ACTIONS = 200


class CustomRoleError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code.replace("_", " ").capitalize() + "."


def holdable(m: Matrix, base_role: str) -> list[dict[str, str]]:
    """The matrix actions the base role holds in some mode: everything a custom role of that base may keep."""
    out = []
    for action_id, row in m.rows.items():
        mode = row.cells.get(base_role)
        if mode is not None and mode is not Mode.DENIED and action_id not in RESERVED:
            out.append({"action_id": action_id, "feature": row.feature, "matrix": row.matrix, "mode": mode.value})
    return sorted(out, key=lambda a: a["action_id"])


EXTENSION_ROLE = "CUSTOM_EXTENSION"
EXTENDABLE_MODES = frozenset({Mode.READ, Mode.PREPARE})  # a read, or a request that still needs an independent approval


def extendable(m: Matrix, base_role: str) -> list[dict[str, str]]:
    """What an EXTEND role built on `base_role` may add: actions the base role lacks that some role can read or prepare, never reserved ones.
    An action whose only cells are execute, approve or own-account is administration or approval and is never offered."""
    out = []
    for action_id, row in m.rows.items():
        if action_id in RESERVED or row.cells.get(base_role, Mode.DENIED) is not Mode.DENIED:
            continue
        modes = {c for c in row.cells.values() if c is not Mode.DENIED}
        if not modes & EXTENDABLE_MODES:
            continue
        mode = Mode.READ if Mode.READ in modes else Mode.PREPARE
        out.append({"action_id": action_id, "feature": row.feature, "matrix": row.matrix, "mode": mode.value})
    return sorted(out, key=lambda a: a["action_id"])


def with_extension(m: Matrix, action_id: str, mode: Mode) -> Matrix | None:
    """The matrix with one extra holder of `action_id` in `mode`, for deciding an action an EXTEND role names. None when the action is not extendable
    in that mode (an execute or approve request, a reserved action, an unknown action): an extension never turns those on."""
    row = m.row(action_id)
    if row is None or mode not in EXTENDABLE_MODES or action_id in RESERVED:
        return None
    if not {c for c in row.cells.values() if c is not Mode.DENIED} & EXTENDABLE_MODES:
        return None
    rows = dict(m.rows)
    rows[action_id] = replace(row, cells={**row.cells, EXTENSION_ROLE: mode})
    return replace(m, rows=rows)


def clean(m: Matrix, *, name: str, base_role: str, actions: list[str], description: str = "", kind: str = "NARROW") -> tuple[str, str, list[str], str]:
    """The normalized definition, or CustomRoleError. `kind` NARROW keeps part of the base role; EXTEND adds read or prepare-only actions the base lacks."""
    name, description = name.strip(), description.strip()
    if not 2 <= len(name) <= 64 or any(not ch.isprintable() for ch in name):
        raise CustomRoleError("CUSTOM_ROLE_INVALID", "The name is 2 to 64 printable characters.")
    if len(description) > 300:
        raise CustomRoleError("CUSTOM_ROLE_INVALID", "The description is at most 300 characters.")
    if base_role in PRIVILEGED:
        raise CustomRoleError("CUSTOM_ROLE_BASE_REFUSED", "A custom role narrows Operator or Viewer; it cannot be built on an administrative role.")
    if base_role not in BASE_ROLES:
        raise CustomRoleError("CUSTOM_ROLE_INVALID", "The base role is OPERATOR or VIEWER.")
    wanted = sorted(set(actions))
    if not wanted or len(wanted) > MAX_ACTIONS:
        raise CustomRoleError("CUSTOM_ROLE_INVALID", f"Choose between 1 and {MAX_ACTIONS} actions.")
    if kind == "EXTEND":
        offered = {a["action_id"] for a in extendable(m, base_role)}
        refused = [a for a in wanted if a not in offered]
        if refused:
            raise CustomRoleError(
                "CUSTOM_ROLE_EXTENSION_REFUSED",
                f"An extension can only add reads and requests the base role lacks, never approvals or administration: {', '.join(refused[:5])}.",
            )
        return name, base_role, wanted, description
    if kind != "NARROW":
        raise CustomRoleError("CUSTOM_ROLE_INVALID", "The kind is NARROW or EXTEND.")
    allowed = {a["action_id"] for a in holdable(m, base_role)}
    beyond = [a for a in wanted if a not in allowed]
    if beyond:
        raise CustomRoleError("CUSTOM_ROLE_WIDENS", f"{base_role.title()} does not hold: {', '.join(beyond[:5])}. A narrowing role can only keep part of its base role; to add reads or requests, make an extension.")
    return name, base_role, wanted, description


def kept(assignments: list[list[str]]) -> frozenset[str]:
    """The actions a person with these custom roles may still use: the union of what each keeps."""
    out: set[str] = set()
    for actions in assignments:
        out.update(actions)
    return frozenset(out)

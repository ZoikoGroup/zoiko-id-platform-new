"""Which modules run in which role of the single Zoiko iD image (DEC-D6).

One codebase and one image. The same image runs in two roles:
- `cell`   — every regional-cell module (deployed once per cell: uk-1, eu-1, …)
- `global` — tenant locator + discovery only, in the `global_directory` schema of the one platform
             database, with no personal data (DEC-G3). It must never load cell modules.

`PLANES` keeps the logical grouping from ZID-SVC-001 §2, so a plane can later be run as its own
replica pool from the same image (bulkhead) or extracted, without changing module code.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    CELL = "cell"
    GLOBAL = "global"


PLANES: dict[str, tuple[str, ...]] = {
    "runtime": (
        "principal",
        "authentication",
        "session",
        "decision",
        "policy_runtime",
        "relationship",
        "authority",
        "mandate",
        "machine",
    ),
    "trust": ("organization", "verification", "credential", "screening"),
    "security-evidence": ("signals", "evidence", "privacy", "assurance"),
    "integration": ("federation", "provisioning", "integration", "onboarding", "webhook"),
    "control": (
        "tenant",
        "resilience",
        "operations",
        "configuration",
        "policy_authoring",
        "operator_access",
        "metering",
        "commercial",
        "notification",
        "communication",
        "search",
    ),
    "global": ("locator", "discovery"),
}

CELL_SCHEMAS = (
    "platform",
    "core",
    "org",
    "authn",
    "verify",
    "cred",
    "authz",
    "agent",
    "privacy",
    "evidence",
    "signals",
    "events",
    "metering",
    "commercial",
    "keys",
    "zid_migrations",
)
GLOBAL_SCHEMAS = ("global_directory", "zid_migrations")


def modules_for(role: Role, *, with_global: bool = False) -> tuple[str, ...]:
    """`with_global`: a cell process that also serves the global plane on its own port (ZID_SERVE_GLOBAL_PLANE)."""
    if role is Role.GLOBAL:
        return PLANES["global"]
    cell = tuple(m for plane, mods in PLANES.items() if plane != "global" for m in mods)
    return cell + PLANES["global"] if with_global else cell


def schemas_for(role: Role, *, with_global: bool = False) -> tuple[str, ...]:
    if role is Role.GLOBAL:
        return GLOBAL_SCHEMAS
    return CELL_SCHEMAS + ("global_directory",) if with_global else CELL_SCHEMAS

"""Policy releases (ZID-AUTHZ-001 §4, §18; DEC-C3).

A release is an immutable bundle: tenant policies + schema version + the test cases that must pass
before it may publish. Its content hash pins exactly what was approved, so evidence can name the
policy that decided (`policy_release_id`) and replay it later. ACTIVE releases are never edited; a
change is a new release. Pure domain, no I/O.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum


class ReleaseStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    REVIEW = "REVIEW"
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"  # no new decisions; kept for replay
    REVOKED = "REVOKED"  # emergency disable; the last approved safe release takes over


_TRANSITIONS: dict[ReleaseStatus, frozenset[ReleaseStatus]] = {
    ReleaseStatus.DRAFT: frozenset({ReleaseStatus.VALIDATING}),
    ReleaseStatus.VALIDATING: frozenset({ReleaseStatus.REVIEW, ReleaseStatus.DRAFT}),
    ReleaseStatus.REVIEW: frozenset(
        {ReleaseStatus.SCHEDULED, ReleaseStatus.ACTIVE, ReleaseStatus.DRAFT}
    ),
    ReleaseStatus.SCHEDULED: frozenset({ReleaseStatus.ACTIVE, ReleaseStatus.REVOKED}),
    ReleaseStatus.ACTIVE: frozenset({ReleaseStatus.RETIRED, ReleaseStatus.REVOKED}),
}


class PolicyError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PolicyCase:
    """A required allow/deny example (AUTHZ §18.1: unit and negative tests per policy change)."""

    name: str
    principal: str  # Cedar entity UID, e.g. 'Principal::"p1"'
    action: str
    resource: str
    context: dict[str, object] = field(default_factory=dict)
    expect_allow: bool = False


@dataclass(frozen=True, slots=True)
class PolicyRelease:
    release_id: str
    tenant_id: str
    policies: str  # Cedar source
    schema_version: str
    status: ReleaseStatus
    cases: tuple[PolicyCase, ...] = ()
    reviewers: tuple[str, ...] = ()
    author: str = ""
    effective_at: datetime | None = None

    @property
    def content_hash(self) -> str:
        h = hashlib.sha256()
        for part in (self.tenant_id, self.schema_version, self.policies):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        return "sha256:" + h.hexdigest()


def advance(
    release: PolicyRelease, target: ReleaseStatus, *, high_risk: bool = False
) -> PolicyRelease:
    """Lifecycle with separation of duties: publishing needs a reviewer who is not the author,
    and an R3 policy needs two (AUTHZ §19)."""
    if target not in _TRANSITIONS.get(release.status, frozenset()):
        raise PolicyError("POLICY_INVALID_TRANSITION", f"{release.status} -> {target}")
    if (
        target in (ReleaseStatus.SCHEDULED, ReleaseStatus.ACTIVE)
        and release.status is ReleaseStatus.REVIEW
    ):
        independent = {r for r in release.reviewers if r and r != release.author}
        if len(independent) < (2 if high_risk else 1):
            raise PolicyError("POLICY_REVIEW_REQUIRED", "independent reviewer(s) missing")
    return replace(release, status=target)

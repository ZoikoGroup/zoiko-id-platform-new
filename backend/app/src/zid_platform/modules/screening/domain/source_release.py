"""Governed release of an official sanctions source (ZID-DTV-WF-06 section 06, S10, ENG-03 to ENG-05; CR-113). Pure domain, no I/O.

A new corpus is never active because it was downloaded. It is STAGED with a receipt, VALIDATED against the publisher and its schema, and only then released by two people who did
not stage it, each acknowledging every anomaly the validation raised. A corpus that fails validation is QUARANTINED and the corpus in use stays in use. Rolling back reactivates
an earlier validated release; nothing is overwritten.

What validation refuses, each a reason code:
- a source that is not in the register, or a legacy source (the OFSI Consolidated List closed on 28 January 2026; the UK Sanctions List replaced it);
- an address that is not https on a host the publisher is recorded as using, or a redirect off it;
- a file whose digest differs from the digest the publisher advertised (when one exists), or whose schema did not validate;
- a UK list published before the cutoff (a stale legacy file passed off as current).
What it flags for two people to acknowledge: no rows at all, and a count that drifts from the release in use by more than the approved share.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from urllib.parse import urlsplit


class ReleaseError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class State(StrEnum):
    STAGED = "STAGED"
    VALIDATED = "VALIDATED"
    QUARANTINED = "QUARANTINED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """One entry of the official source register: who publishes it, on which hosts, and whether it is still a current source."""

    source_id: str
    owner: str
    hosts: frozenset[str]
    legacy: bool = False
    published_not_before: date | None = (
        None  # a file published earlier than this is not the current list
    )
    advertises_digest: bool = False


UK_CUTOFF = date(
    2026, 1, 28
)  # the OFSI Consolidated List closed; the UK Sanctions List (FCDO) is the current list

REGISTER: dict[str, SourceSpec] = {
    s.source_id: s
    for s in (
        SourceSpec(
            "OFAC_SDN",
            "US Treasury OFAC",
            frozenset({"sanctionslistservice.ofac.treas.gov", "www.treasury.gov"}),
        ),
        SourceSpec(
            "OFAC_NON_SDN",
            "US Treasury OFAC",
            frozenset({"sanctionslistservice.ofac.treas.gov", "www.treasury.gov"}),
        ),
        SourceSpec("UN_SC", "UN Security Council", frozenset({"scsanctions.un.org"})),
        SourceSpec("EU_CONSOLIDATED", "European Commission", frozenset({"webgate.ec.europa.eu"})),
        SourceSpec(
            "UK_SANCTIONS",
            "UK FCDO",
            frozenset({"sanctionslist.fcdo.gov.uk"}),
            published_not_before=UK_CUTOFF,
        ),
        SourceSpec(
            "OFSI_CONSOLIDATED",
            "UK OFSI (closed 28 January 2026)",
            frozenset({"ofsi.hmtreasury.gov.uk"}),
            legacy=True,
        ),
    )
}


@dataclass(frozen=True, slots=True)
class Receipt:
    """What the importer recorded when it fetched a file: enough to prove where it came from and to reject a copy that is not it."""

    source_id: str
    url: str
    sha256: str
    fetched_at: datetime
    published_at: datetime | None
    item_count: int
    schema_valid: bool
    advertised_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ReleasePolicy:
    max_drift_percent: Decimal = Decimal(
        20
    )  # the approved share a count may move from the release in use before two people must acknowledge it
    required_approvers: int = 2


@dataclass(frozen=True, slots=True)
class Validation:
    blocked: tuple[str, ...]  # reasons the file can never be released: it is quarantined
    flags: tuple[str, ...]  # anomalies each approver must acknowledge

    @property
    def state(self) -> State:
        return State.QUARANTINED if self.blocked else State.VALIDATED


def validate(
    receipt: Receipt,
    *,
    active_count: int | None,
    active_sha256: str | None,
    policy: ReleasePolicy | None = None,
) -> Validation:
    """Judge one staged file. Nothing here fetches or parses: the receipt carries what the importer saw."""
    policy = policy or ReleasePolicy()
    blocked: list[str] = []
    flags: list[str] = []
    spec = REGISTER.get(receipt.source_id)
    if spec is None:
        return Validation(("SOURCE_NOT_REGISTERED",), ())
    if spec.legacy:
        blocked.append("LEGACY_SOURCE_REJECTED")
    parts = urlsplit(receipt.url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https":
        blocked.append("URL_NOT_HTTPS")
    elif host not in spec.hosts:
        blocked.append("HOST_NOT_THE_PUBLISHERS")
    if parts.username or parts.password:
        blocked.append("URL_HAS_CREDENTIALS")
    if not receipt.schema_valid:
        blocked.append("SCHEMA_INVALID")
    if (
        receipt.advertised_sha256 is not None
        and receipt.advertised_sha256.lower() != receipt.sha256.lower()
    ):
        blocked.append("CHECKSUM_MISMATCH")
    if spec.advertises_digest and receipt.advertised_sha256 is None:
        flags.append("PUBLISHER_DIGEST_NOT_AVAILABLE")
    if (
        spec.published_not_before is not None
        and receipt.published_at is not None
        and receipt.published_at.date() < spec.published_not_before
    ):
        blocked.append("PUBLISHED_BEFORE_CURRENT_LIST")
    if receipt.item_count <= 0:
        flags.append("ZERO_ROWS")
    elif active_count and active_count > 0:
        drift = abs(Decimal(receipt.item_count - active_count)) * 100 / Decimal(active_count)
        if drift > policy.max_drift_percent:
            flags.append("COUNT_DRIFT_ABOVE_LIMIT")
    if (
        active_sha256 is not None
        and active_sha256.lower() == receipt.sha256.lower()
        and not blocked
    ):
        flags.append(
            "IDENTICAL_TO_ACTIVE"
        )  # only a refresh of the fetch time; shown so nobody mistakes it for a change
    return Validation(tuple(dict.fromkeys(blocked)), tuple(dict.fromkeys(flags)))


@dataclass(frozen=True, slots=True)
class Approval:
    approver: str
    at: datetime
    acknowledged: frozenset[str] = frozenset()
    kind: str = "PUBLISH"  # PUBLISH: approves a staged release; ROLLBACK: approves reactivating an earlier one


@dataclass(frozen=True, slots=True)
class Release:
    release_id: str
    source_id: str
    receipt: Receipt
    staged_by: str
    validation: Validation
    state: State = State.STAGED
    approvals: tuple[Approval, ...] = ()
    activated_at: datetime | None = None
    superseded_at: datetime | None = None


def stage(release_id: str, receipt: Receipt, *, staged_by: str, validation: Validation) -> Release:
    return Release(release_id, receipt.source_id, receipt, staged_by, validation, validation.state)


def approve(
    release: Release, *, approver: str, acknowledged: Sequence[str], at: datetime
) -> Release:
    """One person's approval. The stager cannot approve their own staging, one person approves once, a quarantined release cannot be approved, and an approver must acknowledge
    every flag by name: a general "yes" does not."""
    if release.state is not State.VALIDATED:
        raise ReleaseError("RELEASE_NOT_APPROVABLE", release.state.value)
    if approver == release.staged_by:
        raise ReleaseError("APPROVER_STAGED_THE_RELEASE")
    if any(a.approver == approver and a.kind == "PUBLISH" for a in release.approvals):
        raise ReleaseError("APPROVER_ALREADY_APPROVED")
    missing = set(release.validation.flags) - set(acknowledged)
    if missing:
        raise ReleaseError("FLAGS_NOT_ACKNOWLEDGED", ",".join(sorted(missing)))
    return Release(
        release.release_id,
        release.source_id,
        release.receipt,
        release.staged_by,
        release.validation,
        release.state,
        (*release.approvals, Approval(approver, at, frozenset(acknowledged))),
        release.activated_at,
        release.superseded_at,
    )


def publishable(release: Release, policy: ReleasePolicy | None = None) -> list[str]:
    """Why this release cannot be published yet. Empty means it can."""
    policy = policy or ReleasePolicy()
    out: list[str] = []
    if release.state is not State.VALIDATED:
        out.append(f"STATE_{release.state.value}")
    approvals = [a for a in release.approvals if a.kind == "PUBLISH"]
    if len({a.approver for a in approvals}) < policy.required_approvers:
        out.append("TWO_APPROVERS_REQUIRED")
    for flag in release.validation.flags:
        if any(flag not in a.acknowledged for a in approvals):
            out.append(f"FLAG_NOT_ACKNOWLEDGED:{flag}")
    return out


def publish(
    history: Sequence[Release],
    release_id: str,
    *,
    at: datetime,
    policy: ReleasePolicy | None = None,
) -> list[Release]:
    """Make one release the single active release of its source and supersede the one before. Never more than one is active, and a release that is not publishable is refused."""
    target = next((r for r in history if r.release_id == release_id), None)
    if target is None:
        raise ReleaseError("RELEASE_NOT_FOUND")
    problems = publishable(target, policy)
    if problems:
        raise ReleaseError("RELEASE_NOT_PUBLISHABLE", ",".join(problems))
    out: list[Release] = []
    for r in history:
        if r.release_id == release_id:
            out.append(_with(r, State.ACTIVE, activated_at=at, superseded_at=None))
        elif r.source_id == target.source_id and r.state is State.ACTIVE:
            out.append(_with(r, State.SUPERSEDED, superseded_at=at))
        else:
            out.append(r)
    return out


def approve_rollback(release: Release, *, approver: str, at: datetime) -> Release:
    """One person's approval to reactivate an earlier release. Only a superseded release can be rolled back to, never by the person who staged it, and one person approves once."""
    if release.state is not State.SUPERSEDED:
        raise ReleaseError("ROLLBACK_TARGET_INVALID", release.state.value)
    if approver == release.staged_by:
        raise ReleaseError("APPROVER_STAGED_THE_RELEASE")
    if any(a.approver == approver and a.kind == "ROLLBACK" for a in release.approvals):
        raise ReleaseError("APPROVER_ALREADY_APPROVED")
    return _with(
        release,
        release.state,
        approvals=(*release.approvals, Approval(approver, at, frozenset(), "ROLLBACK")),
    )


def rollback_approvers(release: Release) -> list[str]:
    return sorted({a.approver for a in release.approvals if a.kind == "ROLLBACK"})


def rollback(
    history: Sequence[Release], to_release_id: str, *, approvers: Sequence[str], at: datetime
) -> list[Release]:
    """Reactivate an earlier validated release. It needs two people who are both different from the person who staged it; the corpus it replaces is superseded, not deleted."""
    target = next((r for r in history if r.release_id == to_release_id), None)
    if target is None or target.state is not State.SUPERSEDED:
        raise ReleaseError("ROLLBACK_TARGET_INVALID")
    people = set(approvers)
    if len(people) < 2:
        raise ReleaseError("TWO_APPROVERS_REQUIRED")
    if target.staged_by in people:
        raise ReleaseError("APPROVER_STAGED_THE_RELEASE")
    out: list[Release] = []
    for r in history:
        if r.release_id == to_release_id:
            out.append(
                _with(
                    r,
                    State.ACTIVE,
                    activated_at=at,
                    superseded_at=None,
                    approvals=tuple(
                        a for a in r.approvals if a.kind == "PUBLISH"
                    ),  # the rollback approvals are spent
                )
            )
        elif r.source_id == target.source_id and r.state is State.ACTIVE:
            out.append(_with(r, State.SUPERSEDED, superseded_at=at))
        else:
            out.append(r)
    return out


def _with(r: Release, state: State, **kw: object) -> Release:
    fields = {
        "release_id": r.release_id,
        "source_id": r.source_id,
        "receipt": r.receipt,
        "staged_by": r.staged_by,
        "validation": r.validation,
        "state": state,
        "approvals": r.approvals,
        "activated_at": r.activated_at,
        "superseded_at": r.superseded_at,
        **kw,
    }
    return Release(**fields)  # type: ignore[arg-type]


def active(history: Sequence[Release], source_id: str) -> Release | None:
    return next((r for r in history if r.source_id == source_id and r.state is State.ACTIVE), None)


class Freshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    NEVER_RELEASED = "NEVER_RELEASED"


@dataclass(frozen=True, slots=True)
class SourceHealth:
    source_id: str
    freshness: Freshness
    last_validated_at: (
        datetime | None
    )  # when the active corpus was released after validation: never the time a job last ran
    snapshot: str | None


def health(
    history: Sequence[Release], source_id: str, *, now: datetime, max_age: timedelta
) -> SourceHealth:
    """What a screening may rely on. A source is fresh by the time its ACTIVE release was validated and published, not by the last attempt to fetch it."""
    current = active(history, source_id)
    if current is None or current.activated_at is None:
        return SourceHealth(source_id, Freshness.NEVER_RELEASED, None, None)
    age = now - current.activated_at
    return SourceHealth(
        source_id,
        Freshness.FRESH if age <= max_age else Freshness.STALE,
        current.activated_at,
        current.receipt.sha256,
    )


def utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)

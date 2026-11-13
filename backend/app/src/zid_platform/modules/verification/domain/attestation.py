"""Official-register attestations of a provider's certification (ZID-RCG-003 RCG-G02; sections 05.1 and 05.2; CR-073). Pure domain, no I/O.

`certification.py` says who holds what from a configured list. This module is the evidence behind such a record: dated, append-only snapshots of
what an OFFICIAL register said, so that "certified" is a fact somebody checked and can prove, and so that it stops being true when the proof goes
stale. Rules, each with a test:
- a snapshot names the official register entry (URL, entry id, certifying body), when it was read, and the hash of what the register returned;
  the response itself is kept elsewhere and only referenced here;
- certified means: the latest snapshot at the time asked about says ACTIVE, is in its validity dates, covers the exact entity, scheme, country and
  service (a component of a service never covers the service), and is FRESH. Older than the scheme's limit, or no snapshot at all, is HOLD, never
  certified: an unreachable register cannot keep a certification alive (document T07);
- history is never rewritten: asked about a past moment, the answer comes from the snapshot that was the latest then, so a later expiry or
  withdrawal does not change what was true at a signature or a check (T08, T46);
- a snapshot is refused if it is from the future, older than the one it follows for that record, or without a proof hash and an evidence
  reference; recording one never edits another;
- losing a certification is reported as a change of decision for exactly the scopes it covered, nothing else.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from zid_platform.modules.verification.domain.certification import CertStatus

DEFAULT_STALE_AFTER = timedelta(days=7)
CLOCK_SKEW = timedelta(minutes=5)
PROOF_HASH = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_LOCATOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$")
HTTPS = re.compile(r"^https://[^\s/]+\S{0,480}$")


class AttestationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Outcome(StrEnum):
    CERTIFIED = "CERTIFIED"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Scope:
    """The exact thing a certification covers. A neighbouring service, country or entity is a different scope."""

    entity: str
    scheme: str
    country: str
    service_code: str


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    scope: Scope
    scheme_version: str
    certificate_ref: str
    component_only: bool
    status: CertStatus
    valid_from: date
    valid_until: date | None
    register_url: str
    register_entry_id: str
    cab: str | None
    fetched_at: datetime  # when the official register was read
    proof_hash: str  # sha256 of what it returned (the response itself is kept elsewhere)
    evidence_locator: str
    recorded_by: str
    recorded_at: datetime


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str
    snapshot: Snapshot | None = None

    @property
    def certified(self) -> bool:
        return self.outcome is Outcome.CERTIFIED


def check_snapshot(snapshot: Snapshot, *, now: datetime, previous: Sequence[Snapshot] = ()) -> None:
    """The rules for recording a snapshot; `previous` are the earlier ones for the same scope."""
    if not snapshot.recorded_by.strip():
        raise AttestationError("ATTESTATION_RECORDER_REQUIRED")
    if not HTTPS.match(snapshot.register_url):
        raise AttestationError(
            "ATTESTATION_REGISTER_URL_INVALID", "an official register is named by an https address"
        )
    if not snapshot.register_entry_id.strip():
        raise AttestationError("ATTESTATION_ENTRY_REQUIRED", "name the register entry")
    if not PROOF_HASH.match(snapshot.proof_hash):
        raise AttestationError(
            "ATTESTATION_PROOF_REQUIRED", "the hash of the register's response is required"
        )
    if not EVIDENCE_LOCATOR.match(snapshot.evidence_locator):
        raise AttestationError(
            "ATTESTATION_EVIDENCE_REQUIRED", "say where the preserved response is kept"
        )
    if snapshot.fetched_at.tzinfo is None:
        raise AttestationError("ATTESTATION_TIME_INVALID", "times carry a time zone")
    if snapshot.fetched_at > now + CLOCK_SKEW:
        raise AttestationError(
            "ATTESTATION_FROM_THE_FUTURE", "the register cannot have been read later than now"
        )
    if snapshot.valid_until is not None and snapshot.valid_until < snapshot.valid_from:
        raise AttestationError("ATTESTATION_DATES_INVALID", "valid until is before valid from")
    if previous and snapshot.fetched_at <= max(p.fetched_at for p in previous):
        raise AttestationError(
            "ATTESTATION_OUT_OF_ORDER", "a snapshot must be newer than the one before it"
        )


def _in_force(s: Snapshot, on: date) -> bool:
    return (
        s.status is CertStatus.ACTIVE
        and s.valid_from <= on
        and (s.valid_until is None or on <= s.valid_until)
    )


def decide(
    snapshots: Iterable[Snapshot],
    scope: Scope,
    *,
    at: datetime,
    stale_after: timedelta | Mapping[str, timedelta] = DEFAULT_STALE_AFTER,
    full_service: bool = True,
) -> Decision:
    """Whether `scope` was certified at `at`, from the evidence that existed then. Fail closed: HOLD unless a fresh snapshot says otherwise."""
    limit = (
        stale_after.get(scope.scheme, DEFAULT_STALE_AFTER)
        if isinstance(stale_after, Mapping)
        else stale_after
    )
    seen = [s for s in snapshots if s.scope == scope and s.fetched_at <= at]
    if not seen:
        return Decision(Outcome.HOLD, "no_attestation")
    latest = max(seen, key=lambda s: (s.fetched_at, s.recorded_at))
    if at - latest.fetched_at > limit:
        return Decision(Outcome.HOLD, "attestation_stale", latest)
    if latest.component_only and full_service:
        return Decision(Outcome.HOLD, "component_only", latest)
    if latest.status is not CertStatus.ACTIVE:
        return Decision(Outcome.HOLD, latest.status.value.lower(), latest)
    if not _in_force(latest, at.astimezone(UTC).date()):
        return Decision(Outcome.HOLD, "not_in_force", latest)
    return Decision(Outcome.CERTIFIED, "attested", latest)


def changes(
    before: Mapping[Scope, Decision], after: Mapping[Scope, Decision]
) -> dict[Scope, tuple[Outcome, Outcome]]:
    """The scopes whose answer changed, and only those: losing one certification touches nothing else."""
    out: dict[Scope, tuple[Outcome, Outcome]] = {}
    for scope in set(before) | set(after):
        was = before.get(scope, Decision(Outcome.HOLD, "no_attestation")).outcome
        now = after.get(scope, Decision(Outcome.HOLD, "no_attestation")).outcome
        if was is not now:
            out[scope] = (was, now)
    return out

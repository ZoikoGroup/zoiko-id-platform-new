"""Issuer and accreditation history with provenance (ZID-RCG-003 RCG-G04; sections 06, 06.1; CR-075). Pure domain, no I/O.

`trust_graph.py` decides from a registry (who an issuer is, what names it held, which accreditations covered which dates). This module is how that
registry gets its facts: an append-only ledger of dated statements, each naming the official source it came from, so that "issuer at award time" is
something somebody recorded and can prove, not an edit to a file. Rules, each with a test:
- a statement is a full record of one issuer (the same shape `trust_graph.parse` reads) plus the https source, the hash of what the source returned
  and where it is kept; a record that does not parse as an issuer for that id is refused;
- history is never rewritten: asked what was known at a past moment, the answer comes from the statement that was the latest then (T46-style replay);
- accreditation is judged at the award date, from the interval recorded, never from today's status;
- a later statement may add facts and may END an accreditation or a name going forward. It may not silently remove or shorten what an earlier statement
  said covered a past date, move a revocation, or backdate one: that is a CORRECTION, which needs a reason and keeps both statements visible;
- an unknown historical issuer is REVIEW, never a pass (trust_graph already answers UNKNOWN for it).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from zid_platform.modules.credential.domain import trust_graph as tg

CLOCK_SKEW = timedelta(minutes=5)
HASH = re.compile(r"^[0-9a-f]{64}$")
LOCATOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$")
HTTPS = re.compile(r"^https://[^\s/]+\S{0,480}$")
ISSUER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
MIN_REASON = 10


class IssuerHistoryError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Kind(StrEnum):
    UPDATE = "UPDATE"
    CORRECTION = "CORRECTION"


@dataclass(frozen=True)
class Statement:
    statement_id: str
    issuer_id: str
    seq: int  # 1 for the first statement about this issuer, then rising by one
    kind: Kind
    reason: str | None  # required for a CORRECTION
    record: Mapping[str, Any]  # an issuer row as in config/credential-trust/issuer-registry.yaml
    source_url: str
    source_hash: str
    evidence_locator: str
    recorded_by: str
    recorded_at: datetime


def issuer_of(statement: Statement) -> tg.Issuer:
    """The issuer the statement describes, validated by the trust graph's own parser."""
    if not ISSUER_ID.match(statement.issuer_id):
        raise IssuerHistoryError("ISSUER_ID_INVALID", statement.issuer_id)
    try:
        registry = tg.parse({"issuers": [dict(statement.record)]})
    except tg.TrustGraphError as e:
        raise IssuerHistoryError("ISSUER_RECORD_INVALID", str(e)) from e
    issuer = registry.issuers.get(statement.issuer_id)
    if issuer is None or len(registry.issuers) != 1:
        raise IssuerHistoryError(
            "ISSUER_RECORD_INVALID", "the record must describe exactly this issuer"
        )
    return issuer


def retroactive_changes(prev: tg.Issuer, new: tg.Issuer, *, known_on: date) -> list[str]:
    """What the new record changes about the PAST as the earlier statement described it. Going forward is fine: a new accreditation, a new name, an
    accreditation or name that ends from `known_on`, a revocation from `known_on`."""
    out: list[str] = []
    for a in prev.accreditations:
        match = next(
            (
                n
                for n in new.accreditations
                if (n.body_issuer_id, n.program) == (a.body_issuer_id, a.program)
            ),
            None,
        )
        label = f"{a.body_issuer_id}/{a.program or '*'}"
        if match is None:
            out.append(f"accreditation_removed:{label}")
        elif match.valid_from != a.valid_from:
            out.append(f"accreditation_start_changed:{label}")
        elif a.valid_until is not None and match.valid_until != a.valid_until:
            out.append(f"accreditation_end_changed:{label}")
        elif (
            a.valid_until is None and match.valid_until is not None and match.valid_until < known_on
        ):
            out.append(f"accreditation_ended_in_the_past:{label}")
    new_names = {n.name for n in new.names}
    out.extend(f"name_removed:{n.name}" for n in prev.names if n.name not in new_names)
    if new.revoked_on != prev.revoked_on:
        if prev.revoked_on is not None:
            out.append("revocation_changed")
        elif new.revoked_on is not None and new.revoked_on < known_on:
            out.append("revocation_backdated")
    return out


def check_statement(
    statement: Statement, *, now: datetime, previous: Sequence[Statement] = ()
) -> list[str]:
    """The rules for recording a statement; `previous` are the earlier ones for the same issuer, oldest first. Returns the retroactive changes a
    CORRECTION makes (empty for a plain update)."""
    issuer = issuer_of(statement)
    if not statement.recorded_by.strip():
        raise IssuerHistoryError("ISSUER_RECORDER_REQUIRED")
    if not HTTPS.match(statement.source_url):
        raise IssuerHistoryError(
            "ISSUER_SOURCE_URL_INVALID", "an official source is named by an https address"
        )
    if not HASH.match(statement.source_hash):
        raise IssuerHistoryError(
            "ISSUER_PROOF_REQUIRED", "the hash of the source's response is required"
        )
    if not LOCATOR.match(statement.evidence_locator):
        raise IssuerHistoryError(
            "ISSUER_EVIDENCE_REQUIRED", "say where the preserved response is kept"
        )
    if statement.recorded_at.tzinfo is None:
        raise IssuerHistoryError("ISSUER_TIME_INVALID", "times carry a time zone")
    if statement.recorded_at > now + CLOCK_SKEW:
        raise IssuerHistoryError("ISSUER_FROM_THE_FUTURE")
    if issuer.authority is not tg.Authority.OFFICIAL and issuer.claim_types:
        raise IssuerHistoryError(
            "ISSUER_AUTHORITY_UNRECORDED",
            "an issuer trusted for any claim needs an OFFICIAL, recorded basis",
        )
    if statement.kind is Kind.CORRECTION and len((statement.reason or "").strip()) < MIN_REASON:
        raise IssuerHistoryError(
            "ISSUER_CORRECTION_REASON_REQUIRED", f"say why, in at least {MIN_REASON} characters"
        )
    expected = len(previous) + 1
    if statement.seq != expected:
        raise IssuerHistoryError("ISSUER_OUT_OF_ORDER", f"expected statement {expected}")
    changes: list[str] = []
    if previous:
        last = previous[-1]
        if statement.recorded_at <= last.recorded_at:
            raise IssuerHistoryError(
                "ISSUER_OUT_OF_ORDER", "a statement must be newer than the one before it"
            )
        changes = retroactive_changes(
            issuer_of(last), issuer, known_on=statement.recorded_at.date()
        )
        if changes and statement.kind is not Kind.CORRECTION:
            raise IssuerHistoryError(
                "ISSUER_STATEMENT_RETROACTIVE",
                ", ".join(changes) + ": record it as a CORRECTION with a reason",
            )
        if not changes and statement.kind is Kind.CORRECTION:
            raise IssuerHistoryError(
                "ISSUER_NOTHING_TO_CORRECT", "no earlier fact changes; record an update"
            )
    elif statement.kind is Kind.CORRECTION:
        raise IssuerHistoryError(
            "ISSUER_NOTHING_TO_CORRECT", "the first statement cannot correct anything"
        )
    return changes


def known_issuer(history: Sequence[Statement], at: datetime) -> tg.Issuer | None:
    """The issuer as it was known at `at`: the latest statement recorded by then, or None if nothing was known."""
    known = [s for s in history if s.recorded_at <= at]
    return issuer_of(max(known, key=lambda s: s.seq)) if known else None


def registry_as_known_at(
    histories: Mapping[str, Sequence[Statement]], at: datetime, base: tg.Registry | None = None
) -> tg.Registry:
    """A trust-graph registry from the ledger as it stood at `at`. A statement replaces any same-id issuer of `base` (the configured sample
    registry); issuers with no statement by then stay as `base` has them. Recognition rules are `base`'s."""
    issuers = dict(base.issuers) if base else {}
    for issuer_id, history in histories.items():
        issuer = known_issuer(history, at)
        if issuer is not None:
            issuers[issuer_id] = issuer
    return tg.Registry(
        base.version if base else "ledger", issuers, base.recognition if base else ()
    )


def award_check(
    history: Sequence[Statement],
    *,
    program: str,
    awarded_on: date,
    known_at: datetime,
    claim_type: str | None = None,
) -> dict[str, Any]:
    """Issuer at award time: what the ledger knew at `known_at` about this issuer, judged for an award on `awarded_on`. Unknown is REVIEW."""
    issuer = known_issuer(history, known_at)
    if issuer is None:
        return {
            "outcome": "REVIEW",
            "reasons": ["UNKNOWN_HISTORICAL_ISSUER"],
            "accreditation": tg.Accredited.UNKNOWN.value,
        }
    reasons: list[str] = []
    if issuer.revoked_on is not None and awarded_on >= issuer.revoked_on:
        reasons.append("ISSUER_REVOKED_AT_AWARD_DATE")
    if issuer.authority is not tg.Authority.OFFICIAL:
        reasons.append("AUTHORITY_NOT_RECORDED")
    if claim_type is not None and claim_type not in issuer.claim_types:
        reasons.append("NOT_TRUSTED_FOR_CLAIM_TYPE")
    state, why = tg.accreditation_at_award(issuer, program, awarded_on)
    reasons.extend(why)
    name = next(
        (
            n.name
            for n in issuer.names
            if (n.from_ is None or n.from_ <= awarded_on)
            and (n.until is None or awarded_on <= n.until)
        ),
        None,
    )
    outcome = "PASS" if state is tg.Accredited.ACCREDITED and not reasons else "REVIEW"
    return {
        "outcome": outcome,
        "reasons": reasons,
        "accreditation": state.value,
        "name_at_award": name,
    }

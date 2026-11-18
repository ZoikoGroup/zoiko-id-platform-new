"""The stored company grade and what it allows right now (ZID-DTV-WF-02 section 4; CR-109). Orchestration over `domain/kyb_grade.py`; the rules live there.

`record` assesses a company from the facts it is given, appends the result as the next version, and says so on the outbox: `kyb.assertion.issued` when the company reaches a grade it did not hold,
`kyb.assertion.revoked` when claims it held are no longer proven. `current` and `allowed` read the latest record and work the grade out AGAIN from its stored facts at the time of the
question, so a mandate that has ended or a register check that has gone stale lowers what is allowed with no one editing anything. `gate` is the enforcement point for an act: off by default,
shadow records what it would have refused without refusing, enforce refuses.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.organization.domain import kyb_grade as g

GATE_MODES = ("off", "shadow", "enforce")


class GateError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Stored:
    version: int
    grade: g.Grade
    label: str
    claims: tuple[str, ...]
    reasons: tuple[str, ...]
    facts: g.KybFacts
    policy_hash: str | None
    trigger: str
    assessed_at: datetime
    recheck_due: datetime | None


def _row(r: Any) -> Stored:
    grade = g.Grade(r.grade)
    return Stored(
        version=r.version,
        grade=grade,
        label=g.LABEL[grade],
        claims=tuple(r.claims),
        reasons=tuple(r.reasons),
        facts=g.facts_from_dict(r.facts),
        policy_hash=r.policy_hash,
        trigger=r.trigger,
        assessed_at=r.assessed_at,
        recheck_due=r.recheck_due,
    )


async def latest(conn: AsyncConnection, tenant_id: str) -> Stored | None:
    row = (
        await conn.execute(
            text(
                "SELECT version, grade, claims, reasons, facts, policy_hash, trigger, assessed_at, recheck_due "
                "FROM org.kyb_assessment WHERE tenant_id = :t ORDER BY version DESC LIMIT 1"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    return _row(row) if row else None


async def history(conn: AsyncConnection, tenant_id: str, *, limit: int = 50) -> list[Stored]:
    rows = (
        await conn.execute(
            text(
                "SELECT version, grade, claims, reasons, facts, policy_hash, trigger, assessed_at, recheck_due "
                "FROM org.kyb_assessment WHERE tenant_id = :t ORDER BY version DESC LIMIT :n"
            ),
            {"t": uuid.UUID(tenant_id), "n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def record(
    conn: AsyncConnection,
    tenant_id: str,
    facts: g.KybFacts,
    *,
    trigger: str,
    actor: str | None,
    now: datetime,
    policy_hash: str | None = None,
    recheck_days: dict[g.Grade, int] | None = None,
) -> Stored:
    """Assess and append. Emits issued/revoked when the proven claims changed; says nothing when they did not.

    Two assessments of one company at once (a recheck, an event and a person's request can coincide) must not take the same version: they queue on a lock held until the transaction ends,
    and the second one sees the first one's row."""
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"kyb-assessment:{tenant_id}"},
    )
    before = await latest(conn, tenant_id)
    a = g.assess(facts, now=now, recheck_days=recheck_days)
    version = (before.version + 1) if before else 1
    await conn.execute(
        text(
            "INSERT INTO org.kyb_assessment (assessment_id, tenant_id, version, grade, claims, reasons, facts, policy_hash, trigger, actor, recheck_due, assessed_at) "
            "VALUES (:id, :t, :v, :g, CAST(:c AS jsonb), CAST(:r AS jsonb), CAST(:f AS jsonb), :p, :tr, :a, :due, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "v": version,
            "g": a.grade.value,
            "c": _json(list(a.claims)),
            "r": _json(list(a.reasons)),
            "f": _json(g.facts_to_dict(facts)),
            "p": policy_hash,
            "tr": trigger[:80],
            "a": uuid.UUID(actor) if actor else None,
            "due": a.recheck_due,
            "now": now,
        },
    )
    gained = sorted(set(a.claims) - set(before.claims if before else ()))
    lost = sorted(set(before.claims if before else ()) - set(a.claims))
    if gained and (before is None or g.rank(a.grade) > g.rank(before.grade)):
        await emit(
            conn,
            name="organization.kyb.assertion_issued",
            tenant_id=tenant_id,
            aggregate_type="kyb_assessment",
            aggregate_id=tenant_id,
            aggregate_version=version,
            data={
                "version": version,
                "grade": a.grade.value,
                "claims": list(a.claims),
                "claims_gained": gained,
                "policy_hash": policy_hash,
                "recheck_due": a.recheck_due.isoformat() if a.recheck_due else None,
            },
        )
    if lost:
        await emit(
            conn,
            name="organization.kyb.assertion_revoked",
            tenant_id=tenant_id,
            aggregate_type="kyb_assessment",
            aggregate_id=tenant_id,
            aggregate_version=version,
            data={
                "version": version,
                "grade": a.grade.value,
                "claims_revoked": lost,
                "reasons": list(a.reasons),
                "trigger": trigger[:80],
            },
        )
    return Stored(
        version,
        a.grade,
        a.label,
        a.claims,
        a.reasons,
        facts,
        policy_hash,
        trigger[:80],
        now,
        a.recheck_due,
    )


def _json(value: object) -> str:
    import json

    return json.dumps(value, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True, slots=True)
class Entitlement:
    stored_version: int | None
    grade: g.Grade
    label: str
    reasons: tuple[str, ...]
    actions: frozenset[str]
    recheck_due: datetime | None
    lowered_since_assessed: (
        bool  # the grade now is lower than the one recorded: time alone changed it
    )


async def allowed(
    conn: AsyncConnection, tenant_id: str, *, person: str | None = None, now: datetime | None = None
) -> Entitlement:
    """What the company may do now, from the latest record worked out again at `now`. No record is K0 with nothing allowed: absence is not permission."""
    now = now or datetime.now(UTC)
    s = await latest(conn, tenant_id)
    if s is None:
        a0 = g.assess(g.KybFacts(), now=now)
        return Entitlement(None, a0.grade, a0.label, a0.reasons, frozenset(), None, False)
    a = g.assess(s.facts, now=now)
    return Entitlement(
        s.version,
        a.grade,
        a.label,
        a.reasons,
        g.permitted_actions(s.facts, a, person=person, now=now),
        a.recheck_due,
        g.rank(a.grade) < g.rank(s.grade),
    )


async def gate(
    conn: AsyncConnection,
    tenant_id: str,
    action: str,
    *,
    person: str | None,
    mode: str,
    now: datetime | None = None,
) -> Entitlement:
    """The enforcement point for an act. `off` does nothing and reads nothing; `shadow` reads and reports what would have been refused; `enforce` refuses with KYB_GRADE_INSUFFICIENT."""
    if mode not in GATE_MODES:
        raise GateError("KYB_GATE_MODE_INVALID", mode)
    if mode == "off":
        return Entitlement(None, g.Grade.K0, "", (), frozenset({action}), None, False)
    e = await allowed(conn, tenant_id, person=person, now=now)
    if action in e.actions:
        return e
    if mode == "shadow":
        return e
    raise GateError(
        "KYB_GRADE_INSUFFICIENT",
        f"{action} needs more than {e.grade.value} ({e.label}); first reason: {e.reasons[0] if e.reasons else 'none recorded'}",
    )


def view(s: Stored | None, e: Entitlement | None = None) -> Mapping[str, Any]:
    """What a caller may see: the grade, its label, the claims, why not higher, and the limits. Never the facts, the reviewers or the evidence."""
    if s is None:
        return {
            "grade": "K0",
            "label": g.LABEL[g.Grade.K0],
            "claims": [],
            "reasons": ["NO_ASSESSMENT"],
            "permitted_actions": [],
            "recheck_due": None,
        }
    out: dict[str, Any] = {
        "version": s.version,
        "grade": s.grade.value,
        "label": s.label,
        "claims": list(s.claims),
        "reasons": list(s.reasons),
        "assessed_at": s.assessed_at.isoformat(),
        "recheck_due": s.recheck_due.isoformat() if s.recheck_due else None,
        "policy_hash": s.policy_hash,
    }
    if e is not None:
        out["now"] = {
            "grade": e.grade.value,
            "label": e.label,
            "reasons": list(e.reasons),
            "lowered_since_assessed": e.lowered_since_assessed,
        }
        out["permitted_actions"] = sorted(e.actions)
    return out

"""A company's facts, gathered from what is already recorded, and its stored grade (ZID-DTV-WF-02; CR-109). Orchestration: the rules are `domain/kyb_grade.py`, `kyb_mandate.py`, `dual_review.py`.

`gather` reads the company's latest verification case (register and reviewers), its latest ownership graph and the decisions on it, its screening, and its mandates, and turns them into the
plain facts the grade is worked out from. Nothing is invented: no case is no registry fact, no graph is not complete, no screening is NOT_RUN, no mandate is none. `reassess` gathers and records.
A beneficial owner counts as a verified person only when the decision confirming them points at a current identity assertion for exactly that person (`verification:<principal id>`).

Mandates: `record_mandate`, `cosign_mandate` and `revoke_mandate` keep the rules of `kyb_mandate`; each one reassesses, so the grade, its events and what is allowed move with the change.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.organization.application import kyb_assessment as assessment
from zid_platform.modules.organization.application import ownership_service as ownership
from zid_platform.modules.organization.domain import dual_review as dual
from zid_platform.modules.organization.domain import kyb_grade as g
from zid_platform.modules.organization.domain import kyb_mandate as rules
from zid_platform.modules.organization.domain.kyb import KybStatus
from zid_platform.modules.organization.infrastructure import kyb_store, ownership_store, proof_store

# What this module needs from two modules it must not import (they depend on this one, and a cycle is refused): whether a person holds a current identity
# assertion, and where a company's screening stands. The composition root supplies them (`configure_sources`); until it does, nobody is verified and screening is
# NOT_RUN, which is the safe reading: a missing source lowers the grade, it never raises it.
PersonAssurance = Callable[[AsyncConnection, str, datetime], Awaitable[bool]]
ScreeningStatus = Callable[[AsyncConnection, str, str, datetime], Awaitable[str | None]]
_person_assurance: PersonAssurance | None = None
_screening_status: ScreeningStatus | None = None


def configure_sources(
    person_assurance: PersonAssurance | None, screening_status: ScreeningStatus | None
) -> None:
    global _person_assurance, _screening_status
    _person_assurance, _screening_status = person_assurance, screening_status


async def _assured(conn: AsyncConnection, principal_id: str, now: datetime) -> bool:
    return bool(_person_assurance is not None and await _person_assurance(conn, principal_id, now))


REGISTRY_MAX_AGE = timedelta(
    days=90
)  # the wireframe's starting candidate for a current extract; a signed market policy replaces it
_ASSERTION_REF = re.compile(r"^verification:([0-9a-fA-F-]{36})$")
_DISSOLVED = ("dissol", "struck", "closed", "liquidat", "removed", "ceased")


def _utc(v: Any) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _status_reported(body: dict[str, Any]) -> str:
    s = str(body.get("registration_status") or "").lower()
    if not s:
        return "UNKNOWN"
    if any(w in s for w in _DISSOLVED):
        return "DISSOLVED"
    return "ACTIVE" if s == "active" else "UNKNOWN"


async def _case_facts(
    conn: AsyncConnection, tenant_id: str, now: datetime, max_age: timedelta
) -> tuple[g.RegistryFact | None, g.DocumentFact | None]:
    case_id = await kyb_store.latest_case_id(conn, tenant_id)
    if case_id is None:
        return None, None
    rows = await kyb_store.evidence_history(conn, tenant_id, case_id)
    if not rows:
        return None, None
    case = await kyb_store.load_case(conn, tenant_id, case_id)
    registry: g.RegistryFact | None = None
    for r in reversed(
        rows
    ):  # the latest authoritative answer that verified the company: an outage after it does not erase it, it only ages it
        if r.decision_status is KybStatus.VERIFIED and r.body.get("source_authority") is not None:
            registry = g.RegistryFact(
                True,
                _status_reported(r.body),
                _utc(r.body.get("observed_at") or r.created_at),
                max_age,
                reference_only=r.body.get("source_authority") == "gleif",
            )
            break
    if registry is None and rows[-1].decision_status is KybStatus.UNAVAILABLE:
        registry = g.RegistryFact(False, "SOURCE_UNAVAILABLE", _utc(rows[-1].created_at), max_age)
    documents: g.DocumentFact | None = None
    last = rows[-1]
    standing = case.expiry_at is None or case.expiry_at > now
    if (
        last.decision_status is KybStatus.VERIFIED
        and last.body.get("source_authority") is None
        and standing
    ):
        reviewers = tuple(
            dict.fromkeys(
                r.decided_by
                for r in rows
                if r.decided_by
                and ({dual.FIRST_ACCEPTED, dual.AGREED, dual.COMPLIANCE} & set(r.reasons))
            )
        )
        documents = g.DocumentFact(
            reviewers, await proof_store.count(conn, tenant_id, case_id) > 0, _utc(last.created_at)
        )
    return registry, documents


async def _ownership_facts(
    conn: AsyncConnection, tenant_id: str, now: datetime
) -> tuple[bool, bool]:
    graphs = await ownership_store.recent(conn, 1)
    if not graphs:
        return False, False
    graph = graphs[0]
    confirmations = await ownership_store.confirmations_for(conn, str(graph["graph_id"]))
    view = ownership.graph_view(graph, confirmations)
    findings = view.get("findings")
    if not findings or not findings.get("complete"):
        return False, False
    decided = {c["entity_id"]: c for c in confirmations}
    candidates = [c["entity_id"] for c in findings["candidates"]]
    if any(
        decided.get(e, {}).get("decision") not in ("CONFIRMED", "NOT_CONFIRMED") for e in candidates
    ):
        return False, False  # an undecided or escalated candidate means ownership is not settled
    verified = True
    for e in candidates:
        c = decided[e]
        if c["decision"] != "CONFIRMED":
            continue
        m = _ASSERTION_REF.match(str(c["evidence_ref"]))
        if not m or not await _assured(conn, m.group(1), now):
            verified = False
    return True, verified


async def _screening(conn: AsyncConnection, tenant_id: str, now: datetime) -> str:
    case_id = await kyb_store.latest_case_id(conn, tenant_id)
    if case_id is None:
        return "NOT_RUN"
    if _screening_status is None:
        return "NOT_RUN"
    status = await _screening_status(conn, tenant_id, case_id, now)
    if status is None:
        return "NOT_RUN"
    if status == "VERIFIED":
        return "SCREENING_CLEAR"
    if status in ("REVIEW_REQUIRED", "REJECTED", "SUSPENDED"):
        return "POTENTIAL_MATCH"
    return "UNAVAILABLE" if status in ("UNAVAILABLE", "EXPIRED") else "NOT_RUN"


async def _mandates(
    conn: AsyncConnection, tenant_id: str, now: datetime
) -> tuple[g.MandateFact, ...]:
    rows = (
        await conn.execute(
            text(
                "SELECT m.person, m.powers, m.valid_from, m.valid_to, m.revoked_at, m.second_signatory_required, "
                "(SELECT count(*) FROM org.kyb_mandate_cosign c WHERE c.mandate_id = m.mandate_id) AS cosigned "
                "FROM org.kyb_mandate m WHERE m.tenant_id = :t ORDER BY m.created_at"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).all()
    out = []
    for r in rows:
        out.append(
            g.MandateFact(
                person=str(r.person),
                person_verified=await _assured(conn, str(r.person), now),
                powers=frozenset(r.powers),
                valid_from=_utc(r.valid_from),
                valid_to=_utc(r.valid_to) if r.valid_to else None,
                revoked=r.revoked_at is not None,
                second_signatory_required=bool(r.second_signatory_required),
                second_signatory_present=int(r.cosigned) > 0,
            )
        )
    return tuple(out)


async def gather(
    conn: AsyncConnection,
    tenant_id: str,
    now: datetime,
    *,
    registry_max_age: timedelta = REGISTRY_MAX_AGE,
) -> g.KybFacts:
    registry, documents = await _case_facts(conn, tenant_id, now, registry_max_age)
    complete, persons = await _ownership_facts(conn, tenant_id, now)
    return g.KybFacts(
        registry=registry,
        documents=documents,
        ownership_complete=complete,
        persons_verified=persons,
        screening=await _screening(conn, tenant_id, now),
        mandates=await _mandates(conn, tenant_id, now),
    )


async def reassess(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    trigger: str,
    actor: str | None,
    now: datetime | None = None,
) -> assessment.Stored:
    now = now or datetime.now(UTC)
    return await assessment.record(
        conn, tenant_id, await gather(conn, tenant_id, now), trigger=trigger, actor=actor, now=now
    )


async def gate_action(
    conn: AsyncConnection,
    tenant_id: str,
    principal_id: str | None,
    action: str,
    *,
    mode: str,
    mapping: dict[str, str],
    now: datetime | None = None,
) -> None:
    """Apply the company's standing to a protected action at the moment it happens. An action that is not a binding act is not this gate's business. Raises
    assessment.GateError (KYB_GRADE_INSUFFICIENT) only in `enforce`."""
    act = mapping.get(action)
    if act is None or mode == "off":
        return
    await assessment.gate(conn, tenant_id, act, person=principal_id, mode=mode, now=now)


# ------------------------------------------------------------------------------------------------------------------ mandates


async def record_mandate(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    person: str,
    recorder: str,
    role: str,
    source: str,
    powers: list[str],
    valid_from: datetime,
    valid_to: datetime | None,
    evidence_ref: str,
    second_signatory_required: bool = False,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    clean = rules.validate(
        person=person, recorder=recorder, role=role, source=source, powers=powers, valid_from=valid_from, valid_to=valid_to,
        evidence_ref=evidence_ref, second_signatory_required=second_signatory_required, now=now,
    )  # fmt: skip
    mandate_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO org.kyb_mandate (mandate_id, tenant_id, person, recorded_by, role, source, evidence_ref, powers, second_signatory_required, valid_from, valid_to) "
            "VALUES (:id, :t, :p, :r, :ro, :s, :e, :pw, :sec, :vf, :vt)"
        ),
        {
            "id": mandate_id, "t": uuid.UUID(tenant_id), "p": uuid.UUID(person), "r": uuid.UUID(recorder), "ro": role, "s": source,
            "e": evidence_ref.strip(), "pw": list(clean.powers), "sec": clean.second_signatory_required, "vf": valid_from, "vt": valid_to,
        },
    )  # fmt: skip
    await reassess(conn, tenant_id, trigger="MANDATE_RECORDED", actor=recorder, now=now)
    return str(mandate_id)


async def _mandate(conn: AsyncConnection, tenant_id: str, mandate_id: str) -> Any:
    row = (
        await conn.execute(
            text(
                "SELECT person, recorded_by, revoked_at FROM org.kyb_mandate WHERE tenant_id = :t AND mandate_id = :m"
            ),
            {"t": uuid.UUID(tenant_id), "m": uuid.UUID(mandate_id)},
        )
    ).first()
    if row is None:
        raise rules.MandateError("MANDATE_NOT_FOUND", mandate_id)
    return row


async def cosign_mandate(
    conn: AsyncConnection,
    tenant_id: str,
    mandate_id: str,
    *,
    cosigner: str,
    now: datetime | None = None,
) -> None:
    row = await _mandate(conn, tenant_id, mandate_id)
    if row.revoked_at is not None:
        raise rules.MandateError("MANDATE_REVOKED", mandate_id)
    why = rules.cosign_problem(
        person=str(row.person), recorder=str(row.recorded_by), cosigner=cosigner
    )
    if why:
        raise rules.MandateError(why)
    await conn.execute(
        text(
            "INSERT INTO org.kyb_mandate_cosign (cosign_id, tenant_id, mandate_id, cosigner) VALUES (:i, :t, :m, :c) ON CONFLICT (mandate_id, cosigner) DO NOTHING"
        ),
        {
            "i": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "m": uuid.UUID(mandate_id),
            "c": uuid.UUID(cosigner),
        },
    )
    await reassess(conn, tenant_id, trigger="MANDATE_COSIGNED", actor=cosigner, now=now)


async def revoke_mandate(
    conn: AsyncConnection,
    tenant_id: str,
    mandate_id: str,
    *,
    by: str,
    reason: str,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    if len(reason.strip()) < 10:
        raise rules.MandateError("REASON_REQUIRED", "give a reason of at least ten characters")
    row = await _mandate(conn, tenant_id, mandate_id)
    if row.revoked_at is not None:
        raise rules.MandateError("MANDATE_REVOKED", mandate_id)
    await conn.execute(
        text(
            "UPDATE org.kyb_mandate SET revoked_at = :n, revoked_by = :b, revoked_reason = :r WHERE tenant_id = :t AND mandate_id = :m"
        ),
        {
            "n": now,
            "b": uuid.UUID(by),
            "r": reason.strip(),
            "t": uuid.UUID(tenant_id),
            "m": uuid.UUID(mandate_id),
        },
    )
    await reassess(conn, tenant_id, trigger="MANDATE_REVOKED", actor=by, now=now)


async def list_mandates(conn: AsyncConnection, tenant_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT m.mandate_id, m.person, m.role, m.source, m.powers, m.second_signatory_required, m.valid_from, m.valid_to, m.created_at, m.revoked_at, "
                "(SELECT count(*) FROM org.kyb_mandate_cosign c WHERE c.mandate_id = m.mandate_id) AS cosigned "
                "FROM org.kyb_mandate m WHERE m.tenant_id = :t ORDER BY m.created_at DESC LIMIT 200"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).all()
    return [
        {
            "mandate_id": str(r.mandate_id), "person": str(r.person), "role": r.role, "source": r.source, "powers": list(r.powers),
            "second_signatory_required": r.second_signatory_required, "second_signatory_present": int(r.cosigned) > 0,
            "valid_from": r.valid_from.isoformat(), "valid_to": r.valid_to.isoformat() if r.valid_to else None,
            "revoked": r.revoked_at is not None, "recorded_at": r.created_at.isoformat(),
        }
        for r in rows
    ]  # fmt: skip

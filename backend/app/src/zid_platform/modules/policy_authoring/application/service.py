"""Policy administration, testing and release (ZID-AUTHZ-001 §18-19; DEC-C3).

    draft      a tenant's Cedar policy text and its allow/deny cases (DRAFT is editable)
    validate   DRAFT -> VALIDATING -> the §18.1 blocking checks (schema/type, authority invariant,
               every case) plus a privilege diff against the ACTIVE release; clean -> REVIEW,
               otherwise back to DRAFT with the problems
    simulate   one request under the candidate and under the ACTIVE release (no state change)
    review     an independent reviewer (never the author) signs a release in REVIEW
    publish    REVIEW -> ACTIVE; one independent reviewer, two when the release touches an R3 action
               (§19 "Publish R3 policy: two-person control"); the previous ACTIVE release retires

The platform floor is always evaluated on top of tenant policy; a tenant release can only narrow it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_outbox import emit

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.policy_authoring.infrastructure import release_store
from zid_platform.modules.policy_runtime.ports import (
    PolicyCase,
    PolicyError,
    PolicyRelease,
    ReleaseStatus,
    build_schema,
    evaluate,
    prepublication_problems,
)

SCHEMA_VERSION = "1"
_ACTION_REF = re.compile(r'Action::"([a-z0-9_.]+)"')


@dataclass(frozen=True, slots=True)
class Validation:
    release: PolicyRelease
    problems: list[str]
    newly_allowed: list[str]
    newly_denied: list[str]


def referenced_actions(release: PolicyRelease) -> set[str]:
    return set(_ACTION_REF.findall(release.policies)) | {c.action for c in release.cases}


def high_risk(release: PolicyRelease, catalog: Mapping[str, Mapping[str, Any]]) -> bool:
    """An R3 action anywhere in the release makes publishing a two-person act (§19)."""
    return any(
        str(catalog.get(a, {}).get("min_risk_class", "R0")) == "R3"
        for a in referenced_actions(release)
    )


async def _record(
    conn: AsyncConnection,
    release: PolicyRelease,
    change: str,
    actor: str,
    now: datetime,
    **extra: Any,
) -> None:
    event_id = await emit(
        conn,
        name="policy.release.changed",
        tenant_id=release.tenant_id,
        aggregate_type="policy_release_change",
        aggregate_id=str(uuid7()),  # each change is its own fact; the release id is in the data
        aggregate_version=1,
        data={
            "release_id": release.release_id,
            "change": change,
            "content_hash": release.content_hash,
            **extra,
        },
        classification="INTERNAL",
    )
    await record_audit(
        conn,
        release.tenant_id,
        AuditFacts(
            event_type="policy.release.changed",
            actor_type="HUMAN",
            action=change.lower(),
            target_type="policy_release",
            target_ref=release.release_id,
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E3 if change in ("PUBLISHED",) else EvidenceClass.E2,
            occurred_at=now,
            actor_principal_id=actor,
            source_service="policy_authoring",
            source_event_id=event_id,
            details={"change": change, "content_hash": release.content_hash},
        ),
    )


def parse_cases(raw: Sequence[Mapping[str, Any]]) -> tuple[PolicyCase, ...]:
    out = []
    for c in raw:
        try:
            out.append(
                PolicyCase(
                    name=str(c["name"]),
                    principal=str(c["principal"]),
                    action=str(c["action"]),
                    resource=str(c["resource"]),
                    context=dict(c.get("context") or {}),
                    expect_allow=bool(c.get("expect_allow", False)),
                )
            )
        except (KeyError, TypeError) as e:
            raise PolicyError(
                "POLICY_CASE_INVALID", "each case needs name, principal, action, resource"
            ) from e
    return tuple(out)


async def create_draft(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    name: str,
    policies: str,
    cases: tuple[PolicyCase, ...],
    actor: str,
    now: datetime,
) -> PolicyRelease:
    rid = str(uuid7())
    release = PolicyRelease(
        rid, tenant_id, policies, SCHEMA_VERSION, ReleaseStatus.DRAFT, cases, author=actor
    )
    await release_store.create_release(
        conn,
        release,
        policy_name=name,
        version=await release_store.next_version(conn, tenant_id, name),
    )
    await _record(conn, release, "DRAFTED", actor, now, policy_name=name)
    return release


async def edit_draft(
    conn: AsyncConnection,
    release_id: str,
    *,
    policies: str,
    cases: tuple[PolicyCase, ...],
    actor: str,
    now: datetime,
) -> PolicyRelease:
    rel = await release_store.load_release(conn, release_id)
    if rel.status is not ReleaseStatus.DRAFT:
        raise PolicyError(
            "POLICY_RELEASE_IMMUTABLE", "only a DRAFT can change; create a new release"
        )
    if rel.author != actor:
        raise PolicyError("POLICY_AUTHOR_ONLY", "only the author edits a draft")
    new = await release_store.update_draft(conn, release_id, policies=policies, cases=cases)
    await _record(conn, new, "EDITED", actor, now)
    return new


def _outcomes(
    release: PolicyRelease | None, cases: Sequence[PolicyCase], schema: Mapping[str, Any]
) -> list[bool]:
    if release is None:
        return [False] * len(cases)
    return [
        evaluate(
            release,
            principal_id=c.principal,
            action=c.action,
            resource_id=c.resource,
            context=c.context,
            schema=schema,
        ).allowed
        for c in cases
    ]


async def validate(
    conn: AsyncConnection,
    release_id: str,
    *,
    catalog: Mapping[str, Mapping[str, Any]],
    actor: str,
    now: datetime,
) -> Validation:
    rel = await release_store.move(
        conn, release_id, ReleaseStatus.VALIDATING, catalog=catalog, now=now
    )
    problems = prepublication_problems(rel, catalog)
    schema = build_schema(catalog)
    active = await release_store.active_release(conn, rel.tenant_id)
    before = _outcomes(active, rel.cases, schema)
    after = _outcomes(rel, rel.cases, schema)
    newly_allowed = [
        c.name for c, b, a in zip(rel.cases, before, after, strict=True) if a and not b
    ]
    newly_denied = [c.name for c, b, a in zip(rel.cases, before, after, strict=True) if b and not a]
    target = ReleaseStatus.DRAFT if problems else ReleaseStatus.REVIEW
    rel = await release_store.move(conn, release_id, target, catalog=catalog, now=now)
    await _record(
        conn,
        rel,
        "VALIDATED" if not problems else "VALIDATION_FAILED",
        actor,
        now,
        problems=len(problems),
    )
    return Validation(rel, problems, newly_allowed, newly_denied)


async def simulate(
    conn: AsyncConnection,
    release_id: str,
    *,
    principal: str,
    action: str,
    resource: str,
    context: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    rel = await release_store.load_release(conn, release_id)
    schema = build_schema(catalog)
    active = await release_store.active_release(conn, rel.tenant_id)

    def run(r: PolicyRelease | None) -> dict[str, Any] | None:
        if r is None:
            return None
        o = evaluate(
            r,
            principal_id=principal,
            action=action,
            resource_id=resource,
            context=dict(context),
            schema=schema,
        )
        return {"allowed": o.allowed, "determining": list(o.determining), "codes": list(o.codes)}

    return {
        "candidate": run(rel),
        "active": run(active) if active and active.release_id != rel.release_id else None,
    }


async def review(
    conn: AsyncConnection, release_id: str, *, reviewer: str, now: datetime
) -> PolicyRelease:
    rel = await release_store.load_release(conn, release_id)
    if rel.status is not ReleaseStatus.REVIEW:
        raise PolicyError(
            "POLICY_INVALID_TRANSITION", f"a {rel.status.value} release is not in review"
        )
    if reviewer == rel.author:
        raise PolicyError("POLICY_REVIEWER_IS_AUTHOR", "the author cannot review their own release")
    await release_store.add_reviewer(conn, release_id, reviewer)
    rel = await release_store.load_release(conn, release_id)
    await _record(conn, rel, "REVIEWED", reviewer, now)
    return rel


async def publish(
    conn: AsyncConnection,
    release_id: str,
    *,
    catalog: Mapping[str, Mapping[str, Any]],
    actor: str,
    now: datetime,
) -> PolicyRelease:
    rel = await release_store.load_release(conn, release_id)
    risky = high_risk(rel, catalog)
    rel = await release_store.move(
        conn, release_id, ReleaseStatus.ACTIVE, catalog=catalog, now=now, high_risk=risky
    )
    await _record(conn, rel, "PUBLISHED", actor, now, high_risk=risky)
    return rel

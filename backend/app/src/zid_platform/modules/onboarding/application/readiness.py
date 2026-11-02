"""Organization readiness, acknowledgements and activation (ZID-UX-WF-013C screen 1; slice L-13, CR-049).

Readiness is read, not written: the trust facts come from the services that own them, the required items are checked by the server, and the
state is derived each time (`domain/readiness.py`). The only things this module records are what a person did: they accepted a versioned
text, and the organization was activated once its gate passed. Nothing about payment, a plan or a register match can change an answer here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit, next_version

from zid_platform.modules.authentication.ports import passkey_summary
from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.onboarding.domain.readiness import (
    ACTIVATION_REFUSED,
    Projection,
    Setup,
    TrustFact,
    project,
)
from zid_platform.modules.onboarding.infrastructure import readiness_store as store
from zid_platform.modules.tenant.ports import (
    governance_status,
    load_tenant,
    roles_of_principal,
    tenant_roles,
)
from zid_platform.modules.verification.ports import person_assurance_current

_POLICY = (
    Path(os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config")
    / "onboarding"
    / "setup-policy.yaml"
)
_PRIVILEGED = frozenset({"OWNER", "ADMIN"})
_APPROVING = frozenset({"OWNER", "APPROVER"})
_LIVE = frozenset({"ACTIVE", "PENDING_VERIFICATION"})


class ReadinessError(Exception):
    def __init__(
        self, code: str, detail: str = "", *, blockers: tuple[str, ...] = (), persist: bool = False
    ) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.blockers = blockers
        self.persist = persist  # a denial was recorded and must survive the refusal


@lru_cache(maxsize=1)
def setup_policy() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(_POLICY.read_text(encoding="utf-8")) or {}
    return doc


def current_versions() -> dict[str, str]:
    return {k: str(v["version"]) for k, v in (setup_policy().get("acknowledgements") or {}).items()}


async def require_actor(conn: AsyncConnection, actor: str) -> None:
    """An Owner or Administrator of this organization (the bootstrap Owner included). Nobody else changes its setup."""
    held = {r.role.value for r in await roles_of_principal(conn, actor) if r.status.value in _LIVE}
    if not held & _PRIVILEGED:
        raise ReadinessError("SETUP_NOT_PERMITTED", "An Owner or Administrator completes setup.")


async def require_member(conn: AsyncConnection, actor: str) -> None:
    """Any live member of this organization may read its setup; a stranger gets no answer at all."""
    if not [
        r for r in await roles_of_principal(conn, actor) if r.status.value in (*_LIVE, "INVITED")
    ]:
        raise ReadinessError("ORGANIZATION_NOT_FOUND", "No such organization.")


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def trust_facts(claim: dict[str, Any] | None, *, identity_current: bool) -> list[TrustFact]:
    """The four assertions, as the trust services returned them. No claim means none of them has started."""
    started = claim is not None
    checks = (claim or {}).get("checks", {})
    company = (claim or {}).get("company") or {}
    screening = (claim or {}).get("screening") or {}
    person = "COMPLETED" if identity_current else str(checks.get("person_assurance", "PENDING"))
    return [
        TrustFact(
            "company",
            str(checks.get("company", "PENDING")),
            _when(company.get("expires_at")),
            started,
        ),
        TrustFact("person_assurance", person, None, started or identity_current),
        TrustFact("authority", str(checks.get("authority", "PENDING")), None, started),
        TrustFact(
            "risk", str(checks.get("risk", "PENDING")), _when(screening.get("expires_at")), started
        ),
    ]


@dataclass(frozen=True, slots=True)
class Gathered:
    projection: Projection
    acknowledged: dict[str, bool]
    activation: dict[str, Any] | None
    region: str
    claim_started: bool


async def gather(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    claim: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> Gathered:
    now = now or datetime.now(UTC)
    versions = current_versions()
    held = {(a.kind, a.version) for a in await store.acknowledgements(conn)}
    acked = {kind: (kind, v) in held for kind, v in versions.items()}
    roles = await tenant_roles(conn)
    privileged = {
        r.principal_id for r in roles if r.role.value in _PRIVILEGED and r.status.value in _LIVE
    }
    with_keys = set(await passkey_summary(conn, sorted(privileged))) if privileged else set()
    missing = len(privileged - with_keys)
    g = await governance_status(conn, tenant_id)
    actor_approves = any(
        r.principal_id == actor and r.role.value in _APPROVING and r.status.value == "ACTIVE"
        for r in roles
    )
    independent = max(0, int(g.eligible_human_member_count) - (1 if actor_approves else 0)) + (
        1 if g.independent_routes else 0
    )
    tenant = await load_tenant(conn, tenant_id)
    activation = await store.activation(conn)
    projection = project(
        trust_facts(claim, identity_current=await person_assurance_current(conn, actor, now)),
        Setup(
            controls_accepted=acked.get("ORGANIZATIONAL_CONTROLS", False),
            privileged_accounts_secured=bool(privileged) and missing == 0,
            region_policy_confirmed=acked.get("REGION_POLICY", False),
            independent_approvers=independent,
            privileged_without_passkey=missing,
        ),
        claim_started=claim is not None,
        activated=activation is not None,
        any_acknowledgement=any(acked.values()),
        now=now,
    )
    return Gathered(
        projection, acked, activation, tenant.home_cell_id if tenant else "", claim is not None
    )


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    event: str,
    outcome: str,
    reason: str | None,
    details: dict[str, Any],
    now: datetime,
) -> None:
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=event,
            actor_type="HUMAN",
            action=event.rsplit(".", 1)[-1],
            target_type="organization_setup",
            target_ref=tenant_id,
            outcome=outcome,
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor,
            reason_code=reason,
            source_service="onboarding",
            source_event_id=_event_id(),
            details=details,
        ),
    )


def _event_id() -> str:
    from zid_context import uuid7

    return str(uuid7())


async def _emit(conn: AsyncConnection, tenant_id: str, name: str, data: dict[str, Any]) -> None:
    await emit(
        conn,
        name=name,
        tenant_id=tenant_id,
        aggregate_type="organization_setup",
        aggregate_id=tenant_id,
        aggregate_version=await next_version(conn, "organization_setup", tenant_id),
        data=data,
    )


async def acknowledge(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    kind: str,
    version: str,
    locale: str | None,
    idempotency_key: str | None,
    now: datetime | None = None,
) -> bool:
    """Record that this organization accepted this text at this version. True if it was new, False if it was already accepted (a retry)."""
    now = now or datetime.now(UTC)
    await require_actor(conn, actor)
    wanted = current_versions().get(kind)
    if wanted is None:
        raise ReadinessError(
            "ACKNOWLEDGEMENT_UNKNOWN", "That is not something this organization is asked to accept."
        )
    if version != wanted:  # they read an older text: show the current one
        raise ReadinessError(
            "ACKNOWLEDGEMENT_VERSION_STALE", "A newer version is current. Read it and accept it."
        )
    created = await store.insert_ack(
        conn, tenant_id=tenant_id, kind=kind, version=version, actor=actor, locale=(locale or "und")[:35],
        idempotency_key=idempotency_key, now=now,
    )  # fmt: skip
    if created:
        await _audit(
            conn,
            tenant_id,
            actor,
            "onboarding.organization.acknowledged",
            "SUCCESS",
            None,
            {"kind": kind, "version": version, "locale": locale or "und"},
            now,
        )
        await _emit(
            conn,
            tenant_id,
            "onboarding.organization.acknowledged",
            {"kind": kind, "version": version},
        )
    return created


async def activate(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    claim: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> tuple[Gathered, bool]:
    """The activation gate. Every trust assertion must be verified and fresh and every required item done; nothing else opens it, and there
    is no payment, plan or override shortcut. A failed attempt is recorded and refused with the reasons and what to do next."""
    now = now or datetime.now(UTC)
    await require_actor(conn, actor)
    got = await gather(conn, tenant_id, actor, claim, now=now)
    if got.activation is not None:
        return got, False  # already active: the same answer, nothing new recorded
    if not got.projection.can_activate:
        await _audit(conn, tenant_id, actor, ACTIVATION_REFUSED, "DENIED", "ACTIVATION_BLOCKED",
                     {"state": got.projection.state.value, "blockers": len(got.projection.blockers)}, now)  # fmt: skip
        raise ReadinessError(
            "ACTIVATION_BLOCKED",
            "The organization cannot be activated yet.",
            blockers=got.projection.blockers,
            persist=True,
        )
    report = {i.key: i.word for i in (*got.projection.trust, *got.projection.required)}
    created = await store.insert_activation(
        conn, tenant_id=tenant_id, actor=actor, gate_report=report,
        policy_versions={**current_versions(), "setup": str(setup_policy().get("version", ""))}, now=now,
    )  # fmt: skip
    if created:
        await _audit(
            conn,
            tenant_id,
            actor,
            "onboarding.organization.activated",
            "SUCCESS",
            None,
            {"gate": report},
            now,
        )
        await _emit(
            conn,
            tenant_id,
            "onboarding.organization.activated",
            {"policy_versions": current_versions()},
        )
    return await gather(conn, tenant_id, actor, claim, now=now), created

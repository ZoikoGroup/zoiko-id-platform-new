"""Residency service: placement record, transfer rules and the transfer gateway decision
(ZID-TENANCY-001 §11, §13, §19; F-W5). Every change and every restricted transfer decision leaves a
protected AuditRecord (E2/E3, TENANCY §11 "Evidence"); denials are audited too (TEN-21).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_outbox import emit

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, commitment, record_audit
from zid_platform.modules.privacy.ports import pack_transfer_mechanisms
from zid_platform.modules.tenant.domain.residency import (
    CATEGORY_CLASS,
    PROFILES,
    Residency,
    ResidencyError,
    TransferDecision,
    TransferRule,
    check_placement,
    check_transfer,
    dependency_findings,
    operator_jurisdictions,
)
from zid_platform.modules.tenant.infrastructure import residency_store as store
from zid_platform.modules.tenant.infrastructure import tenant_store
from zid_platform.modules.tenant.infrastructure.catalog import catalog


def _now() -> datetime:
    return datetime.now(UTC)


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    event_id: str | None,
    name: str,
    *,
    actor: str | None,
    target_type: str,
    target_ref: str,
    outcome: str,
    reason: str | None,
    details: dict[str, object],
    evidence_class: EvidenceClass = EvidenceClass.E3,
) -> None:
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=name,
            actor_type="HUMAN" if actor else "SERVICE",
            action=name.rsplit(".", 1)[-1],
            target_type=target_type,
            target_ref=target_ref,
            outcome=outcome,
            reason_code=reason,
            evidence_class=evidence_class,
            occurred_at=_now(),
            actor_principal_id=actor,
            source_service="tenant",
            source_event_id=event_id,
            details=details,
        ),
    )


async def set_residency(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    profile: str,
    home_cell_id: str,
    recovery_cells: Sequence[str],
    prohibited_cells: Sequence[str],
    operator_juris: Sequence[str],
    key_profile: str,
    jurisdiction_pack_id: str,
    approval_ref: str,
    actor: str,
) -> Residency:
    """Assign or change the tenant's residency profile (a stricter profile only moves forward
    through approval; the capability check runs against the cell catalog, TENANCY §13)."""
    check_placement(catalog(), profile, home_cell_id, recovery_cells)
    tenant_row = await tenant_store.load_tenant(conn, tenant_id)
    if (
        tenant_row is not None
        and tenant_row.home_cell_id
        and tenant_row.home_cell_id != home_cell_id
    ):
        # Moving a company to another cell is a migration, not a profile edit: pointing the profile at a cell that
        # does not serve the company would make every request for it fail with CELL_NOT_AUTHORITATIVE.
        raise ResidencyError(
            "PLACEMENT_MOVE_NOT_ALLOWED",
            f"{tenant_row.home_cell_id} is this company's home cell; moving to {home_cell_id} is a migration",
        )
    ops = tuple(sorted({j.strip().upper() for j in operator_juris}))
    if any(len(j) != 2 or not j.isalpha() for j in ops):
        raise ResidencyError(
            "RESIDENCY_PROFILE_UNSUPPORTED", "operator jurisdictions are ISO 3166-1 alpha-2"
        )
    if PROFILES.index(profile) >= 2 and not ops:
        raise ResidencyError(
            "SOVEREIGN_OPERATOR_DENIED",
            "SRP-2/3 tenants must name their permitted operator jurisdictions",
        )
    pack_transfer_mechanisms(jurisdiction_pack_id)  # the pack must exist
    from zid_platform.modules.tenant.application.routing import forget

    forget(tenant_id)
    previous = await store.load(conn, tenant_id, lock=True)
    r = await store.save(
        conn,
        {
            "tenant_id": tenant_id,
            "residency_profile_id": profile,
            "home_cell_id": home_cell_id,
            "permitted_recovery_cells": list(recovery_cells),
            "prohibited_cells": list(prohibited_cells),
            "operator_jurisdictions": list(ops),
            "key_profile": key_profile,
            "jurisdiction_pack_id": jurisdiction_pack_id,
            "approval_ref": approval_ref,
            "updated_by": actor,
        },
    )
    data = {
        "residency_profile_id": r.profile,
        "previous_profile_id": previous.profile if previous else None,
        "home_cell_id": r.home_cell_id,
        "placement_epoch": r.placement_epoch,
        "revision": r.revision,
        "approval_ref_commitment": commitment(approval_ref, tenant_id=tenant_id, kind="approval"),
        "evidence_class": "E3",
    }
    event_id = await emit(
        conn,
        name="tenant.residency_profile.changed",
        tenant_id=tenant_id,
        aggregate_type="tenant_residency",
        aggregate_id=tenant_id,
        aggregate_version=r.revision,
        data=data,
    )
    await _audit(
        conn,
        tenant_id,
        event_id,
        "tenant.residency_profile.changed",
        actor=actor,
        target_type="tenant_residency",
        target_ref=tenant_id,
        outcome="SUCCESS",
        reason=None,
        details={k: v for k, v in data.items() if k != "evidence_class"},
    )
    return r


async def add_rule(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    data_class: str,
    destination_cell_id: str,
    purpose_code: str,
    mechanism: str,
    field_allowlist: Sequence[str],
    approval_ref: str,
    created_by: str,
    approved_by: str,
    valid_to: datetime | None,
) -> TransferRule:
    r = await store.load(conn, tenant_id)
    if r is None:
        raise ResidencyError("RESIDENCY_PROFILE_UNSUPPORTED", "assign a residency profile first")
    if created_by == approved_by:
        raise ResidencyError(
            "RESIDENCY_TRANSFER_DENIED", "a transfer rule needs an independent approver"
        )
    if mechanism not in pack_transfer_mechanisms(r.jurisdiction_pack_id):
        raise ResidencyError(
            "RESIDENCY_TRANSFER_DENIED",
            f"{mechanism} is not a mechanism of {r.jurisdiction_pack_id}",
        )
    if r.profile == "SRP-3" and data_class == "DC3":
        raise ResidencyError("RESIDENCY_TRANSFER_DENIED", "SRP-3 DC3 data never leaves its cell")
    rule = await store.insert_rule(
        conn,
        {
            "rule_id": str(uuid7()),
            "tenant_id": tenant_id,
            "data_class": data_class,
            "destination_cell_id": destination_cell_id,
            "purpose_code": purpose_code,
            "mechanism": mechanism,
            "jurisdiction_pack_id": r.jurisdiction_pack_id,
            "field_allowlist": sorted(set(field_allowlist)),
            "approval_ref": approval_ref,
            "created_by": created_by,
            "approved_by": approved_by,
            "valid_from": _now(),
            "valid_to": valid_to,
        },
    )
    data = {
        "rule_id": rule.rule_id,
        "data_class": data_class,
        "destination_cell_id": destination_cell_id,
        "purpose_code": purpose_code,
        "mechanism": mechanism,
        "evidence_class": "E3",
    }
    event_id = await emit(
        conn,
        name="privacy.transfer_policy.changed",
        tenant_id=tenant_id,
        aggregate_type="residency_transfer_rule",
        aggregate_id=rule.rule_id,
        aggregate_version=1,
        data=data,
    )
    await _audit(
        conn,
        tenant_id,
        event_id,
        "privacy.transfer_policy.changed",
        actor=created_by,
        target_type="residency_transfer_rule",
        target_ref=rule.rule_id,
        outcome="SUCCESS",
        reason=None,
        details={k: v for k, v in data.items() if k != "evidence_class"},
    )
    return rule


async def suspend_rule(
    conn: AsyncConnection, tenant_id: str, rule_id: str, *, actor: str
) -> TransferRule:
    rule = await store.set_rule_status(conn, rule_id, "SUSPENDED")
    if rule is None:
        raise ResidencyError("NOT_FOUND", "unknown or retired transfer rule")
    await _audit(
        conn,
        tenant_id,
        None,
        "privacy.transfer_policy.changed",
        actor=actor,
        target_type="residency_transfer_rule",
        target_ref=rule_id,
        outcome="SUCCESS",
        reason="SUSPENDED",
        details={"rule_id": rule_id, "status": "SUSPENDED"},
    )
    return rule


async def authorize_transfer(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    data_class: str,
    destination_cell_id: str,
    purpose_code: str,
    actor: str | None = None,
    now: datetime | None = None,
) -> TransferDecision:
    """The cross-cell transfer gateway decision (TENANCY §11). Fails closed without a residency
    record for restricted data. Restricted decisions - allowed or denied - are evidenced."""
    now = now or _now()
    r = await store.load(conn, tenant_id)
    if r is None:
        decision = TransferDecision(
            data_class == "DC0",
            "ALLOWED" if data_class == "DC0" else "RESIDENCY_PROFILE_UNSUPPORTED",
        )
    else:
        decision = check_transfer(
            catalog(),
            r,
            data_class=data_class,
            destination_cell_id=destination_cell_id,
            purpose_code=purpose_code,
            rules=await store.rules(conn, active_only=True),
            pack_mechanisms=pack_transfer_mechanisms(r.jurisdiction_pack_id),
            now=now,
        )
    if data_class != "DC0" and destination_cell_id != (r.home_cell_id if r else None):
        transfer_id = str(uuid.uuid4())
        await _audit(
            conn,
            tenant_id,
            None,
            "cross_cell_transfer.authorized" if decision.allowed else "cross_cell_transfer.denied",
            actor=actor,
            target_type="cross_cell_transfer",
            target_ref=transfer_id,
            outcome="SUCCESS" if decision.allowed else "DENIED",
            reason=decision.code,
            details={
                "data_class": data_class,
                "destination_cell_id": destination_cell_id,
                "purpose_code": purpose_code,
                "rule_id": decision.rule_id,
                "cross_border": decision.cross_border,
            },
            evidence_class=EvidenceClass.E3 if data_class == "DC3" else EvidenceClass.E2,
        )
    return decision


async def check_category_transfer(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    data_category: str,
    destination_cell_id: str,
    purpose_code: str,
) -> TransferDecision:
    """Privacy-side entry point: a PRIV-001 data category (D0-D4) instead of a data class."""
    return await authorize_transfer(
        conn,
        tenant_id,
        data_class=CATEGORY_CLASS.get(data_category, "DC3"),
        destination_cell_id=destination_cell_id,
        purpose_code=purpose_code,
    )


async def check_dependency_locations(
    conn: AsyncConnection, tenant_id: str, locations: Sequence[str]
) -> None:
    """TEN-20: refuse a dependency (a subprocessor behind a feature) whose locations cannot satisfy the tenant's
    residency profile. `conn` is scoped to `tenant_id`."""
    problems = dependency_findings(catalog(), await store.load(conn, tenant_id), locations)
    if problems:
        raise ResidencyError("DEPENDENCY_RESIDENCY_UNMET", "; ".join(problems))


REMOTE_SUPPORT = "REMOTE_SUPPORT"  # the purpose code a transfer rule names for support staff reading data from abroad


async def authorize_remote_access(
    conn: AsyncConnection, tenant_id: str, operator_jurisdiction: str, *, actor: str | None = None
) -> None:
    """PRIV-25: support staff reading a company's data from outside its region need an approved transfer for that.

    Reading customer data from a country is a cross-border transfer into it. Inside the company's region (its home
    country for SRP-2/3, its legal zone for SRP-1) nothing more is needed; from anywhere else there must be an
    ACTIVE, independently approved transfer rule for restricted data to a cell in that country for REMOTE_SUPPORT, or
    the access is refused. No profile, or SRP-0, makes no regional promise. `conn` is scoped to `tenant_id`."""
    r = await store.load(conn, tenant_id)
    if r is None or r.level < 1:
        return
    cat = catalog()
    home = cat.cell(r.home_cell_id)
    j = operator_jurisdiction.upper()
    zone = {
        c.country.upper()
        for c in cat.cells.values()
        if c.country and c.legal_zone == home.legal_zone
    }
    if j in ({home.country.upper()} if r.level >= 2 and home.country else zone):
        return
    destination = next(
        (c.cell_id for c in cat.cells.values() if c.country and c.country.upper() == j), None
    )
    if destination is None:
        raise ResidencyError(
            "REMOTE_ACCESS_TRANSFER_REQUIRED", f"no approved route for support access from {j}"
        )
    decision = await authorize_transfer(
        conn,
        tenant_id,
        data_class="DC2",
        destination_cell_id=destination,
        purpose_code=REMOTE_SUPPORT,
        actor=actor,
    )
    if not decision.allowed:
        raise ResidencyError(
            "REMOTE_ACCESS_TRANSFER_REQUIRED",
            f"support access from {j} needs an approved transfer rule for {REMOTE_SUPPORT}",
        )


async def effective_operator_jurisdictions(
    conn: AsyncConnection, tenant_id: str, configured: Sequence[str]
) -> tuple[str, ...]:
    """`conn` is scoped to `tenant_id`. SRP-2/3 restrict operators even when the tenant's own
    operator-access policy is empty (TENANCY §19; TEN-19)."""
    return operator_jurisdictions(catalog(), await store.load(conn, tenant_id), configured)


__all__ = [
    "add_rule",
    "authorize_remote_access",
    "authorize_transfer",
    "check_category_transfer",
    "check_dependency_locations",
    "effective_operator_jurisdictions",
    "set_residency",
    "suspend_rule",
]

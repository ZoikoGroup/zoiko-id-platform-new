"""Privacy control plane (ZID-PRIV-001; DEC-F1, DEC-F3; F-W3).

Registry   purposes, processing activities, role map (DEC-F3: no ACTIVE activity without one)
Runtime    `check_processing` at every processing boundary that can vary by purpose
Consent    only where the activity's basis is consent (PRIV-04); withdrawal is immutable evidence
Rights     case-managed requests with proportional verification, jurisdiction deadlines,
           per-item fulfilment and evidence; never COMPLETED while a stage is pending
Retention  data map entries with fixed due dates; the worker disposes what is due and not held

Evidence: every consent, rights, restriction, retention and role-map change writes a protected
AuditRecord (EVIDENCE-001 §7.1 "Privacy") in the same transaction.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_evidence import canonicalize, hash_bytes, hash_json
from zid_outbox import emit

from zid_platform.modules.evidence.ports import (
    AuditFacts,
    EvidenceClass,
    commitment,
    erase_subject,
    is_held,
    principal_handle,
    read_object,
    receipt_summary,
    record_audit,
    store_object,
)
from zid_platform.modules.privacy.application import registry
from zid_platform.modules.privacy.domain.model import (
    NO_VERIFICATION,
    PREFERENCE_FOR,
    PREFERENCE_TYPES,
    TERMINAL_RIGHTS,
    DataCategory,
    LawfulBasis,
    PrivacyError,
    ProcessingDecision,
    RetentionTrigger,
    RightsStatus,
    RightsType,
    ZoikoRole,
    check_code,
    check_consent_basis,
    due_at,
    initial_verification,
    require_disclosure_verification,
    summarize,
)
from zid_platform.modules.privacy.domain.model import (
    check_processing as decide_processing,
)
from zid_platform.modules.privacy.infrastructure import store
from zid_platform.modules.privacy.infrastructure.packs import pack

EXPORT_TTL = timedelta(days=7)
# store -> disposer(conn, data map entry row, action) for governed deletion (PRIV-001 §17)
Disposer = Callable[[AsyncConnection, Row[Any], str], Awaitable[None]]
# store -> exporter(conn, data map entry row) -> JSON-able data for access/portability (§15, §19)
Exporter = Callable[[AsyncConnection, Row[Any]], Awaitable[Any]]
# store -> locator(conn, subject) -> the subject's objects in that store (data-map discovery, §15)
Locator = Callable[[AsyncConnection, str], Awaitable[list[dict[str, Any]]]]
_DISPOSERS: dict[str, Disposer] = {}
_EXPORTERS: dict[str, Exporter] = {}
_LOCATORS: dict[str, Locator] = {}


def register_store(
    store_name: str,
    *,
    disposer: Disposer | None = None,
    exporter: Exporter | None = None,
    locator: Locator | None = None,
) -> None:
    """A module that keeps personal data registers how it is found, exported and disposed.

    The owning module's adapter touches only its own tables; privacy orchestrates."""
    if disposer:
        _DISPOSERS[store_name] = disposer
    if exporter:
        _EXPORTERS[store_name] = exporter
    if locator:
        _LOCATORS[store_name] = locator


def registered_stores() -> list[str]:
    return sorted(set(_DISPOSERS) | set(_EXPORTERS) | set(_LOCATORS))


async def discover_subject(conn: AsyncConnection, tenant_id: str, subject_principal_id: str) -> int:
    """Refresh the data map from every registered store's locator (PRIV-001 §15 "discover
    processing activities + subject mappings"): rights requests then cover data written by any
    module, whether or not it registered the object when it wrote it."""
    found = 0
    for store_name, locate in sorted(_LOCATORS.items()):
        for obj in await locate(conn, subject_principal_id):
            await register_personal_data(
                conn,
                tenant_id,
                subject_principal_id=subject_principal_id,
                store_name=store_name,
                object_type=obj["object_type"],
                object_ref=obj["object_ref"],
                purpose_code=obj["purpose_code"],
                data_category=obj["data_category"],
                retention_policy_code=obj.get("retention_policy_code"),
                trigger_at=obj.get("trigger_at"),
                region=obj.get("region"),
            )
            found += 1
    return found


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
    outcome: str = "SUCCESS",
    reason: str | None = None,
    details: Mapping[str, Any] | None = None,
    evidence_class: EvidenceClass = EvidenceClass.E2,
) -> None:
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=name,
            actor_type="HUMAN" if actor else "SYSTEM",
            action=name.rsplit(".", 1)[-1],
            target_type=target_type,
            target_ref=target_ref,
            outcome=outcome,
            reason_code=reason,
            evidence_class=evidence_class,
            occurred_at=_now(),
            actor_principal_id=actor,
            source_service="privacy",
            source_event_id=event_id,
            details=dict(details or {}),
        ),
    )


async def _event(
    conn: AsyncConnection,
    tenant_id: str,
    name: str,
    aggregate: tuple[str, str],
    data: Mapping[str, Any],
    *,
    actor: str | None,
    version: int = 1,
    evidence_class: EvidenceClass = EvidenceClass.E2,
    outcome: str = "SUCCESS",
    reason: str | None = None,
) -> None:
    """Outbox event + its protected audit record. Payloads carry ids, codes and commitments."""
    event_id = await emit(
        conn,
        name=name,
        tenant_id=tenant_id,
        aggregate_type=aggregate[0],
        aggregate_id=aggregate[1],
        aggregate_version=version,
        data={**data, "evidence_class": evidence_class.value},
    )
    await _audit(
        conn,
        tenant_id,
        event_id,
        name,
        actor=actor,
        target_type=aggregate[0],
        target_ref=aggregate[1],
        outcome=outcome,
        reason=reason,
        details=data,
        evidence_class=evidence_class,
    )


def _categories(values: Sequence[str]) -> list[str]:
    try:
        return sorted({DataCategory(v).value for v in values})
    except ValueError as e:
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", "data categories are D0-D4") from e


# ----------------------------------------------------------------------------- purpose registry


async def create_purpose(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    code: str,
    description: str,
    data_categories: Sequence[str],
    recipient_classes: Sequence[str] = (),
    compatible_secondary_purposes: Sequence[str] = (),
    prohibited_uses: Sequence[str] = (),
    retention_policy_code: str | None = None,
    actor: str,
) -> str:
    """A new DRAFT version; a purpose is immutable once ACTIVE (§3)."""
    check_code(code, "purpose_code")
    purpose_id = str(uuid7())
    version = await store.next_version(conn, "purpose", "purpose_code", code, "purpose_version")
    await store.insert_purpose(
        conn,
        {
            "purpose_id": purpose_id,
            "tenant_id": tenant_id,
            "purpose_code": code,
            "purpose_version": version,
            "description": description,
            "allowed_data_categories": _categories(data_categories),
            "allowed_recipient_classes": sorted(set(recipient_classes)),
            "compatible_secondary_purposes": sorted(set(compatible_secondary_purposes)),
            "prohibited_uses": sorted(set(prohibited_uses)),
            "retention_policy_code": retention_policy_code,
            "created_by": actor,
        },
    )
    return purpose_id


async def activate_purpose(
    conn: AsyncConnection, tenant_id: str, purpose_id: str, *, approver: str
) -> Row[Any]:
    """Approval by someone other than the author (PRIV-27: no purpose expansion without review)."""
    draft = next(
        (
            p
            for p in await store.list_purposes(conn, status="DRAFT")
            if str(p.purpose_id) == purpose_id
        ),
        None,
    )
    if draft is not None and str(draft.created_by) == approver:
        raise PrivacyError(
            "PRIVACY_APPROVAL_REQUIRED", "the author cannot activate their own purpose"
        )
    row = await store.activate_purpose(conn, purpose_id, approver, _now())
    if row is None:
        raise PrivacyError("NOT_FOUND", "unknown purpose")
    if row.status != "ACTIVE":
        raise PrivacyError("PRIVACY_INVALID_STATE", f"purpose is {row.status}")
    await _event(
        conn,
        tenant_id,
        "privacy.processing_activity.changed",
        ("purpose", purpose_id),
        {
            "purpose_code": row.purpose_code,
            "purpose_version": row.purpose_version,
            "change": "PURPOSE_ACTIVATED",
        },
        actor=approver,
        evidence_class=EvidenceClass.E3,
    )
    return row


# ------------------------------------------------------------------------ processing activities


async def create_activity(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    code: str,
    name: str,
    purpose_codes: Sequence[str],
    data_categories: Sequence[str],
    lawful_basis: str,
    jurisdiction_pack_id: str,
    data_subject_classes: Sequence[str] = (),
    special_category_condition: str | None = None,
    source_classes: Sequence[str] = (),
    recipient_classes: Sequence[str] = (),
    retention_policy_code: str | None = None,
    residency_profile: str | None = None,
    dpia_ref: str | None = None,
    launch_flow: bool = False,
    actor: str,
) -> str:
    check_code(code, "activity_code")
    for p in purpose_codes:
        check_code(p, "purpose_code")
    pack(jurisdiction_pack_id)
    try:
        basis = LawfulBasis(lawful_basis)
    except ValueError as e:
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", "unknown lawful basis") from e
    cats = _categories(data_categories)
    if "D3" in cats and not special_category_condition and basis is not LawfulBasis.CONSENT:
        raise PrivacyError(
            "PRIVACY_SCHEMA_INVALID", "D3 processing needs a special-category condition (§4, §6)"
        )
    activity_id = str(uuid7())
    await store.insert_activity(
        conn,
        {
            "processing_activity_id": activity_id,
            "tenant_id": tenant_id,
            "activity_code": code,
            "version": await store.next_version(
                conn, "processing_activity", "activity_code", code, "version"
            ),
            "name": name,
            "purpose_codes": sorted(set(purpose_codes)),
            "data_categories": cats,
            "data_subject_classes": sorted(set(data_subject_classes)),
            "lawful_basis": basis.value,
            "special_category_condition": special_category_condition,
            "source_classes": sorted(set(source_classes)),
            "recipient_classes": sorted(set(recipient_classes)),
            "retention_policy_code": retention_policy_code,
            "residency_profile": residency_profile,
            "jurisdiction_pack_id": jurisdiction_pack_id,
            "dpia_ref": dpia_ref,
            "launch_flow": launch_flow,
            "created_by": actor,
        },
    )
    return activity_id


async def assign_role(
    conn: AsyncConnection,
    tenant_id: str,
    activity_id: str,
    *,
    data_controller_principal_id: str,
    zoiko_role: str,
    jurisdiction_pack_id: str,
    approval_ref: str,
    created_by: str,
    approved_by: str,
    processor_principal_ids: Sequence[str] = (),
    recipient_classes: Sequence[str] = (),
    contract_or_dpa_ref: str | None = None,
    effective_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> Row[Any]:
    """DEC-F3 §2 role map record. Legal owns the answer (approval_ref = counsel's approval);
    engineering enforces that it exists, is independent and is current."""
    try:
        role = ZoikoRole(zoiko_role)
    except ValueError as e:
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", "unknown zoiko_role") from e
    if created_by == approved_by:
        raise PrivacyError(
            "PRIVACY_APPROVAL_REQUIRED", "a role assignment needs an independent approver"
        )
    if role is ZoikoRole.PROCESSOR and not contract_or_dpa_ref:
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", "a processor role needs its DPA reference")
    if await store.load_activity(conn, activity_id) is None:
        raise PrivacyError("NOT_FOUND", "unknown processing activity")
    pack(jurisdiction_pack_id)
    assignment_id = str(uuid7())
    row = await store.insert_assignment(
        conn,
        {
            "assignment_id": assignment_id,
            "tenant_id": tenant_id,
            "processing_activity_id": activity_id,
            "data_controller_principal_id": data_controller_principal_id,
            "zoiko_role": role.value,
            "processor_principal_ids": list(processor_principal_ids),
            "recipient_classes": sorted(set(recipient_classes)),
            "jurisdiction_pack_id": jurisdiction_pack_id,
            "contract_or_dpa_ref": contract_or_dpa_ref,
            "effective_from": effective_from or _now(),
            "valid_to": valid_to,
            "approval_ref": approval_ref,
            "created_by": created_by,
            "approved_by": approved_by,
        },
    )
    await _event(
        conn,
        tenant_id,
        "privacy.processing_activity.changed",
        ("processing_role_assignment", assignment_id),
        {
            "processing_activity_id": activity_id,
            "zoiko_role": role.value,
            "jurisdiction_pack_id": jurisdiction_pack_id,
            "approval_ref_commitment": commitment(
                approval_ref, tenant_id=tenant_id, kind="approval"
            ),
            "change": "ROLE_ASSIGNED",
        },
        actor=approved_by,
        evidence_class=EvidenceClass.E3,
    )
    return row


async def activate_activity(
    conn: AsyncConnection, tenant_id: str, activity_id: str, *, actor: str
) -> Row[Any]:
    """DRAFT/APPROVED -> ACTIVE: every purpose ACTIVE, a DPIA for D3, an approved role (trigger)."""
    row = await store.load_activity(conn, activity_id, lock=True)
    if row is None:
        raise PrivacyError("NOT_FOUND", "unknown processing activity")
    if row.status not in ("DRAFT", "APPROVED"):
        raise PrivacyError("PRIVACY_INVALID_STATE", f"activity is {row.status}")
    for p in row.purpose_codes:
        if await store.active_purpose(conn, p) is None:
            raise PrivacyError("PRIVACY_PURPOSE_NOT_ACTIVE", p)
    if "D3" in row.data_categories and not row.dpia_ref:
        raise PrivacyError("PRIVACY_DPIA_REQUIRED", "new D3 processing needs a DPIA (§25)")
    if not await _has_role(conn, activity_id):
        raise PrivacyError(
            "PRIVACY_ROLE_ASSIGNMENT_MISSING", "no approved role assignment (DEC-F3)"
        )
    out = await store.set_activity_status(conn, activity_id, "ACTIVE", _now())
    await _event(
        conn,
        tenant_id,
        "privacy.processing_activity.changed",
        ("processing_activity", activity_id),
        {"activity_code": out.activity_code, "version": out.version, "change": "ACTIVATED"},
        actor=actor,
        evidence_class=EvidenceClass.E3,
        version=out.version,
    )
    return out


async def _has_role(conn: AsyncConnection, activity_id: str) -> bool:
    now = _now()
    return any(
        a.status == "APPROVED"
        and a.effective_from <= now
        and (a.valid_to is None or a.valid_to > now)
        for a in await store.assignments(conn, activity_id)
    )


async def release_check(conn: AsyncConnection) -> list[dict[str, Any]]:
    """DEC-F3 release check: launch flows that would block G4 (no approved role assignment)."""
    return store.rows(await store.launch_flows_without_role(conn, _now()))


# ---------------------------------------------------------------------------- runtime check


async def check_processing(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    activity_code: str,
    purpose_code: str,
    data_category: str,
    subject_principal_id: str | None,
    recipient_class: str | None = None,
    destination_cell_id: str | None = None,
) -> ProcessingDecision:
    """May this processing happen now? (PRIV-01/02/05/18/24). Default deny, typed reason."""
    activity = await store.active_activity(conn, activity_code)
    purpose = await store.active_purpose(conn, purpose_code)
    consent_id, restricted = None, []
    if subject_principal_id:
        consent = await store.active_consent(conn, subject_principal_id, purpose_code, _now())
        consent_id = str(consent.consent_id) if consent else None
        restricted = [
            r.purpose_code for r in await store.restrictions_of(conn, subject_principal_id)
        ]
    decision = decide_processing(
        activity=activity,
        purpose=purpose,
        purpose_code=purpose_code,
        data_category=data_category,
        recipient_class=recipient_class,
        consent_id=consent_id,
        restricted_purposes=restricted,
        activity_code=activity_code,
    )
    if decision.allowed and destination_cell_id:
        from zid_platform.modules.tenant.ports import check_category_transfer  # avoid import cycle

        transfer = await check_category_transfer(
            conn,
            tenant_id,
            data_category=data_category,
            destination_cell_id=destination_cell_id,
            purpose_code=purpose_code,
        )
        if not transfer.allowed:
            return ProcessingDecision(False, transfer.code, activity_code, purpose_code)
    return decision


def pack_transfer_mechanisms(pack_id: str) -> tuple[str, ...]:
    return pack(pack_id).transfer_mechanisms


# ------------------------------------------------------------------------------------- consent


async def give_consent(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    subject_principal_id: str,
    activity_code: str,
    purpose_code: str,
    notice_version: str,
    capture_method: str,
    affirmative_action: str,
    processing_operations: Sequence[str] = ("process",),
    expires_at: datetime | None = None,
) -> str:
    """Capture consent for a consent-based activity; a renewal supersedes the previous one."""
    activity = await store.active_activity(conn, activity_code)
    if activity is None:
        raise PrivacyError("PRIVACY_ACTIVITY_NOT_ACTIVE", activity_code)
    check_consent_basis(activity.lawful_basis)
    if purpose_code not in activity.purpose_codes:
        raise PrivacyError("PRIVACY_PURPOSE_NOT_DECLARED", purpose_code)
    purpose = await store.active_purpose(conn, purpose_code)
    if purpose is None:
        raise PrivacyError("PRIVACY_PURPOSE_NOT_ACTIVE", purpose_code)
    await registry.require_notice(conn, notice_version)  # PRIV-26: the exact notice shown
    consent_id, now = str(uuid7()), _now()
    body = {
        "consent_id": consent_id,
        "tenant_id": tenant_id,
        "purpose_code": purpose_code,
        "purpose_version": purpose.purpose_version,
        "notice_version": notice_version,
        "jurisdiction_pack_id": activity.jurisdiction_pack_id,
        "capture_method": capture_method,
        "affirmative_action": affirmative_action,
        "granted_at": now.isoformat(),
    }
    await store.insert_consent(
        conn,
        {
            **body,
            "subject_principal_id": subject_principal_id,
            "controller_principal_id": tenant_id,
            "recipient_principal_id": None,
            "data_categories": list(activity.data_categories),
            "processing_operations": list(processing_operations),
            "granted_at": now,
            "expires_at": expires_at,
            "receipt_hash": str(hash_json(body, alg="sha-256")),
        },
    )
    await _event(
        conn,
        tenant_id,
        "privacy.consent.given",
        ("consent_receipt", consent_id),
        {"consent_id": consent_id, "purpose_code": purpose_code, "notice_version": notice_version},
        actor=subject_principal_id,
    )
    return consent_id


async def withdraw_consent(
    conn: AsyncConnection,
    tenant_id: str,
    consent_id: str,
    *,
    subject_principal_id: str,
    reason: str,
) -> Row[Any]:
    """Stops only consent-dependent processing (PRIV-02); the record is never rewritten (PRIV-03)."""
    row = await store.load_consent(conn, consent_id, lock=True)
    if row is None or str(row.subject_principal_id) != subject_principal_id:
        raise PrivacyError("NOT_FOUND", "unknown consent")
    if row.status != "ACTIVE":
        raise PrivacyError("PRIVACY_CONSENT_TERMINAL", f"consent is {row.status}")
    out = await store.withdraw_consent(conn, consent_id, reason, _now())
    await _event(
        conn,
        tenant_id,
        "privacy.consent.withdrawn",
        ("consent_receipt", consent_id),
        {"consent_id": consent_id, "purpose_code": row.purpose_code},
        actor=subject_principal_id,
        version=2,
    )
    return out


# --------------------------------------------------------------------------------- preferences


async def set_preference(
    conn: AsyncConnection,
    tenant_id: str,
    subject_principal_id: str,
    preference_type: str,
    value: bool,
    *,
    source: str,
) -> Row[Any]:
    """Opt-out / limit preferences, including Global Privacy Control signals (PRIV-21): never
    gated behind identity verification."""
    if preference_type not in PREFERENCE_TYPES:
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", "unknown preference type")
    row = await store.set_preference(
        conn, tenant_id, subject_principal_id, preference_type, value, source
    )
    await _event(
        conn,
        tenant_id,
        "privacy.preference.changed",
        ("preference", f"{subject_principal_id}:{preference_type}"),
        {"preference_type": preference_type, "value": value, "source": source},
        actor=subject_principal_id if source == "USER" else None,
        version=int(row.revision),
    )
    return row


# -------------------------------------------------------------------------------- restrictions


async def restrict(
    conn: AsyncConnection,
    tenant_id: str,
    subject_principal_id: str,
    *,
    kind: str,
    purpose_code: str | None,
    reason_code: str,
    rights_request_id: str | None = None,
    actor: str | None = None,
) -> str:
    restriction_id = str(uuid7())
    await store.add_restriction(
        conn,
        {
            "restriction_id": restriction_id,
            "tenant_id": tenant_id,
            "subject_principal_id": subject_principal_id,
            "kind": kind,
            "purpose_code": purpose_code,
            "reason_code": reason_code,
            "rights_request_id": rights_request_id,
        },
    )
    await _event(
        conn,
        tenant_id,
        "privacy.restriction.applied",
        ("processing_restriction", restriction_id),
        {
            "restriction_id": restriction_id,
            "kind": kind,
            "purpose_code": purpose_code,
            "reason_code": reason_code,
        },
        actor=actor,
    )
    return restriction_id


async def lift_restriction(
    conn: AsyncConnection, tenant_id: str, restriction_id: str, *, actor: str
) -> None:
    if not await store.lift_restriction(conn, restriction_id, actor, _now()):
        raise PrivacyError("NOT_FOUND", "no active restriction")
    await _event(
        conn,
        tenant_id,
        "privacy.restriction.lifted",
        ("processing_restriction", restriction_id),
        {"restriction_id": restriction_id},
        actor=actor,
        version=2,
    )


# ------------------------------------------------------------------------- retention, data map


async def create_retention_policy(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    code: str,
    object_type: str,
    jurisdiction_pack_id: str,
    trigger: str,
    retention_days: int,
    action: str,
    created_by: str,
    approved_by: str,
    purpose_code: str | None = None,
    backup_erasure_window_days: int | None = None,
    legal_hold_suspends: bool = True,
    legally_required: bool = False,
) -> Row[Any]:
    """Publish a retention policy version (governed: an independent approver). The backup
    erasure window is the DPO's number (DEC-F1 §4); it stays unset until they supply it."""
    check_code(code, "policy_code")
    RetentionTrigger(trigger)
    pack(jurisdiction_pack_id)
    if created_by == approved_by:
        raise PrivacyError(
            "PRIVACY_APPROVAL_REQUIRED", "retention policies need an independent approver"
        )
    row = await store.insert_retention_policy(
        conn,
        {
            "retention_policy_id": str(uuid7()),
            "tenant_id": tenant_id,
            "policy_code": code,
            "version": await store.next_version(
                conn, "retention_policy", "policy_code", code, "version"
            ),
            "object_type": object_type,
            "purpose_code": purpose_code,
            "jurisdiction_pack_id": jurisdiction_pack_id,
            "retention_trigger": trigger,
            "retention_days": retention_days,
            "action_on_expiry": action,
            "backup_erasure_window_days": backup_erasure_window_days,
            "legal_hold_suspends": legal_hold_suspends,
            "legally_required": legally_required,
            "created_by": created_by,
            "approved_by": approved_by,
        },
    )
    await _event(
        conn,
        tenant_id,
        "privacy.processing_activity.changed",
        ("retention_policy", str(row.retention_policy_id)),
        {
            "policy_code": code,
            "version": row.version,
            "retention_days": retention_days,
            "change": "RETENTION_PUBLISHED",
        },
        actor=approved_by,
        evidence_class=EvidenceClass.E3,
    )
    return row


async def register_personal_data(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    subject_principal_id: str,
    store_name: str,
    object_type: str,
    object_ref: str,
    purpose_code: str,
    data_category: str,
    retention_policy_code: str | None,
    trigger_at: datetime | None = None,
    processor_id: str | None = None,
    region: str | None = None,
) -> Row[Any]:
    """Record where a subject's personal data lives (the data map, PRIV-001 §10, §17). due_at is
    fixed now from the active policy, so a later hold release never restarts the clock."""
    trigger_at = trigger_at or _now()
    if processor_id:  # §23: no processor holds personal data without approved metadata
        await registry.require_processor(conn, processor_id, DataCategory(data_category).value)
    due = None
    if retention_policy_code:
        # A policy the tenant has not published yet leaves due_at open; the sweeper fixes it from
        # the original trigger once the policy exists (no silent "retain forever").
        policy = await store.active_retention_policy(conn, retention_policy_code)
        if policy is not None:
            due = due_at(trigger_at, int(policy.retention_days))
    return await store.upsert_data_map(
        conn,
        {
            "entry_id": str(uuid7()),
            "tenant_id": tenant_id,
            "subject_principal_id": subject_principal_id,
            "store": store_name,
            "object_type": object_type,
            "object_ref": object_ref,
            "purpose_code": purpose_code,
            "data_category": DataCategory(data_category).value,
            "retention_policy_code": retention_policy_code,
            "processor_id": processor_id,
            "region": region,
            "trigger_at": trigger_at,
            "due_at": due,
        },
    )


async def retention_sweep(
    conn: AsyncConnection, now: datetime | None = None, *, limit: int = 500
) -> dict[str, int]:
    """Worker pass (BYPASSRLS role): dispose every due entry no hold covers (PRIV-06/07); held
    entries are marked HELD and re-evaluated on every pass; an entry with no registered disposer
    stays due and is counted (no silent missed schedule)."""
    now = now or _now()
    counts = {
        "disposed": 0,
        "held": 0,
        "no_disposer": 0,
        "scheduled": await store.schedule_open_entries(conn),
    }
    for e in await store.due_entries(conn, now, limit):
        tenant = str(e.tenant_id)
        policy = (
            await store.active_retention_policy(conn, e.retention_policy_code)
            if e.retention_policy_code
            else None
        )
        suspends = policy.legal_hold_suspends if policy is not None else True
        if suspends and await is_held(
            conn,
            tenant,
            subject_principal_id=str(e.subject_principal_id),
            object_type=e.object_type,
            now=now,
        ):
            if e.status != "HELD":
                await store.set_entry_status(conn, e.entry_id, "HELD")
            counts["held"] += 1
            continue
        disposer = _DISPOSERS.get(e.store)
        if disposer is None:
            counts["no_disposer"] += 1
            continue
        action = policy.action_on_expiry if policy is not None else "DELETE"
        await disposer(conn, e, action)
        await store.set_entry_status(conn, e.entry_id, "DISPOSED", action=action, now=now)
        counts["disposed"] += 1
        await _event(
            conn,
            tenant,
            "privacy.retention.due",
            ("data_map_entry", str(e.entry_id)),
            {
                "entry_id": str(e.entry_id),
                "store": e.store,
                "object_type": e.object_type,
                "policy_code": e.retention_policy_code,
                "action": action,
                "due_at": e.due_at.isoformat(),
            },
            actor=None,
        )
    return counts


# ------------------------------------------------------------------------------ rights requests


def request_view(r: Row[Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    consumed = None
    if r.deadline_at is not None:
        total = (r.deadline_at - r.received_at).total_seconds() or 1
        consumed = round(min(1.0, max(0.0, (now - r.received_at).total_seconds() / total)), 2)
    return {
        "request_id": str(r.request_id),
        "request_type": r.request_type,
        "subject_principal_id": str(r.subject_principal_id),
        "requester_principal_id": str(r.requester_principal_id)
        if r.requester_principal_id
        else None,
        "jurisdiction_pack_id": r.jurisdiction_pack_id,
        "status": r.status,
        "identity_verification_state": r.identity_verification_state,
        "received_at": r.received_at.isoformat(),
        "deadline_at": r.deadline_at.isoformat() if r.deadline_at else None,
        "deadline_source": r.deadline_source,
        "deadline_consumed": consumed,
        "deadline_warning": next(
            (w for w in (0.9, 0.75, 0.5) if consumed is not None and consumed >= w), None
        ),
        "exemptions": list(r.exemptions),
        "fulfilment_items": list(r.fulfilment_items),
        "response_package_ref": str(r.response_package_ref) if r.response_package_ref else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


async def open_request(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    request_type: str,
    subject_principal_id: str,
    requester_principal_id: str | None,
    jurisdiction_pack_id: str,
    authn_level: int = 0,
    auth_age: timedelta | None = None,
    authorized_agent_ref: str | None = None,
    scope: Mapping[str, Any] | None = None,
) -> Row[Any]:
    """Every request is accepted and tracked, whatever the pack's state (a rights request is
    never refused for a configuration gap). Opt-outs take effect at once, without verification."""
    try:
        rtype = RightsType(request_type)
    except ValueError as e:
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", "unknown request type") from e
    jp = pack(jurisdiction_pack_id)
    now = _now()
    deadline, source = jp.deadline(rtype, now)
    self_request = requester_principal_id == subject_principal_id and authorized_agent_ref is None
    verification, level = initial_verification(
        rtype, self_request=self_request, authn_level=authn_level, auth_age=auth_age
    )
    request_id = str(uuid7())
    row = await store.insert_request(
        conn,
        {
            "request_id": request_id,
            "tenant_id": tenant_id,
            "request_type": rtype.value,
            "subject_principal_id": subject_principal_id,
            "requester_principal_id": requester_principal_id,
            "authorized_agent_ref": authorized_agent_ref,
            "jurisdiction_pack_id": jurisdiction_pack_id,
            "received_at": now,
            "deadline_at": deadline,
            "deadline_source": source,
            "identity_verification_state": verification,
            "verification_level": level,
            "status": RightsStatus.RECEIVED.value
            if verification == "PENDING"
            else RightsStatus.SCOPING.value,
            "scope": dict(scope or {}),
            "linked_request_id": None,
        },
    )
    await _event(
        conn,
        tenant_id,
        "privacy.rights.requested",
        ("rights_request", request_id),
        {
            "request_id": request_id,
            "request_type": rtype.value,
            "jurisdiction_pack_id": jurisdiction_pack_id,
            "deadline_source": source,
            "identity_verification_state": verification,
        },
        actor=requester_principal_id,
    )
    if rtype in NO_VERIFICATION and rtype in PREFERENCE_FOR:
        row = await fulfil_request(conn, tenant_id, request_id, actor=None)
    return row


async def verify_request(
    conn: AsyncConnection,
    tenant_id: str,
    request_id: str,
    *,
    verified: bool,
    level: int,
    method: str,
    actor: str,
) -> Row[Any]:
    """Proportional verification outcome (§14); verification evidence has its own short retention."""
    r = await _load(conn, request_id)
    if r.status in TERMINAL_RIGHTS:
        raise PrivacyError("PRIVACY_INVALID_STATE", f"request is {r.status}")
    out = await store.update_request(
        conn,
        r,
        identity_verification_state="VERIFIED" if verified else "FAILED",
        verification_level=level if verified else 0,
        status=RightsStatus.SCOPING.value if verified else RightsStatus.VERIFYING.value,
    )
    await _event(
        conn,
        tenant_id,
        "privacy.rights.verified",
        ("rights_request", request_id),
        {"request_id": request_id, "verified": verified, "level": level, "method": method},
        actor=actor,
        version=int(out.version),
    )
    return out


async def _load(conn: AsyncConnection, request_id: str) -> Row[Any]:
    r = await store.load_request(conn, request_id, lock=True)
    if r is None:
        raise PrivacyError("NOT_FOUND", "unknown rights request")
    return r


def _item(name: str, state: str, **extra: Any) -> dict[str, Any]:
    return {"item_id": name, "state": state, **extra}


async def _access_package(
    conn: AsyncConnection, tenant_id: str, r: Row[Any]
) -> tuple[str, list[dict[str, Any]]]:
    """Access/portability package from authoritative sources (§15, §19): structured JSON with a
    manifest of per-section hashes; stored encrypted under the subject's key, short-lived."""
    subject = str(r.subject_principal_id)
    await discover_subject(conn, tenant_id, subject)
    entries = await store.subject_entries(conn, subject)
    sections: dict[str, Any] = {
        "data_locations": [
            {
                "store": e.store,
                "object_type": e.object_type,
                "purpose_code": e.purpose_code,
                "data_category": e.data_category,
                "region": e.region,
                "retained_until": e.due_at.isoformat() if e.due_at else None,
                "status": e.status,
            }
            for e in entries
        ],
        "consents": [
            {
                "consent_id": str(c.consent_id),
                "purpose_code": c.purpose_code,
                "notice_version": c.notice_version,
                "status": c.status,
                "granted_at": c.granted_at.isoformat(),
                "withdrawn_at": c.withdrawn_at.isoformat() if c.withdrawn_at else None,
            }
            for c in await store.consents_of(conn, subject)
        ],
        "preferences": [
            {
                "preference_type": p.preference_type,
                "value": p.value,
                "updated_at": p.updated_at.isoformat(),
            }
            for p in await store.preferences_of(conn, subject)
        ],
        "restrictions": [
            {"kind": x.kind, "purpose_code": x.purpose_code, "since": x.created_at.isoformat()}
            for x in await store.restrictions_of(conn, subject)
        ],
        "processing": [
            {
                "activity_code": a.activity_code,
                "purposes": list(a.purpose_codes),
                "lawful_basis": a.lawful_basis,
                "recipient_classes": list(a.recipient_classes),
            }
            for a in await store.list_activities(conn)
            if a.status == "ACTIVE"
        ],
        "records": {},
    }
    items = []
    for e in entries:
        exporter = _EXPORTERS.get(e.store)
        if e.status == "DISPOSED":
            continue
        if exporter is None:
            items.append(_item(f"export:{e.store}:{e.object_ref}", "FAILED", reason="NO_EXPORTER"))
            continue
        sections["records"].setdefault(e.store, []).append(
            json.loads(json.dumps(await exporter(conn, e), default=str))
        )
        items.append(_item(f"export:{e.store}:{e.object_ref}", "DONE"))
    manifest = {
        "format": "zid-privacy-export/1",
        "request_id": str(r.request_id),
        "request_type": r.request_type,
        "generated_at": _now().isoformat(),
        "sections": {
            k: str(hash_bytes(canonicalize(v), alg="sha-256")) for k, v in sections.items()
        },
    }
    package = canonicalize({"manifest": manifest, **sections})
    object_id, _ = await store_object(
        conn,
        tenant_id,
        content=package,
        object_type="privacy_export_package",
        classification="D3",
        retention_class="privacy_export",
        subject_principal_id=subject,
        expires_at=_now() + EXPORT_TTL,
    )
    items.append(_item("package", "DONE", evidence_object_id=object_id))
    return object_id, items


async def _erase(
    conn: AsyncConnection, tenant_id: str, r: Row[Any], actor: str | None
) -> list[dict[str, Any]]:
    """§17: classify every data map entry, dispose what is eligible, keep what a hold or a legal
    obligation requires (restricted, reasoned), instruct processors, then crypto-erase the vault."""
    subject = str(r.subject_principal_id)
    await discover_subject(conn, tenant_id, subject)
    await _event(
        conn,
        tenant_id,
        "privacy.deletion.started",
        ("rights_request", str(r.request_id)),
        {"request_id": str(r.request_id)},
        actor=actor,
        version=int(r.version) + 1,
        evidence_class=EvidenceClass.E3,
    )
    items: list[dict[str, Any]] = []
    backup_days: list[int] = []
    for e in await store.subject_entries(conn, subject):
        name = f"{e.store}:{e.object_ref}"
        policy = (
            await store.active_retention_policy(conn, e.retention_policy_code)
            if e.retention_policy_code
            else None
        )
        if policy is not None and policy.backup_erasure_window_days is not None:
            backup_days.append(
                int(policy.backup_erasure_window_days)
            )  # disposed copies age out too
        if e.status == "DISPOSED":
            continue
        if await is_held(conn, tenant_id, subject_principal_id=subject, object_type=e.object_type):
            await store.set_entry_status(conn, e.entry_id, "RESTRICTED")
            items.append(_item(name, "RETAINED_RESTRICTED", reason="LEGAL_HOLD"))
            continue
        if policy is not None and policy.legally_required:
            await store.set_entry_status(conn, e.entry_id, "RESTRICTED")
            items.append(
                _item(
                    name,
                    "RETAINED_RESTRICTED",
                    reason="LEGAL_OBLIGATION",
                    policy_code=policy.policy_code,
                )
            )
            continue
        if e.processor_id:
            proc = await registry.processor(conn, e.processor_id)
            sla = proc.deletion_sla_days if proc is not None else None
            items.append(
                _item(
                    name,
                    "PENDING_PROCESSOR_ACK",
                    processor_id=e.processor_id,
                    deletion_due=(_now() + timedelta(days=sla)).isoformat()
                    if sla is not None
                    else None,
                )
            )
            continue
        disposer = _DISPOSERS.get(e.store)
        if disposer is None:
            items.append(_item(name, "FAILED", reason="NO_DISPOSER"))
            continue
        action = policy.action_on_expiry if policy is not None else "DELETE"
        await disposer(conn, e, action)
        await store.set_entry_status(conn, e.entry_id, "DISPOSED", action=action, now=_now())
        items.append(_item(name, "DONE", action=action))
    erasure = await erase_subject(conn, tenant_id, subject, request_ref=str(r.request_id))
    vault_state = (
        "DONE" if erasure.outcome in ("ERASED", "NOTHING_TO_ERASE") else "RETAINED_RESTRICTED"
    )
    items.append(
        _item(
            "evidence-vault",
            vault_state,
            outcome=erasure.outcome,
            tombstone_id=erasure.tombstone_id,
        )
    )
    window = max(backup_days) if backup_days else None
    items.append(
        _item(
            "backups",
            "SCHEDULED",
            backup_erasure_due=(_now() + timedelta(days=window)).isoformat()
            if window is not None
            else None,
            window_source="retention_policy" if window is not None else "DPO_PENDING",
        )
    )
    return items


async def fulfil_request(
    conn: AsyncConnection, tenant_id: str, request_id: str, *, actor: str | None
) -> Row[Any]:
    r = await _load(conn, request_id)
    if r.status in TERMINAL_RIGHTS:
        raise PrivacyError("PRIVACY_INVALID_STATE", f"request is {r.status}")
    rtype = RightsType(r.request_type)
    if rtype not in NO_VERIFICATION and r.identity_verification_state != "VERIFIED":
        raise PrivacyError("PRIVACY_VERIFICATION_REQUIRED", "verify the requester first")
    require_disclosure_verification(rtype, r.identity_verification_state, int(r.verification_level))
    subject = str(r.subject_principal_id)
    package_ref = None
    if rtype in (RightsType.ACCESS, RightsType.PORTABILITY):
        package_ref, items = await _access_package(conn, tenant_id, r)
    elif rtype is RightsType.ERASURE:
        items = await _erase(conn, tenant_id, r, actor)
    elif rtype in (RightsType.RESTRICTION, RightsType.OBJECTION):
        rid = await restrict(
            conn,
            tenant_id,
            subject,
            kind=rtype.value,
            purpose_code=(r.scope or {}).get("purpose_code"),
            reason_code=f"RIGHTS_{rtype.value}",
            rights_request_id=request_id,
            actor=actor,
        )
        items = [_item("restriction", "DONE", restriction_id=rid)]
    elif rtype in PREFERENCE_FOR:
        await set_preference(
            conn, tenant_id, subject, PREFERENCE_FOR[rtype], True, source="RIGHTS_REQUEST"
        )
        items = [_item("preference", "DONE", preference_type=PREFERENCE_FOR[rtype])]
    elif rtype in (RightsType.HUMAN_REVIEW, RightsType.ADMT_ACCESS):
        items = await _automated_decision_items(conn, tenant_id, r, rtype)
        return await store.update_request(
            conn, r, status=RightsStatus.REVIEW.value, fulfilment_items=items
        )
    else:  # RECTIFICATION, COMPLAINT: privacy operations decide
        items = [_item("review", "PENDING", route="privacy-ops")]
        return await store.update_request(
            conn, r, status=RightsStatus.REVIEW.value, fulfilment_items=items
        )
    status = summarize(items)
    out = await store.update_request(
        conn,
        r,
        status=status.value,
        fulfilment_items=items,
        response_package_ref=package_ref,
        completed_at=_now() if status in TERMINAL_RIGHTS else None,
    )
    await _after_fulfil(conn, tenant_id, out, actor)
    return out


async def _automated_decision_items(
    conn: AsyncConnection, tenant_id: str, r: Row[Any], rtype: RightsType
) -> list[dict[str, Any]]:
    """PRIV-23: a challenge of an automated decision goes to a qualified human with the original
    decision evidence; ADMT access returns the profile's explanation (PRIV-001 §20)."""
    receipt_id = (r.scope or {}).get("receipt_id")
    if not receipt_id:
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", "name the decision: scope.receipt_id")
    receipt = await receipt_summary(conn, str(receipt_id))
    subject_handle = await principal_handle(tenant_id, str(r.subject_principal_id))
    if receipt is None or receipt["actor_handle"] != subject_handle:
        raise PrivacyError("NOT_FOUND", "no such decision about this subject")
    profile = await registry.adm_profile(conn, receipt["action"])
    if profile is None or profile.significance_class == "NONE":
        raise PrivacyError(
            "PRIVACY_ADM_PROFILE_MISSING",
            "this decision class is not a registered significant automated decision",
        )
    common = {
        "receipt_id": receipt["receipt_id"],
        "decision_class": receipt["action"],
        "outcome": receipt["outcome"],
        "profile_id": str(profile.profile_id),
        "significance_class": profile.significance_class,
        "solely_automated": profile.solely_automated,
    }
    if rtype is RightsType.ADMT_ACCESS:
        return [
            _item(
                "explanation", "PENDING", explanation_profile=profile.explanation_profile, **common
            )
        ]
    return [_item("human-review", "PENDING", route=profile.human_review_route, **common)]


async def _after_fulfil(
    conn: AsyncConnection, tenant_id: str, r: Row[Any], actor: str | None
) -> None:
    rtype = RightsType(r.request_type)
    status = RightsStatus(r.status)
    if rtype is RightsType.ERASURE and status in TERMINAL_RIGHTS:
        await _event(
            conn,
            tenant_id,
            "privacy.deletion.completed",
            ("rights_request", str(r.request_id)),
            {"request_id": str(r.request_id), "status": status.value},
            actor=actor,
            version=int(r.version) + 1,
            evidence_class=EvidenceClass.E3,
        )
    if status in TERMINAL_RIGHTS:
        await _event(
            conn,
            tenant_id,
            "privacy.rights.completed",
            ("rights_request", str(r.request_id)),
            {"request_id": str(r.request_id), "request_type": rtype.value, "status": status.value},
            actor=actor,
            version=int(r.version) + 2,
            evidence_class=EvidenceClass.E3
            if rtype in (RightsType.ERASURE, RightsType.ACCESS, RightsType.PORTABILITY)
            else EvidenceClass.E2,
        )


async def acknowledge_item(
    conn: AsyncConnection,
    tenant_id: str,
    request_id: str,
    item_id: str,
    *,
    ack_ref: str,
    actor: str,
) -> Row[Any]:
    """A processor/recipient confirmed deletion (PRIV-12, PRIV-20): only an explicit acknowledgement
    moves its stage on."""
    r = await _load(conn, request_id)
    items = [dict(i) for i in r.fulfilment_items]
    target = next((i for i in items if i["item_id"] == item_id), None)
    if target is None or target["state"] != "PENDING_PROCESSOR_ACK":
        raise PrivacyError("PRIVACY_INVALID_STATE", "no pending processor stage with that id")
    target.update(
        state="DONE",
        ack_ref_commitment=commitment(ack_ref, tenant_id=tenant_id, kind="processor-ack"),
    )
    status = summarize(items)
    out = await store.update_request(
        conn,
        r,
        fulfilment_items=items,
        status=status.value,
        completed_at=_now() if status in TERMINAL_RIGHTS else None,
    )
    await _event(
        conn,
        tenant_id,
        "privacy.processor.deletion.confirmed",
        ("rights_request_item", f"{request_id}:{item_id}"),
        {"request_id": request_id, "item_id": item_id},
        actor=actor,
    )
    await _after_fulfil(conn, tenant_id, out, actor)
    return out


async def close_request(
    conn: AsyncConnection,
    tenant_id: str,
    request_id: str,
    *,
    outcome: str,
    reason_code: str,
    actor: str,
    exemptions: Sequence[Mapping[str, Any]] = (),
) -> Row[Any]:
    """Human decision for REVIEW cases, or a reasoned denial/withdrawal (§12-13)."""
    if outcome not in ("COMPLETED", "PARTIALLY_DENIED", "DENIED", "WITHDRAWN"):
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", "outcome")
    r = await _load(conn, request_id)
    if r.status in TERMINAL_RIGHTS:
        raise PrivacyError("PRIVACY_INVALID_STATE", f"request is {r.status}")
    items = [
        dict(i, state="DONE" if i["state"] == "PENDING" else i["state"]) for i in r.fulfilment_items
    ]
    out = await store.update_request(
        conn,
        r,
        status=outcome,
        fulfilment_items=items,
        exemptions=[
            {**dict(e), "reason_code": e.get("reason_code", reason_code)} for e in exemptions
        ],
        completed_at=_now(),
    )
    await _after_fulfil(conn, tenant_id, out, actor)
    return out


async def read_package(
    conn: AsyncConnection, tenant_id: str, request_id: str, *, subject_principal_id: str
) -> bytes:
    """Only the subject downloads their package, while it exists (short-lived, encrypted)."""
    r = await store.load_request(conn, request_id)
    if (
        r is None
        or str(r.subject_principal_id) != subject_principal_id
        or r.response_package_ref is None
    ):
        raise PrivacyError("NOT_FOUND", "no package for this request")
    return await read_object(conn, tenant_id, str(r.response_package_ref))


async def privacy_pass(conn: AsyncConnection, *, now: datetime | None = None) -> dict[str, Any]:
    """Worker: the retention sweep plus open-request deadline consumption (alerts at 50/75/90%)."""
    now = now or _now()
    counts = await retention_sweep(conn, now)
    warnings = {"50": 0, "75": 0, "90": 0, "counsel_pending": 0}
    for r in await store.open_requests(conn):
        if r.deadline_at is None:
            warnings["counsel_pending"] += 1
            continue
        total = (r.deadline_at - r.received_at).total_seconds() or 1
        used = (now - r.received_at).total_seconds() / total
        for w in ("90", "75", "50"):
            if used >= int(w) / 100:
                warnings[w] += 1
                break
    return {**counts, "deadline_warnings": warnings}


__all__ = [
    "acknowledge_item",
    "activate_activity",
    "activate_purpose",
    "assign_role",
    "check_processing",
    "close_request",
    "create_activity",
    "create_purpose",
    "create_retention_policy",
    "discover_subject",
    "fulfil_request",
    "give_consent",
    "lift_restriction",
    "open_request",
    "pack_transfer_mechanisms",
    "privacy_pass",
    "read_package",
    "register_personal_data",
    "register_store",
    "registered_stores",
    "release_check",
    "request_view",
    "restrict",
    "retention_sweep",
    "set_preference",
    "verify_request",
    "withdraw_consent",
]


# ------------------------------------------------------------------- enforcement at the boundary

_ENFORCEMENT: str | None = None


def configure_enforcement(mode: str | None) -> None:
    """`enforce` (default outside local/test): refuse processing the registry does not permit.
    `audit`: allow, but record every would-be refusal as protected evidence (onboarding a tenant
    whose processing activities are not registered yet)."""
    global _ENFORCEMENT
    if mode not in (None, "enforce", "audit"):
        raise ValueError("privacy enforcement is 'enforce' or 'audit'")
    _ENFORCEMENT = mode


def enforcement_mode() -> str:
    import os

    if _ENFORCEMENT is not None:
        return _ENFORCEMENT
    return "audit" if os.environ.get("ZID_ENVIRONMENT", "local") in ("local", "test") else "enforce"


def purpose_code(purpose: str) -> str:
    """Free-text purpose labels from protocol requests -> registry purpose codes (`kyc onboarding`
    -> `KYC_ONBOARDING`); the registry, not the caller, decides whether the code is permitted."""
    import re

    code = re.sub(r"[^A-Z0-9]+", "_", purpose.upper()).strip("_")[:63] or "UNSPECIFIED"
    return code if code[0].isalpha() else f"P_{code}"[:63]


async def enforce_processing(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    activity_code: str,
    purpose: str,
    data_category: str,
    subject_principal_id: str | None,
    recipient_class: str | None = None,
    destination_cell_id: str | None = None,
) -> ProcessingDecision:
    """The processing boundary (PRIV-01/05/24): refuse (enforce) or evidence (audit) any processing
    the tenant's registered purposes, activities, consents and restrictions do not permit."""
    from zid_errors import ErrorDetail, ZidError

    code = purpose_code(purpose)
    decision = await check_processing(
        conn,
        tenant_id,
        activity_code=activity_code,
        purpose_code=code,
        data_category=data_category,
        subject_principal_id=subject_principal_id,
        recipient_class=recipient_class,
        destination_cell_id=destination_cell_id,
    )
    if decision.allowed:
        return decision
    mode = enforcement_mode()
    await _audit(
        conn,
        tenant_id,
        None,
        "privacy.processing.refused" if mode == "enforce" else "privacy.processing.unregistered",
        actor=None,
        target_type="processing_activity",
        target_ref=activity_code,
        outcome="DENIED" if mode == "enforce" else "SUCCESS",
        reason=decision.reason,
        details={"purpose_code": code, "data_category": data_category, "mode": mode},
    )
    if mode == "enforce":
        raise ZidError(
            "PRIVACY_PROCESSING_NOT_PERMITTED",
            "This processing is not permitted by the tenant's privacy registry.",
            http_status=403,
            details=[ErrorDetail(issue=decision.reason)],
        )
    return decision

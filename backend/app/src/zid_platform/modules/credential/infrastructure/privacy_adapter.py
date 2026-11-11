"""The credential module's personal data for the privacy data map.

1. Presentation records where the subject is known: disclosed claim values are personal data (D3); erasure removes them and keeps
   only the claim commitments and the outcome the relying party relied on.
2. Credential claim checks (CR-069): the checks about a person, their consent grants, eligibility decisions, appeals and issuer
   outreach. These tables are immutable by design, so erasure goes through `cred.erase_subject`, the one controlled path past the
   guards (migration credential_0008). It runs after the privacy module's own legal-hold and retention checks, removes everything
   held about that subject in these tables, and is idempotent.
Access requests export a readable summary: what was checked, the decision, when, and the reasons, never a hash or a key.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.ports import register_store

STORE = "cred.presentation_record"
CHECK_STORE = "cred.claim_check"
GRANT_STORE = "cred.consent_grant"
DECISION_STORE = "cred.eligibility_decision"
APPEAL_STORE = "cred.claim_appeal"
OUTREACH_STORE = "cred.outreach_request"


async def _locate(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT presentation_id, created_at FROM cred.presentation_record WHERE subject_principal_id = :p"
            ),
            {"p": uuid.UUID(subject)},
        )
    ).all()
    return [
        {
            "object_ref": str(r.presentation_id),
            "object_type": "credential.presentation",
            "purpose_code": "VERIFY_CREDENTIAL",
            "data_category": "D3",
            "trigger_at": r.created_at,
            "region": None,
            "retention_policy_code": "PRESENTATION_RECORD",
        }
        for r in rows
    ]


async def _export(conn: AsyncConnection, entry: Row[Any]) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "SELECT relying_client_id, purpose, requested_claims, status, disclosed_claims, created_at, "
                "completed_at FROM cred.presentation_record WHERE presentation_id = :p"
            ),
            {"p": uuid.UUID(entry.object_ref)},
        )
    ).first()
    return dict(row._mapping) if row else {}


async def _dispose(conn: AsyncConnection, entry: Row[Any], action: str) -> None:
    await conn.execute(
        text(
            "UPDATE cred.presentation_record SET disclosed_claims = NULL WHERE presentation_id = :p"
        ),
        {"p": uuid.UUID(entry.object_ref)},
    )


# ---------------------------------------------------------------------------------------- claim checks and their records

# store -> (SQL listing (ref, created_at) for a subject, object_type, purpose code). The retention policy code is left unset until
# the DPO defines one: the privacy module then applies its default (delete on request, unless a hold applies).
_CHECK_STORES = {
    CHECK_STORE: (
        text(
            "SELECT check_id AS ref, created_at FROM cred.claim_check WHERE subject_principal_id = :p"
        ),
        "credential.claim_check",
        "VERIFY_CLAIM",
    ),
    GRANT_STORE: (
        text(
            "SELECT grant_id AS ref, created_at FROM cred.consent_grant WHERE subject_principal_id = :p"
        ),
        "credential.consent_grant",
        "CREDENTIAL_DISCLOSURE",
    ),
    DECISION_STORE: (
        text(
            "SELECT decision_id AS ref, created_at FROM cred.eligibility_decision WHERE subject_principal_id = :p"
        ),
        "credential.eligibility_decision",
        "PROFESSIONAL_ELIGIBILITY",
    ),
    APPEAL_STORE: (
        text(
            "SELECT a.appeal_id AS ref, a.created_at FROM cred.claim_appeal a "
            "JOIN cred.claim_check c ON c.check_id = a.check_id WHERE c.subject_principal_id = :p"
        ),
        "credential.claim_appeal",
        "VERIFY_CLAIM",
    ),
    OUTREACH_STORE: (
        text(
            "SELECT request_id AS ref, created_at FROM cred.outreach_request WHERE subject_principal_id = :p"
        ),
        "credential.outreach_request",
        "VERIFY_CLAIM",
    ),
}

_CHECK_EXPORTS = {
    CHECK_STORE: text(
        "SELECT claim_type, decision, assurance_tier, jurisdiction, source_checked_at, created_at, superseded_at, "
        "assertion->'reason_codes' AS reason_codes, assertion->'reason_text' AS reason_text "
        "FROM cred.claim_check WHERE check_id = :r"
    ),
    GRANT_STORE: text(
        "SELECT recipient, consumer, purpose, fields, created_at, expires_at, withdrawn_at "
        "FROM cred.consent_grant WHERE grant_id = :r"
    ),
    DECISION_STORE: text(
        "SELECT kind, policy_id, policy_version, outcome, reasons, restrictions, consumer, created_at "
        "FROM cred.eligibility_decision WHERE decision_id = :r"
    ),
    APPEAL_STORE: text(
        "SELECT reason, state, resolution, created_at, resolved_at FROM cred.claim_appeal WHERE appeal_id = :r"
    ),
    OUTREACH_STORE: text(
        "SELECT issuer_id, claim_type, state, outcome, created_at, deadline, concluded_at "
        "FROM cred.outreach_request WHERE request_id = :r"
    ),
}


def _locator(store_name: str):  # type: ignore[no-untyped-def]
    sql, object_type, purpose = _CHECK_STORES[store_name]

    async def locate(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
        rows = (await conn.execute(sql, {"p": uuid.UUID(subject)})).all()
        return [
            {
                "object_ref": str(r.ref),
                "object_type": object_type,
                "purpose_code": purpose,
                "data_category": "D3",
                "trigger_at": r.created_at,
                "region": None,
                "retention_policy_code": None,
            }
            for r in rows
        ]

    return locate


def _exporter(store_name: str):  # type: ignore[no-untyped-def]
    sql = _CHECK_EXPORTS[store_name]

    async def export(conn: AsyncConnection, entry: Row[Any]) -> dict[str, Any]:
        row = (await conn.execute(sql, {"r": uuid.UUID(entry.object_ref)})).first()
        return dict(row._mapping) if row else {}

    return export


async def _erase(conn: AsyncConnection, entry: Row[Any], action: str) -> None:
    """Erase everything held about the entry's subject in the credential-check tables. One call covers all five stores, and a
    second call finds nothing, so each data map entry can call it without coordination."""
    await conn.execute(
        text("SELECT cred.erase_subject(:t, :s)"),
        {"t": uuid.UUID(str(entry.tenant_id)), "s": uuid.UUID(str(entry.subject_principal_id))},
    )


def register() -> None:
    register_store(STORE, locator=_locate, exporter=_export, disposer=_dispose)
    for name in _CHECK_STORES:
        register_store(name, locator=_locator(name), exporter=_exporter(name), disposer=_erase)


__all__ = ["register"]

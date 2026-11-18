"""The screening module's personal data for the privacy data map: rights requests, retention and legal holds (ZID-DTV-WF-06 section 17, ENG-17, QA-042; CR-116).

The privacy module owns the rules (a legal hold, a schedule, the two-step request); this adapter only says where a person's screening data is, what may be shown, and how it is disposed.

- **Found by** the person's account reference on the case (`screening_request.subject_ref`), within the caller's tenant (row-level security).
- **Exported** as what the person gave and the fact that a check happened: when, in which market, which classes. It never includes candidates, reasons, dispositions or reviewer notes. Telling a person what a sanctions or PEP check found can be unlawful (tipping off) and is Legal's decision per case, so
  the export says it is withheld and why, never that nothing was found.
- **Disposed** by scrubbing the personal columns in place: the subject's name, date of birth, countries and identifiers, and each candidate's matched name, reasons and programs. The case row, its sources, the
  supplier-call log and the **append-only decision chain stay**: they hold codes, hashes, dates and reviewer rationale that the regulator may need and that no longer name anyone once the subject is scrubbed. A
  reviewer's free-text rationale could still mention a name; that is why the chain is listed as kept and the decision to remove it is a Legal act, not an automatic one.
Disposal runs after the privacy module's own legal-hold and retention checks, is idempotent, and records nothing about the person.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

STORE = "screening.request"
ERASED = "[erased]"
WITHHELD = (
    "The result of a sanctions, politically exposed person or adverse media check is not disclosed through a rights request. "
    "Our compliance team will review your request and tell you what the law allows."
)


async def _locate(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT request_id, created_at, region FROM verify.screening_request WHERE subject_ref = :s"
            ),
            {"s": subject},
        )
    ).all()
    return [
        {
            "object_ref": str(r.request_id),
            "object_type": "screening.request",
            "purpose_code": "SCREENING",
            "data_category": "D3",
            "trigger_at": r.created_at,
            "region": r.region,
            "retention_policy_code": None,  # the DPO sets the schedule; until then the privacy default applies (delete on request unless held)
        }
        for r in rows
    ]


async def _export(conn: AsyncConnection, entry: Row[Any]) -> dict[str, Any]:
    key = uuid.UUID(entry.object_ref)
    row = (
        await conn.execute(
            text(
                "SELECT r.market, r.purpose, r.classes, r.created_at, s.name, s.birth, s.countries "
                "FROM verify.screening_request r JOIN verify.screening_request_subject s USING (request_id) WHERE r.request_id = :r"
            ),
            {"r": key},
        )
    ).first()
    if row is None:
        return {}
    return {
        "screening_performed": True,
        "market": row.market,
        "purpose": row.purpose,
        "classes_checked": row.classes,
        "performed_at": row.created_at.isoformat(),
        "details_you_gave": {"name": row.name, "birth": row.birth, "countries": row.countries},
        "result": "WITHHELD",
        "note": WITHHELD,
    }


async def _dispose(conn: AsyncConnection, entry: Row[Any], action: str) -> None:
    key = uuid.UUID(entry.object_ref)
    await conn.execute(
        text(
            "UPDATE verify.screening_request_subject SET name = :e, birth = NULL, countries = '[]', identifiers = '[]', expired_identifiers = '[]' "
            "WHERE request_id = :r"
        ),
        {"e": ERASED, "r": key},
    )
    await conn.execute(
        text(
            "UPDATE verify.screening_request_candidate SET matched_name = :e, reasons = '[]', programs = '[]' WHERE request_id = :r"
        ),
        {"e": ERASED, "r": key},
    )


def register(register_store: Any) -> None:
    """The composition root passes the privacy module's `register_store`, so this module never imports privacy (it would join a dependency cycle)."""
    register_store(STORE, locator=_locate, exporter=_export, disposer=_dispose)


__all__ = ["ERASED", "STORE", "WITHHELD", "register"]

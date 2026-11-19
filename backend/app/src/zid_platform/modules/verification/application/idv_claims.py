"""The identity claim an approved proofing case leaves (ZID-DTV-WF-03; CR-125). Kept apart from `idv_case` so the case service imports this and not the other way round.

The claim says WHO was verified, for which market under which pinned rules, at what GRADE and until when. Whether a consumer accepts a grade is the consumer's decision: the grade travels with the claim. A market with no
validity period leaves no claim, and the case says so (`NO_CLAIM_ISSUED:RESULT_VALIDITY_NOT_SET`) rather than leaving the platform to guess how long an approval lasts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

CLAIM = "IDENTITY_VERIFIED_FOR_SCOPE"
CHIP, REVIEWED, AUTOMATED = "CHIP_VERIFIED", "REVIEWED_NO_CHIP", "AUTOMATED_NO_CHIP"
# the lowest grade (CR-129): a live face looked like the photograph on an uploaded document, with no liveness test and no check that the document is genuine
FACE_MATCH = "FACE_MATCH_NO_LIVENESS"


def grade_of(assertions: tuple[str, ...], *, automatic: bool = False) -> str:
    """CHIP_VERIFIED only when the chip's data was proved issuer-signed; every other approval is the reviewed grade."""
    if "CHIP_DATA_ISSUER_SIGNED" in assertions:
        return CHIP
    if "FACE_MATCHES_DOCUMENT_PHOTO_NO_LIVENESS" in assertions:
        return FACE_MATCH
    return AUTOMATED if automatic else REVIEWED  # automatic: no person looked at it, and no face or authenticity check ran


async def issue(
    conn: AsyncConnection, *, tenant_id: str, case_id: str, applicant: str, market: str, policy_version: str, policy_hash: str,
    assertions: tuple[str, ...], valid_days: int | None, now: datetime, automatic: bool = False,
) -> str | None:  # fmt: skip
    """Record the claim, once per case. None when the market sets no validity period."""
    if not valid_days:
        return None
    claim_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO verify.idv_case_claim (claim_id, tenant_id, case_id, subject_principal_id, claim_type, grade, market, policy_version, policy_hash, performed_at, valid_until) "
            "VALUES (:i, :t, :c, :s, :ct, :g, :m, :pv, :ph, :now, :until) ON CONFLICT (case_id) DO NOTHING"
        ),
        {
            "i": claim_id, "t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id), "s": uuid.UUID(applicant), "ct": CLAIM, "g": grade_of(assertions, automatic=automatic), "m": market,
            "pv": policy_version, "ph": policy_hash, "now": now, "until": now + timedelta(days=valid_days),
        },
    )  # fmt: skip
    return str(claim_id)


async def current(
    conn: AsyncConnection, principal_id: str, now: datetime
) -> tuple[str, datetime] | None:
    """The claim type and time of this person's newest valid, unexpired case claim, or None."""
    row = (
        await conn.execute(
            text(
                "SELECT claim_type, performed_at FROM verify.idv_case_claim WHERE subject_principal_id = :s AND status = 'VALID' AND valid_until > :now ORDER BY performed_at DESC LIMIT 1"
            ),
            {"s": uuid.UUID(principal_id), "now": now},
        )
    ).first()
    return (str(row.claim_type), row.performed_at) if row else None


async def revoke_for_case(conn: AsyncConnection, case_id: str, reason: str, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE verify.idv_case_claim SET status = 'REVOKED', revoked_at = :now, revoke_reason = :r WHERE case_id = :c AND status = 'VALID'"
        ),
        {"now": now, "r": reason[:120], "c": uuid.UUID(case_id)},
    )


async def for_case(conn: AsyncConnection, case_id: str) -> dict[str, object] | None:
    row = (
        await conn.execute(
            text(
                "SELECT grade, market, performed_at, valid_until, status FROM verify.idv_case_claim WHERE case_id = :c"
            ),
            {"c": uuid.UUID(case_id)},
        )
    ).first()
    if row is None:
        return None
    return {
        "grade": row.grade,
        "market": row.market,
        "performed_at": row.performed_at.isoformat(),
        "valid_until": row.valid_until.isoformat(),
        "status": row.status,
    }

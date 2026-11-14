"""Who has approved the public pricing page, and which privacy notice it serves (ZID-UX-PRICE-004 section 09, WF-P07).

`page_state` says APPROVED only while the page file's CURRENT content hash carries a signature from an appointed Founder and from an appointed CCO who are different people; any change to the
file is unsigned again. `sign_page` records one signature, by the hash the signer was shown. `served_notice` is the privacy notice the inquiry form shows: the active version, or, until Legal's
text is activated, the clearly marked draft the page shipped with. Nothing here writes the notice or decides what it says.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.application import finance_service
from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.domain.catalogue import CommercialError
from zid_platform.modules.commercial.infrastructure.pricing_page_config import (
    content_hash,
    pricing_page,
)

SIGNERS = ("FOUNDER", "CCO")

DRAFT_NOTICE_VERSION = "pricing-inquiry-notice-2026-11-draft"
DRAFT_NOTICE_TEXT = (
    "We use these details only to reply to your inquiry. Sending this does not create an account, a quote, a contract or a payment, and you will not be charged. "
    "Please do not upload or type identity documents, biometric data or document numbers."
)


async def page_state(conn: AsyncConnection) -> dict[str, Any]:
    h = content_hash()
    rows = (
        await conn.execute(
            text(
                "SELECT signer_role, signer_principal, signed_at FROM commercial.pricing_page_signature WHERE content_hash = :h"
            ),
            {"h": h},
        )
    ).all()
    roles = {r[0] for r in rows}
    people = {str(r[1]) for r in rows}
    approved = set(SIGNERS) <= roles and len(people) >= 2
    return {
        "version": pricing_page().version,
        "content_hash": h,
        "status": "APPROVED" if approved else "PROPOSED",
        "signed_by": sorted(roles),
        "missing": sorted(set(SIGNERS) - roles),
    }


async def sign_page(
    conn: AsyncConnection, *, signer: str, shown_hash: str, now: datetime | None = None
) -> dict[str, Any]:
    """An appointed Founder or CCO signs the page file as it is now. The signer signs the hash they were shown, so a file that changed under them is not signed."""
    at = now or datetime.now(UTC)
    h = content_hash()
    if shown_hash != h:
        raise CommercialError(
            "PAGE_CHANGED", "the page is not the one you were shown; read it again"
        )
    role = None
    for candidate in SIGNERS:
        if await finance_service.holds(conn, signer, candidate, at):
            role = candidate
            break
    if role is None:
        raise CommercialError(
            "NOT_A_SIGNATORY", "only an appointed Founder or CCO signs the pricing page"
        )
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO commercial.pricing_page_signature (content_hash, page_version, signer_principal, signer_role, signed_at) VALUES (:h, :v, :p, :r, :n)"
                ),
                {"h": h, "v": pricing_page().version, "p": uuid.UUID(signer), "r": role, "n": at},
            )
    except Exception as e:
        raise CommercialError("ALREADY_SIGNED", f"{role} has signed this version already") from e
    await log_change(
        conn,
        kind="PRICING_PAGE_SIGNED",
        ref=h,
        tenant_id=None,
        actor=signer,
        reason=role,
        detail={"version": pricing_page().version},
    )
    return await page_state(conn)


async def served_notice(conn: AsyncConnection) -> dict[str, str]:
    """The privacy notice the inquiry form shows now: the active version, else the marked draft."""
    r = (
        await conn.execute(
            text(
                "SELECT version, body FROM commercial.public_notice WHERE kind = 'PRICING_INQUIRY' AND status = 'ACTIVE'"
            )
        )
    ).first()
    if r:
        return {"version": r[0], "text": r[1], "status": "ACTIVE"}
    return {"version": DRAFT_NOTICE_VERSION, "text": DRAFT_NOTICE_TEXT, "status": "DRAFT"}

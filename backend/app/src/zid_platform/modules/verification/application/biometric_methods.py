"""Biometric method lifecycle service (ZID-DTV-WF-04 BIO-03; CR-107): propose a step up, approve or reject it, step down (the kill switch), list.

    propose        a step up for an exact scope; changes nothing until someone else approves it
    approve        the second person: the proposal becomes the scope's next version, if the state has not moved since
    reject         refuse (or withdraw) an open proposal; nothing changes state
    step_down      PAUSED, a lower state, or OFF: one person, at once
    list_scopes    each scope's versions, oldest first, and the open proposals

All of it runs inside the caller's transaction, under a row lock on the scope's history. Who may call it, and the audit record, is the API's job.
The service is shared with the WF-05 check methods (`method_lifecycle.Lifecycle`, CR-112); this module binds it to the biometric tables.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import biometric_release
from zid_platform.modules.verification.application.method_lifecycle import Lifecycle
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.infrastructure.biometric_method_store import TABLES

LIFECYCLE = Lifecycle(TABLES)

propose = LIFECYCLE.propose


async def approve(
    conn: AsyncConnection, proposal_id: str, *, by: str, reason: str, now: datetime
) -> tuple[bm.Proposal, bm.Record]:
    """The second person (CR-107), and the release attestation in the same transaction: the build and capture SDK this approval was signed for
    (CR-113, QA-041)."""
    p, rec = await LIFECYCLE.approve(conn, proposal_id, by=by, reason=reason, now=now)
    await biometric_release.attest(conn, rec, by=by, now=now)
    return p, rec


reject = LIFECYCLE.reject
step_down = LIFECYCLE.step_down
list_scopes = LIFECYCLE.list_scopes
availability_for = LIFECYCLE.availability_for

__all__ = [
    "LIFECYCLE",
    "approve",
    "availability_for",
    "list_scopes",
    "propose",
    "reject",
    "step_down",
]

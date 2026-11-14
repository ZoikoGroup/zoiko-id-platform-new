"""The read-only identity metadata adapter: the two read tools of the agent (ZID-AG-WF-001 sections 03, 09; CR-091).

`read_mandate_metadata` returns the approved projection of the tenant's live mandates homed in this cell, joined with the approved scope
and evidence reference of the grant each rests on; `read_agent_identity_status` returns whether an executing agent's sponsor is active.
Both go through the ports of the modules that own the data, only return ids, states, dates and scope sets, and never a document, a
credential, a biometric or a personal field. Every call is authorized by the caller before it reaches here (AT-09).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.access_readiness.domain.policy import READ_AGENT, READ_MANDATE
from zid_platform.modules.access_readiness.domain.rules import LIVE_STATUSES, MandateFacts
from zid_platform.modules.authority.ports import grant_review_facts
from zid_platform.modules.machine.ports import agent_identity_status
from zid_platform.modules.mandate.ports import mandate_metadata_page

# (tool, resource) -> None, or raises ReadinessError after recording the denial
Authorize = Callable[[str, str], Awaitable[None]]


class MetadataAdapter(Protocol):
    async def read_mandates(
        self,
        conn: AsyncConnection,
        *,
        authorize: Authorize,
        cell_uuid: str | None,
        limit: int,
        now: datetime,
    ) -> list[MandateFacts]: ...


class LiveMetadataAdapter:
    """The real projection over the mandate, authority and machine ports."""

    async def read_mandates(
        self,
        conn: AsyncConnection,
        *,
        authorize: Authorize,
        cell_uuid: str | None,
        limit: int,
        now: datetime,
    ) -> list[MandateFacts]:
        if cell_uuid is None:
            raise RuntimeError(
                "no cell configured"
            )  # fail closed: the run is FAILED, nothing is written
        await authorize(READ_MANDATE, "mandates:page")
        page = await mandate_metadata_page(
            conn, home_cell=cell_uuid, statuses=sorted(LIVE_STATUSES), limit=limit
        )
        sponsors: dict[str, tuple[bool, str | None, bool | None]] = {}
        out: list[MandateFacts] = []
        for m in page:
            if m.executor_principal_id not in sponsors:
                await authorize(READ_AGENT, f"agent:{m.executor_principal_id}")
                sponsors[m.executor_principal_id] = await _sponsor(
                    conn, m.executor_principal_id, now
                )
            required, sponsor_ref, sponsor_active = sponsors[m.executor_principal_id]
            grant = await grant_review_facts(conn, m.authority_grant_id)
            out.append(
                MandateFacts(
                    mandate_id=m.mandate_id,
                    status=m.status,
                    valid_to=m.valid_to,
                    assigned_actions=m.allowed_actions,
                    assigned_tools=m.tools,
                    grant_found=grant is not None,
                    approved_actions=grant.actions if grant else None,
                    approved_tools=grant.tools if grant else None,
                    evidence_ref=grant.evidence_ref if grant else None,
                    grant_ref=grant.grant_id if grant else None,
                    sponsor_required=required,
                    sponsor_ref=sponsor_ref,
                    sponsor_active=sponsor_active,
                )
            )
        return out


async def _sponsor(
    conn: AsyncConnection, executor_id: str, now: datetime
) -> tuple[bool, str | None, bool | None]:
    """(a sponsor is required, its reference, whether it is active). Only an AI agent has a registered sponsor; None means unreadable."""
    try:
        agent = await agent_identity_status(conn, executor_id, now=now)
    except Exception:  # noqa: BLE001 - an unreadable registry is an unknown, never a healthy sponsor
        return True, None, None
    if agent is None:
        return False, None, None
    return True, agent.sponsor_principal_id, agent.sponsor_active

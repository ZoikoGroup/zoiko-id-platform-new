"""What a decision needs to know about a mandate (ZID-MANDATE-001 §9, §12; ZID-AUTHZ-001 §12 step 7).

Read only: the decision service asks whether the named mandate is live and covers this executor, the
represented principal, the action, the resource and the tool. It never reserves or spends budget: that happens
in the business operation (reserve, then commit), not in an authorization question.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.mandate.domain.ledger import LedgerError, MandateStatus
from zid_platform.modules.mandate.infrastructure.mandate_store import load_terms


@dataclass(frozen=True, slots=True)
class MandateCheck:
    mandate_id: str
    active: bool  # ACTIVE and inside its validity window
    in_scope: bool  # this executor, this represented principal, this action, this resource
    tool_allowed: bool  # no tool named, or the tool is one the mandate lists
    valid_until: datetime


def _covers(patterns: frozenset[str], resource: str) -> bool:
    """`type:id` exactly, or `type:*` for every resource of that type."""
    kind = resource.split(":", 1)[0]
    return resource in patterns or f"{kind}:*" in patterns


async def check_for_decision(
    conn: AsyncConnection,
    mandate_id: str,
    *,
    executor_id: str,
    represented_id: str | None,
    action: str,
    resource: str,
    tool: str | None,
    now: datetime,
) -> MandateCheck | None:
    """None when no such mandate exists in this tenant (RLS scopes the lookup): the decision then denies."""
    try:
        terms, _budget, _grant = await load_terms(conn, mandate_id)
    except (LedgerError, ValueError):
        return None
    return MandateCheck(
        mandate_id=terms.mandate_id,
        active=terms.status is MandateStatus.ACTIVE and terms.valid_from <= now < terms.valid_to,
        in_scope=(
            terms.executor_principal_id == executor_id
            and (represented_id is None or terms.represented_principal_id == represented_id)
            and action in terms.actions
            and _covers(terms.resource_scope, resource)
        ),
        tool_allowed=tool is None or tool in terms.tools,
        valid_until=terms.valid_to,
    )

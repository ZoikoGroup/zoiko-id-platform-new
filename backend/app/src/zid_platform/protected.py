"""Protected administrative actions (ZID-AUTH-001 §24; ZID-AUTHZ-001 §12; DEC-B1).

Identity administration (federation integrations, SCIM connectors, signal streams, recovery
approvals, security-state overrides) is never authorized by a role flag: the caller must be
*allowed by the PDP* for the catalogued action on that resource - authority chain, policy release,
assurance floor and freshness for the action's risk class, security state - and, where the catalog
requires it, carry an APPROVED transaction-bound approval from a second administrator.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_errors import ErrorDetail, ZidError

from zid_platform.api_auth import Caller
from zid_platform.modules.authority.ports import Request as AuthorityRequest
from zid_platform.modules.authority.ports import evaluate_approval
from zid_platform.modules.decision.ports import (
    Decision,
    DecisionRequest,
    Outcome,
    action_spec,
    evaluate_request,
)

CATALOG_FILE = Path(
    os.environ.get("ZID_ACTION_CATALOG")
    or Path(__file__).resolve().parents[4] / "contracts" / "action-catalog" / "actions.yaml"
)


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    return yaml.safe_load(CATALOG_FILE.read_text(encoding="utf-8"))["actions"]  # type: ignore[no-any-return]


def transaction_context(action: str, resource: str, change: str) -> str:
    """What an approval is bound to: this action, on this resource, with this exact change."""
    return f"{action}|{resource}|{change}"


async def protected_action(
    conn: AsyncConnection,
    caller: Caller,
    *,
    action: str,
    resource: str,
    change: str = "",
    approval_request_id: str | None = None,
    now: datetime | None = None,
) -> Decision:
    """ALLOW or raise. STEP_UP and APPROVAL_REQUIRED come back as 403s the client can act on."""
    now = now or datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    try:
        spec = action_spec(action, catalog())
    except KeyError as e:
        raise ZidError("ACTION_UNKNOWN", "Action is not catalogued.", http_status=403) from e
    approval_state = None
    if approval_request_id is not None:
        try:
            outcome = await evaluate_approval(
                conn,
                request_id=approval_request_id,
                current_transaction_context=transaction_context(action, resource, change),
                now=now,
            )
            approval_state = outcome.state.value
        except ValueError:
            approval_state = "DENIED"
    decision = await evaluate_request(
        conn,
        req=DecisionRequest(
            decision_id=str(uuid7()),
            tenant_id=tenant,
            action=spec,
            resource_id=resource,
            represented_principal_id=None,
            purpose=None,
            tool_id=None,
            at=now,
            approval_state=approval_state,
        ),
        executor_id=caller.ctx.principal_id or "",
        authority_request=AuthorityRequest(
            action, resource, now, actor_assurance=caller.authn.level
        ),
        authn=caller.authn,
        security_state=caller.security_state,
        catalog=catalog(),
    )
    if decision.outcome is Outcome.ALLOW:
        return decision
    codes = list(decision.reason_codes)
    if decision.outcome is Outcome.STEP_UP:
        raise ZidError(
            "STEP_UP_REQUIRED",
            "A fresh strong authentication is required.",
            http_status=403,
            headers={
                "WWW-Authenticate": 'Bearer error="insufficient_user_authentication", '
                'acr_values="urn:zoiko:aal:z3"'
            },
            details=[ErrorDetail(issue=c) for c in codes],
        )
    if decision.outcome is Outcome.APPROVAL_REQUIRED:
        raise ZidError(
            "APPROVAL_REQUIRED",
            "A second administrator must approve this exact change.",
            http_status=403,
        )
    raise ZidError(
        "PROTECTED_ACTION_DENIED" if decision.outcome is Outcome.DENY else "STATE_UNAVAILABLE",
        "The action is not permitted.",
        http_status=403 if decision.outcome is Outcome.DENY else 503,
        details=[ErrorDetail(issue=c) for c in codes],
    )

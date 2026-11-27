"""Protected administrative actions (ZID-AUTH-001 §24; ZID-AUTHZ-001 §12; DEC-B1).

Identity administration (federation integrations, SCIM connectors, signal streams, recovery
approvals, security-state overrides) is never authorized by a role flag: the caller must be
*allowed by the PDP* for the catalogued action on that resource - authority chain, policy release,
assurance floor and freshness for the action's risk class, security state - and, where the catalog
requires it, carry an APPROVED transaction-bound approval from a second administrator.
"""

from __future__ import annotations

import os
from dataclasses import replace
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
from zid_platform.modules.organization.ports import KybGateError, kyb_gate_action
from zid_platform.modules.session.ports import signed_key_service_caller
from zid_platform.modules.signals.ports import security_view

# A SUSPENDED tenant gets no protected decisions except its governed lifecycle (resume/offboard;
# OVERLAY-001 §2 "ACTIVE after governed recovery").
SUSPENDED_TENANT_ACTIONS = frozenset({"tenant.lifecycle.manage"})

CATALOG_FILE = Path(
    os.environ.get("ZID_ACTION_CATALOG")
    or Path(__file__).resolve().parents[4] / "contracts" / "action-catalog" / "actions.yaml"
)


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    return yaml.safe_load(CATALOG_FILE.read_text(encoding="utf-8"))["actions"]  # type: ignore[no-any-return]


KYB_GATE_FILE = Path(
    os.environ.get("ZID_KYB_ACTION_GATE")
    # an installed image keeps its reviewed configuration under ZID_PLATFORM_CONFIG; a source checkout keeps it beside the package
    or (
        Path(os.environ["ZID_PLATFORM_CONFIG"]) / "kyb" / "action-gate.yaml"
        if os.environ.get("ZID_PLATFORM_CONFIG")
        else None
    )
    or Path(__file__).resolve().parents[3] / "config" / "kyb" / "action-gate.yaml"
)


def kyb_gate_mode() -> str:
    """off (default), shadow or enforce. Read on each call so a deployment can change it without a restart."""
    mode = os.environ.get("ZID_KYB_GATE", "off").strip().lower()
    return mode if mode in ("off", "shadow", "enforce") else "off"


@lru_cache(maxsize=1)
def kyb_gate_actions() -> dict[str, str]:
    loaded = yaml.safe_load(KYB_GATE_FILE.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in (loaded.get("actions") or {}).items()}


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
    require_approval: bool = False,
) -> Decision:
    """ALLOW or raise. STEP_UP and APPROVAL_REQUIRED come back as 403s the client can act on.

    `require_approval`: a change that always needs a second administrator, whatever the policy
    release says (e.g. enabling IdP-initiated SSO): without an APPROVED approval bound to exactly
    this change it is refused before the PDP is asked."""
    now = now or datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    if caller.tenant_status == "SUSPENDED" and action not in SUSPENDED_TENANT_ACTIONS:
        raise ZidError(
            "TENANT_SUSPENDED",
            "This tenant is suspended; only its governed resumption is possible.",
            http_status=403,
        )
    try:
        await kyb_gate_action(
            conn,
            tenant,
            caller.ctx.principal_id,
            action,
            mode=kyb_gate_mode(),
            mapping=kyb_gate_actions(),
            now=now,
        )
    except (
        KybGateError
    ) as e:  # the company's verified standing does not cover this act (ZID-DTV-WF-02; CR-109)
        raise ZidError(
            "KYB_GRADE_INSUFFICIENT",
            e.detail or "The company's verification does not cover this act.",
            http_status=403,
        ) from e
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
    if require_approval and approval_state != "APPROVED":
        raise ZidError(
            "APPROVAL_REQUIRED",
            "A second administrator must approve this exact change.",
            http_status=403,
        )
    # CR-066: an action the catalogue marks `machine_callable` may be called by a signed-key service client, which has no session and so no passkey.
    # Its signed key (proof of possession) counts as phishing-resistant, AAL-Z2, for THAT action only; any other action still sees the caller's real level.
    authn = caller.authn
    if (
        catalog().get(action, {}).get("machine_callable") is True
        and caller.session_id is None
        and authn.level < 2
        and await signed_key_service_caller(
            conn, client_id=caller.client_id, principal_id=caller.ctx.principal_id or ""
        )
    ):
        authn = replace(authn, level=2)
    # FR-SIG-006: protected actions are R2/R3 - they never act on a cached security state,
    # but read the committed one inside this very transaction.
    fresh = await security_view(
        conn,
        tenant_id=tenant,
        principal_id=caller.ctx.principal_id or "",
        auth_time=caller.session_auth_time,
        risk_class=max(int(spec.min_risk), 2),
        now=now,
    )
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
            workload_ref=caller.certificate_thumbprint,
            approval_state=approval_state,
        ),
        executor_id=caller.ctx.principal_id or "",
        authority_request=AuthorityRequest(action, resource, now, actor_assurance=authn.level),
        authn=authn,
        security_state=fresh.state,
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


async def approved_by_second_admin(
    conn: AsyncConnection,
    caller: Caller,
    *,
    action: str,
    resource: str,
    change: str,
    approval_request_id: str,
    now: datetime | None = None,
) -> str:
    """Authorize a two-person change and return the approving administrator (never the caller).

    The approval must be APPROVED and bound to exactly this action, resource and change."""
    now = now or datetime.now(UTC)
    await protected_action(
        conn,
        caller,
        action=action,
        resource=resource,
        change=change,
        approval_request_id=approval_request_id,
        now=now,
        require_approval=True,
    )
    outcome = await evaluate_approval(
        conn,
        request_id=approval_request_id,
        current_transaction_context=transaction_context(action, resource, change),
        now=now,
    )
    approver = next((p for p in outcome.counted if p != caller.ctx.principal_id), None)
    if approver is None:
        raise ZidError(
            "APPROVAL_REQUIRED",
            "An independent second administrator must approve.",
            http_status=403,
        )
    return approver

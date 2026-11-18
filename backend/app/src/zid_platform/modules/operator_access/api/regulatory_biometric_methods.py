"""Biometric method lifecycle and kill switch, operator plane (ZID-DTV-WF-04 section 06, BIO-03; CR-107).

    POST /v1/regulatory/biometric-methods/proposals                    propose one step up           (regulatory.biometric_method.propose)
    POST /v1/regulatory/biometric-methods/proposals/{id}:approve       the second person approves    (regulatory.biometric_method.approve)
    POST /v1/regulatory/biometric-methods/proposals/{id}:reject        refuse or withdraw            (regulatory.biometric_method.approve)
    POST /v1/regulatory/biometric-methods/{scope_id}:step-down         PAUSED / lower state / OFF    (regulatory.biometric_method.pause)
    GET  /v1/regulatory/biometric-methods                              every scope, its versions, and the open proposals

A step up is two people and one step at a time (OFF or PAUSED -> LAB_ONLY -> PILOT_GATED -> PRODUCTION_ELIGIBLE); the domain and the table both
refuse an approval by the proposer. A step down (the kill switch) is one person and takes effect at once; a resume from PAUSED is a new approval. Every
action writes an audit record in the same transaction (regulatory.biometric_method.proposed, .approved, .rejected, .stepped_down); a refusal is recorded
as regulatory.biometric_method.refused. The audit carries the scope, the states and the references, never the reason text.

Approval is not activation: a live session still needs the deployment's accepted engine, the tenant's own approved DPIA and the country overlay
(`GET /v1/biometric-methods/eligibility`). The routes are built by `method_lifecycle_routes` (shared with the WF-05 check methods, CR-112).
"""

from __future__ import annotations

import uuid
from typing import Any

from zid_platform.modules.operator_access.api.method_lifecycle_routes import Family, build


def _ports() -> Any:
    import zid_platform.modules.verification.ports as ports  # noqa: PLR0402 - imported on first use (avoids a dependency cycle)

    return ports


PROPOSE = "regulatory.biometric_method.propose"
APPROVE = "regulatory.biometric_method.approve"
PAUSE = "regulatory.biometric_method.pause"
STATE_CHANGED = "verification.biometric_method.state_changed"  # CR-108: approvals and step downs are announced (priority lane)

FAMILY = Family(
    base_path="/v1/regulatory/biometric-methods",
    capability="regulatory.biometric_method",
    audit="regulatory.biometric_method",
    state_changed=STATE_CHANGED,
    target_type="biometric_method",
    namespace=uuid.UUID("b7c41e0a-2f6d-4a19-9e83-5d0c7a2b1f64"),
    refused_code="BIOMETRIC_METHOD_REFUSED",
    methods=("FACE_MATCH", "LIVENESS"),
    method_enum=lambda: _ports().biometric_method_model.Method,
    service=lambda: _ports().biometric_methods,
    model=lambda: _ports().biometric_method_model,
    list_resource="regulatory:biometric-methods",
)
router, actions = build(FAMILY)


def _with_markets() -> None:
    from zid_platform.modules.operator_access.api.regulatory_biometric_markets import (
        router as markets,
    )

    router.include_router(markets)  # CR-113 (QA-039): retire a market's biometric policy


_with_markets()

__all__ = ["APPROVE", "FAMILY", "PAUSE", "PROPOSE", "STATE_CHANGED", "actions", "router"]

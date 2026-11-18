"""WF-05 check method lifecycle and kill switch, operator plane (ZID-DTV-WF-05 §04, §05, §27, A12; CR-112).

    POST /v1/regulatory/check-methods/proposals                    propose one step up           (regulatory.check_method.propose)
    POST /v1/regulatory/check-methods/proposals/{id}:approve       the second person approves    (regulatory.check_method.approve)
    POST /v1/regulatory/check-methods/proposals/{id}:reject        refuse or withdraw            (regulatory.check_method.approve)
    POST /v1/regulatory/check-methods/{scope_id}:step-down         PAUSED / lower state / OFF    (regulatory.check_method.pause)
    GET  /v1/regulatory/check-methods                              every scope, its versions, and the open proposals

The methods are the WF-05 ones: EMAIL_CHALLENGE, PHONE_SMS_CHALLENGE, PHONE_VOICE_CHALLENGE, CARRIER_INTELLIGENCE, ADDRESS_LOOKUP, ADDRESS_DOCUMENT,
ADDRESS_DATA_SOURCE, ADDRESS_POSTAL_CHALLENGE and DEVICE_RISK. A scope names the adapter's engine id and version, a country, a purpose and optionally
one tenant. The rules are the biometric ones (two people up, one step at a time; one person down, at once; a resume is a new approval). For a check
method, `dpia_ref` names the privacy impact assessment (DTV-12 carry-over) and `evaluation_ref` the executed QA or certification record (G2).

Approval is not activation: a live case still needs the deployment's adapter, an approved retention rule for every data class the method creates, an
entitlement for a licensed route, and the country's signed privacy overlay (`GET /v1/check-methods/eligibility`).
"""

from __future__ import annotations

import uuid
from typing import Any

from zid_platform.modules.operator_access.api.method_lifecycle_routes import Family, build


def _ports() -> Any:
    import zid_platform.modules.verification.ports as ports  # noqa: PLR0402 - imported on first use (avoids a dependency cycle)

    return ports


PROPOSE = "regulatory.check_method.propose"
APPROVE = "regulatory.check_method.approve"
PAUSE = "regulatory.check_method.pause"
STATE_CHANGED = "verification.check_method.state_changed"
METHODS = (
    "EMAIL_CHALLENGE",
    "PHONE_SMS_CHALLENGE",
    "PHONE_VOICE_CHALLENGE",
    "CARRIER_INTELLIGENCE",
    "ADDRESS_LOOKUP",
    "ADDRESS_DOCUMENT",
    "ADDRESS_DATA_SOURCE",
    "ADDRESS_POSTAL_CHALLENGE",
    "DEVICE_RISK",
)

FAMILY = Family(
    base_path="/v1/regulatory/check-methods",
    capability="regulatory.check_method",
    audit="regulatory.check_method",
    state_changed=STATE_CHANGED,
    target_type="check_method",
    namespace=uuid.UUID("4e2b9c71-8a3d-4f05-b6e1-7c9d2a5f0b38"),
    refused_code="CHECK_METHOD_REFUSED",
    methods=METHODS,
    method_enum=lambda: _ports().check_method_model.CheckMethod,
    service=lambda: _ports().check_methods,
    model=lambda: (
        _ports().biometric_method_model
    ),  # the shared lifecycle model: Scope, State, MethodError
    list_resource="regulatory:check-methods",
)
router, actions = build(FAMILY)

__all__ = ["APPROVE", "FAMILY", "METHODS", "PAUSE", "PROPOSE", "STATE_CHANGED", "actions", "router"]

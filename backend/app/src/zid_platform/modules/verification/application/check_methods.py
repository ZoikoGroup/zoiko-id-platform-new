"""WF-05 check methods: the lifecycle service and live eligibility (ZID-DTV-WF-05 §04, §05, §19 requirements, A01; CR-112).

The lifecycle (propose, approve, reject, step down, list) is the biometric one (`method_lifecycle.Lifecycle`) over `verify.check_method_*`.

Eligibility answers, for one tenant, country and purpose, which WF-05 methods a live case may use now and every reason each may not
(`domain/check_methods.eligibility`):
1. the deployment ships an adapter for the method (`config/verification/check-adapters.yaml`);
2. the method's lifecycle scope for the adapter's exact engine and version is live here (a pause always wins);
3. every data class the method creates has an APPROVED retention rule (`config/privacy/check-data-classes.yaml`);
4. a LICENSED route is entitled for the tenant. No licensed route is wired yet, so every one is refused (no hidden paid fallback);
5. the country's privacy overlay exists and is signed (as for biometrics); a missing or unsigned overlay refuses every method in that country.

The answer carries `policy_hash`, a digest of exactly what it rested on, so a case can name the policy it was opened under.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application.method_lifecycle import Lifecycle
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain import privacy_overlay as po
from zid_platform.modules.verification.infrastructure import check_method_config
from zid_platform.modules.verification.infrastructure.method_store import MethodTables

TABLES = MethodTables("verify.check_method_state", "verify.check_method_proposal", cm.CheckMethod)
LIFECYCLE = Lifecycle(TABLES)

propose = LIFECYCLE.propose
approve = LIFECYCLE.approve
reject = LIFECYCLE.reject
step_down = LIFECYCLE.step_down
list_scopes = LIFECYCLE.list_scopes
availability_for = LIFECYCLE.availability_for


def overlay_reasons(overlay: po.Overlay | None) -> list[str]:
    if overlay is None:
        return ["PRIVACY_OVERLAY_MISSING"]
    return [f"OVERLAY_{p.upper().replace(':', '_')}" for p in po.problems(overlay)]


@dataclass(frozen=True, slots=True)
class Eligibility:
    country: str
    purpose: str
    methods: tuple[cm.MethodEligibility, ...]
    policy_hash: str

    def available(self, method: cm.CheckMethod) -> bool:
        return any(m.method is method and m.available for m in self.methods)

    def view(self) -> dict[str, Any]:
        return {
            "country": self.country,
            "purpose": self.purpose,
            "available_methods": [m.method.value for m in self.methods if m.available],
            "methods": [method_view(m) for m in self.methods],
            "policy_hash": self.policy_hash,
        }


def method_view(m: cm.MethodEligibility) -> dict[str, Any]:
    return {
        "method": m.method.value,
        "available": m.available,
        "state": m.state.value,
        "reasons": list(m.reasons),
        "route": m.route.value if m.route else None,
        "engine": f"{m.engine_id}@{m.engine_version}" if m.engine_id else None,
        "produces": list(m.produces),
        "scope_ref": f"{m.scope_id}:v{m.scope_version}" if m.scope_id else None,
    }


def policy_hash(
    country: str,
    purpose: str,
    methods: tuple[cm.MethodEligibility, ...],
    overlay: po.Overlay | None,
) -> str:
    body = {
        "country": country,
        "purpose": purpose,
        "overlay": po.overlay_hash(overlay) if overlay is not None else None,
        "methods": [method_view(m) for m in methods],
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def eligibility(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    country: str,
    purpose: str,
    overlays: Mapping[str, po.Overlay],
    now: datetime,
    rollout_key: str | None = None,
    adapters: Mapping[cm.CheckMethod, cm.Adapter] | None = None,
    retention: Mapping[str, cm.RetentionRule] | None = None,
) -> Eligibility:
    """One answer per WF-05 method, in a fixed order. `adapters` and `retention` default to the deployment's configuration."""
    country = country.upper()
    adapters = check_method_config.adapters() if adapters is None else adapters
    retention = check_method_config.retention() if retention is None else retention
    overlay = overlays.get(country)
    o_reasons = overlay_reasons(overlay)
    answers: list[cm.MethodEligibility] = []
    for method in cm.CheckMethod:
        adapter = adapters.get(method)
        availability = None
        if adapter is not None:
            availability = await availability_for(
                conn,
                bm.Call(
                    method,
                    adapter.engine_id,
                    adapter.engine_version,
                    country,
                    purpose,
                    tenant_id,
                    rollout_key,
                ),
                at=now,
            )
        answer = cm.eligibility(
            method,
            adapter=adapter,
            availability=availability,
            retention=retention,
            licensed_entitled=False,  # no licensed route is wired yet: refuse rather than fall back to a paid supplier
        )
        if o_reasons:
            reasons = answer.reasons if not answer.available else ()
            answer = replace(
                answer, available=False, reasons=tuple(dict.fromkeys((*reasons, *o_reasons)))
            )
        answers.append(answer)
    result = tuple(answers)
    return Eligibility(country, purpose, result, policy_hash(country, purpose, result, overlay))


__all__ = [
    "LIFECYCLE",
    "TABLES",
    "Eligibility",
    "approve",
    "availability_for",
    "eligibility",
    "list_scopes",
    "method_view",
    "overlay_reasons",
    "propose",
    "reject",
    "step_down",
]

"""Which biometric methods a live session may use, and why not (ZID-DTV-WF-04 section 19 `GET /v1/biometric-methods/eligibility`; BIO-01, BIO-03;
QA-001, QA-015, QA-043; CR-107).

One answer per method (face match, liveness), from six independent gates. Every gate that fails is named; a method is available only when none does:

1. the ENGINE gate: the deployment's accepted engine (`config/self-run-verification/self-run.yaml`, `biometric_engine.engine_gate`). Off, or switched
   on with an incomplete record, is a refusal;
2. the METHOD state for this exact scope (`domain/biometric_methods.py`): engine id and version, country, purpose, tenant. PAUSED always wins;
3. the tenant's own DPIA, read LIVE from the privacy module's release gate for market = country, product = `biometric.<method>`, version = engine
   version. This replaces trusting a reference string: a package that is not APPROVED, has expired, misses a gate signature, carries an unconsulted
   high residual risk or names a model that is not the evaluated one in service refuses the method, and its blockers are listed;
4. the COUNTRY overlay (`domain/privacy_overlay.py`): missing or not signed by DPO and Legal refuses; biometrics PROHIBITED refuses; CONSENT_REQUIRED
   is reported (`consent_required`) and enforced per person when the session starts;
5. BREACH containment (CR-108): an open breach case of this tenant that paused biometric capture in this country refuses every method there;
6. RETENTION (CR-108, BIO-08): every biometric asset class the method produces needs an ACTIVE retention policy of this tenant for the purpose
   ("blank retention => route OFF"); each class without one is named.

The answer carries `policy_hash`, a digest of exactly what it rested on (state versions, DPIA package ids, engine record, overlay hash), so a decision
can name the policy it was made under. Nothing here reads or keeps a face, a template or any media.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import biometric_methods as methods
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import privacy_overlay as po
from zid_platform.modules.verification.domain.biometric_engine import EngineGate, EngineGateError
from zid_platform.modules.verification.infrastructure import self_run_config


def dpia_product(method: bm.Method) -> str:
    """The privacy release-gate product name a tenant's biometric DPIA package is filed under."""
    return f"biometric.{method.value.lower()}"


@dataclass(frozen=True, slots=True)
class MethodAnswer:
    method: bm.Method
    available: bool
    state: bm.State
    reasons: tuple[str, ...]
    consent_required: bool
    scope_ref: str | None = None  # "<scope_id>:v<version>" the state rests on
    dpia_package_id: str | None = None
    dpia_blockers: tuple[str, ...] = ()
    breaches: tuple[str, ...] = ()  # open breach cases pausing capture here (CR-108)
    missing_retention: tuple[str, ...] = ()  # asset classes with no retention period (CR-108)

    def view(self) -> dict[str, Any]:
        return {
            "method": self.method.value,
            "available": self.available,
            "state": self.state.value,
            "reasons": list(self.reasons),
            "consent_required": self.consent_required,
            "scope_ref": self.scope_ref,
            "dpia_package_id": self.dpia_package_id,
            "dpia_blockers": list(self.dpia_blockers),
            "breaches": list(self.breaches),
            "missing_retention": list(self.missing_retention),
        }


@dataclass(frozen=True, slots=True)
class Eligibility:
    country: str
    purpose: str
    engine: Mapping[str, Any] | None
    methods: tuple[MethodAnswer, ...]
    policy_hash: str
    notes: tuple[str, ...] = field(default=())

    def available(self, method: bm.Method) -> bool:
        return any(m.method is method and m.available for m in self.methods)

    def view(self) -> dict[str, Any]:
        return {
            "country": self.country,
            "purpose": self.purpose,
            "engine": dict(self.engine) if self.engine else None,
            "available_methods": [m.method.value for m in self.methods if m.available],
            "methods": [m.view() for m in self.methods],
            "policy_hash": self.policy_hash,
            "notes": list(self.notes),
        }


def overlay_reasons(overlay: po.Overlay | None) -> tuple[list[str], bool]:
    """(refusal reasons, consent required) from the country overlay, for a biometric route."""
    if overlay is None:
        return ["PRIVACY_OVERLAY_MISSING"], False
    reasons = [f"OVERLAY_{p.upper().replace(':', '_')}" for p in po.problems(overlay)]
    if overlay.biometrics is po.Biometrics.PROHIBITED:
        reasons.append("BIOMETRIC_NOT_PERMITTED_IN_COUNTRY")
    return reasons, overlay.biometrics is po.Biometrics.CONSENT_REQUIRED


def combine(
    method: bm.Method,
    *,
    engine_reasons: list[str],
    state: bm.Availability | None,
    dpia: Mapping[str, Any] | None,
    overlay: po.Overlay | None,
    breaches: tuple[str, ...] = (),
    missing_retention: tuple[str, ...] = (),
) -> MethodAnswer:
    """Pure: one method's answer from what each gate said. `state` and `dpia` are None when the engine gate already failed (they cannot be asked
    without an engine id and version)."""
    reasons = list(engine_reasons)
    s = state.state if state is not None else bm.State.OFF
    scope_ref = None
    if state is not None:
        reasons += [r for r in state.reasons if r != "METHOD_PILOT"]
        if state.record is not None:
            scope_ref = f"{state.record.scope_id}:v{state.record.version}"
    dpia_blockers: tuple[str, ...] = ()
    package = None
    if dpia is not None:
        package = dpia.get("package_id")
        if not dpia.get("ready"):
            dpia_blockers = tuple(str(b) for b in dpia.get("blockers") or ("DPIA_NOT_READY",))
            reasons.append("DPIA_NOT_READY")
    o_reasons, consent = overlay_reasons(overlay)
    reasons += o_reasons
    if breaches:
        reasons.append("BREACH_CAPTURE_PAUSED")
    reasons += [f"RETENTION_NOT_SET:{c}" for c in missing_retention]
    ok = not reasons and state is not None and state.available
    return MethodAnswer(
        method=method,
        available=ok,
        state=s,
        reasons=tuple(dict.fromkeys(reasons)),
        consent_required=consent,
        scope_ref=scope_ref,
        dpia_package_id=str(package) if package else None,
        dpia_blockers=dpia_blockers,
        breaches=tuple(breaches),
        missing_retention=tuple(missing_retention),
    )


def policy_hash(
    *,
    country: str,
    purpose: str,
    engine: Mapping[str, Any] | None,
    answers: tuple[MethodAnswer, ...],
    overlay: po.Overlay | None,
) -> str:
    body = {
        "country": country,
        "purpose": purpose,
        "engine": dict(engine) if engine else None,
        "overlay": po.overlay_hash(overlay) if overlay is not None else None,
        "methods": [
            {
                "method": a.method.value,
                "state": a.state.value,
                "scope_ref": a.scope_ref,
                "dpia_package_id": a.dpia_package_id,
                "available": a.available,
                "breaches": list(a.breaches),
                "missing_retention": list(a.missing_retention),
            }
            for a in answers
        ],
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
    gate: EngineGate | None = None,
    gate_given: bool = False,
) -> Eligibility:
    """Run inside the TENANT's transaction: the DPIA packages are tenant data behind row-level security, and the answer must use this tenant's own.
    `gate`/`gate_given` let a caller (or a test) supply the engine gate instead of reading the deployment's configuration."""
    from zid_platform.modules.privacy.ports import (
        capture_paused_breaches,
        missing_biometric_retention,
        release_gate,
    )

    country = country.upper()
    engine_reasons: list[str] = []
    if not gate_given:
        try:
            gate = self_run_config.accepted_engine(now.date())
        except EngineGateError as e:
            gate = None
            engine_reasons.append("ENGINE_GATE_INVALID")
            engine_reasons += [f"ENGINE_{p}" for p in e.problems]
    if gate is None and not engine_reasons:
        engine_reasons.append("ENGINE_NOT_ACCEPTED")
    engine = gate.audit() if gate is not None else None
    overlay = overlays.get(country)
    blocked = list(engine_reasons)
    if not tenant_id:  # a method scope and a DPIA belong to a tenant; a caller without one gets no biometric method
        blocked.append("TENANT_REQUIRED")
    breaches = tuple(await capture_paused_breaches(conn, country)) if tenant_id else ()
    answers = []
    for method in bm.Method:
        if gate is None or blocked:
            answers.append(
                combine(method, engine_reasons=blocked, state=None, dpia=None, overlay=overlay)
            )
            continue
        call = bm.Call(method, gate.engine_id, gate.engine_version, country, purpose, tenant_id)
        state = await methods.availability_for(conn, call, at=now)
        dpia = await release_gate(conn, country, dpia_product(method), gate.engine_version)
        answers.append(
            combine(
                method,
                engine_reasons=[],
                state=state,
                dpia=dpia,
                overlay=overlay,
                breaches=breaches,
                missing_retention=await missing_biometric_retention(conn, method.value, purpose),
            )
        )
    result = tuple(answers)
    notes = (
        "Consent, where the country requires it, is checked for the person when the session starts.",
        "An available method still needs a capture that passes it: availability is permission to try, never a result.",
    )
    return Eligibility(
        country=country,
        purpose=purpose,
        engine=engine,
        methods=result,
        policy_hash=policy_hash(
            country=country, purpose=purpose, engine=engine, answers=result, overlay=overlay
        ),
        notes=notes,
    )

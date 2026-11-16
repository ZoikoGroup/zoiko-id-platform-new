"""Run one self-run identity check: chip evidence in, a normalized result out (CR-095). The engine, if any, is injected; this layer never imports a model.

An engine that raises, or that answers with something that is not a score, gives PENDING and nothing else. Media are passed to the engine for the
length of the call and are not kept here.
"""

from __future__ import annotations

from datetime import date

from zid_platform.modules.verification.domain.biometric_engine import BiometricEngine, EngineGate
from zid_platform.modules.verification.domain.self_run import (
    CHIP_PHOTO,
    ChipEvidence,
    SelfRunOutcome,
    self_run_result,
)
from zid_platform.modules.verification.domain.verification import ProviderResult
from zid_platform.modules.verification.infrastructure import self_run_config


def assess(
    chip: ChipEvidence,
    *,
    session_ref: str,
    engine: BiometricEngine | None = None,
    probe: bytes | None = None,
    chip_photo: bytes | None = None,
    media: bytes | None = None,
    gate: EngineGate | None = None,
    extra_fraud: tuple[str, ...] = (),
    today: date | None = None,
) -> SelfRunOutcome:
    """`chip_photo` is the DG2 image taken from the chip whose hash passed. `gate` defaults to the deployment's accepted engine (None while off)."""
    gate = gate if gate is not None else self_run_config.accepted_engine(today)
    if gate is None or engine is None or probe is None or chip_photo is None or media is None:
        return self_run_result(chip, session_ref=session_ref, gate=gate, extra_fraud=extra_fraud)
    if (
        chip.status != "VERIFIED_LIMITED"
    ):  # no point scoring a face against a chip that proved nothing
        return self_run_result(chip, session_ref=session_ref, gate=gate, extra_fraud=extra_fraud)
    try:
        face = engine.match(probe, chip_photo)
        live = engine.liveness(media)
    except Exception:  # noqa: BLE001 - an engine that cannot decide is never a pass; the reason is the same whatever broke
        return SelfRunOutcome(
            ProviderResult(
                "PENDING",
                (),
                None,
                None,
                tuple(extra_fraud),
                ("SELF_RUN_ENGINE_UNAVAILABLE",),
                None,
            ),
            {
                "chip_status": chip.status,
                "engine": gate.audit(),
                "codes": ["SELF_RUN_ENGINE_UNAVAILABLE"],
            },
        )
    return self_run_result(
        chip,
        session_ref=session_ref,
        gate=gate,
        face=face,
        face_reference=CHIP_PHOTO,
        liveness=live,
        extra_fraud=extra_fraud,
    )

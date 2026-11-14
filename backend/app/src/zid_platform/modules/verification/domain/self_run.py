"""Self-run identity verification: a passport or eID chip, optionally a face match and a liveness check, turned into the normalized `ProviderResult`
the decision engine already understands (CR-095). Pure domain, no I/O. No Veriff, no Sumsub, no model.

What it takes: the verdict of passive authentication of the chip (what a signed EF.SOD proves), and, only when a person has accepted a biometric
engine (`biometric_engine.engine_gate`), the engine's face match and liveness scores. What it never does is decide: the profile decides what the
facts are worth, exactly as for a provider. Rules, all failing closed:

- the chip is the only source of a claim. A chip that is not verified gives no claim: NOT_VERIFIED is a FAILED result, INDETERMINATE (no country
  signing CA configured, an unreadable object) is PENDING, never a pass;
- chip only is the best a chip alone can be: no person-to-document binding, no liveness. A profile that needs binding then says BINDING_FAILED;
- binding is BIOMETRIC only when ALL of these hold: an accepted engine, a face match AND a liveness result from that same engine id and version, the
  reference photo is the one read from a chip data group whose hash the signed object vouches for (a photo OCR'd from a picture of a document, or a
  name, never binds), and both scores reach the thresholds written with the engine's measured error rates;
- a face that does not reach the threshold is FAILED, never favourable; a liveness score under its threshold keeps the binding but marks liveness
  failed and raises a presentation-attack signal, which sends the session to review or fails it;
- any score that is not a number in [0, 1], an engine that is not the accepted one, or an engine that could not answer is PENDING or unbound,
  never an approval;
- risk signals from anywhere (a document check, a watchlist) are passed through as fraud signals. Nothing here claims a fraud check was performed.

What is kept: scores, the engine id and version, the evaluation id and thresholds, a commitment. Never a photo, never a template.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from zid_platform.modules.verification.domain.biometric_engine import EngineGate, FaceMatch, Liveness
from zid_platform.modules.verification.domain.verification import ClaimResult, ProviderResult

CHIP_PHOTO = "CHIP_DG2"  # the only reference that can bind a person to a document in this path
_SAFE = re.compile(r"[^A-Z0-9_]")


def _code(prefix: str, value: object) -> str:
    return f"{prefix}_{_SAFE.sub('_', str(value).upper())}"[:64]


@dataclass(frozen=True, slots=True)
class ChipEvidence:
    """What the chip check established, without importing the module that did it. `from_verdict` reads the same shape as `ChipVerdict`."""

    status: str  # VERIFIED_LIMITED | NOT_VERIFIED | INDETERMINATE
    reasons: tuple[str, ...]
    legal_name: str | None = None
    birth_date: str | None = None
    photo_verified: bool = False  # DG2 was supplied and its hash matched the signed object

    @classmethod
    def from_verdict(
        cls, status: str, reasons: Sequence[str], detail: Mapping[str, Any] | None
    ) -> ChipEvidence:
        detail = detail or {}
        verified = detail.get("data_groups_verified")
        groups = {int(g) for g in verified} if isinstance(verified, list | tuple) else set()
        doc = detail.get("document")
        name = birth = None
        # a name or a date counts only when it came from a data group the signed object vouches for, the line holds together and the
        # document has not expired
        if (
            status == "VERIFIED_LIMITED"
            and isinstance(doc, Mapping)
            and 1 in groups
            and doc.get("mrz_check_digits_hold") is True
            and "DOCUMENT_EXPIRED" not in reasons
        ):
            given, surname = str(doc.get("given_names") or "").strip(), str(doc.get("surname") or "").strip()
            name = " ".join(p for p in (given, surname) if p) or None
            birth = str(doc["birth_date"]) if doc.get("birth_date") else None
        return cls(status, tuple(reasons), name, birth, 2 in groups and status == "VERIFIED_LIMITED")


@dataclass(frozen=True, slots=True)
class SelfRunOutcome:
    result: ProviderResult
    audit: dict[str, Any]  # what the decision record keeps: scores, versions, references, a commitment


def _score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    f = float(value)
    return f if math.isfinite(f) and 0.0 <= f <= 1.0 else None


def _commitment(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def self_run_result(
    chip: ChipEvidence,
    *,
    session_ref: str,
    gate: EngineGate | None = None,
    face: FaceMatch | None = None,
    face_reference: str | None = None,
    liveness: Liveness | None = None,
    extra_fraud: Sequence[str] = (),
) -> SelfRunOutcome:
    fraud = [_code("SELF_RUN_RISK", s) for s in extra_fraud if isinstance(s, str) and s]
    audit: dict[str, Any] = {"chip_status": chip.status, "engine": gate.audit() if gate else None}

    def done(status: str, claims: tuple[ClaimResult, ...], binding: str | None, live: bool | None, codes: Sequence[str]) -> SelfRunOutcome:
        audit["commitment"] = _commitment(
            "self_run", session_ref, chip.status, status, binding, live,
            gate.engine_id if gate else "", gate.engine_version if gate else "", gate.evaluation_id if gate else "",
            audit.get("face_score"), audit.get("liveness_score"),
        )  # fmt: skip
        audit["codes"] = list(codes)
        return SelfRunOutcome(
            ProviderResult(status, claims, binding, live, tuple(dict.fromkeys(fraud)), tuple(dict.fromkeys(codes)), audit["commitment"]),
            audit,
        )

    if chip.status == "NOT_VERIFIED":
        return done("FAILED", (), None, None, ("SELF_RUN_CHIP_NOT_VERIFIED", *chip.reasons[:6]))
    if chip.status != "VERIFIED_LIMITED":
        return done("PENDING", (), None, None, ("SELF_RUN_CHIP_INDETERMINATE", *chip.reasons[:6]))

    codes: list[str] = ["CHIP_PASSIVE_AUTH_ONLY"]
    claims: list[ClaimResult] = []

    def build(method: str, evidence: tuple[str, ...]) -> tuple[ClaimResult, ...]:
        out = []
        if chip.legal_name:
            out.append(ClaimResult("legal_name", True, chip.legal_name, method, evidence))
        if chip.birth_date:
            out.append(ClaimResult("birthdate", True, chip.birth_date, "chip", ("GOVERNMENT_ID",)))
        return tuple(out)

    claims = list(build("chip", ("GOVERNMENT_ID",)))
    if not claims:
        codes.append("SELF_RUN_NO_READABLE_CLAIMS")
    if gate is None:
        codes.append("SELF_RUN_BIOMETRICS_OFF")
        return done("COMPLETED", tuple(claims), None, None, codes)
    if face is None or liveness is None:
        codes.append("SELF_RUN_BIOMETRIC_EVIDENCE_MISSING")
        return done("COMPLETED", tuple(claims), None, None, codes)
    for got in (face, liveness):
        if got.engine_id != gate.engine_id or got.engine_version != gate.engine_version:
            codes.append("SELF_RUN_ENGINE_NOT_THE_ACCEPTED_ONE")
            return done("COMPLETED", tuple(claims), None, None, codes)
    face_score, live_score = _score(face.score), _score(liveness.score)
    if face_score is None or live_score is None:
        return done("PENDING", (), None, None, ("SELF_RUN_SCORE_INVALID",))
    audit["face_score"], audit["liveness_score"] = round(face_score, 4), round(live_score, 4)
    if face_reference != CHIP_PHOTO or not chip.photo_verified:
        codes.append("SELF_RUN_PHOTO_NOT_CHIP_VERIFIED")  # a photo of a document, or a name, never binds a person to it
        return done("COMPLETED", tuple(claims), None, None, codes)
    if face_score < gate.face_threshold:
        return done("FAILED", (), None, None, ("SELF_RUN_FACE_NO_MATCH",))
    live_ok = live_score >= gate.liveness_threshold
    if not live_ok:
        fraud.append("SELF_RUN_PAD_SUSPECTED")
        codes.append("SELF_RUN_LIVENESS_UNDER_THRESHOLD")
    codes.append("SELF_RUN_FACE_MATCHED_TO_CHIP_PHOTO")
    return done(
        "COMPLETED", build("chip+biometric", ("GOVERNMENT_ID", "BIOMETRIC_BINDING")), "BIOMETRIC", live_ok, codes
    )

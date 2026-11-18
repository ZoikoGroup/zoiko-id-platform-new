"""The face match and liveness ENGINE PORT, and the gate that keeps it off until a person has done the work (CR-095). Pure domain, no I/O, no model.

No model ships with the platform. An engine (for example DeepFace, MIT licence; Silent-Face-Anti-Spoofing, Apache-2.0; InsightFace code is open but its
pretrained models are non-commercial) is an adapter somebody writes against `BiometricEngine`. This module decides whether the platform may use one:

- the engine is OFF by default. Nothing here is ever on because a file exists;
- to switch it on a deployment must record, in `config/self-run-verification/self-run.yaml`, the engine id and version, an independent
  evaluation id with its date and its MEASURED error rates (false accept, false reject, presentation-attack accept and bona-fide reject), a
  reference to an approved data protection impact assessment, and a statement that the licence of the engine and of its models was checked;
- the measured rates must be inside engineering ceilings (provisional: they are not a regulator's numbers, the owner may tighten them), and the
  evaluation must be current;
- the thresholds the engine's scores are judged against are set there too, and a result from any other engine id or version is not trusted.

What this does NOT do: it does not evaluate an engine, does not certify presentation-attack detection (an ISO/IEC 30107-3 style test by an
independent lab is a person's job), and does not make a DPIA. It only refuses to run without the references and says why.

A photo or a template is a function argument and nothing more. The result carries scores, the engine id and version, and the evaluation id.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

# Engineering ceilings, PROVISIONAL (CR-095). Not a regulator's numbers: the security owner and the privacy owner may only tighten them.
MAX_FALSE_ACCEPT = 0.001
MAX_FALSE_REJECT = 0.05
MAX_ATTACK_ACCEPT = 0.05  # APCER: presentation attacks wrongly accepted
MAX_BONA_FIDE_REJECT = 0.10  # BPCER
MAX_EVALUATION_AGE = timedelta(days=365)
MIN_FACE_THRESHOLD = 0.5
MIN_LIVENESS_THRESHOLD = 0.5

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{3,120}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_RATES = ("false_accept", "false_reject", "attack_accept", "bona_fide_reject")
ENGINE_MODES = (
    "DISABLED",
    "LAB_ONLY",
    "APPROVED_FOR_SCOPE",
)  # WF01 screen O1: an open-source engine is LAB ONLY until a person approves a scope
_COUNTRY = re.compile(r"^[A-Z]{2}$")


@dataclass(frozen=True, slots=True)
class FaceMatch:
    """What an engine says about a probe against a reference. A score and who produced it, never the images."""

    score: float
    engine_id: str
    engine_version: str


@dataclass(frozen=True, slots=True)
class Liveness:
    score: float
    engine_id: str
    engine_version: str


class BiometricEngine(Protocol):
    """An adapter's contract. Both calls take the media as bytes for the length of the call and return only a score. An engine that cannot
    decide raises, and the caller treats that as PENDING, never as a pass."""

    engine_id: str
    engine_version: str

    def match(self, probe: bytes, reference: bytes) -> FaceMatch: ...

    def liveness(self, media: bytes) -> Liveness: ...


@dataclass(frozen=True, slots=True)
class EngineGate:
    """A biometric engine that a person has allowed. Only `engine_gate` can make one."""

    engine_id: str
    engine_version: str
    evaluation_id: str
    evaluated_on: date
    dpia_ref: str
    false_accept: float
    false_reject: float
    attack_accept: float
    bona_fide_reject: float
    face_threshold: float
    liveness_threshold: float
    countries: frozenset[str] = frozenset()

    def allows(self, country: str | None) -> bool:
        """An approved engine runs only in the countries a person approved it for. No country, no approval."""
        return country is not None and country in self.countries

    def audit(self) -> dict[str, Any]:
        """What a decision record keeps about the engine. Versions, references and numbers: no media, no template."""
        return {
            "engine_id": self.engine_id,
            "engine_version": self.engine_version,
            "evaluation_id": self.evaluation_id,
            "evaluated_on": self.evaluated_on.isoformat(),
            "dpia_ref": self.dpia_ref,
            "measured": {k: getattr(self, k) for k in _RATES},
            "face_threshold": self.face_threshold,
            "liveness_threshold": self.liveness_threshold,
            "mode": "APPROVED_FOR_SCOPE",
            "countries": sorted(self.countries),
        }


def _rate(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    f = float(value)
    return f if math.isfinite(f) and 0.0 <= f <= 1.0 else None


def register_problems(components: object, register: Mapping[str, Any] | None) -> list[str]:
    """Why the engine's components keep it shut (WF01-011). Every component the engine is built from must be in the licence register with commercial use
    confirmed (`YES`), the pretrained-model licences read (`model_licence_confirmed`) and the date a person verified them. Anything less is a problem.
    `register` None means the caller did not supply one (pure rule tests); the deployment's loader always does."""
    if register is None:
        return []
    if (
        not isinstance(components, list)
        or not components
        or not all(isinstance(c, str) for c in components)
    ):
        return ["ENGINE_COMPONENTS_MISSING"]
    listed = {
        str(c.get("id")): c for c in (register.get("components") or ()) if isinstance(c, Mapping)
    }
    out: list[str] = []
    for name in components:
        c = listed.get(name)
        if c is None:
            out.append(f"COMPONENT_{name.upper()}_NOT_IN_LICENCE_REGISTER")
            continue
        if not (c.get("commercial_use") is True or str(c.get("commercial_use")).upper() == "YES"):
            out.append(f"COMPONENT_{name.upper()}_COMMERCIAL_USE_NOT_CONFIRMED")
        if c.get("model_licence_confirmed") is not True:
            out.append(f"COMPONENT_{name.upper()}_MODEL_LICENCE_NOT_CONFIRMED")
        if not c.get("licence_verified_on"):
            out.append(f"COMPONENT_{name.upper()}_LICENCE_NOT_VERIFIED")
    return out


def engine_gate_problems(
    cfg: Mapping[str, Any], *, today: date, register: Mapping[str, Any] | None = None
) -> list[str]:
    """Every reason the gate stays shut. Empty only when the configuration is enabled and complete. A disabled configuration is not a problem:
    it is the default, and `engine_gate` answers None for it."""
    if cfg.get("enabled") is not True:
        return []
    out: list[str] = []
    engine = cfg.get("engine")
    engine = engine if isinstance(engine, Mapping) else {}
    mode = cfg.get("mode")
    if mode not in ENGINE_MODES:
        return [
            "ENGINE_MODE_MISSING"
        ]  # the owner who switches an engine on must also say how far it may go
    if mode == "DISABLED":
        return []
    if (
        mode == "LAB_ONLY"
    ):  # a lab engine has no evaluation yet by definition: it only has to be identifiable
        if not isinstance(engine.get("id"), str) or not _ID.match(engine["id"]):
            out.append("ENGINE_ID_MISSING")
        if not isinstance(engine.get("version"), str) or not engine["version"].strip():
            out.append("ENGINE_VERSION_MISSING")
        return out
    out.extend(register_problems(engine.get("components"), register))
    scope = cfg.get("scope")
    countries = scope.get("countries") if isinstance(scope, Mapping) else None
    if (
        not isinstance(countries, list)
        or not countries
        or not all(isinstance(c, str) and _COUNTRY.match(c) for c in countries)
    ):
        out.append("ENGINE_SCOPE_MISSING")
    thresholds = cfg.get("thresholds")
    thresholds = thresholds if isinstance(thresholds, Mapping) else {}
    if not isinstance(engine.get("id"), str) or not _ID.match(engine["id"]):
        out.append("ENGINE_ID_MISSING")
    if not isinstance(engine.get("version"), str) or not engine["version"].strip():
        out.append("ENGINE_VERSION_MISSING")
    for key, code in (
        ("evaluation_id", "EVALUATION_ID_MISSING"),
        ("dpia_ref", "DPIA_REFERENCE_MISSING"),
    ):
        value = engine.get(key)
        if not isinstance(value, str) or not _REF.match(value):
            out.append(code)
    if engine.get("licence_checked") is not True:
        out.append("LICENCE_CHECK_NOT_RECORDED")
    if engine.get("pad_standard") != "ISO/IEC 30107-3":
        out.append("PAD_EVALUATION_STANDARD_NOT_RECORDED")
    on = engine.get("evaluated_on")
    when: date | None = None
    if isinstance(on, date):
        when = on
    elif isinstance(on, str):
        try:
            when = date.fromisoformat(on)
        except ValueError:
            when = None
    if when is None:
        out.append("EVALUATION_DATE_INVALID")
    elif when > today:
        out.append("EVALUATION_DATE_IN_THE_FUTURE")
    elif today - when > MAX_EVALUATION_AGE:
        out.append("EVALUATION_TOO_OLD")
    measured = engine.get("measured")
    measured = measured if isinstance(measured, Mapping) else {}
    ceilings = {
        "false_accept": MAX_FALSE_ACCEPT,
        "false_reject": MAX_FALSE_REJECT,
        "attack_accept": MAX_ATTACK_ACCEPT,
        "bona_fide_reject": MAX_BONA_FIDE_REJECT,
    }
    for key in _RATES:
        r = _rate(measured.get(key))
        if r is None:
            out.append(f"MEASURED_{key.upper()}_MISSING")
        elif r > ceilings[key]:
            out.append(f"MEASURED_{key.upper()}_ABOVE_CEILING")
    face, live = _rate(thresholds.get("face_match_min")), _rate(thresholds.get("liveness_min"))
    if face is None or face < MIN_FACE_THRESHOLD:
        out.append("FACE_THRESHOLD_INVALID")
    if live is None or live < MIN_LIVENESS_THRESHOLD:
        out.append("LIVENESS_THRESHOLD_INVALID")
    return out


def engine_gate(
    cfg: Mapping[str, Any], *, today: date, register: Mapping[str, Any] | None = None
) -> EngineGate | None:
    """None while the engine is off (the default). An enabled but incomplete configuration is an error, never a silent off: the owner who
    switched it on must be told it is not accepted."""
    if cfg.get("enabled") is not True:
        return None
    problems = engine_gate_problems(cfg, today=today, register=register)
    if not problems and cfg.get("mode") != "APPROVED_FOR_SCOPE":
        return None  # DISABLED or LAB_ONLY: never routed to a live decision, whatever else the file says (WF01 T02)
    if problems:
        raise EngineGateError(problems)
    e, m, t = cfg["engine"], cfg["engine"]["measured"], cfg["thresholds"]
    on = e["evaluated_on"]
    return EngineGate(
        engine_id=e["id"],
        engine_version=e["version"],
        evaluation_id=e["evaluation_id"],
        evaluated_on=on if isinstance(on, date) else date.fromisoformat(on),
        dpia_ref=e["dpia_ref"],
        false_accept=float(m["false_accept"]),
        false_reject=float(m["false_reject"]),
        attack_accept=float(m["attack_accept"]),
        bona_fide_reject=float(m["bona_fide_reject"]),
        face_threshold=float(t["face_match_min"]),
        liveness_threshold=float(t["liveness_min"]),
        countries=frozenset(cfg["scope"]["countries"]),
    )


def readiness(
    cfg: Mapping[str, Any], register: Mapping[str, Any] | None, *, today: date
) -> dict[str, Any]:
    """What a person needs to see before an engine is allowed: the mode the file is in, the engine's references as written, each component's licence record, and every
    reason the engine could not be approved for live use *today* (computed as if it were switched on for approval, so a disabled engine shows its checklist). Facts from
    versioned configuration: nothing here is tenant data, a media item or a template, and nothing here approves anything."""
    engine = cfg.get("engine")
    engine = engine if isinstance(engine, Mapping) else {}
    scope = cfg.get("scope")
    scope = scope if isinstance(scope, Mapping) else {}
    thresholds = cfg.get("thresholds")
    thresholds = thresholds if isinstance(thresholds, Mapping) else {}
    measured = engine.get("measured")
    measured = measured if isinstance(measured, Mapping) else {}
    used = engine.get("components")
    used = [c for c in used if isinstance(c, str)] if isinstance(used, list) else []
    listed = [c for c in ((register or {}).get("components") or ()) if isinstance(c, Mapping)]
    needs = engine_gate_problems(
        {**cfg, "enabled": True, "mode": "APPROVED_FOR_SCOPE"}, today=today, register=register
    )
    return {
        "mode": engine_mode(cfg),
        "enabled": cfg.get("enabled") is True,
        "approvable": not needs,
        "problems_for_approval": needs,
        "countries": sorted(c for c in (scope.get("countries") or ()) if isinstance(c, str)),
        "engine": {
            "id": engine.get("id") or None,
            "version": engine.get("version") or None,
            "evaluation_id": engine.get("evaluation_id") or None,
            "evaluated_on": str(engine.get("evaluated_on")) if engine.get("evaluated_on") else None,
            "pad_standard": engine.get("pad_standard") or None,
            "licence_checked": engine.get("licence_checked") is True,
            "dpia_ref": engine.get("dpia_ref") or None,
            "components": used,
            "measured": {k: _rate(measured.get(k)) for k in _RATES},
        },
        "thresholds": {
            "face_match_min": _rate(thresholds.get("face_match_min")),
            "liveness_min": _rate(thresholds.get("liveness_min")),
        },
        "ceilings": {
            "false_accept": MAX_FALSE_ACCEPT,
            "false_reject": MAX_FALSE_REJECT,
            "attack_accept": MAX_ATTACK_ACCEPT,
            "bona_fide_reject": MAX_BONA_FIDE_REJECT,
        },
        "register": {
            "version": (register or {}).get("version"),
            "status": (register or {}).get("status"),
            "components": [
                {
                    "id": str(c.get("id")),
                    "role": c.get("role"),
                    "code_licence": c.get("code_licence"),
                    "commercial_use": str(c.get("commercial_use")),
                    "model_licence_confirmed": c.get("model_licence_confirmed") is True,
                    "licence_verified_on": str(c.get("licence_verified_on"))
                    if c.get("licence_verified_on")
                    else None,
                    "status": c.get("status"),
                    "in_engine": str(c.get("id")) in used,
                }
                for c in listed
            ],
        },
    }


def engine_mode(cfg: Mapping[str, Any]) -> str:
    """What the deployment's file says an engine may do: DISABLED (the default), LAB_ONLY or APPROVED_FOR_SCOPE. A file that switches an engine on
    without a mode reports DISABLED here and is refused by `engine_gate`."""
    if cfg.get("enabled") is not True:
        return "DISABLED"
    mode = cfg.get("mode")
    return str(mode) if mode in ENGINE_MODES else "DISABLED"


class EngineGateError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "the biometric engine is switched on but not accepted: " + ", ".join(problems)
        )
        self.problems = tuple(problems)


@dataclass(frozen=True, slots=True)
class FakeEngine:
    """A deterministic engine for tests only. It looks at nothing: it returns the scores it was built with."""

    match_score: float = 0.0
    liveness_score: float = 0.0
    engine_id: str = "fake-engine"
    engine_version: str = "0"

    def match(self, probe: bytes, reference: bytes) -> FaceMatch:
        return FaceMatch(self.match_score, self.engine_id, self.engine_version)

    def liveness(self, media: bytes) -> Liveness:
        return Liveness(self.liveness_score, self.engine_id, self.engine_version)

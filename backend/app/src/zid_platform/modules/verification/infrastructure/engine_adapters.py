"""Adapters of open-source face match and liveness engines to the `BiometricEngine` port (CR-102). Nothing here switches an engine on.

An adapter only translates: the media goes in as bytes for the length of one call and a score in [0, 1] comes out. The libraries are imported when a
call is made, never at import time, so the platform starts and the tests run without them. If a library or a model is missing, or anything goes
wrong inside it, the adapter RAISES and the caller treats that as PENDING: it never invents a score and never returns a pass.

Every adapter is still behind `biometric_engine.engine_gate`: with no independent evaluation, no data protection impact assessment and no licence
check written in `config/self-run-verification/self-run.yaml`, none of this runs. Licence notes, which are not legal advice:
- DeepFace (MIT) downloads model weights whose licences differ per model; somebody must read each one used.
- Silent-Face-Anti-Spoofing (Apache-2.0) is research code: it carries no ISO/IEC 30107-3 result of its own.
- InsightFace's code is open but its pretrained models are non-commercial: no adapter for them is shipped here on purpose.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from zid_platform.modules.verification.domain.biometric_engine import FaceMatch, Liveness


class EngineUnavailable(RuntimeError):
    """The engine cannot decide (library missing, model missing, unreadable media). Never a score."""


def _score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EngineUnavailable("ENGINE_SCORE_NOT_A_NUMBER")
    v = float(value)
    if not 0.0 <= v <= 1.0:  # a NaN fails this comparison too
        raise EngineUnavailable("ENGINE_SCORE_OUT_OF_RANGE")
    return v


def _load(module: str) -> Any:
    try:
        return importlib.import_module(module)
    except Exception as e:
        raise EngineUnavailable("ENGINE_LIBRARY_UNAVAILABLE") from e


class DeepFaceEngine:
    """Face match through DeepFace's `verify`, mapped to a similarity in [0, 1] from the distance and the model's own threshold. Liveness is not
    offered by DeepFace, so this adapter's `liveness` always raises: pair it with a liveness engine in `ComposedEngine`."""

    def __init__(
        self,
        engine_id: str,
        engine_version: str,
        *,
        model: str = "ArcFace",
        loader: Callable[[str], Any] = _load,
    ) -> None:
        self.engine_id, self.engine_version, self._model, self._load = (
            engine_id,
            engine_version,
            model,
            loader,
        )

    def _get(self, module: str) -> Any:
        try:
            return self._load(module)
        except EngineUnavailable:
            raise
        except Exception as e:
            raise EngineUnavailable("ENGINE_LIBRARY_UNAVAILABLE") from e

    def match(self, probe: bytes, reference: bytes) -> FaceMatch:
        deepface, np, cv2 = (self._get(m) for m in ("deepface.DeepFace", "numpy", "cv2"))
        try:
            a = cv2.imdecode(np.frombuffer(probe, np.uint8), cv2.IMREAD_COLOR)
            b = cv2.imdecode(np.frombuffer(reference, np.uint8), cv2.IMREAD_COLOR)
            if a is None or b is None:
                raise EngineUnavailable("ENGINE_MEDIA_UNREADABLE")
            out = deepface.verify(a, b, model_name=self._model, enforce_detection=True)
            distance, threshold = float(out["distance"]), float(out["threshold"])
        except EngineUnavailable:
            raise
        except Exception as e:
            raise EngineUnavailable("ENGINE_FAILED") from e
        if threshold <= 0:
            raise EngineUnavailable("ENGINE_THRESHOLD_INVALID")
        # a distance at the model's own threshold maps to 0.5, zero distance to 1.0, twice the threshold or more to 0.0
        return FaceMatch(
            _score(max(0.0, min(1.0, 1.0 - distance / (2 * threshold)))),
            self.engine_id,
            self.engine_version,
        )

    def liveness(self, media: bytes) -> Liveness:
        raise EngineUnavailable("ENGINE_HAS_NO_LIVENESS")


class SilentFaceEngine:
    """Liveness through a callable that wraps Silent-Face-Anti-Spoofing's predictor and returns the probability that the face is real. The wrapper is
    supplied by the deployment (the project ships scripts, not a package), so this class only checks and translates what it returns."""

    def __init__(
        self, engine_id: str, engine_version: str, predictor: Callable[[bytes], float] | None
    ) -> None:
        self.engine_id, self.engine_version, self._predict = engine_id, engine_version, predictor

    def match(self, probe: bytes, reference: bytes) -> FaceMatch:
        raise EngineUnavailable("ENGINE_HAS_NO_FACE_MATCH")

    def liveness(self, media: bytes) -> Liveness:
        if self._predict is None:
            raise EngineUnavailable("ENGINE_LIBRARY_UNAVAILABLE")
        try:
            return Liveness(_score(self._predict(media)), self.engine_id, self.engine_version)
        except EngineUnavailable:
            raise
        except Exception as e:
            raise EngineUnavailable("ENGINE_FAILED") from e


class ComposedEngine:
    """One engine id and version made of a face matcher and a liveness engine. The id and version are the pair the evaluation covered: both results
    carry this identity, so a result from any other combination is not trusted by the gate."""

    def __init__(self, engine_id: str, engine_version: str, matcher: Any, liveness: Any) -> None:
        self.engine_id, self.engine_version = engine_id, engine_version
        self._matcher, self._liveness = matcher, liveness

    def match(self, probe: bytes, reference: bytes) -> FaceMatch:
        got = self._matcher.match(probe, reference)
        return FaceMatch(_score(got.score), self.engine_id, self.engine_version)

    def liveness(self, media: bytes) -> Liveness:
        got = self._liveness.liveness(media)
        return Liveness(_score(got.score), self.engine_id, self.engine_version)

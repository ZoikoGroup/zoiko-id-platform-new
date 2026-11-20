"""The face-match sidecar, for the lab (docs/signoff/face-engine-owner-pack.md; CR-128). The address is operator configuration (`ZID_FACE_URL`), never anything a caller can influence.

Two things use it:
- `compare`, the Face lab in the Operator Console: two photographs go in, a measurement comes out. Nothing is stored and nothing is decided.
- `SidecarFaceEngine`, an adapter to the `BiometricEngine` port, so the engine can be put behind the gate like any other. It is still refused by `engine_gate` until every record in
  `self-run.yaml` exists, and the sidecar has no liveness test, so `liveness` always raises: a face match alone is never an approval.
"""

from __future__ import annotations

import base64
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from zid_platform.modules.verification.domain.biometric_engine import FaceMatch, Liveness
from zid_platform.modules.verification.infrastructure.engine_adapters import EngineUnavailable

TIMEOUT = httpx.Timeout(60.0, connect=5.0)
ENGINE_ID = "opencv-yunet-sface"
ENGINE_VERSION = "2023mar+2021dec"


class FaceLabError(RuntimeError):
    def __init__(self, code: str, http_status: int = 503) -> None:
        super().__init__(code)
        self.code, self.http_status = code, http_status


def configured_url() -> str | None:
    url = (os.environ.get("ZID_FACE_URL") or "").strip().rstrip("/")
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.hostname else None


async def compare(
    probe: bytes, reference: bytes, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """The sidecar's measurement of two photographs. Raises FaceLabError with the sidecar's own reason (no face, more than one face, unreadable image) or when it is unavailable."""
    url = configured_url()
    if url is None:
        raise FaceLabError("FACE_ENGINE_NOT_CONFIGURED")
    body = {
        "probe_base64": base64.b64encode(probe).decode(),
        "reference_base64": base64.b64encode(reference).decode(),
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
            r = await client.post(f"{url}/match", json=body)
    except httpx.HTTPError as e:
        raise FaceLabError("FACE_ENGINE_UNAVAILABLE") from e
    try:
        data = r.json()
    except ValueError as e:
        raise FaceLabError("FACE_ENGINE_UNAVAILABLE") from e
    if not isinstance(data, dict):
        raise FaceLabError("FACE_ENGINE_UNAVAILABLE")
    if r.status_code == 422:
        raise FaceLabError(str(data.get("error") or "IMAGE_REFUSED"), 422)
    if r.status_code != 200:
        raise FaceLabError("FACE_ENGINE_UNAVAILABLE")
    return data


class SidecarFaceEngine:
    """The sidecar as a `BiometricEngine`. Face match only: liveness is not installed, so it raises and the caller treats the check as PENDING, never as a pass."""

    engine_id = ENGINE_ID
    engine_version = ENGINE_VERSION

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def match(self, probe: bytes, reference: bytes) -> FaceMatch:
        url = configured_url()
        if url is None:
            raise EngineUnavailable("ENGINE_NOT_CONFIGURED")
        body = {
            "probe_base64": base64.b64encode(probe).decode(),
            "reference_base64": base64.b64encode(reference).decode(),
        }
        try:
            with httpx.Client(timeout=TIMEOUT, transport=self._transport) as client:
                r = client.post(f"{url}/match", json=body)
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise EngineUnavailable("ENGINE_FAILED") from e
        if r.status_code != 200 or not isinstance(data, dict):
            raise EngineUnavailable(str(data.get("error")) if isinstance(data, dict) and data.get("error") else "ENGINE_FAILED")
        score = data.get("score")
        if isinstance(score, bool) or not isinstance(score, int | float) or not 0.0 <= float(score) <= 1.0:
            raise EngineUnavailable("ENGINE_SCORE_OUT_OF_RANGE")
        return FaceMatch(float(score), self.engine_id, self.engine_version)

    def liveness(self, media: bytes) -> Liveness:
        raise EngineUnavailable("ENGINE_HAS_NO_LIVENESS")


def gate_status() -> dict[str, Any]:
    """Where the engine gate stands (read only): the mode, whether it could be approved today, and every record still missing."""
    from datetime import UTC, datetime

    from zid_platform.modules.verification.domain.biometric_engine import readiness
    from zid_platform.modules.verification.infrastructure import engine_register, self_run_config

    r = readiness(self_run_config.raw(), engine_register.register(), today=datetime.now(UTC).date())
    return {"mode": r["mode"], "approvable": bool(r["approvable"]), "problems": list(r["problems_for_approval"])}

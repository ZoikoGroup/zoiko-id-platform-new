"""A small face-match service for the lab (CR-102; docs/signoff/face-engine-owner-pack.md). Runs in its own container; the backend never links an imaging library.

POST /match    {"probe_base64": "...", "reference_base64": "..."}
               -> 200 {"cosine": 0.71, "score": 0.71, "probe": {"faces": 1, "detection": 0.93, "face_ratio": 0.18}, "reference": {...},
                       "engine": "opencv-yunet-2023mar+sface-2021dec", "recommended_cosine_threshold": 0.363}
               -> 422 {"error": "NO_FACE_PROBE" | "NO_FACE_REFERENCE" | "MULTIPLE_FACES_PROBE" | "MULTIPLE_FACES_REFERENCE" | "IMAGE_UNREADABLE" | ...}
POST /liveness {"image_base64": "..."}  -> 503 {"error": "LIVENESS_ENGINE_NOT_INSTALLED"}   (there is no open-source liveness model in this lab image: a face match alone
               can be shown another person's photograph, which is why the platform refuses to approve anyone on a match without a liveness result)
GET  /healthz  -> {"ok": true, "engine": "..."}

`score` is the cosine similarity clipped to [0, 1]; `cosine` is the raw value in [-1, 1]. SFace's authors suggest 0.363 as a starting cosine threshold; the real threshold is
chosen from an independent evaluation, never from this file. Nothing is stored: each image is decoded in memory, used for one call and dropped.
"""

from __future__ import annotations

import base64
import binascii
import json
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

MODELS = "/app/models"
DETECTOR = f"{MODELS}/face_detection_yunet_2023mar.onnx"
RECOGNISER = f"{MODELS}/face_recognition_sface_2021dec.onnx"
ENGINE = "opencv-yunet-2023mar+sface-2021dec"
RECOMMENDED_COSINE = 0.363
MAX_BODY = 12 * 1024 * 1024
MAX_PIXELS = 25_000_000
MIN_DETECTION = 0.8  # a detection below this confidence is not counted as a face


def image_size(raw: bytes) -> tuple[int, int] | None:
    """Width and height read from the header of a PNG or JPEG, before anything is decoded (a decompression-bomb guard). None for anything else."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        w, h = struct.unpack(">II", raw[16:24])
        return w, h
    if raw[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(raw):
            if raw[i] != 0xFF:
                i += 1
                continue
            marker = raw[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", raw[i + 2 : i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", raw[i + 5 : i + 9])
                return w, h
            i += 2 + length
    return None


class Faces:
    """Loaded once. The detector is told each image's size before use; the recogniser aligns a detected face and returns its feature vector."""

    def __init__(self) -> None:
        self.detector = cv2.FaceDetectorYN.create(DETECTOR, "", (320, 320), MIN_DETECTION, 0.3, 5000)
        self.recogniser = cv2.FaceRecognizerSF.create(RECOGNISER, "")

    def decode(self, b64: object) -> np.ndarray:
        if not isinstance(b64, str):
            raise ValueError("IMAGE_UNREADABLE")
        try:
            raw = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("IMAGE_UNREADABLE") from None
        size = image_size(raw)
        if size is None:
            raise ValueError("IMAGE_TYPE_NOT_ALLOWED")  # only JPEG and PNG
        if size[0] * size[1] > MAX_PIXELS:
            raise ValueError("IMAGE_TOO_LARGE")
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("IMAGE_UNREADABLE")
        return img

    def faces(self, img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        self.detector.setInputSize((w, h))
        _, found = self.detector.detect(img)
        return found if found is not None else np.empty((0, 15), dtype=np.float32)

    def feature(self, img: np.ndarray, face: np.ndarray) -> np.ndarray:
        return self.recogniser.feature(self.recogniser.alignCrop(img, face))


def describe(img: np.ndarray, found: np.ndarray) -> dict:
    h, w = img.shape[:2]
    best = found[int(np.argmax(found[:, -1]))] if len(found) else None
    return {
        "faces": int(len(found)),
        "detection": round(float(best[-1]), 3) if best is not None else None,
        "face_ratio": round(float(best[2] * best[3]) / float(w * h), 4) if best is not None else None,  # the face's share of the picture
    }


def match(models: Faces, body: dict) -> tuple[int, dict]:
    try:
        probe, reference = models.decode(body.get("probe_base64")), models.decode(body.get("reference_base64"))
    except ValueError as e:
        return 422, {"error": str(e)}
    pf, rf = models.faces(probe), models.faces(reference)
    if len(pf) == 0:
        return 422, {"error": "NO_FACE_PROBE", "probe": describe(probe, pf)}
    if len(rf) == 0:
        return 422, {"error": "NO_FACE_REFERENCE", "reference": describe(reference, rf)}
    if len(pf) > 1:
        return 422, {"error": "MULTIPLE_FACES_PROBE", "probe": describe(probe, pf)}  # one person at a time: never guess which face is meant
    if len(rf) > 1:
        return 422, {"error": "MULTIPLE_FACES_REFERENCE", "reference": describe(reference, rf)}
    cosine = float(models.recogniser.match(models.feature(probe, pf[0]), models.feature(reference, rf[0]), cv2.FaceRecognizerSF_FR_COSINE))
    return 200, {
        "cosine": round(cosine, 4),
        "score": round(max(0.0, min(1.0, cosine)), 4),
        "probe": describe(probe, pf),
        "reference": describe(reference, rf),
        "engine": ENGINE,
        "recommended_cosine_threshold": RECOMMENDED_COSINE,
    }


class Handler(BaseHTTPRequestHandler):
    models: Faces

    def log_message(self, *_: object) -> None:  # nothing about a request is logged: it may carry a face
        return

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send(200, {"ok": True, "engine": ENGINE})
        else:
            self._send(404, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:
        if self.path not in ("/match", "/liveness"):
            self._send(404, {"error": "NOT_FOUND"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            self._send(413, {"error": "BODY_SIZE"})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            self._send(422, {"error": "BODY_NOT_JSON"})
            return
        if not isinstance(body, dict):
            self._send(422, {"error": "BODY_NOT_JSON"})
            return
        if self.path == "/liveness":
            self._send(503, {"error": "LIVENESS_ENGINE_NOT_INSTALLED"})
            return
        try:
            code, payload = match(self.models, body)
        except Exception:  # noqa: BLE001 - nothing inside the model is allowed to become a score
            code, payload = 500, {"error": "ENGINE_FAILED"}
        self._send(code, payload)


def main() -> None:
    Handler.models = Faces()
    ThreadingHTTPServer(("0.0.0.0", 8091), Handler).serve_forever()


if __name__ == "__main__":
    main()

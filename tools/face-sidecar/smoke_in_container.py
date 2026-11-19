"""Smoke test for the face sidecar, run inside its container:  docker exec -i <face container> python - < tools/face-sidecar/smoke_in_container.py

It fetches OpenCV's own sample portrait (a public test image), so it needs network access from the container and ships no photograph of anyone. It proves the plumbing:
the same face matches itself, a mirrored copy still matches, noise has no face, junk is refused. It proves nothing about accuracy: that is the independent evaluation's job.
"""

import base64
import json
import urllib.error
import urllib.request

import cv2
import numpy as np

URL = "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/lena.jpg"
SERVICE = "http://127.0.0.1:8091"
failed = 0


def call(path: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(SERVICE + path, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def check(name: str, ok: bool, detail: object = "") -> None:
    global failed
    print(("PASS " if ok else "FAIL ") + name, detail if not ok else "")
    failed += 0 if ok else 1


def b64(img: np.ndarray) -> str:
    return base64.b64encode(cv2.imencode(".jpg", img)[1].tobytes()).decode()


face_bytes = urllib.request.urlopen(URL, timeout=60).read()
face = cv2.imdecode(np.frombuffer(face_bytes, np.uint8), cv2.IMREAD_COLOR)
rng = np.random.default_rng(1)
noise = rng.integers(0, 255, face.shape, dtype=np.uint8)

check("health answers", call("/healthz")[0] == 200)
code, same = call("/match", {"probe_base64": b64(face), "reference_base64": b64(face)})
check("the same face matches itself (cosine above 0.9)", code == 200 and same["cosine"] > 0.9, (code, same))
code, mirrored = call("/match", {"probe_base64": b64(face), "reference_base64": b64(cv2.flip(face, 1))})
check("a mirrored copy still matches (cosine above the suggested 0.363)", code == 200 and mirrored["cosine"] > 0.363, (code, mirrored))
code, nothing = call("/match", {"probe_base64": b64(noise), "reference_base64": b64(face)})
check("noise has no face: NO_FACE_PROBE, never a score", code == 422 and nothing["error"] == "NO_FACE_PROBE" and "score" not in nothing, (code, nothing))
code, nothing = call("/match", {"probe_base64": b64(face), "reference_base64": b64(noise)})
check("noise as the reference: NO_FACE_REFERENCE", code == 422 and nothing["error"] == "NO_FACE_REFERENCE", (code, nothing))
code, junk = call("/match", {"probe_base64": "not base64!", "reference_base64": b64(face)})
check("bad base64 is refused", code == 422 and junk["error"] == "IMAGE_UNREADABLE", (code, junk))
code, gif = call("/match", {"probe_base64": base64.b64encode(b"GIF89a....").decode(), "reference_base64": b64(face)})
check("a GIF is refused", code == 422 and gif["error"] == "IMAGE_TYPE_NOT_ALLOWED", (code, gif))
code, live = call("/liveness", {"image_base64": b64(face)})
check("liveness says it is not installed, never a score", code == 503 and live["error"] == "LIVENESS_ENGINE_NOT_INSTALLED", (code, live))
print("INFO self match:", same.get("cosine"), " mirrored:", mirrored.get("cosine"))
print("\nRESULT:", "ALL PASSED" if failed == 0 else f"{failed} FAILED")

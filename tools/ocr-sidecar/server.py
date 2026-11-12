"""A small OCR service for the document extraction route (CR-073 P2-4). Runs in its own container; the backend never links an imaging library.

POST /ocr  {"image_base64": "...", "regions": [{"name": "surname", "x": 0.1, "y": 0.2, "w": 0.4, "h": 0.1}], "lang": "eng"}
  -> {"regions": [{"name": "surname", "text": "ERIKSSON", "confidence": 91.2}], "engine": "tesseract <version>"}
GET  /healthz -> {"ok": true, "engine": "tesseract <version>"}

Region coordinates are fractions of the image (0 to 1), so a template does not depend on the scan's resolution. Reading is bounded to the regions
the caller names; with no regions the whole page is read as one region called "page".

What it is, and is not: a bounded text reader. It says what the characters in a region look like, with the engine's own confidence. It never
decides what a document is, and nothing it returns is evidence of authenticity.

Hardening: only PNG and JPEG (checked by their first bytes), a body size cap, a pixel cap, a region cap, an allowlist of languages, a per-region
time limit, no network use, and no file written outside the temporary directory the container provides.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

MAX_BODY = 8 * 1024 * 1024
MAX_PIXELS = 25_000_000
MAX_REGIONS = 50
MAX_TEXT = 2000
TIMEOUT_SECONDS = 20
LANGUAGES = {"eng", "fra", "spa", "deu", "ara"}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow refuses a larger image outright (a decompression-bomb guard)


def engine() -> str:
    try:
        out = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, timeout=10, check=False)
        return "tesseract " + ((out.stdout or out.stderr).splitlines() or ["unknown"])[0].split()[-1]
    except (OSError, subprocess.SubprocessError):
        return "tesseract unavailable"


def read_region(img: Image.Image, box: tuple[int, int, int, int], lang: str) -> tuple[str, float]:
    crop = img.crop(box).convert("L")
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    run = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", lang, "--psm", "6", "tsv"],
        input=buf.getvalue(),
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    words, confs, last_line = [], [], None
    for row in run.stdout.decode("utf-8", "replace").splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) < 12 or not parts[11].strip():
            continue
        line = tuple(parts[1:5])
        if last_line is not None and line != last_line:
            words.append("\n")
        last_line = line
        words.append(parts[11] + " ")
        try:
            confs.append(float(parts[10]))
        except ValueError:
            pass
    text = "".join(words).replace(" \n", "\n").strip()[:MAX_TEXT]
    return text, round(sum(confs) / len(confs), 1) if confs else 0.0


def handle(body: dict) -> tuple[int, dict]:
    lang = body.get("lang", "eng")
    if lang not in LANGUAGES:
        return 422, {"error": "LANGUAGE_NOT_SUPPORTED"}
    try:
        raw = base64.b64decode(str(body.get("image_base64", "")), validate=True)
    except (binascii.Error, ValueError):
        return 422, {"error": "IMAGE_NOT_BASE64"}
    if not (raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff")):
        return 422, {"error": "IMAGE_TYPE_NOT_ALLOWED"}
    regions = body.get("regions") or [{"name": "page", "x": 0, "y": 0, "w": 1, "h": 1}]
    if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
        return 422, {"error": "REGIONS_INVALID"}
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return 422, {"error": "IMAGE_NOT_READABLE"}
    width, height = img.size
    out = []
    for r in regions:
        try:
            name = str(r["name"])[:60]
            x, y, w, h = (float(r[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            return 422, {"error": "REGION_INVALID"}
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.0001 and y + h <= 1.0001):
            return 422, {"error": "REGION_OUT_OF_BOUNDS", "region": name}
        box = (int(x * width), int(y * height), max(int(x * width) + 1, int((x + w) * width)), max(int(y * height) + 1, int((y + h) * height)))
        try:
            text, conf = read_region(img, box, lang)
        except subprocess.TimeoutExpired:
            return 504, {"error": "OCR_TIMEOUT", "region": name}
        out.append({"name": name, "text": text, "confidence": conf})
    return 200, {"regions": out, "engine": engine()}


class Handler(BaseHTTPRequestHandler):
    server_version = "ocr-sidecar"

    def log_message(self, fmt: str, *args: object) -> None:  # never log request content
        return

    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send(200, {"ok": True, "engine": engine()})
        else:
            self._send(404, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/ocr":
            return self._send(404, {"error": "NOT_FOUND"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._send(400, {"error": "LENGTH_INVALID"})
        if not 0 < length <= MAX_BODY:
            return self._send(413, {"error": "BODY_TOO_LARGE"})
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"error": "BODY_NOT_JSON"})
        if not isinstance(body, dict):
            return self._send(400, {"error": "BODY_NOT_AN_OBJECT"})
        code, payload = handle(body)
        self._send(code, payload)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()  # noqa: S104 - the container's own network only

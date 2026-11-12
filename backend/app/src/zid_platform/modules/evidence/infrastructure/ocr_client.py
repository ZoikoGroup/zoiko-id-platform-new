"""The client of the OCR sidecar, and the loader of the document templates (CR-073 P2-4).

The sidecar (tools/ocr-sidecar) is a separate container, so this process links no imaging library. Its address is operator configuration
(`ZID_OCR_URL`), never anything a caller can influence. With no address set the extraction route answers 503 for an image: nothing is read,
nothing is guessed. The client sends the image and the template's regions and returns what was read, with the engine's own confidence.
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from zid_platform.modules.evidence.domain.templates import Template, parse

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
TIMEOUT = httpx.Timeout(45.0, connect=5.0)


class OcrError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def configured_url() -> str | None:
    """The sidecar's address, or None. Only an http(s) address with a host is accepted."""
    url = (os.environ.get("ZID_OCR_URL") or "").strip().rstrip("/")
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.hostname else None


async def read(
    base_url: str,
    image: bytes,
    regions: list[dict[str, Any]],
    lang: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """(what was read per region, the engine's name). The sidecar's refusals are named; an unreachable or failing sidecar is OCR_UNAVAILABLE."""
    body = {"image_base64": base64.b64encode(image).decode(), "regions": regions, "lang": lang}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
            r = await client.post(f"{base_url}/ocr", json=body)
    except httpx.HTTPError as e:
        raise OcrError("OCR_UNAVAILABLE", type(e).__name__) from e
    if r.status_code == 200:
        try:
            data = r.json()
            return list(data["regions"]), str(data.get("engine", "unknown"))
        except (ValueError, KeyError, TypeError) as e:
            raise OcrError("OCR_UNAVAILABLE", "bad answer") from e
    if r.status_code == 504:
        raise OcrError("OCR_TIMEOUT")
    if r.status_code in {413, 422}:
        try:
            reason = str(r.json().get("error", "rejected"))
        except ValueError:
            reason = "rejected"
        raise OcrError("OCR_INPUT_REFUSED", reason)
    raise OcrError("OCR_UNAVAILABLE", f"status {r.status_code}")


@lru_cache(maxsize=1)
def templates() -> dict[str, Template]:
    doc: dict[str, Any] = (
        yaml.safe_load(
            (CONFIG / "document-templates" / "templates.yaml").read_text(encoding="utf-8")
        )
        or {}
    )
    return parse(doc)


__all__ = ["CONFIG", "OcrError", "configured_url", "read", "templates"]

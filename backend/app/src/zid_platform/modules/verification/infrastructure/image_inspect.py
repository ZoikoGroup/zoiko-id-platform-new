"""The client of the imaging sidecar for proofing cases (WF03; CR-110): measurements of a document photograph and a read of its machine-readable lines.

The sidecar is a separate container, so this process links no imaging library. Its address is operator configuration (`ZID_OCR_URL`), never anything a caller can influence; with none set every call raises
`ImagingUnavailable` and the case routes answer 503: nothing is measured, nothing is guessed, and no check result is invented."""

from __future__ import annotations

import base64
import binascii
import os
from typing import Any
from urllib.parse import urlparse

import httpx

TIMEOUT = httpx.Timeout(45.0, connect=5.0)
MRZ_REGION = {
    "name": "mrz",
    "x": 0.0,
    "y": 0.62,
    "w": 1.0,
    "h": 0.38,
}  # the lower part of a passport data page or the back of a card; a different layout needs its own template


class ImagingUnavailable(RuntimeError):
    def __init__(self, code: str = "IMAGING_UNAVAILABLE") -> None:
        super().__init__(code)
        self.code = code


async def sanitize(image: bytes, *, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    """The photograph re-encoded as a plain JPEG with its metadata removed, by the sidecar (the backend links no imaging library). Raises `ImagingUnavailable` when it cannot: an image is never kept or shown as it arrived."""
    answer = await _post("/sanitize", {"image_base64": base64.b64encode(image).decode()}, transport)
    try:
        clean = base64.b64decode(str(answer["image_base64"]), validate=True)
    except (KeyError, ValueError, binascii.Error) as e:
        raise ImagingUnavailable("IMAGING_ANSWER_UNREADABLE") from e
    if not clean.startswith(b"\xff\xd8\xff"):
        raise ImagingUnavailable("IMAGING_ANSWER_UNREADABLE")
    return clean


def configured_url() -> str | None:
    url = (os.environ.get("ZID_OCR_URL") or "").strip().rstrip("/")
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.hostname else None


async def _post(
    path: str, body: dict[str, Any], transport: httpx.AsyncBaseTransport | None
) -> dict[str, Any]:
    url = configured_url()
    if url is None:
        raise ImagingUnavailable("IMAGING_NOT_CONFIGURED")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
            r = await client.post(f"{url}{path}", json=body)
    except httpx.HTTPError as e:
        raise ImagingUnavailable() from e
    if r.status_code in (413, 422):
        raise ImagingUnavailable("IMAGE_REFUSED")
    if r.status_code != 200:
        raise ImagingUnavailable()
    try:
        data = r.json()
    except ValueError as e:
        raise ImagingUnavailable() from e
    if not isinstance(data, dict):
        raise ImagingUnavailable()
    return data


async def inspect(
    image: bytes, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    return await _post("/inspect", {"image_base64": base64.b64encode(image).decode()}, transport)


async def read_mrz_text(image: bytes, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    data = await _post(
        "/ocr",
        {"image_base64": base64.b64encode(image).decode(), "regions": [MRZ_REGION], "lang": "eng"},
        transport,
    )
    regions = data.get("regions")
    if not isinstance(regions, list) or not regions or not isinstance(regions[0], dict):
        raise ImagingUnavailable()
    return str(regions[0].get("text", ""))


async def read_page_text(image: bytes, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """All the text the sidecar can read from the whole picture, in memory for one comparison and never kept."""
    data = await _post("/ocr", {"image_base64": base64.b64encode(image).decode(), "lang": "eng"}, transport)
    regions = data.get("regions")
    if not isinstance(regions, list) or not regions or not isinstance(regions[0], dict):
        raise ImagingUnavailable()
    return str(regions[0].get("text", ""))

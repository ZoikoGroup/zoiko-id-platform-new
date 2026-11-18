"""Reading a document photograph for a proofing case (ZID-DTV-WF-03 E07; CR-110): image checks and the printed machine-readable lines, both produced by the platform.

The checks are the measurable ones (see `idv_image`): quality and a re-compression screen. No template or security-feature result is produced, so a market that requires one is told "not run".
The printed data is whatever the platform itself read from the lines on the photograph and parsed with its own check digits; an unreadable or implausible block gives no printed data, never a guess."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from zid_platform.modules.evidence.ports import MrzError, parse_mrz
from zid_platform.modules.verification.domain import idv_image as img
from zid_platform.modules.verification.infrastructure import image_inspect as client


@dataclass(frozen=True, slots=True)
class DocumentReading:
    checks: dict[str, str]
    printed: dict[str, str] | None
    reasons: tuple[str, ...]


def printed_fields(
    lines: list[str], today: datetime | None = None
) -> tuple[dict[str, str] | None, bool | None]:
    """(the printed holder data in the chip's field names, whether every check digit held), or (None, None) when the lines do not parse."""
    if not lines:
        return None, None
    try:
        r = parse_mrz(lines, today=(today or datetime.now(UTC)).date())
    except (MrzError, ValueError):
        return None, None
    fields = {
        "surname": r.surname,
        "given_names": r.given_names,
        "document_number": r.document_number.value,
        "birth_date": r.birth_date_iso or "",
        "expiry_date": r.expiry_date_iso or "",
    }
    return {k: v for k, v in fields.items() if v}, r.all_checks_hold


async def read_document(
    image: bytes,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    thresholds: img.Thresholds = img.DEFAULTS,
) -> DocumentReading:
    """Raises `ImagingUnavailable` when the sidecar is not configured or cannot answer: no result is ever invented."""
    raw: dict[str, Any] = await client.inspect(image, transport=transport)
    metrics = img.parse_metrics(raw)
    if metrics is None:
        raise client.ImagingUnavailable("IMAGING_ANSWER_UNREADABLE")
    results = img.checks(metrics, thresholds)
    reasons: list[str] = []
    printed: dict[str, str] | None = None
    try:
        text = await client.read_mrz_text(image, transport=transport)
    except client.ImagingUnavailable:
        text = ""
        reasons.append("MRZ_NOT_READ")
    fields, digits_ok = printed_fields(img.mrz_lines(text))
    if fields is None:
        if "MRZ_NOT_READ" not in reasons:
            results["MRZ_READABLE"] = "INCONCLUSIVE"
            reasons.append("MRZ_NOT_FOUND_IN_IMAGE")
    else:
        printed = fields
        results["MRZ_READABLE"] = "PASS"
        results["MRZ_CHECK_DIGITS"] = "PASS" if digits_ok else "FAIL"
    return DocumentReading(results, printed, tuple(reasons))

"""The checks every photograph goes through before the engine looks at it, shared by the signed-in identity page and a partner's customer (CR-164).

One place, so the two paths cannot drift apart: a file is scanned for malware, re-encoded by the imaging sidecar to a plain image with all its metadata removed, the printed lines of a document are read by the
platform itself, and a live photograph is compared with the photograph on the document, in memory, for this one call. Neither image is kept. A face that cannot be found is a retake, never a failure.

`CaptureError` carries an error code, a plain message, an HTTP status and whether the same request is worth repeating. Callers turn it into their own error type.
"""

from __future__ import annotations

import logging
from typing import Any

from zid_platform.modules.evidence.ports import scan_content
from zid_platform.modules.verification.application.idv_document import (
    DocumentReading,
    read_document,
)
from zid_platform.modules.verification.domain.idv_decision import FaceMatch, face_signal
from zid_platform.modules.verification.infrastructure import face_lab
from zid_platform.modules.verification.infrastructure.image_inspect import (
    ImagingUnavailable,
)
from zid_platform.modules.verification.infrastructure.image_inspect import (
    sanitize as sanitize_image,
)

log = logging.getLogger(__name__)

# The owners' acknowledgement that the face-match route runs no liveness test and has not been formally evaluated. Without it the route is refused.
FACE_ROUTE_ACK = "accept-no-liveness-no-evaluation"


class CaptureError(Exception):
    def __init__(
        self, code: str, detail: str = "", *, status: int = 422, retryable: bool = False
    ) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail, self.status, self.retryable = (
            code,
            detail or code,
            status,
            retryable,
        )


async def scan_and_clean(raw: bytes, *, scanner: Any, transport: Any, what: str = "file") -> bytes:
    """Scan for malware, then re-encode. A deployed cell with no scanner refuses rather than pretend to scan."""
    verdict = await scan_content(scanner, raw)
    if verdict.state == "INFECTED":
        raise CaptureError("IMAGE_REFUSED", "That file could not be accepted.")
    if verdict.state != "CLEAN":
        raise CaptureError(
            "SCAN_UNAVAILABLE",
            "The file could not be checked right now. Try again soon.",
            status=503,
            retryable=True,
        )
    try:
        return await sanitize_image(raw, transport=transport)
    except ImagingUnavailable as e:
        raise CaptureError(
            e.code, f"The {what} could not be inspected right now.", status=503, retryable=True
        ) from e


async def clean_and_read(
    raw: bytes, *, scanner: Any, transport: Any
) -> tuple[bytes, DocumentReading]:
    """A document photograph: scanned, cleaned, and its printed lines read by the platform. The replay digest is the caller's, taken from the bytes as received."""
    clean = await scan_and_clean(raw, scanner=scanner, transport=transport, what="document image")
    try:
        return clean, await read_document(clean, transport=transport)
    except ImagingUnavailable as e:
        raise CaptureError(
            e.code,
            "The document image could not be inspected right now.",
            status=503,
            retryable=True,
        ) from e


async def face_against_document(
    *,
    face_ack: str | None,
    consent: bool,
    selfie: bytes | None,
    document: bytes,
    threshold: float,
    scanner: Any,
    transport: Any,
) -> tuple[FaceMatch, float | None, str | None]:
    """The face-match route's comparison (CR-129): the live photograph against the document's own photograph. Returns the signal, the similarity (for the owner testing it) and the sidecar's reason when no
    comparison could be made. It needs the owner's acknowledgement, the person's agreement and a live photograph."""
    if face_ack != FACE_ROUTE_ACK:
        raise CaptureError(
            "FACE_ROUTE_NOT_ENABLED",
            "Verification with a face match is not enabled in this deployment.",
            status=403,
        )
    if not consent:
        raise CaptureError(
            "FACE_CONSENT_REQUIRED", "Agree to the comparison of your photographs first."
        )
    if selfie is None:
        raise CaptureError(
            "LIVE_PHOTO_REQUIRED", "A live photograph is needed with the document photograph."
        )
    clean_selfie = await scan_and_clean(
        selfie, scanner=scanner, transport=transport, what="live photograph"
    )
    try:
        got = await face_lab.compare(clean_selfie, document)
    except face_lab.FaceLabError as e:
        if e.http_status == 422:
            log.info(
                "idv face comparison: no result (%s)", e.code
            )  # the sidecar's reason only: which photograph, no image
            return (
                face_signal(None, threshold),
                None,
                e.code,
            )  # no face, more than one face, unreadable: take it again
        raise CaptureError(
            "FACE_ENGINE_UNAVAILABLE",
            "The face comparison is not available right now. Try again soon.",
            status=503,
            retryable=True,
        ) from e
    cosine = float(got["cosine"])
    log.info(
        "idv face comparison: similarity %.3f, threshold %.3f", cosine, threshold
    )  # the number only; no photograph, no name
    return face_signal(cosine, threshold), cosine, None

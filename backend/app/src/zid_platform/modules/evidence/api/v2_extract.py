"""POST /v2/documents/extract (ZID-GAP-001 v2.0 DTV-16; CR-073 section B): read the fields of a travel document's MRZ.

Stateless and deterministic: the machine readable zone is parsed and its ICAO 9303 check digits are tested; nothing is stored and no model,
OCR or outside service is involved. The caller supplies the MRZ lines (read from a scan, a chip, or typed).

What the answer is, and is not: extraction. A line whose check digits all hold is internally consistent, which anyone can print, so the
result always says `authenticity: NOT_ASSESSED` and `assurance_ceiling: SELF_DECLARED`. It can feed a claim check as evidence of what a
document SAYS; it can never stand as issuer confirmation. A check digit that fails is reported by field and never repaired.
"""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.application import barcode, cms, epassport, pdfobjects
from zid_platform.modules.evidence.domain import mrz, templates
from zid_platform.modules.evidence.infrastructure import ocr_client
from zid_platform.modules.evidence.infrastructure.trust_config import trust_config
from zid_platform.protected import protected_action

router = APIRouter(tags=["evidence-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
LIMITATIONS = [
    "A machine readable zone whose check digits hold is internally consistent. That does not make the document genuine: anyone can print one.",
    "Nothing here compares the zone with the printed page, the photograph or the chip; those are separate checks.",
    "A failed check digit is reported, never repaired. A better scan or a person resolves it.",
    "Nothing is stored.",
]


MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 6 * 1024 * 1024
PDF_LIMITATIONS = [
    "Form field values are what the PDF says. They are never proof that a person or an issuer said them.",
    "document_signed_and_unchanged is true only when every signature is cryptographically sound, chains to a trust anchor this deployment configured, and nothing was added after signing; the fields are then exactly what was signed.",
    "Only AcroForm fields are read. Text drawn on the page, scanned images and XFA forms are not.",
    "Nothing is stored.",
]


class V2ExtractIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mrz_lines: list[str] | None = Field(default=None, min_length=2, max_length=3)
    barcode_text: str | None = Field(default=None, min_length=8, max_length=barcode.MAX_TEXT)
    pdf_base64: str | None = Field(
        default=None, min_length=8, max_length=(MAX_PDF_BYTES * 4) // 3 + 16
    )
    image_base64: str | None = Field(
        default=None, min_length=8, max_length=(MAX_IMAGE_BYTES * 4) // 3 + 16
    )  # a scan or photo, read through a layout template
    template_id: str | None = Field(default=None, min_length=1, max_length=80)

    def model_post_init(self, _ctx: Any) -> None:
        given = [
            v is not None
            for v in (self.mrz_lines, self.pdf_base64, self.barcode_text, self.image_base64)
        ]
        if sum(given) != 1:
            raise ValueError(
                "send exactly one of mrz_lines, pdf_base64, barcode_text and image_base64"
            )
        if (self.image_base64 is None) != (self.template_id is None):
            raise ValueError("image_base64 and template_id go together")
        if self.mrz_lines and any(len(line) > 60 for line in self.mrz_lines):
            raise ValueError("an MRZ line is at most 44 characters")


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


def _pdf_form(pdf_base64: str, request: Request) -> dict[str, Any]:
    try:
        pdf = base64.b64decode(pdf_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ZidError(
            "VALIDATION_FAILED", "pdf_base64 is not valid base64.", http_status=422
        ) from e
    if len(pdf) > MAX_PDF_BYTES:
        raise ZidError("DOCUMENT_TOO_LARGE", "Document too large.", http_status=413)
    if not pdf.startswith(b"%PDF-"):
        raise ZidError("TYPE_MISMATCH", "That is not a PDF.", http_status=422)
    fields = pdfobjects.form_fields(pdf)
    config = getattr(request.app.state, "trust_config", None) or trust_config()
    findings = cms.inspect_pdf(
        pdf,
        anchors=config.anchors,
        tsa_roots=config.tsa_roots,
        crls=config.crls,
        ocsp_responses=config.ocsp_responses,
    )
    unchanged = bool(findings) and all(
        f.cryptographically_valid and f.covers_whole_file and f.chain is cms.Chain.TRUSTED
        for f in findings
    )
    return {
        **_ids(),
        "form": "PDF_FORM",
        "fields": [
            {"name": f.name, "type": f.field_type, "value": f.value, "source": "PDF_FORM"}
            for f in fields
        ],
        "field_count": len(fields),
        "signatures_found": len(findings),
        "document_signed_and_unchanged": unchanged,
        "authenticity": "NOT_ASSESSED",
        "assurance_ceiling": "DOCUMENT_INTEGRITY" if unchanged else "SELF_DECLARED",
        "limitations": PDF_LIMITATIONS,
    }


BARCODE_LIMITATIONS = [
    "A valid signature says the holder of a key this deployment configured signed these exact bytes. It does not say the person showing the barcode is the person it describes.",
    "It does not say the claims are true, current, or lawful authority for any purpose.",
    "A payload signed by a key nobody configured is INDETERMINATE, never verified. A payload with no signature, or an algorithm other than ES256 and PS256, is never accepted.",
    "Nothing is stored.",
]


def _barcode(text: str, request: Request) -> dict[str, Any]:
    try:
        msg = barcode.decode_text(text)
    except barcode.BarcodeError as e:
        raise ZidError(e.code, e.code.replace("_", " ").capitalize() + ".", http_status=422) from e
    config = getattr(request.app.state, "trust_config", None) or trust_config()
    verdict = barcode.verify(msg, list(config.barcode_keys))
    return {
        **_ids(),
        "form": "SIGNED_BARCODE",
        "signature": {
            "status": verdict.status,
            "reasons": list(verdict.reasons),
            "key_id": verdict.key_id_hex,
            "signer": verdict.signer,
        },
        **barcode.claims_of(msg),
        "authenticity": "NOT_ASSESSED",
        "assurance_ceiling": "DOCUMENT_INTEGRITY"
        if verdict.status == "VERIFIED"
        else "SELF_DECLARED",
        "limitations": BARCODE_LIMITATIONS,
    }


OCR_LIMITATIONS = [
    "A field read by OCR is what the page says, at the reader's own confidence. It is never evidence that the document is genuine.",
    "Matching a layout template proves nothing: anyone can print a card with the same layout. A sample template has not been checked against any real issuer's documents.",
    "A field that does not match its pattern, or was read with low confidence, is reported as such and is never repaired or guessed.",
    "Nothing is stored.",
]


async def _ocr_fields(image_base64: str, template_id: str) -> dict[str, Any]:
    try:
        image = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ZidError(
            "VALIDATION_FAILED", "image_base64 is not valid base64.", http_status=422
        ) from e
    if len(image) > MAX_IMAGE_BYTES:
        raise ZidError("DOCUMENT_TOO_LARGE", "Document too large.", http_status=413)
    if not image.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
        raise ZidError("TYPE_MISMATCH", "Only PNG and JPEG images are read.", http_status=422)
    template = ocr_client.templates().get(template_id)
    if template is None:
        raise ZidError("TEMPLATE_UNKNOWN", "That template does not exist.", http_status=422)
    url = ocr_client.configured_url()
    if url is None:
        raise ZidError(
            "OCR_NOT_CONFIGURED", "No OCR service is configured; nothing was read.", http_status=503
        )
    try:
        read, engine = await ocr_client.read(url, image, template.ocr_regions(), template.lang)
    except ocr_client.OcrError as e:
        status = 422 if e.code == "OCR_INPUT_REFUSED" else 504 if e.code == "OCR_TIMEOUT" else 502
        raise ZidError(
            e.code, e.code.replace("_", " ").capitalize() + ".", http_status=status
        ) from e
    fields = templates.apply(template, read)
    return {
        **_ids(),
        "form": "OCR_TEMPLATE",
        "template": {
            "id": template.template_id,
            "version": template.version,
            "status": template.status,
            "issuer_ref": template.issuer_ref,
        },
        "fields": [
            {
                "name": f.name,
                "text": f.text,
                "value": f.value,
                "confidence": f.confidence,
                "matched": f.matched,
                "usable": f.usable,
                "reasons": list(f.reasons),
                "source": "OCR",
            }
            for f in fields
        ],
        "all_fields_usable": all(f.usable for f in fields),
        "ocr_engine": engine,
        "authenticity": "NOT_ASSESSED",
        "assurance_ceiling": "SELF_DECLARED",
        "limitations": OCR_LIMITATIONS,
    }


MAX_CHIP_GROUP_BYTES = epassport.MAX_DATA_GROUP


class V2ChipIn(BaseModel):
    """What the phone app read from the chip: the signed object (EF.SOD) and the data groups, each as base64. The photo group (2) is not needed and
    is best not sent: nothing here uses it."""

    model_config = ConfigDict(extra="forbid")
    sod_base64: str = Field(min_length=8, max_length=(epassport.MAX_SOD * 4) // 3 + 16)
    data_groups: dict[
        Annotated[str, Field(pattern=r"^([1-9]|1[0-6])$")],
        Annotated[str, Field(min_length=4, max_length=(MAX_CHIP_GROUP_BYTES * 4) // 3 + 16)],
    ] = Field(min_length=1, max_length=16)


@router.post("/v2/documents/chip-verify")
async def chip_verify(body: V2ChipIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Passive authentication of a passport or eID chip (ICAO Doc 9303). Stateless: nothing is stored and the photo is never read."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="evidence.verify", resource="document:chip")
    try:
        sod = base64.b64decode(body.sod_base64, validate=True)
        groups = {int(k): base64.b64decode(v, validate=True) for k, v in body.data_groups.items()}
    except (binascii.Error, ValueError) as e:
        raise ZidError(
            "VALIDATION_FAILED", "The chip data is not valid base64.", http_status=422
        ) from e
    config = getattr(request.app.state, "trust_config", None) or trust_config()
    verdict = epassport.verify(sod, groups, csca=config.csca, crls=config.crls)
    return {
        **_ids(),
        **verdict.public(),
        "authenticity": "DOCUMENT_INTEGRITY" if verdict.document_authentic else "NOT_ASSESSED",
        "assurance_ceiling": "ISSUER_AUTHENTICATED"
        if verdict.document_authentic
        else "SELF_DECLARED",
        "csca_configured": bool(config.csca),
    }


@router.post("/v2/documents/extract")
async def extract(body: V2ExtractIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="evidence.verify", resource="document:extract")
    if body.image_base64 is not None and body.template_id is not None:
        return await _ocr_fields(body.image_base64, body.template_id)
    if body.pdf_base64 is not None:
        return _pdf_form(body.pdf_base64, request)
    if body.barcode_text is not None:
        return _barcode(body.barcode_text, request)
    try:
        r = mrz.parse(body.mrz_lines or [])
    except mrz.MrzError as e:
        raise ZidError(e.code, e.code.replace("_", " ").capitalize() + ".", http_status=422) from e

    def field(c: mrz.Checked) -> dict[str, Any]:
        return {"value": c.value, "check_digit_ok": c.check_ok, "source": "MRZ"}

    return {
        **_ids(),
        "form": r.form,
        "fields": {
            "document_code": r.document_code,
            "issuing_state": r.issuing_state,
            "surname": r.surname,
            "given_names": r.given_names,
            "document_number": field(r.document_number),
            "nationality": r.nationality,
            "birth_date": {**field(r.birth_date), "iso": r.birth_date_iso},
            "sex": r.sex,
            "expiry_date": {**field(r.expiry_date), "iso": r.expiry_date_iso},
            "optional_data": r.optional_data,
        },
        "composite_check_digit_ok": r.composite_ok,
        "all_check_digits_hold": r.all_checks_hold,
        "reason_codes": list(r.reason_codes),
        "authenticity": "NOT_ASSESSED",
        "assurance_ceiling": "SELF_DECLARED",
        "limitations": LIMITATIONS,
    }

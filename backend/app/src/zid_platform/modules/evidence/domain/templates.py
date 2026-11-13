"""Document layout templates for bounded OCR (ZID-GAP-001 v2.0 DTV-16; CR-073 P2-4). Pure: no I/O, no OCR here.

A template is DATA: where on the page to read (regions, as fractions of the image) and what each region is expected to look like (a pattern).
It exists to bound the reader to places that matter, so a scan is not turned into free text. What it does NOT do, and says so:

- matching a template proves NOTHING about a document. Anyone can print a card with the same layout. A field read by OCR is what the page SAYS,
  at the engine's own confidence, never evidence of authenticity (the document's section 11);
- a field that does not match its pattern, or was read with low confidence, is reported as exactly that. It is never repaired, never guessed,
  and never silently dropped;
- a template is `SAMPLE` until someone has checked it against real documents of that issuer and recorded who and when (`APPROVED` needs both).
  Every template shipped here is a sample.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

LANGUAGES = frozenset({"eng", "fra", "spa", "deu", "ara"})
STATUSES = frozenset({"SAMPLE", "APPROVED"})
MAX_REGIONS = 50
DEFAULT_MIN_CONFIDENCE = 60.0


class TemplateError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Region:
    name: str
    x: float
    y: float
    w: float
    h: float
    pattern: re.Pattern[str] | None = (
        None  # fullmatch; a group named `value` picks the part to keep
    )


@dataclass(frozen=True, slots=True)
class Template:
    template_id: str
    version: int
    status: str
    lang: str
    description: str
    regions: tuple[Region, ...]
    issuer_ref: str | None = None
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    approved_by: str | None = None
    approved_on: str | None = None

    def ocr_regions(self) -> list[dict[str, Any]]:
        return [{"name": r.name, "x": r.x, "y": r.y, "w": r.w, "h": r.h} for r in self.regions]


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    text: str
    value: str | None  # what the pattern kept; None when it did not match
    confidence: float
    matched: bool
    usable: bool  # matched AND read with enough confidence
    reasons: tuple[str, ...]


def parse(doc: Mapping[str, Any]) -> dict[str, Template]:
    out: dict[str, Template] = {}
    for raw in doc.get("templates") or []:
        try:
            tid = str(raw["id"])
            regions = []
            for r in raw["regions"]:
                x, y, w, h = (float(r[k]) for k in ("x", "y", "w", "h"))
                if not (
                    0 <= x < 1
                    and 0 <= y < 1
                    and 0 < w <= 1
                    and 0 < h <= 1
                    and x + w <= 1.0001
                    and y + h <= 1.0001
                ):
                    raise TemplateError("REGION_OUT_OF_BOUNDS", f"{tid}.{r['name']}")
                pattern = re.compile(str(r["pattern"])) if r.get("pattern") else None
                regions.append(Region(str(r["name"]), x, y, w, h, pattern))
            template = Template(
                template_id=tid,
                version=int(raw["version"]),
                status=str(raw.get("status", "SAMPLE")),
                lang=str(raw.get("lang", "eng")),
                description=str(raw["description"]),
                regions=tuple(regions),
                issuer_ref=raw.get("issuer_ref"),
                min_confidence=float(raw.get("min_confidence", DEFAULT_MIN_CONFIDENCE)),
                approved_by=raw.get("approved_by"),
                approved_on=raw.get("approved_on"),
            )
        except (KeyError, TypeError, ValueError, re.error) as e:
            if isinstance(e, TemplateError):
                raise
            raise TemplateError("TEMPLATE_INVALID", str(e)[:80]) from e
        if not template.regions or len(template.regions) > MAX_REGIONS:
            raise TemplateError("REGIONS_INVALID", tid)
        if len({r.name for r in template.regions}) != len(template.regions):
            raise TemplateError("REGION_NAME_DUPLICATE", tid)
        if template.status not in STATUSES:
            raise TemplateError("STATUS_UNKNOWN", f"{tid}: {template.status}")
        if template.lang not in LANGUAGES:
            raise TemplateError("LANGUAGE_NOT_SUPPORTED", f"{tid}: {template.lang}")
        if template.status == "APPROVED" and not (template.approved_by and template.approved_on):
            raise TemplateError("APPROVAL_NOT_RECORDED", tid)  # approved by nobody is not approved
        if tid in out:
            raise TemplateError("TEMPLATE_DUPLICATE", tid)
        out[tid] = template
    return out


def apply(template: Template, ocr: Sequence[Mapping[str, Any]]) -> list[Field]:
    """Turn what the reader returned for each region into fields, one per region of the template, in the template's order."""
    by_name = {str(r.get("name")): r for r in ocr}
    fields: list[Field] = []
    for region in template.regions:
        got = by_name.get(region.name)
        if got is None:
            fields.append(Field(region.name, "", None, 0.0, False, False, ("REGION_NOT_READ",)))
            continue
        text = str(got.get("text", "")).strip()
        confidence = float(got.get("confidence", 0.0))
        reasons: list[str] = []
        value: str | None = text or None
        matched = bool(text)
        if not text:
            reasons.append("REGION_EMPTY")
        elif region.pattern is not None:
            m = region.pattern.fullmatch(" ".join(text.split()))
            if m is None:
                matched, value = False, None
                reasons.append("PATTERN_NOT_MATCHED")
            else:
                value = m.group("value") if "value" in region.pattern.groupindex else m.group(0)
        if confidence < template.min_confidence:
            reasons.append("LOW_CONFIDENCE")
        fields.append(
            Field(
                region.name,
                text,
                value,
                confidence,
                matched,
                matched and not reasons,
                tuple(reasons),
            )
        )
    return fields

"""The method certification board: for each identity-proofing method, how far from "certified" it really is (ZID-DTV-WF-03 screen P10; CR-119). Pure: facts in, rows out.

Four levels, in order of what has been proved:

- `NOT_BUILT`: no result is ever produced, so nothing can be approved on it.
- `BUILT_UNPROVEN`: the software exists and is tested against fixtures and the standard's examples. It has never met a real document, a real device or a lab. This is the ceiling the platform can reach by itself.
- `EVIDENCE_INCOMPLETE`: the method is switched on or waiting for approval, and named records are missing.
- `APPROVED`: every reference the approval needs is present (for an engine: a recorded evaluation, licences read, a DPIA, measured rates inside the ceilings). Only a method with such records can be here.

The board never approves anything and never invents evidence: it reads what the versioned configuration and the stores say and says what is still missing and who must supply it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Level(StrEnum):
    NOT_BUILT = "NOT_BUILT"
    BUILT_UNPROVEN = "BUILT_UNPROVEN"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
    APPROVED = "APPROVED"


@dataclass(frozen=True, slots=True)
class Row:
    method: str
    level: Level
    summary: str
    needs: tuple[
        str, ...
    ]  # what is still missing, in plain words, with the owner where one is obvious


AUTHENTICITY_CHECKS = frozenset({"TEMPLATE", "TAMPER", "PHOTO_SUBSTITUTION"})
CROSSCHECK_NEED = (
    "The independent cross-check of our own reader against JMRTD or NFCPassportReader across the device matrix, "
    "with a recorded gate policy and no critical disagreement (E03, tools/scripts/chip_crosscheck.py)"
)


def board(
    *,
    trust_list_active: bool,
    engine: Mapping[str, Any],
    optical_checks_produced: Iterable[str],
    apple_attestation_configured: bool,
    imaging_configured: bool,
) -> list[Row]:
    produced = set(optical_checks_produced)
    rows: list[Row] = []

    rows.append(
        Row(
            "CHIP_PASSIVE_AUTHENTICATION",
            Level.BUILT_UNPROVEN if trust_list_active else Level.EVIDENCE_INCOMPLETE,
            "Signed-object and issuer-chain checks, run by the platform against the approved country lists."
            if trust_list_active
            else "Built, but no approved country list is active, so no chip can be called verified.",
            (
                (
                    "A real passport read end to end on a real phone (QA, with consenting staff)",
                    CROSSCHECK_NEED,
                )
                if trust_list_active
                else (
                    "An approved, active country list (PKI owner and a second approver)",
                    "A real passport read end to end on a real phone",
                    CROSSCHECK_NEED,
                )
            ),
        )
    )
    rows.append(
        Row(
            "CHIP_CLONE_CHECK",
            Level.BUILT_UNPROVEN,
            "Active Authentication and the PACE chip authentication mapping, proved against the standard's worked example and a simulated chip.",
            ("A real chip that supports it, and a known clone from the lab to prove it fails",),
        )
    )
    rows.append(
        Row(
            "PRINTED_DOCUMENT_QUALITY",
            Level.BUILT_UNPROVEN if imaging_configured else Level.EVIDENCE_INCOMPLETE,
            "Resolution, sharpness, glare, exposure and a re-compression screen. A good score is not authenticity."
            if imaging_configured
            else "Built, but the photo-checking service is not configured, so a photograph answers 'not available' and nothing is read.",
            (
                (
                    "Thresholds measured against a real document population (the current ones are proposals)",
                )
                if imaging_configured
                else (
                    "Run the photo-checking service (docker compose --profile ocr) and set ZID_OCR_URL",
                    "Read the Tesseract licence and sign its register entry",
                    "Thresholds measured against a real document population (the current ones are proposals)",
                )
            ),
        )
    )
    authentic = AUTHENTICITY_CHECKS & produced
    rows.append(
        Row(
            "PRINTED_DOCUMENT_AUTHENTICITY",
            Level.BUILT_UNPROVEN if authentic else Level.NOT_BUILT,
            "Template, security-feature and photo-substitution checks."
            if authentic
            else "No result is produced, so a market that requires it can never approve.",
            ("Genuine specimens of each document and an evaluation against them",),
        )
    )

    mode = str(engine.get("mode", "OFF"))
    approvable = engine.get("approvable") is True
    problems = tuple(str(p) for p in engine.get("problems_for_approval") or ())
    if mode == "APPROVED_FOR_SCOPE" and engine.get("enabled") is True and approvable:
        countries = ", ".join(engine.get("countries") or ()) or "no country"
        rows.append(
            Row(
                "FACE_AND_LIVENESS_ENGINE",
                Level.APPROVED,
                f"Approved for: {countries}. Every reference the approval needs is recorded.",
                (),
            )
        )
    elif mode == "OFF" and not engine.get("enabled"):
        rows.append(
            Row(
                "FACE_AND_LIVENESS_ENGINE",
                Level.EVIDENCE_INCOMPLETE if problems else Level.BUILT_UNPROVEN,
                "Off. No selfie is compared with a document portrait.",
                problems
                or ("Switching it on is a decision for the owners once the records exist",),
            )
        )
    else:
        rows.append(
            Row(
                "FACE_AND_LIVENESS_ENGINE",
                Level.EVIDENCE_INCOMPLETE,
                "Switched on or awaiting approval, with records missing.",
                problems or ("The engine's approval records",),
            )
        )

    rows.append(
        Row(
            "DEVICE_ATTESTATION",
            Level.BUILT_UNPROVEN,
            "Apple App Attest is verified in code, tested on a synthetic chain only."
            if apple_attestation_configured
            else "Apple App Attest is verified in code, tested on a synthetic chain only; it is not configured.",
            (
                "Android attestation (not built)",
                "A real device attestation chain",
                "The app's identifiers in configuration",
            ),
        )
    )
    return rows


def summary(rows: Iterable[Row]) -> dict[str, int]:
    counts = {level.value: 0 for level in Level}
    for r in rows:
        counts[r.level.value] += 1
    return counts

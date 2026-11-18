"""Identity proofing: turning raw evidence into signal states (ZID-DTV-WF-03 E02, E04, E07, E08; CR-110). Pure domain, no I/O.

Three jobs, each answering one signal and nothing else:
- `chip_signals`: the platform's own passive-authentication verdict, in the WF03 signal words. A chip that was read is not a verified chip; only a verdict that chained to a root we hold is TRUSTED_PASS, and with no
  revocation check the answer is REVOCATION_UNKNOWN, never a pass.
- `capture_integrity`: whether a capture can be trusted to have come from a live camera in front of this case: a server-issued one-time nonce, still fresh, bound to the case; a media digest not seen before; no virtual
  camera; no failed device or app attestation. Anything missing is "not established", which is not "safe".
- `optical_state` and `compare_printed_to_chip`: how many printed-document checks passed, failed or could not run (the checks themselves are an engine's job and there is none here), and whether the printed
  holder data agrees with the chip's data group 1 after a documented normalisation.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from zid_platform.modules.verification.domain.idv_decision import (
    NfcAccess,
    Optical,
    PassiveAuth,
)

# ------------------------------------------------------------------------------------------------------------------------------ the chip

_SIGNATURE_FAILURES = frozenset(
    {
        "SOD_SIGNATURE_INVALID",
        "SOD_DIGEST_MISMATCH",
        "DATA_GROUP_HASH_MISMATCH",
        "DATA_GROUP_NOT_COVERED_BY_SOD",
        "DATA_GROUP_INVALID",
        "HASH_ALGORITHM_WEAK",
        "DOCUMENT_SIGNER_CERTIFICATE_REVOKED",
    }
)


@dataclass(frozen=True, slots=True)
class ChipSignals:
    nfc: NfcAccess
    passive_auth: PassiveAuth
    mrz_check_digits_ok: bool | None
    document_expired: bool | None
    reasons: tuple[str, ...]


def chip_signals(status: str, reasons: tuple[str, ...]) -> ChipSignals:
    """The platform's ChipVerdict (status VERIFIED_LIMITED | NOT_VERIFIED | INDETERMINATE and its reason codes) as WF03 signals. A chip that was supplied at all is READ_OK; everything after that is the passive check."""
    rs = set(reasons)
    mrz_ok: bool | None = None
    if "MRZ_CHECK_DIGITS_FAILED" in rs:
        mrz_ok = False
    elif (
        status == "VERIFIED_LIMITED"
        and "DG1_NOT_SUPPLIED" not in rs
        and not any(r.startswith("DG1_") for r in rs)
    ):
        mrz_ok = True
    expired = (
        True
        if "DOCUMENT_EXPIRED" in rs
        else (False if status == "VERIFIED_LIMITED" and mrz_ok is not None else None)
    )
    if status == "VERIFIED_LIMITED":
        pa = (
            PassiveAuth.TRUSTED_PASS
            if "REVOCATION_NOT_CHECKED" not in rs
            else PassiveAuth.REVOCATION_UNKNOWN
        )
    elif status == "NOT_VERIFIED":
        pa = PassiveAuth.SIGNATURE_FAIL if rs & _SIGNATURE_FAILURES else PassiveAuth.NOT_TESTED
    elif "NO_CSCA_CONFIGURED" in rs:
        pa = PassiveAuth.CERT_MISSING
    elif "SIGNER_NOT_ISSUED_BY_A_CONFIGURED_CSCA" in rs:
        pa = PassiveAuth.CERT_UNTRUSTED
    else:
        pa = PassiveAuth.NOT_TESTED
    return ChipSignals(NfcAccess.READ_OK, pa, mrz_ok, expired, tuple(reasons))


# -------------------------------------------------------------------------------------------------------------------------- the capture


@dataclass(frozen=True, slots=True)
class Envelope:
    nonce: str
    media_sha256: str
    captured_at: datetime
    virtual_camera: bool | None = None  # the app's own detection of a virtual or emulated camera
    device_attested: bool | None = (
        None  # a platform attestation of the device and app, when the platform offers one
    )


@dataclass(frozen=True, slots=True)
class IssuedNonce:
    value: str
    case_id: str
    issued_at: datetime
    used: bool = False


@dataclass(frozen=True, slots=True)
class CaptureVerdict:
    injection_risk: bool | None  # True: a finding; False: established clean; None: not established
    reasons: tuple[str, ...]


def capture_integrity(
    env: Envelope,
    issued: IssuedNonce | None,
    *,
    case_id: str,
    media_seen_before: bool,
    now: datetime,
    max_age: timedelta = timedelta(minutes=10),
    attestation_required: bool = False,
) -> CaptureVerdict:
    """Findings first (any one makes it a risk), then what could not be established. Never returns False unless the nonce, freshness, media digest and camera all checked out."""
    findings: list[str] = []
    if issued is None or issued.value != env.nonce:
        findings.append("CAPTURE_NONCE_UNKNOWN")
    else:
        if issued.case_id != case_id:
            findings.append("CAPTURE_NONCE_FOR_ANOTHER_CASE")
        if issued.used:
            findings.append("CAPTURE_NONCE_ALREADY_USED")
        if now - issued.issued_at > max_age or env.captured_at < issued.issued_at - timedelta(
            seconds=5
        ):
            findings.append("CAPTURE_NOT_FRESH")
    if env.captured_at > now + timedelta(minutes=2):
        findings.append("CAPTURE_TIME_IN_THE_FUTURE")
    if media_seen_before:
        findings.append("CAPTURE_MEDIA_REPLAYED")
    if env.virtual_camera is True:
        findings.append("VIRTUAL_CAMERA_DETECTED")
    if env.device_attested is False:
        findings.append("DEVICE_ATTESTATION_FAILED")
    if not re.fullmatch(r"[0-9a-f]{64}", env.media_sha256):
        findings.append("CAPTURE_MEDIA_DIGEST_INVALID")
    if findings:
        return CaptureVerdict(True, tuple(findings))
    unknown: list[str] = []
    if env.virtual_camera is None:
        unknown.append("VIRTUAL_CAMERA_NOT_ASSESSED")
    if env.device_attested is None and attestation_required:
        unknown.append("DEVICE_ATTESTATION_NOT_AVAILABLE")
    if unknown:
        return CaptureVerdict(None, tuple(unknown))
    return CaptureVerdict(False, ())


# -------------------------------------------------------------------------------------------------------------------------- the printed document

CHECK_RESULTS = frozenset({"PASS", "FAIL", "INCONCLUSIVE"})
DEFAULT_FATAL = frozenset(
    {"TEMPLATE", "TAMPER", "PHOTO_SUBSTITUTION"}
)  # a FAIL on one of these is manipulation, not doubt


def optical_state(
    results: Mapping[str, str], required: frozenset[str], *, fatal: frozenset[str] = DEFAULT_FATAL
) -> tuple[Optical, tuple[str, ...]]:
    """PASS only if every required check ran and passed and none failed. A fatal check that failed is FAIL; any other failure or an inconclusive check asks for a person; a required check that did not run is UNKNOWN."""
    bad = [k for k, v in results.items() if v not in CHECK_RESULTS]
    if bad:
        return Optical.UNKNOWN, tuple(f"OPTICAL_RESULT_UNREADABLE:{k}" for k in sorted(bad))
    failed = sorted(k for k, v in results.items() if v == "FAIL")
    if any(k in fatal for k in failed):
        return Optical.FAIL, tuple(f"OPTICAL_{k}_FAILED" for k in failed)
    if failed:
        return Optical.REVIEW, tuple(f"OPTICAL_{k}_FAILED" for k in failed)
    inconclusive = sorted(k for k, v in results.items() if v == "INCONCLUSIVE")
    if inconclusive:
        return Optical.REVIEW, tuple(f"OPTICAL_{k}_INCONCLUSIVE" for k in inconclusive)
    missing = sorted(required - set(results))
    if missing:
        return Optical.UNKNOWN, tuple(f"OPTICAL_{k}_NOT_RUN" for k in missing)
    return Optical.PASS, ()


def _norm(value: str) -> str:
    """Upper case, accents removed, the MRZ filler and punctuation read as a space, runs of space collapsed: the way a printed name and its MRZ spelling are compared."""
    s = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").upper()
    s = re.sub(r"[<\-'.,]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


FIELDS = ("surname", "given_names", "document_number", "birth_date", "expiry_date")


def compare_declared(printed: Mapping[str, str], declared: Mapping[str, str]) -> tuple[str, tuple[str, ...]]:
    """What the applicant typed against the platform's own reading of the printed lines: MATCH only when all four of surname, given names, document number and birth date were compared and agree;
    MISMATCH as soon as one differs; UNKNOWN when any could not be compared (a field not typed, or not read)."""
    fields = ("surname", "given_names", "document_number", "birth_date")
    differing = tuple(f for f in fields if printed.get(f) and declared.get(f) and _norm(printed[f]) != _norm(declared[f]))
    if differing:
        return "MISMATCH", differing
    if all(printed.get(f) and declared.get(f) for f in fields):
        return "MATCH", ()
    return "UNKNOWN", ()


def compare_printed_to_chip(
    printed: Mapping[str, str], chip: Mapping[str, str]
) -> tuple[str, tuple[str, ...]]:
    """(MATCH | MISMATCH | UNKNOWN, the fields that differ). UNKNOWN when nothing could be compared, MISMATCH as soon as one compared field differs; a field missing on either side is not a match."""
    differing: list[str] = []
    compared = 0
    for f in FIELDS:
        a, b = printed.get(f), chip.get(f)
        if not a or not b:
            continue
        compared += 1
        if _norm(a) != _norm(b):
            differing.append(f)
    if differing:
        return "MISMATCH", tuple(differing)
    if compared < 3:
        return "UNKNOWN", ()
    return "MATCH", ()


def printed_digests(printed: Mapping[str, str], salt: str) -> dict[str, str]:
    """Salted digests of the normalised printed fields, so the chip comparison can be made later without keeping the data. The salt is per case; a digest of a low-entropy field resists only a casual reader of the table."""
    return {
        f: hashlib.sha256(f"{salt}|{f}|{_norm(printed[f])}".encode()).hexdigest()
        for f in FIELDS
        if printed.get(f)
    }


def compare_digests(
    stored: Mapping[str, str], chip: Mapping[str, str], salt: str
) -> tuple[str, tuple[str, ...]]:
    """The same verdict as `compare_printed_to_chip`, made against the stored digests."""
    mine = printed_digests(chip, salt)
    both = [f for f in FIELDS if f in stored and f in mine]
    differing = tuple(f for f in both if stored[f] != mine[f])
    if differing:
        return "MISMATCH", differing
    return ("MATCH", ()) if len(both) >= 3 else ("UNKNOWN", ())


# ----------------------------------------------------------------------------------------------------------------- the face engine's scores


def engine_signals(
    live_score: float | None, face_score: float | None, *, liveness_min: float, face_min: float
) -> tuple[str, str]:
    """(liveness, face match) as WF03 states from an approved engine's scores and its own thresholds. None means the engine could not decide: INCONCLUSIVE, never a pass.
    A face is never matched against a presentation that failed or could not be judged, so then the match is NOT_RUN."""
    if live_score is None:
        return "INCONCLUSIVE", "NOT_RUN"
    if live_score < liveness_min:
        return "FAIL", "NOT_RUN"
    if face_score is None:
        return "PASS", "INCONCLUSIVE"
    return "PASS", ("MATCH" if face_score >= face_min else "NON_MATCH")

"""Identity proofing: the signals, and the one deterministic decision they support (ZID-DTV-WF-03 sections 6 and 18; epics E01 and E10; CR-110). Pure domain, no I/O.

Each piece of evidence is its own signal with its own states, and no signal stands in for another:
- reading a chip (`NFC READ_OK`) proves the data could be read, not that an issuer signed it; only a passive authentication that chains to a root the platform trusts (`TRUSTED_PASS`) supports an
  issuer-signed integrity claim, and with no root the answer is CERT_MISSING, never a pass;
- passive authentication says nothing about cloning (that is chip or active authentication, `PASS` only when it ran and passed), and nothing about who is holding the document (that is liveness AND a face
  match against a reference whose own authenticity is tagged);
- printed text and OCR are extraction, not authenticity; a transcription typed by a person is never an authenticated-document claim;
- a similarity score never repairs a failed or inconclusive presentation-attack check.

The decision is pinned to the market's signed policy (which methods are required, which alternative routes are signed, what an expired document or an unknown revocation means) and is one of APPROVED,
REVIEW_REQUIRED, REJECTED, INDETERMINATE or ABANDONED. REJECTED needs a defined failure; missing evidence is INDETERMINATE, never an accusation; nothing is ever downgraded implicitly from a mandatory
chip to an optical alternative. No assurance level of any framework is named here: a label like IAL2 needs an independent control mapping, not a passing test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Optical(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"
    UNKNOWN = "UNKNOWN"


class NfcAccess(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    UNSUPPORTED = "UNSUPPORTED"
    USER_DECLINED = "USER_DECLINED"
    READ_FAILED = "READ_FAILED"
    READ_OK = "READ_OK"


class PassiveAuth(StrEnum):
    TRUSTED_PASS = "TRUSTED_PASS"
    SIGNATURE_FAIL = "SIGNATURE_FAIL"
    CERT_UNTRUSTED = "CERT_UNTRUSTED"
    CERT_MISSING = "CERT_MISSING"
    REVOCATION_UNKNOWN = "REVOCATION_UNKNOWN"
    NOT_TESTED = "NOT_TESTED"


class ChipAuth(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_TESTED = "NOT_TESTED"


class FaceMatch(StrEnum):
    MATCH = "MATCH"
    NON_MATCH = "NON_MATCH"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_RUN = "NOT_RUN"


class Liveness(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_RUN = "NOT_RUN"


class Decision(StrEnum):
    APPROVED = "APPROVED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"
    INDETERMINATE = "INDETERMINATE"
    ABANDONED = "ABANDONED"


class Requirement(StrEnum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    NOT_USED = "NOT_USED"


@dataclass(frozen=True, slots=True)
class AlternateRoute:
    """A route a market's signed policy allows instead of a mandatory chip. It never carries chip-grade claims; `may_approve` says whether, with the reviewers it needs, it can end in APPROVED at all."""

    route_id: str
    may_approve: bool = False
    reviewers_needed: int = 2
    automatic: bool = False  # decided by the platform from the document and the applicant's own typed details: no reviewer; a lower grade
    face_match: bool = False  # an automatic route decided on a live face against the document's own photograph (CR-129): no liveness test, no authenticity check, the lowest grade
    face_threshold: float = 0.5  # the similarity the face must reach (the owner's choice; the engine's own suggestion is only a starting point)


@dataclass(frozen=True, slots=True)
class Policy:
    """The market's signed policy as the decision sees it (pinned to the case by its version)."""

    version: str
    chip: Requirement = Requirement.REQUIRED
    optical: Requirement = Requirement.REQUIRED
    face: Requirement = Requirement.REQUIRED
    clone_check: Requirement = Requirement.OPTIONAL
    expired_document: str = (
        "REFUSE"  # REFUSE | REVIEW | ACCEPT: country-specific, never a universal rule
    )
    on_revocation_unknown: str = "INDETERMINATE"  # INDETERMINATE | REVIEW
    reject_on_optical_fail: bool = True
    capture_integrity: Requirement = (
        Requirement.REQUIRED
    )  # a capture whose origin was not established blocks approval
    alternates: tuple[AlternateRoute, ...] = ()


@dataclass(frozen=True, slots=True)
class Signals:
    optical: Optical = Optical.NOT_STARTED
    nfc: NfcAccess = NfcAccess.NOT_STARTED
    passive_auth: PassiveAuth = PassiveAuth.NOT_TESTED
    chip_auth: ChipAuth = ChipAuth.NOT_TESTED
    face: FaceMatch = FaceMatch.NOT_RUN
    liveness: Liveness = Liveness.NOT_RUN
    mrz_check_digits_ok: bool | None = None  # None: not checked
    optical_matches_chip: str = "UNKNOWN"  # MATCH | MISMATCH | UNKNOWN (printed data against DG1)
    reference_source: str = "NONE"  # CHIP_DG2_TRUSTED | CHIP_DG2_UNTRUSTED | OPTICAL | NONE: where the face was compared to
    injection_risk: bool | None = (
        None  # a virtual camera, a forged capture envelope or a substituted stream
    )
    document_expired: bool | None = None
    web_only_capture: bool = False  # the capture came from a browser: it can never be chip-verified
    typed_transcription_only: bool = False
    route: str = "CHIP"  # CHIP or the id of a signed alternate route
    route_enabled: bool = True  # the provider route and its entitlement are in force
    method_released: bool = True  # the method is RELEASE_APPROVED for this market
    reviewers: tuple[str, ...] = ()
    abandoned: bool = False
    declared_matches_document: str = "UNKNOWN"  # MATCH | MISMATCH | UNKNOWN: what the applicant typed against the platform's own reading of the printed lines
    reject_code: str | None = (
        None  # a reviewer on a reviewed route rejected the case, for one of a fixed set of defined failures
    )


@dataclass(frozen=True, slots=True)
class Result:
    decision: Decision
    reasons: tuple[str, ...]
    claims: dict[str, str] = field(
        default_factory=dict
    )  # the signal states, as the redacted decision event carries them
    allowed_assertions: tuple[str, ...] = ()  # what may be said, and nothing else
    policy_version: str = ""


def face_signal(cosine: float | None, threshold: float) -> FaceMatch:
    """The face-match route's signal from the engine's similarity. No similarity (no face found, an unreadable picture) is INCONCLUSIVE: a retake, never a failure."""
    if cosine is None:
        return FaceMatch.INCONCLUSIVE
    return FaceMatch.MATCH if cosine >= threshold else FaceMatch.NON_MATCH


def _claims(s: Signals) -> dict[str, str]:
    return {
        "optical": s.optical.value,
        "nfc_read": s.nfc.value,
        "passive_auth": s.passive_auth.value,
        "chip_auth": s.chip_auth.value,
        "liveness": s.liveness.value,
        "face_match": s.face.value,
    }


def assertions(s: Signals) -> tuple[str, ...]:
    """What the signals support saying, one claim per fact and no claim without its own proof."""
    out: list[str] = []
    if s.optical is Optical.PASS and not s.typed_transcription_only:
        out.append("DOCUMENT_OPTICALLY_AUTHENTIC")
    if s.nfc is NfcAccess.READ_OK:
        out.append("CHIP_DATA_READ")
    if (
        s.nfc is NfcAccess.READ_OK
        and s.passive_auth is PassiveAuth.TRUSTED_PASS
        and not s.web_only_capture
    ):
        out.append("CHIP_DATA_ISSUER_SIGNED")
    if s.chip_auth is ChipAuth.PASS and s.passive_auth is PassiveAuth.TRUSTED_PASS:
        out.append("CHIP_NOT_CLONED")
    if s.liveness is Liveness.PASS and s.injection_risk is not True:
        out.append("LIVE_PRESENCE")
    if (
        s.face is FaceMatch.MATCH
        and s.liveness is Liveness.NOT_RUN
        and s.reference_source == "OPTICAL"
        and s.injection_risk is not True
    ):
        # the face-match route (CR-129): a live face looked like the photograph printed on the document, and that is all this says. It is not LIVE_PRESENCE, and the
        # photograph itself was never authenticated, so it is NOT a holder claim: whoever uploads a document can choose the face on it.
        out.append("FACE_MATCHES_DOCUMENT_PHOTO_NO_LIVENESS")
    if (
        s.face is FaceMatch.MATCH
        and s.liveness is Liveness.PASS
        and s.injection_risk is not True
        and s.reference_source in ("CHIP_DG2_TRUSTED", "OPTICAL")
    ):
        out.append(
            "HOLDER_MATCHES_TRUSTED_CHIP_PORTRAIT"
            if s.reference_source == "CHIP_DG2_TRUSTED"
            else "HOLDER_MATCHES_DOCUMENT_PORTRAIT"
        )
    return tuple(out)


def _chip_problems(s: Signals, p: Policy) -> tuple[Decision, list[str]] | None:
    """What stops a mandatory chip from supporting approval: (decision, reasons) or None when the chip is fully good."""
    if s.web_only_capture:
        return Decision.INDETERMINATE, ["WEB_CAPTURE_CANNOT_BE_CHIP_VERIFIED"]
    if s.nfc in (
        NfcAccess.UNSUPPORTED,
        NfcAccess.USER_DECLINED,
        NfcAccess.READ_FAILED,
        NfcAccess.NOT_STARTED,
    ):
        return Decision.INDETERMINATE, [f"CHIP_{s.nfc.value}"]
    pa = s.passive_auth
    if pa is PassiveAuth.SIGNATURE_FAIL:
        return Decision.REJECTED, [
            "CHIP_INTEGRITY_FAILED"
        ]  # a defined failure: the signed hashes do not match the data read
    if pa is PassiveAuth.CERT_MISSING:
        return Decision.INDETERMINATE, [
            "CHIP_TRUST_ROOT_MISSING"
        ]  # no root: no verified chip claim, and not a finding of fraud
    if pa is PassiveAuth.CERT_UNTRUSTED:
        return Decision.INDETERMINATE, ["CHIP_SIGNER_NOT_TRUSTED"]
    if pa is PassiveAuth.REVOCATION_UNKNOWN:
        return (
            Decision.REVIEW_REQUIRED
            if p.on_revocation_unknown == "REVIEW"
            else Decision.INDETERMINATE
        ), ["CHIP_REVOCATION_UNKNOWN"]
    if pa is PassiveAuth.NOT_TESTED:
        return Decision.INDETERMINATE, ["CHIP_NOT_AUTHENTICATED"]
    return None


def decide(s: Signals, p: Policy) -> Result:
    """The decision these signals support under this policy. Deterministic: the same signals and policy give the same answer, and a missing signal can only lower it."""
    claims, allowed = _claims(s), assertions(s)

    def out(decision: Decision, *reasons: str) -> Result:
        return Result(decision, tuple(dict.fromkeys(reasons)), claims, allowed, p.version)

    if s.abandoned:
        return out(Decision.ABANDONED, "APPLICANT_ABANDONED")
    if not s.route_enabled:
        return out(
            Decision.INDETERMINATE, "ROUTE_DISABLED"
        )  # a provider entitlement lost, or a route switched off: never the unsafe open model
    if not s.method_released:
        return out(Decision.INDETERMINATE, "METHOD_NOT_RELEASED_FOR_MARKET")

    on_chip_route = s.route == "CHIP"
    alternate = next((a for a in p.alternates if a.route_id == s.route), None)
    if not on_chip_route and alternate is None:
        return out(
            Decision.INDETERMINATE, "ROUTE_NOT_ALLOWED_BY_POLICY"
        )  # an alternative exists only if the signed policy names it

    if s.reject_code:
        return out(
            Decision.REJECTED, f"REVIEWER_REJECTED:{s.reject_code}"
        )  # a defined failure, never a bare accusation

    automatic = alternate is not None and alternate.automatic
    face_route = alternate is not None and alternate.automatic and alternate.face_match
    reasons: list[str] = []
    worst = Decision.APPROVED
    rank = {
        Decision.APPROVED: 0,
        Decision.INDETERMINATE: 1,
        Decision.REVIEW_REQUIRED: 2,
        Decision.REJECTED: 3,
    }

    def lower(to: Decision, *why: str) -> None:
        nonlocal worst
        reasons.extend(why)
        if rank[to] > rank[worst]:
            worst = to

    # --- the chip
    if on_chip_route and p.chip is not Requirement.NOT_USED:
        problem = _chip_problems(s, p)
        if problem is not None:
            if p.chip is Requirement.REQUIRED:
                lower(*(problem[0], *problem[1]))
            elif s.passive_auth is PassiveAuth.SIGNATURE_FAIL:
                lower(
                    Decision.REJECTED, "CHIP_INTEGRITY_FAILED"
                )  # an optional chip that was read and failed is still a failure
    if not on_chip_route:
        # the alternate route is never chip grade, and a chip claim cannot come from it
        if s.passive_auth is PassiveAuth.TRUSTED_PASS or s.nfc is NfcAccess.READ_OK:
            reasons.append("ALTERNATE_ROUTE_CARRIES_NO_CHIP_GRADE")
        if alternate is not None and not alternate.may_approve:
            lower(Decision.REVIEW_REQUIRED, "ALTERNATE_ROUTE_REVIEW_ONLY")
        elif face_route:
            # the face-match route: no machine-readable lines, no typed details. A match is the one thing that approves; anything else asks for a retake and is never an
            # automatic rejection on a biometric score.
            if not (s.face is FaceMatch.MATCH and s.reference_source == "OPTICAL"):
                if s.face is FaceMatch.NON_MATCH:
                    lower(Decision.INDETERMINATE, "FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO")
                elif s.face is FaceMatch.INCONCLUSIVE:
                    lower(Decision.INDETERMINATE, "FACE_NOT_FOUND_OR_UNCLEAR")
                else:
                    lower(Decision.INDETERMINATE, "FACE_MATCH_NOT_RUN")
        elif alternate is not None and alternate.automatic:
            # no person decides here, so the evidence has to: a reading the platform trusts, and the applicant's own details agreeing with it
            if s.mrz_check_digits_ok is not True:
                lower(Decision.INDETERMINATE, "DOCUMENT_READING_NOT_RELIABLE")
            if s.declared_matches_document == "MISMATCH":
                lower(Decision.REJECTED, "DECLARED_DATA_DIFFERS_FROM_DOCUMENT")
            elif s.declared_matches_document != "MATCH":
                lower(Decision.INDETERMINATE, "DECLARED_DATA_NOT_COMPARED")
        elif alternate is not None and len(set(s.reviewers)) < alternate.reviewers_needed:
            lower(Decision.REVIEW_REQUIRED, "ALTERNATE_ROUTE_NEEDS_INDEPENDENT_REVIEWERS")

    # --- clone resistance
    if on_chip_route and p.clone_check is not Requirement.NOT_USED:
        if s.chip_auth is ChipAuth.FAIL:
            lower(Decision.REVIEW_REQUIRED, "CLONE_SUSPECTED")
        elif p.clone_check is Requirement.REQUIRED and s.chip_auth is not ChipAuth.PASS:
            lower(
                Decision.INDETERMINATE, "CLONE_CHECK_NOT_PASSED"
            )  # unsupported or not tested: no clone-resistance claim

    # --- the printed document
    if p.optical is not Requirement.NOT_USED:
        if s.typed_transcription_only:
            lower(Decision.REVIEW_REQUIRED, "TYPED_TRANSCRIPTION_IS_NOT_AUTHENTICATION")
        elif s.optical is Optical.FAIL:
            lower(
                Decision.REJECTED if p.reject_on_optical_fail else Decision.REVIEW_REQUIRED,
                "DOCUMENT_MANIPULATION_INDICATED",
            )
        elif s.optical is Optical.REVIEW:
            lower(Decision.INDETERMINATE if automatic else Decision.REVIEW_REQUIRED, "OPTICAL_REVIEW")  # nobody reviews an automatic route: it asks for a retake
        elif p.optical is Requirement.REQUIRED and s.optical is not Optical.PASS:
            lower(Decision.INDETERMINATE, f"OPTICAL_{s.optical.value}")
    if s.mrz_check_digits_ok is False and not face_route:
        lower(
            Decision.INDETERMINATE if automatic else Decision.REVIEW_REQUIRED, "MRZ_CHECK_DIGITS_FAILED"
        )  # on an automatic route nobody reviews, so an unreliable reading asks for a retake  # blocks automatic approval and is explained to the applicant
    if s.optical_matches_chip == "MISMATCH":
        lower(
            Decision.REVIEW_REQUIRED, "PRINTED_DATA_DIFFERS_FROM_CHIP"
        )  # a hard exception: never approved automatically
    elif (
        on_chip_route
        and p.chip is Requirement.REQUIRED
        and p.optical is Requirement.REQUIRED
        and s.optical_matches_chip == "UNKNOWN"
        and not reasons
    ):
        lower(Decision.INDETERMINATE, "PRINTED_DATA_NOT_COMPARED_WITH_CHIP")

    # --- the capture
    if s.injection_risk is True:
        lower(Decision.REVIEW_REQUIRED, "CAPTURE_INJECTION_RISK")
    elif s.injection_risk is None and p.capture_integrity is Requirement.REQUIRED:
        lower(Decision.INDETERMINATE, "CAPTURE_INTEGRITY_NOT_ESTABLISHED")
    # --- the document's own date, by the market's policy and no universal rule
    if s.document_expired is True:
        if p.expired_document == "REFUSE":
            lower(Decision.REJECTED, "DOCUMENT_EXPIRED")
        elif p.expired_document == "REVIEW":
            lower(Decision.REVIEW_REQUIRED, "DOCUMENT_EXPIRED_POLICY_REVIEW")
    elif s.document_expired is None and p.expired_document != "ACCEPT" and not face_route:
        lower(Decision.INDETERMINATE, "DOCUMENT_DATE_NOT_ESTABLISHED")

    # --- the person: presence first, a match only after a viable live capture
    if p.face is not Requirement.NOT_USED and not face_route:
        if s.liveness is Liveness.FAIL:
            lower(
                Decision.REVIEW_REQUIRED, "PRESENTATION_ATTACK_DETECTED"
            )  # high similarity does not repair it
        elif s.liveness is Liveness.INCONCLUSIVE:
            lower(Decision.REVIEW_REQUIRED, "LIVENESS_INCONCLUSIVE")
        elif s.liveness is Liveness.NOT_RUN:
            lower(
                Decision.INDETERMINATE if p.face is Requirement.REQUIRED else Decision.APPROVED,
                "LIVENESS_NOT_RUN" if p.face is Requirement.REQUIRED else "",
            )
        if s.liveness is Liveness.PASS:
            if s.face is FaceMatch.NON_MATCH:
                lower(
                    Decision.REVIEW_REQUIRED, "FACE_DOES_NOT_MATCH_REFERENCE"
                )  # a person decides: no automatic rejection on a biometric score
            elif s.face is FaceMatch.INCONCLUSIVE:
                lower(Decision.REVIEW_REQUIRED, "FACE_MATCH_INCONCLUSIVE")
            elif s.face is FaceMatch.NOT_RUN and p.face is Requirement.REQUIRED:
                lower(Decision.INDETERMINATE, "FACE_MATCH_NOT_RUN")
            elif (
                s.face is FaceMatch.MATCH
                and s.reference_source in ("NONE", "CHIP_DG2_UNTRUSTED")
                and p.face is Requirement.REQUIRED
            ):
                lower(Decision.REVIEW_REQUIRED, "FACE_REFERENCE_NOT_AUTHENTIC")
    reasons = [r for r in reasons if r]
    if worst is Decision.APPROVED:
        return out(Decision.APPROVED, *reasons)
    return out(worst, *reasons)

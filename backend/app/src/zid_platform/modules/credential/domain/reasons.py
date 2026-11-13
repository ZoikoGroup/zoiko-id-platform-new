"""Plain-language reasons for credential outcomes (ZID-GAP-001 v2.0 GID-059, GID-062). Pure domain.

Every reason code the credential rules can emit has a title, an explanation a person can read, and a next step. Rules:
- an outcome is never shown as a bare code: appeals and the holder see words (section 03.1, "no secret fraud-score-only denial");
- the words never say more than the rule did, and never accuse: "could not be confirmed" is not "false";
- codes and ids are locale-free; only the text is translated. English is the source text and the fallback, and `describe` reports
  which locale it actually used so a caller never presents English as if it were translated;
- an unknown code never raises and never invents a meaning: it gets a neutral text that asks for human review.
Dynamic codes (`<CLAIM>_MISSING`, `CREDENTIAL_<STATUS>`, `<FIELD>_NOT_<VALUE>`) are described by pattern.
"""

from __future__ import annotations

import re
from typing import Final

from zid_platform.modules.credential.domain import reasons_es, reasons_fr

DEFAULT_LOCALE: Final = "en"

CLAIM_NAMES = {
    "ACADEMIC_AWARD": "academic award",
    "PROFESSIONAL_LICENSE": "professional license",
    "INDUSTRY_CERTIFICATE": "certificate",
    "CPD_RECORD": "continuing education record",
    "EMPLOYMENT_ASSERTION": "employment record",
    "INSURANCE_ASSERTION": "insurance record",
    "DISCIPLINARY_FINDING": "regulator record",
}

# code -> (title, explanation, next step)
EN: dict[str, tuple[str, str, str]] = {
    # --- the decision
    "SOURCE_UNAVAILABLE": (
        "The source could not be reached",
        "The organization that holds this record did not answer. That says nothing about you.",
        "Try again later. No action is needed from you.",
    ),
    "NO_SOURCE_CAN_CONFIRM": (
        "No source can confirm this",
        "The organization that would hold this record no longer exists or cannot answer.",
        "You can send other evidence for a person to review.",
    ),
    "SOURCE_HAS_NO_RECORD": (
        "The source holds no record",
        "The source answered and has nothing under these details.",
        "Check the details you gave, or send other evidence for a person to review.",
    ),
    "PRIMARY_SOURCE_NOT_CONFIRMED": (
        "Not confirmed by the source",
        "A document or a signature was checked, but the organization that holds the record has not confirmed it. A document that looks right is not confirmation.",
        "A check with the source is needed.",
    ),
    "ISSUER_NOT_AUTHENTICATED": (
        "The issuer could not be confirmed",
        "We could not confirm that this organization is the one that issued it.",
        "Ask the issuer to provide it again, or request a person to review.",
    ),
    "SUBJECT_NOT_CHECKED": (
        "Nobody was compared",
        "The check stopped before the record was compared with you.",
        "No action is needed unless the reason above says otherwise.",
    ),
    "SUBJECT_MATCH_AMBIGUOUS": (
        "A person needs to decide",
        "The record could belong to you or to someone with a similar name. Nothing adverse is decided on a similar name.",
        "A reviewer will compare the details. You may add evidence.",
    ),
    "SUBJECT_NOT_MATCHED": (
        "The record is about someone else",
        "The record found does not match your details.",
        "If you think this is wrong, appeal and add evidence such as a legal name change.",
    ),
    "AWARD_STATUS_NOT_CONFERRED": (
        "The award was not conferred",
        "The institution records attendance or another status, not a conferred award. Attending is not the same as graduating.",
        "If you believe the award was conferred, appeal with your certificate number.",
    ),
    "ISSUER_LOOKALIKE": (
        "The issuer looks like a known one",
        "The name is close to a known organization but not the same. We do not guess.",
        "A person will check which organization it is.",
    ),
    "ISSUER_NOT_TRUSTED": (
        "The issuer is not on our trusted list",
        "We have no record that this organization may issue this kind of credential.",
        "Ask the issuer for a credential from a recognized source.",
    ),
    "CPD_REQUIREMENT_NOT_CONFIGURED": (
        "The requirement is not set up",
        "We cannot say whether this record meets the rule, because the rule has not been configured for this check.",
        "A person will review. No action is needed from you.",
    ),
    # --- receipts (the evidence record always carries a reason)
    "CLAIM_VERIFIED": (
        "The claim was confirmed",
        "The source confirmed this claim at the time shown.",
        "No action is needed.",
    ),
    "NO_REASON_RECORDED": (
        "No further reason on record",
        "This outcome has no more specific reason on record.",
        "A reviewer can add context if you appeal.",
    ),
    "STATUS_CHANGED_ACTIVE": (
        "The status changed to active",
        "The source now reports the credential as active.",
        "No action is needed.",
    ),
    # --- accreditation
    "NO_ACCREDITATION_RECORD": (
        "No accreditation record",
        "We have no accreditation record for this institution, so we cannot say whether the program was accredited. That is not a finding that it was not.",
        "A person may check the accreditation with the accrediting body.",
    ),
    "NOT_ACCREDITED_AT_AWARD_DATE": (
        "Not accredited when awarded",
        "The program was not covered by an accreditation on the date the award was made, even if it is accredited now.",
        "You may appeal with evidence of accreditation on the award date.",
    ),
    "UNKNOWN_HISTORICAL_ISSUER": (
        "We do not know this institution",
        "We have no recorded history for this institution at the time of the check, so we cannot say it was authorized to award this credential.",
        "A person may verify the institution with an official source.",
    ),
    "AUTHORITY_NOT_RECORDED": (
        "The institution's authority is not recorded",
        "We have no recorded legal or regulatory basis for this institution to issue this kind of credential.",
        "A person may check the institution's authority with an official source.",
    ),
    "ISSUER_REVOKED_AT_AWARD_DATE": (
        "The institution was no longer recognized",
        "The institution's recognition had been withdrawn by the date the award was made.",
        "You may appeal with evidence that it was recognized on the award date.",
    ),
    "NOT_TRUSTED_FOR_CLAIM_TYPE": (
        "The institution is not recorded for this kind of claim",
        "This institution is not recorded as an authority for this kind of credential.",
        "A person may check whether another source can confirm it.",
    ),
    # --- practice rules and insurance adequacy
    "QUALIFICATION_VERIFIED": (
        "The qualification is verified",
        "The qualification itself was checked and confirmed. That is separate from whether this person may practise this service here.",
        "No action is needed for the qualification.",
    ),
    "QUALIFICATION_NOT_ESTABLISHED": (
        "Qualification is not established",
        "No degree or certification check has confirmed a qualification for this engagement.",
        "Ask for the qualification to be checked, or provide the missing evidence.",
    ),
    "LICENSE_NOT_VERIFIED": (
        "Licence is not confirmed",
        "The licence check has not confirmed a current licence for this engagement.",
        "Wait for the licence check to finish, or provide the licence details again.",
    ),
    "ENGAGEMENT_PERIOD_ENDED": (
        "The engagement period has ended",
        "The period this engagement was checked for is over.",
        "Ask for a new decision for the new period.",
    ),
    "PRACTICE_ELIGIBILITY_NOT_ESTABLISHED": (
        "Permission to practise is not established",
        "We could not establish that this person may offer this service in this place, even though other checks passed.",
        "A person may review it, or you can provide the missing evidence.",
    ),
    "CONDITION_BLOCKS_SERVICE": (
        "A licence condition closes this service",
        "A condition on the licence means this service is not open to this person in this place.",
        "You may appeal with evidence that the condition no longer applies.",
    ),
    "CONDITION_NEEDS_REVIEW": (
        "A licence condition needs a person to look at it",
        "A condition on the licence needs to be reviewed before this service can go ahead.",
        "A reviewer will look at it; you may add context.",
    ),
    "CONDITION_NOT_CLASSIFIED": (
        "A licence condition is not recognized",
        "A condition on the licence is not one we have a rule for, so we cannot treat it as permitting this service.",
        "A reviewer will look at it.",
    ),
    "COVERAGE_LIMIT_NOT_REPORTED": (
        "The cover limit is not reported",
        "The insurance record does not state a coverage limit, so we cannot tell whether it is enough.",
        "Ask the insurer to confirm the limit.",
    ),
    "COVERAGE_CURRENCY_MISMATCH": (
        "The cover is in a different currency",
        "The cover limit is in a different currency from the one required. We do not convert between currencies.",
        "Provide cover in the required currency.",
    ),
    "COVERAGE_LIMIT_BELOW_MINIMUM": (
        "The cover limit is too low",
        "The cover limit is below the minimum required for this service.",
        "Increase the cover or provide additional cover.",
    ),
    "PRACTICE_CLASS_NOT_COVERED": (
        "The cover is for a different kind of practice",
        "The insurance record does not cover the kind of practice this service needs.",
        "Ask the insurer to confirm cover for this practice.",
    ),
    "COVER_BASIS_NOT_REPORTED": (
        "The cover basis is not reported",
        "The insurance record does not say whether it is claims-made or occurrence cover.",
        "Ask the insurer to confirm the basis of the cover.",
    ),
    "RETROACTIVE_DATE_MISSING": (
        "The retroactive date is missing",
        "Claims-made cover needs a retroactive date, and the record does not give one.",
        "Ask the insurer to confirm the retroactive date.",
    ),
    "RETROACTIVE_DATE_AFTER_WORK_START": (
        "The cover starts too late",
        "The retroactive date of this claims-made cover is after the work starts, so earlier work would not be covered.",
        "Provide cover with an earlier retroactive date.",
    ),
    # --- EU digital identity wallet (CR-080)
    "ACCESS_CERTIFICATE_MISSING": (
        "The request is not signed with a registered certificate",
        "A request to an EU digital identity wallet must be signed with the certificate registered for it, and this one was not.",
        "Zoiko iD must configure its registered certificate before asking the wallet.",
    ),
    "ACCESS_CERTIFICATE_MISMATCH": (
        "The request is signed with a different certificate",
        "The request is signed with a certificate other than the one registered for EU digital identity wallets.",
        "Zoiko iD must sign with its registered certificate.",
    ),
    "INTENDED_USE_NOT_REGISTERED": (
        "This use is not registered",
        "Zoiko iD is not registered to ask an EU digital identity wallet for information for this purpose.",
        "No action is needed from you; the request was not sent.",
    ),
    "ATTESTATION_TYPE_NOT_REGISTERED": (
        "This kind of credential is not registered",
        "Zoiko iD is not registered to ask an EU digital identity wallet for this kind of credential for this purpose.",
        "No action is needed from you; the request was not sent.",
    ),
    "PID_PROVIDER_NOT_LISTED": (
        "The identity data provider is not recognized",
        "The person identification data did not come from a provider we can confirm on an official list.",
        "Use another way to verify your identity.",
    ),
    "QEAA_STATUS_NOT_ESTABLISHED": (
        "Not a qualified attestation",
        "The issuer was not confirmed as a qualified provider for this attestation on the date it was issued, so it is treated as an ordinary attestation.",
        "No action is needed; the attestation is still read, at its proper level.",
    ),
    # --- standing
    "LICENSE_NOT_YET_EFFECTIVE": (
        "The license has not started",
        "The regulator reports a start date that has not arrived.",
        "Check again after the start date.",
    ),
    "LICENSE_PERIOD_ENDED": (
        "The license period has ended",
        "The regulator reports an end date that has passed.",
        "Renew with the regulator, then request a new check.",
    ),
    "NOT_IN_GOOD_STANDING": (
        "Not in good standing",
        "The regulator reports the license is not in good standing.",
        "Resolve it with the regulator, then request a new check.",
    ),
    "CONDITIONS_UNSPECIFIED": (
        "Conditions are not specified",
        "The regulator reports conditions but not what they are, so we cannot treat the license as clear.",
        "Ask the regulator for the conditions in writing.",
    ),
    "STANDING_NOT_REPORTED": (
        "Standing was not reported",
        "The regulator did not say whether the license is in good standing.",
        "Request a new check later.",
    ),
    "RENEWAL_OVERDUE": (
        "The renewal date has passed",
        "The certificate was due for renewal and has not been renewed.",
        "Renew it, then request a new check.",
    ),
    "PROGRAM_RETIRED_NO_RENEWAL": (
        "The program has been retired",
        "The certificate is genuine, but the program no longer issues renewals.",
        "No action is needed for a past award.",
    ),
    "EVIDENCE_NOT_SOURCE_CONFIRMED": (
        "Not confirmed by the provider",
        "A certificate was supplied but the provider has not confirmed it.",
        "Ask the provider to confirm it directly.",
    ),
    "PROVIDER_NOT_APPROVED": (
        "Provider not approved",
        "The record is kept, but this provider is not approved for the requirement.",
        "Choose an approved provider.",
    ),
    "CREDITS_UNREADABLE": (
        "Credits could not be read",
        "The number of credits was not in a form we can use.",
        "Send the record again.",
    ),
    "CREDITS_BELOW_REQUIREMENT": (
        "Not enough credits yet",
        "The record shows fewer credits than the requirement.",
        "Complete the remaining credits, then request a new check.",
    ),
    "NO_RECORD_AT_THIS_SOURCE": (
        "Nothing found at this regulator",
        "This regulator holds no adverse record. That says nothing about other regulators.",
        "No action is needed.",
    ),
    "FINDING_NOT_SOURCE_CONFIRMED": (
        "The finding is not confirmed",
        "A possible finding was not confirmed by the regulator, so nothing adverse is decided.",
        "A person will review.",
    ),
    "APPEAL_PENDING": (
        "The finding is under appeal",
        "The regulator reports an appeal is in progress, so this is not treated as final.",
        "A person will review. You may add evidence.",
    ),
    "FINDING_NOT_FINAL": (
        "The finding is not final",
        "The regulator's decision has not become final.",
        "A person will review.",
    ),
    # --- insurance
    "INSURED_ENTITY_MISMATCH": (
        "The cover is for someone else",
        "The policy names another person or a firm, not you. A firm's policy is not a personal policy.",
        "Provide a policy in your name, or one that names you.",
    ),
    "TERRITORY_NOT_COVERED": (
        "The place is not covered",
        "The policy does not cover the place where the work would be done.",
        "Extend the cover or choose a covered place.",
    ),
    "COVER_ENDS_BEFORE_PERIOD": (
        "The cover ends too soon",
        "The policy ends before the period of work.",
        "Renew the cover, then request a new check.",
    ),
    "WORK_NOT_COVERED": (
        "The work is not covered",
        "The policy does not list this kind of work.",
        "Ask your insurer to add it.",
    ),
    "WORK_EXCLUDED": (
        "The work is excluded",
        "The policy specifically excludes this work.",
        "Choose different work or a different policy.",
    ),
    # --- eligibility and engagement
    "POLICY_DOES_NOT_MATCH_ENGAGEMENT": (
        "This is not the place the rules were made for",
        "Eligibility is decided for one place and one service. This decision was for another.",
        "Request a decision for this exact place and service.",
    ),
    "IDENTITY_NOT_VERIFIED": (
        "Identity is not verified",
        "Identity verification is a separate step and is not complete.",
        "Complete identity verification.",
    ),
    "CREDENTIAL_SIGNATURE_NOT_VALID": (
        "The credential's signature did not check out",
        "The credential was changed after it was signed, or it was not signed by the key its issuer published.",
        "Ask the issuer for a fresh credential. Do not rely on this one.",
    ),
    "CREDENTIAL_TYPE_NOT_ACCEPTED_AS_EVIDENCE_OF_IDENTITY": (
        "This kind of credential is not accepted as proof of who someone is",
        "The credential may be genuine, but its type is not on the list of credentials accepted as identity evidence.",
        "Present an accepted identity credential, or use another way of verifying identity.",
    ),
    "CREDENTIAL_STATUS_NOT_CONFIRMED": (
        "The credential's status could not be confirmed",
        "The issuer's status information was missing or could not be read, so it is not clear the credential is still valid.",
        "Try again later, or present a credential whose issuer publishes its status.",
    ),
    "BIRTH_DATE_NOT_A_VALID_DATE": (
        "The birth date in the credential is not a valid date",
        "The credential's birth date is missing its proper form, falls in the future or is implausibly old.",
        "Ask the issuer to correct the credential.",
    ),
    "RESTRICTION_ON_RECORD": (
        "A restriction is on record",
        "A confirmed, final restriction applies.",
        "You may appeal with evidence.",
    ),
    "INSURANCE_NOT_SATISFIED": (
        "Insurance does not meet the requirement",
        "The cover on file does not meet this engagement's needs.",
        "See the insurance reasons for what to fix.",
    ),
    "INSURANCE_NOT_CHECKED": (
        "Insurance has not been checked",
        "No verified insurance check is on file.",
        "Add an insurance check.",
    ),
    "DISCIPLINARY_REVIEW_PENDING": (
        "A regulator record is being reviewed",
        "A regulator record is under appeal or not yet resolved, so the engagement is held for a person.",
        "No action is needed. A person will decide.",
    ),
    # --- signed credentials
    "PROOF_INVALID": (
        "The signature does not check out",
        "The credential's signature could not be verified.",
        "Ask the issuer to provide it again.",
    ),
    "SEAL_INVALID": (
        "The seal does not check out",
        "The electronic seal on this credential could not be verified.",
        "Ask the issuer for a valid copy.",
    ),
    "ISSUER_NOT_IN_REGISTRY": (
        "A genuine signature from an unrecognized issuer",
        "The credential is unaltered, but we do not recognize this organization as a source for this kind of credential.",
        "A recognized source is needed.",
    ),
    "HOLDER_BINDING_FAILED": (
        "Not shown to be yours",
        "The credential was not shown to belong to the person presenting it. A copied credential is not accepted.",
        "Present it again from the wallet it was issued to.",
    ),
    "STATUS_UNKNOWN": (
        "The status could not be read",
        "We could not check whether the credential is still valid.",
        "Try again later.",
    ),
    "NOT_YET_VALID": (
        "Not valid yet",
        "The credential's start date has not arrived.",
        "Try again after the start date.",
    ),
    "PAST_VALID_UNTIL": (
        "The credential has expired",
        "The credential's end date has passed.",
        "Ask for a renewed credential.",
    ),
    # --- name matching
    "MATCHED_VIA_FORMER_NAME_WITH_EVIDENCE": (
        "Matched through a former name",
        "The record is in a former name, and a name-change record is on file.",
        "No action is needed.",
    ),
    "FORMER_NAME_WITHOUT_CHANGE_EVIDENCE": (
        "A former name needs evidence",
        "The record is in a former name but there is no name-change record.",
        "Add evidence of the change, such as a marriage or court record.",
    ),
    "NAME_PARTIAL": (
        "The name only partly matches",
        "The names are similar but not close enough to be sure.",
        "A person will compare the details.",
    ),
    "SCRIPT_NOT_COMPARABLE": (
        "A person needs to compare the names",
        "One of the names is written in a script that cannot be compared automatically. That is never treated as a different person.",
        "A reviewer will compare the names. You may add a document that shows both spellings.",
    ),
    "NAME_DIFFERENT": (
        "The names are different",
        "The record has a different name.",
        "If this is wrong, appeal and add evidence.",
    ),
    "BIRTH_YEAR_CONFLICT": (
        "The year of birth differs",
        "The name matches but the year of birth does not, so nothing is decided.",
        "A person will review. You may add evidence.",
    ),
    # --- issuer outreach
    "ISSUER_DENIED": (
        "The issuer says it did not issue this",
        "The organization replied that it did not issue this credential.",
        "You may appeal with evidence.",
    ),
    "ISSUER_NOT_HELD": (
        "The issuer holds no such record",
        "The organization replied that it has no such record.",
        "Check the details, or send other evidence.",
    ),
    "ISSUER_DID_NOT_ANSWER": (
        "The issuer did not answer in time",
        "We asked the organization and heard nothing before the deadline. That says nothing about you.",
        "You may send other evidence for a person to review.",
    ),
    "RESPONSE_REJECTED_BY_REVIEW": (
        "The answer was not accepted",
        "The reply we received did not pass review, so it was not used.",
        "No action is needed. We may ask the issuer again.",
    ),
    "SIGNED_DOCUMENT_CHAINS_TO_A_CONFIGURED_ANCHOR": (
        "The document is signed by a certificate we trust",
        "The signature is sound and nothing was changed after it. That shows the document is as it was signed. It does not show that the person who signed was entitled to sign for this purpose.",
        "A person decides whether the signatory had the authority needed.",
    ),
    "NO_LEGAL_ENTITY_IDENTIFIER_GIVEN": (
        "No company identifier was given",
        "No legal entity identifier was supplied, so nothing was checked about the company itself.",
        "Add the company's identifier if you want it checked.",
    ),
    "LEI_MALFORMED": (
        "The company identifier is not in the right form",
        "A legal entity identifier has 20 letters and digits. This one does not.",
        "Check the identifier and send it again.",
    ),
    "LEI_CHECK_DIGITS_INVALID": (
        "The company identifier has a typing mistake",
        "The last two digits of a legal entity identifier prove it was copied correctly. These do not match.",
        "Check the identifier against the company's own records and send it again.",
    ),
    "LEI_WELL_FORMED_EXISTENCE_NOT_LOOKED_UP_HERE": (
        "The company identifier is in the right form",
        "It looks like a valid identifier, but this check did not look the company up, so it does not show the company exists or is active.",
        "Use the organization check to look the company up on an official register.",
    ),
    "RIGHT_TO_RENT_REQUIRES_A_REGISTERED_PROVIDER": (
        "A registered provider is needed for this check",
        "In England the digital Right to Rent check must be done by a provider that is registered for it. This service is not that provider.",
        "Use a registered provider for the check. Your own duties as a landlord are unchanged.",
    ),
    "NO_RULE_FOR_THIS_JURISDICTION": (
        "There is no rule for this place here",
        "A rule from another place is never applied by default, so nothing was decided for this location.",
        "A person will look at what applies where the property is.",
    ),
    "IDENTITY_IS_CHECKED_BY_THE_IDENTITY_AND_CREDENTIAL_ROUTES": (
        "Identity is checked somewhere else",
        "This check does not look at who the person is. Identity has its own check.",
        "Run the identity check, then use its result alongside this one.",
    ),
}

LOCALES: dict[str, dict[str, tuple[str, str, str]]] = {
    DEFAULT_LOCALE: EN,
    "es": reasons_es.TEXT,
    "fr": reasons_fr.TEXT,
}
# Review record per locale. English is the source. A locale becomes REVIEWED ONLY after a native-speaker and legal review, and the record
# then carries who reviewed it and on what date: `{"status": "REVIEWED", "reviewer": "...", "reviewed_on": "YYYY-MM-DD"}`. Until then
# `describe` says MACHINE_DRAFT_UNREVIEWED so no screen presents a draft as reviewed. A test refuses a REVIEWED entry with no reviewer or
# date, and a DRAFT entry that names one.
LOCALE_REVIEWS: dict[str, dict[str, str]] = {
    DEFAULT_LOCALE: {"status": "SOURCE"},
    "es": {"status": "DRAFT"},
    "fr": {"status": "DRAFT"},
}
REVIEWED_LOCALES: frozenset[str] = frozenset(
    loc for loc, r in LOCALE_REVIEWS.items() if r["status"] in {"SOURCE", "REVIEWED"}
)
_STATUS_TEXT = {
    "SUSPENDED": ("suspended", "has been suspended"),
    "REVOKED": ("revoked", "has been revoked"),
    "EXPIRED": ("expired", "has expired"),
    "UNKNOWN": ("not confirmed active", "could not be confirmed as active"),
}
_SUFFIX = {
    "MISSING": ("is missing", "There is no {what} on file.", "Add a {what} check."),
    "NOT_VERIFIED": (
        "is not verified",
        "The {what} on file is not verified.",
        "See the reasons for that check.",
    ),
    "EXPIRED": (
        "has expired",
        "The {what} on file has expired.",
        "Renew it and request a new check.",
    ),
    "STALE": (
        "is out of date",
        "The {what} was checked too long ago to rely on.",
        "Request a new check.",
    ),
    "WRONG_JURISDICTION": (
        "is for another place",
        "The {what} on file is for a different place than the one needed.",
        "Provide one for the right place.",
    ),
    "SERVICE_NOT_PERMITTED": (
        "does not cover this service",
        "The {what} on file does not allow this service.",
        "Provide one that covers the service.",
    ),
}
_NEUTRAL = (
    "Needs a person to look at it",
    "This result has a reason we cannot describe automatically.",
    "A reviewer will look at it. You may appeal.",
)


def _claim_and_rest(code: str) -> tuple[str, str] | None:
    for claim in sorted(CLAIM_NAMES, key=len, reverse=True):
        if code.startswith(claim + "_"):
            return claim, code[len(claim) + 1 :]
    return None


def describe(code: str, locale: str = DEFAULT_LOCALE) -> dict[str, object]:
    """Words for one reason code. Never raises. `known` says whether the code had its own text; `locale_used` says which
    language the text is really in (English when the asked-for locale has no entry for it)."""
    table = LOCALES.get(locale.split("-")[0].lower())
    used = locale.split("-")[0].lower() if table else DEFAULT_LOCALE
    entry = (table or EN).get(code) or (EN.get(code) if table else None)
    if entry is not None and table is not None and code not in table:
        used = DEFAULT_LOCALE
    known = entry is not None
    if entry is None:
        m = re.fullmatch(r"CREDENTIAL_([A-Z]+)", code)
        if m and m.group(1) in _STATUS_TEXT:
            noun, verb = _STATUS_TEXT[m.group(1)]
            entry = (
                f"The credential is {noun}",
                f"The source reports the credential {verb}.",
                "Resolve it with the issuer, then request a new check.",
            )
            known, used = True, DEFAULT_LOCALE
    if entry is None and (parts := _claim_and_rest(code)):
        claim, rest = parts
        sfx = _SUFFIX.get(rest)
        if sfx:
            what = CLAIM_NAMES[claim]
            entry = (f"The {what} {sfx[0]}", sfx[1].format(what=what), sfx[2].format(what=what))
            known, used = True, DEFAULT_LOCALE
        elif rest.startswith("RECOGNITION_"):
            entry = (
                "Recognition needs a decision",
                "This award was earned elsewhere. Whether it counts here is decided by the competent body, never assumed.",
                "A person will review the recognition rules for this pair of places.",
            )
            known, used = True, DEFAULT_LOCALE
    if entry is None and (m2 := re.fullmatch(r"([A-Z]+)_NOT_([A-Z_]+)", code)):
        entry = (
            f"The {m2.group(1).lower()} did not meet the requirement",
            f"The source did not report {m2.group(1).lower()} as one of: {m2.group(2).replace('_OR_', ', ').lower()}.",
            "A person will review. You may appeal with evidence.",
        )
        known, used = True, DEFAULT_LOCALE
    title, explanation, step = entry or _NEUTRAL
    shown = used if known else DEFAULT_LOCALE
    review = LOCALE_REVIEWS.get(shown, {})
    out_review: dict[str, str] = (
        {"reviewed_by": review["reviewer"], "reviewed_on": review["reviewed_on"]}
        if review.get("status") == "REVIEWED"
        else {}
    )
    return {
        **out_review,
        "code": code,
        "title": title,
        "explanation": explanation,
        "next_step": step,
        "known": known,
        "locale_used": used if known else DEFAULT_LOCALE,
        "translation": "SOURCE"
        if (used if known else DEFAULT_LOCALE) == DEFAULT_LOCALE
        else ("REVIEWED" if used in REVIEWED_LOCALES else "MACHINE_DRAFT_UNREVIEWED"),
    }


def describe_all(codes: list[str], locale: str = DEFAULT_LOCALE) -> list[dict[str, object]]:
    return [describe(c, locale) for c in codes]

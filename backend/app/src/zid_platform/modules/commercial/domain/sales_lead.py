"""Public sales inquiries (ZID-UX-PRICE-004 WF-P07; QA P28, P38, P39). Pure.

A lead is a request for a conversation. It creates no tenant, entitlement, contract, payment customer or authority mandate, and it never carries identity evidence: no
number that looks like an identity or document number, no biometric or document upload. The form asks for the least that lets Sales respond, records which privacy notice
the person saw, and refuses a robot (a hidden field that must stay empty). A repeat of an open inquiry returns the same reference instead of a second record.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

KINDS = ("ENTERPRISE", "PARTNER")
PARTNER_TYPES = ("RESELLER", "REFERRAL", "EMBEDDED_ISV")
VOLUME_BANDS = ("UNDER_1K", "FROM_1K_TO_10K", "FROM_10K_TO_100K", "OVER_100K", "UNSURE")
# the order a lead moves in; a lead can also be closed or marked spam from any open state, and nothing moves backwards
FLOW = ("RECEIVED", "QUALIFIED", "SCOPED", "REVIEW", "OFFER_SENT")
TERMINAL = ("CLOSED", "SPAM")

_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(\.[A-Za-z0-9\-]{1,63}){1,5}$")
_DIGIT_RUN = re.compile(
    r"\d[\d\s\-]{7,}\d"
)  # nine or more digits, however spaced: an identity, passport, account or card number
_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)


class LeadError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Lead:
    kind: str
    contact_name: str
    work_email: str
    company: str
    country: str
    region: str | None
    capabilities: tuple[str, ...]
    volume_band: str
    entities: int | None
    referral_source: str | None
    security_needs: str | None
    partner_type: str | None
    notice_version: str


def _text(value: str | None, label: str, lo: int, hi: int, *, required: bool = True) -> str | None:
    s = (value or "").strip()
    if not s:
        if required:
            raise LeadError("FIELD_MISSING", label)
        return None
    if not lo <= len(s) <= hi:
        raise LeadError("FIELD_LENGTH", f"{label}: {lo} to {hi} characters")
    if _DIGIT_RUN.search(s):
        raise LeadError(
            "IDENTITY_NUMBER_IN_TEXT",
            f"{label} must not contain an identity, document or account number",
        )
    if _LINK.search(s):
        raise LeadError("LINK_IN_TEXT", f"{label} must not contain a link")
    return s


def check_lead(
    *,
    kind: str,
    contact_name: str,
    work_email: str,
    company: str,
    country: str,
    region: str | None,
    capabilities: list[str],
    volume_band: str,
    entities: int | None,
    referral_source: str | None,
    security_needs: str | None,
    partner_type: str | None,
    notice_version: str,
    honeypot: str | None,
    known_families: set[str],
) -> Lead:
    """Validate and normalise one inquiry, or raise naming the first problem."""
    if honeypot:  # a person never sees this field
        raise LeadError("SPAM_SUSPECTED")
    if kind not in KINDS:
        raise LeadError("KIND_UNKNOWN", kind)
    if kind == "PARTNER" and partner_type not in PARTNER_TYPES:
        raise LeadError("PARTNER_TYPE_REQUIRED")
    if kind == "ENTERPRISE" and partner_type is not None:
        raise LeadError("PARTNER_TYPE_NOT_ALLOWED")
    email = (work_email or "").strip().lower()
    if not _EMAIL.match(email) or len(email) > 254:
        raise LeadError("EMAIL_INVALID")
    if not re.match(r"^[A-Z]{2}$", country or ""):
        raise LeadError("COUNTRY_INVALID")
    if volume_band not in VOLUME_BANDS:
        raise LeadError("VOLUME_BAND_INVALID", volume_band)
    if entities is not None and not 1 <= entities <= 10_000:
        raise LeadError("ENTITIES_INVALID")
    unknown = sorted({c for c in capabilities if c not in known_families})
    if unknown or len(set(capabilities)) > 16:
        raise LeadError("CAPABILITY_UNKNOWN", ",".join(unknown))
    if not (notice_version or "").strip():
        raise LeadError(
            "NOTICE_VERSION_REQUIRED", "the privacy notice the person saw must be recorded"
        )
    return Lead(
        kind=kind,
        contact_name=_text(contact_name, "name", 2, 120) or "",
        work_email=email,
        company=_text(company, "company", 2, 160) or "",
        country=country,
        region=_text(region, "region", 2, 40, required=False),
        capabilities=tuple(sorted(set(capabilities))),
        volume_band=volume_band,
        entities=entities,
        referral_source=_text(referral_source, "referral source", 2, 80, required=False),
        security_needs=_text(security_needs, "security needs", 2, 500, required=False),
        partner_type=partner_type,
        notice_version=notice_version.strip()[:40],
    )


def dedupe_key(lead: Lead) -> str:
    """One open inquiry per person, company and kind: the same three fields always give the same key."""
    raw = f"{lead.work_email}|{lead.company.lower()}|{lead.kind}"
    return hashlib.sha256(raw.encode()).hexdigest()


def next_states(current: str) -> tuple[str, ...]:
    """Where a lead may go from here: the next step in the flow, or closed or spam. A closed or spam lead goes nowhere."""
    if current in TERMINAL:
        return ()
    i = FLOW.index(current)
    forward = (FLOW[i + 1],) if i + 1 < len(FLOW) else ()
    return (*forward, *TERMINAL)

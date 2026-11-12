"""Typed shapes of the publication registers P01..P05, the answer library and the intents (ZI-CHAT-CSR-008, CONTENT-005).

All content is content-as-code (YAML under `backend/assistant/content/`) and is compiled into ONE immutable, digest-pinned
snapshot. Nothing here is approved by existing: every entry starts `DRAFT_NOT_PUBLISHED` / `EVIDENCE_PENDING` and only a signed
snapshot with PUBLISHED / RELEASED entries can make a factual claim (INV-02).
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

LOCALE_DEFAULT = "en-US"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------------------------- states
class ContentState(StrEnum):
    DRAFT_NOT_PUBLISHED = "DRAFT_NOT_PUBLISHED"
    REVIEWED = "REVIEWED"
    APPROVED_UNPUBLISHED = "APPROVED_UNPUBLISHED"
    PUBLISHED = "PUBLISHED"
    SUSPENDED = "SUSPENDED"
    WITHDRAWN = "WITHDRAWN"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"


class CapabilityStatus(StrEnum):
    EVIDENCE_PENDING = "EVIDENCE_PENDING"
    REVIEWED = "REVIEWED"
    APPROVED_UNPUBLISHED = "APPROVED_UNPUBLISHED"
    RELEASED = "RELEASED"
    PILOT = "PILOT"
    SUSPENDED = "SUSPENDED"
    WITHDRAWN = "WITHDRAWN"
    SUPERSEDED = "SUPERSEDED"


class LinkState(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    EXPIRED = "EXPIRED"


class Route(StrEnum):
    """How an intent is answered (ZI-CHAT-KB-002 section 04)."""

    DETERMINISTIC = "DETERMINISTIC"  # a fixed, approved answer entry
    RAG = "RAG"  # constrained retrieval + (optional) drafted explanation, always verified
    REFUSE = "REFUSE"
    ESCALATE = "ESCALATE"
    CLARIFY = "CLARIFY"


# ---------------------------------------------------------------------------------------------- P01..P05
class Capability(_Strict):  # P01
    capability_id: str = Field(pattern=r"^CAP-[A-Z0-9-]+$")
    display_name: str
    status: CapabilityStatus = CapabilityStatus.EVIDENCE_PENDING
    public_eligibility: str = "NOT_PUBLIC"  # NOT_PUBLIC | PUBLIC
    market_codes: tuple[str, ...] = ()  # ISO 3166-1 alpha-2; empty = nowhere
    audience_codes: tuple[str, ...] = ()
    protocols: tuple[str, ...] = ()
    release_commit: str | None = None
    test_evidence_refs: tuple[str, ...] = ()
    approver: str | None = None
    effective_from: date | None = None
    expires_at: date | None = None


class SourceSpan(_Strict):
    span_id: str = Field(pattern=r"^s[0-9]+$")
    text: str = Field(min_length=1, max_length=2000)


class Source(_Strict):  # P02
    source_id: str = Field(pattern=r"^[A-Z]+-[0-9]+$")
    title: str
    canonical_url: str  # informational; user-facing links go through P03 link ids only
    locale: str = LOCALE_DEFAULT
    state: ContentState = ContentState.DRAFT_NOT_PUBLISHED
    revision: str = "r0"
    spans: tuple[SourceSpan, ...] = ()
    claim_ids: tuple[str, ...] = ()
    eligible_capability_ids: tuple[str, ...] = ()
    review_due_at: date | None = None
    owner: str | None = None


class Destination(_Strict):  # P03
    link_id: str = Field(pattern=r"^[A-Z0-9-]+$")
    label: str
    url: str  # https only (or an http localhost origin in local/test); validated at load
    purpose: (
        str  # LEARN | DEVELOP | SALES | SUPPORT | PRIVACY | SECURITY | TRUST | SIGN_IN | VERIFY
    )
    state: LinkState = LinkState.ACTIVE
    owner: str | None = None
    secure_route: bool = False  # authenticated product flow: never opened with chat data


class ClaimKind(StrEnum):
    CONCEPT = "CONCEPT"  # general education, no product assertion
    PRODUCT = "PRODUCT"  # asserts what Zoiko iD does or supports: needs P01 RELEASED
    SENSITIVE = "SENSITIVE"  # certification, legal, residency, encryption: needs P04 approval
    COMMERCIAL = "COMMERCIAL"  # price, plan, SLA, region offer: needs P05 fact
    SECURE_ROUTE = "SECURE_ROUTE"  # account/identity operation: always deny and route (CL-08)


class ClaimClass(_Strict):  # P04
    claim_id: str = Field(pattern=r"^CL-[0-9]{2}$")
    name: str
    kind: ClaimKind
    approved: bool = False  # P04 sign-off for SENSITIVE classes
    approvers: tuple[str, ...] = ()


class CommercialFact(_Strict):  # P05
    fact_id: str
    statement: str
    market_codes: tuple[str, ...] = ()
    audience_codes: tuple[str, ...] = ()
    state: ContentState = ContentState.DRAFT_NOT_PUBLISHED
    approver: str | None = None


# ---------------------------------------------------------------------------------------------- answers, intents
class Answer(_Strict):
    answer_id: str = Field(pattern=r"^KB-[0-9]{3}$")
    question: str
    text: str = Field(min_length=1, max_length=1200)
    intent_id: str = Field(pattern=r"^INT-[0-9]{2}$")
    claim_ids: tuple[str, ...] = ()
    cta_link_id: str | None = None  # a P03 link id
    source_ids: tuple[str, ...] = ()  # P02 sources whose spans support this text
    capability_ids: tuple[
        str, ...
    ] = ()  # P01 capabilities this text asserts as available (normally none)
    # `asserts`: the text itself asserts the claims in claim_ids (e.g. "Zoiko iD supports X"). The default, False, is for the hedged and
    # explanatory answers of the launch library: their claim ids label the TOPIC, and no product/assurance/commercial fact is asserted.
    # An asserting answer needs every claim class eligible (P01/P04/P05) before it can be shown.
    asserts: bool = False
    state: ContentState = ContentState.DRAFT_NOT_PUBLISHED
    locale: str = LOCALE_DEFAULT
    match: tuple[str, ...] = ()  # extra trigger phrases for the deterministic matcher


class Intent(_Strict):
    intent_id: str = Field(pattern=r"^INT-[0-9]{2}$")
    family: str
    description: str
    route: Route
    priority: int = 50  # higher wins on ties; safety intents carry the highest
    keywords: tuple[str, ...] = ()  # any-of (lower-case, matched on word boundaries)
    phrases: tuple[str, ...] = ()  # exact substrings, lower-case
    refusal_key: str | None = None  # key into copy.yaml for REFUSE / ESCALATE routes
    cta_link_id: str | None = None


class CopyItem(_Strict):
    key: str
    text: str
    link_id: str | None = None


class StarterPrompt(_Strict):
    label: str
    text: str
    # Optional topic-tile fields (the widget's welcome screen). The icon is one of a fixed set, never markup.
    description: str | None = Field(default=None, max_length=90)
    icon: str | None = Field(
        default=None, pattern=r"^(lock|document|shield|share|code|building|user|chat)$"
    )


class Manifest(_Strict):
    snapshot_id: str
    version: str
    sample: bool = True  # engineering sample: never approved content
    locale_default: str = LOCALE_DEFAULT
    notes: str = ""

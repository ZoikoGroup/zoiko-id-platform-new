"""Portable credentials: consented disclosure and the public verifier view (ZID-GAP-001 v2.0 GID-053, GID-061). Pure domain.

A disclosure is a grant from a person to one recipient, for one purpose, through one consumer, for named fields, until a time.
- every term must match at the moment of disclosure; a withdrawn or expired grant discloses nothing, and neither does any grant
  derived from it (GLB-20);
- a grant cannot name a raw document, a birth date or a transcript: those are never disclosed through this path (GLB-27);
- a decision made for one consumer is not usable by another: Zoiko Rooms cannot read a Zoikorum professional decision (GLB-33);
- the public view is bounded: what the claim is, the decision, its tier, when it was checked and who issued it. No names, no dates
  of birth, no document, no free text.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

# fields that are never disclosed through a grant, whatever the grant says
NEVER_DISCLOSED = frozenset(
    {
        "raw_document",
        "document_image",
        "transcript",
        "transcript_ref",
        "date_of_birth",
        "birth_date",
        "birthdate",
        "national_id",
        "address",
        "email",
        "phone",
    }
)


class DisclosureError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Grant:
    grant_id: str
    subject: str
    recipient: str
    consumer: str
    purpose: str
    fields: frozenset[str]
    created_at: datetime
    expires_at: datetime
    withdrawn_at: datetime | None = None
    parent_id: str | None = None  # a grant derived from another (re-sharing) lives and dies with it


def new_grant(
    *,
    grant_id: str,
    subject: str,
    recipient: str,
    consumer: str,
    purpose: str,
    fields: Collection[str],
    created_at: datetime,
    expires_at: datetime,
    parent: Grant | None = None,
) -> Grant:
    banned = sorted(set(fields) & NEVER_DISCLOSED)
    if banned:
        raise DisclosureError("FIELD_NEVER_DISCLOSED", ",".join(banned))
    if not fields:
        raise DisclosureError("FIELDS_REQUIRED")
    if expires_at <= created_at:
        raise DisclosureError("GRANT_WINDOW_INVALID")
    if parent is not None:
        if parent.subject != subject:
            raise DisclosureError("PARENT_GRANT_OTHER_SUBJECT")
        if not set(fields) <= parent.fields or expires_at > parent.expires_at:
            raise DisclosureError("GRANT_WIDER_THAN_PARENT")  # a derived grant may only narrow
    return Grant(
        grant_id,
        subject,
        recipient,
        consumer,
        purpose,
        frozenset(fields),
        created_at,
        expires_at,
        None,
        parent.grant_id if parent else None,
    )


def withdraw(grant: Grant, now: datetime) -> Grant:
    return grant if grant.withdrawn_at is not None else replace(grant, withdrawn_at=now)


def in_force(grant: Grant, grants: Mapping[str, Grant], now: datetime) -> bool:
    """True only if the grant and every ancestor is unexpired and not withdrawn. A parent we cannot see, or a cycle,
    is corrupt data: nothing is in force."""
    seen: set[str] = set()
    cur = grant
    while True:
        if cur.grant_id in seen:
            return False
        seen.add(cur.grant_id)
        if cur.withdrawn_at is not None and cur.withdrawn_at <= now:
            return False
        if now >= cur.expires_at or now < cur.created_at:
            return False
        if cur.parent_id is None:
            return True
        parent = grants.get(cur.parent_id)
        if parent is None:
            return False
        cur = parent


def disclose(
    grant: Grant,
    grants: Mapping[str, Grant],
    claim_fields: Mapping[str, Any],
    *,
    recipient: str,
    consumer: str,
    purpose: str,
    now: datetime,
) -> dict[str, Any]:
    """The granted fields of an assertion, for exactly this recipient, consumer and purpose, or a refusal with its code."""
    if not in_force(grant, grants, now):
        raise DisclosureError("GRANT_NOT_IN_FORCE")
    if consumer != grant.consumer:
        raise DisclosureError("CONSUMER_SCOPE_MISMATCH")
    if recipient != grant.recipient:
        raise DisclosureError("RECIPIENT_MISMATCH")
    if purpose != grant.purpose:
        raise DisclosureError("PURPOSE_MISMATCH")
    return {k: v for k, v in claim_fields.items() if k in grant.fields and k not in NEVER_DISCLOSED}


PUBLIC_FIELDS = (
    "claim_type",
    "decision",
    "credential_status",
    "assurance_tier",
    "source_checked_at",
    "issuer_ref",
    "policy_ref",
)


def public_view(assertion: Mapping[str, Any], *, revoked: bool = False) -> dict[str, Any]:
    """The bounded, privacy-safe verifier view. A superseded or revoked assertion says so instead of its old decision."""
    view = {k: assertion[k] for k in PUBLIC_FIELDS if k in assertion}
    if revoked:
        view["decision"] = "NO_LONGER_VALID"
    return view

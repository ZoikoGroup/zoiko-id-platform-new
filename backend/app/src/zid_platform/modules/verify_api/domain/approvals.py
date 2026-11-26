"""Two-person approval of a production credential (ZID-EXT-IDV-001 WF-05, section 05; CR-162). Pure rules, no I/O.

A production credential is created, or rotated, only with an approval: one company administrator asks, a DIFFERENT one approves, and the approval is spent once, within a day, on exactly what it names (the
application, the action, the credential being rotated, and for a private-key credential the fingerprint of the exact public key). The person who asked cannot approve, and an approval cannot be reused or moved
to another application or key. Sandbox applications need none of this.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from zid_platform.modules.verify_api.domain.model import PartnerError

APPROVAL_TTL = timedelta(hours=24)
NOTE_MAX = 1000


class Action(StrEnum):
    ISSUE = "ISSUE"
    ROTATE = "ROTATE"


class Status(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CONSUMED = "CONSUMED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


def check_action(value: str | None) -> Action:
    try:
        return Action(str(value).upper())
    except ValueError as e:
        raise PartnerError("VALIDATION_FAILED", "Choose ISSUE or ROTATE.") from e


def check_shape(action: Action, *, credential_id: str | None) -> None:
    """A rotation names the credential it replaces; a new credential names none."""
    if action is Action.ROTATE and not credential_id:
        raise PartnerError("VALIDATION_FAILED", "A rotation names the credential to rotate.")
    if action is Action.ISSUE and credential_id:
        raise PartnerError("VALIDATION_FAILED", "A new credential does not name one to rotate.")


def key_digest(public_jwk: dict[str, Any] | None) -> bytes | None:
    """A fingerprint of the exact key an approval is for, in one fixed spelling. None for a secret credential."""
    if public_jwk is None:
        return None
    return hashlib.sha256(
        json.dumps(public_jwk, sort_keys=True, separators=(",", ":")).encode()
    ).digest()


def check_note(note: str | None, *, required: bool) -> str:
    clean = " ".join((note or "").split())
    if len(clean) > NOTE_MAX:
        raise PartnerError("VALIDATION_FAILED", f"The note is longer than {NOTE_MAX} characters.")
    if required and len(clean) < 3:
        raise PartnerError("NOTE_REQUIRED", "Give a reason.")
    return clean


def check_decider(requester: str, decider: str) -> None:
    if requester == decider:
        raise PartnerError(
            "APPROVAL_SELF_DECISION",
            "Someone other than the person who asked must decide it. Ask another administrator.",
        )


def check_usable(
    row: dict[str, Any],
    *,
    principal: str,
    application_id: str,
    action: Action,
    credential_id: str | None,
    digest: bytes | None,
    now: datetime,
) -> None:
    """Whether an approval may be spent now, on exactly this. One answer for every way it can be wrong: it names what it was for, and nothing more."""
    bad = PartnerError(
        "APPROVAL_REQUIRED",
        "A production credential needs a second administrator's approval for exactly this: "
        "ask for one, have another administrator approve it, then use it within 24 hours.",
    )
    if row["status"] != Status.APPROVED.value or row["expires_at"] <= now:
        raise bad
    if str(row["requested_by"]) != principal or str(row["application_id"]) != application_id:
        raise bad
    if row["action"] != action.value:
        raise bad
    if (str(row["credential_id"]) if row["credential_id"] else None) != credential_id:
        raise bad
    if (bytes(row["key_digest"]) if row["key_digest"] is not None else None) != digest:
        raise bad

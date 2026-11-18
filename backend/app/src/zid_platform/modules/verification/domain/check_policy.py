"""A company's verification policy (ZID-DTV-WF-05 A11; CR-118). Pure domain, no I/O.

Per market and purpose: which checks every case must ask for, and which proof of address documents the company accepts and how old they may be.

Rules, each with a test:
- a policy is DRAFT, then SUBMITTED, then APPROVED or REJECTED; only an APPROVED policy has any effect ("values cannot become effective without signed
  controls", A11), and the approver is never the author ("policy author != policy approver");
- approving supersedes the earlier approved policy of the same market and purpose; history is kept;
- a policy only NARROWS the platform: a document type must be one the platform accepts for that market, and its age can only be shorter. A company
  cannot require a method the platform has not cleared: requiring it only means its cases ask for it (and say it is unavailable if it is not);
- requiring a method never switches it on: activation stays with the method lifecycle (A11: "customer admin can request, not unilaterally activate").
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from zid_platform.modules.verification.domain import check_methods as cm

COUNTRY = re.compile(r"^[A-Z]{2}$")
PURPOSE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
DOC_TYPE = re.compile(r"^[A-Z][A-Z_]{2,40}$")


class PolicyError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Status(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class Policy:
    policy_id: str
    market: str
    purpose: str
    required_methods: tuple[cm.CheckMethod, ...]
    document_types: Mapping[str, int]  # accepted type -> max age in days
    status: Status
    author: str
    created_at: datetime
    approver: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None


def draft(
    *,
    policy_id: str,
    market: str,
    purpose: str,
    required: list[cm.CheckMethod],
    document_types: Mapping[str, int],
    platform_types: Mapping[str, int] | None,
    author: str,
    now: datetime,
) -> Policy:
    """`platform_types` is the platform's accepted types and ages for the market (None when it accepts none)."""
    market = market.upper()
    if not COUNTRY.match(market):
        raise PolicyError("MARKET_INVALID", market)
    if not PURPOSE.match(purpose):
        raise PolicyError("PURPOSE_INVALID", purpose)
    if len(set(required)) != len(required):
        raise PolicyError("METHODS_DUPLICATED")
    for kind, days in document_types.items():
        if (
            not DOC_TYPE.match(kind)
            or not isinstance(days, int)
            or isinstance(days, bool)
            or days < 1
        ):
            raise PolicyError("DOCUMENT_TYPE_INVALID", kind)
        limit = (platform_types or {}).get(kind)
        if limit is None:
            raise PolicyError("DOCUMENT_TYPE_NOT_ACCEPTED_BY_PLATFORM", kind)
        if days > limit:
            raise PolicyError("DOCUMENT_AGE_ABOVE_PLATFORM", f"{kind}: at most {limit} days")
    if not author.strip():
        raise PolicyError("AUTHOR_REQUIRED")
    return Policy(
        policy_id, market, purpose, tuple(required), dict(document_types), Status.DRAFT, author, now
    )


def submit(p: Policy, *, by: str) -> Policy:
    if p.status is not Status.DRAFT:
        raise PolicyError("POLICY_NOT_DRAFT", p.status.value)
    if by != p.author:
        raise PolicyError("ONLY_AUTHOR_SUBMITS")
    return replace(p, status=Status.SUBMITTED)


def decide(p: Policy, *, approve: bool, by: str, reason: str, now: datetime) -> Policy:
    if p.status is not Status.SUBMITTED:
        raise PolicyError("POLICY_NOT_SUBMITTED", p.status.value)
    if by == p.author:
        raise PolicyError("APPROVER_IS_AUTHOR", "a different person approves a policy")
    if len(reason.strip()) < 10:
        raise PolicyError("REASON_REQUIRED", "at least 10 characters")
    return replace(
        p,
        status=Status.APPROVED if approve else Status.REJECTED,
        approver=by,
        decided_at=now,
        decision_reason=reason,
    )


def effective_types(platform: Mapping[str, int] | None, company: Policy | None) -> dict[str, int]:
    """The document types and ages in force: the platform's, narrowed by the company's approved policy when it names any."""
    base = dict(platform or {})
    if company is None or company.status is not Status.APPROVED or not company.document_types:
        return base
    return {k: min(v, base[k]) for k, v in company.document_types.items() if k in base}


def requested_with_policy(
    requested: list[cm.CheckMethod], company: Policy | None
) -> list[cm.CheckMethod]:
    """The checks a new case asks for: what the caller asked, plus what the approved policy requires (order kept, no duplicates)."""
    if company is None or company.status is not Status.APPROVED:
        return list(requested)
    return list(dict.fromkeys([*requested, *company.required_methods]))


__all__ = [
    "Policy",
    "PolicyError",
    "Status",
    "decide",
    "draft",
    "effective_types",
    "requested_with_policy",
    "submit",
]

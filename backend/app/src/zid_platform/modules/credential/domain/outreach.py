"""Issuer outreach and manual verification (ZID-GAP-001 v2.0 GID-054). Pure domain.

When no automatic source exists, Zoiko asks the issuer directly. The answer is only as good as its proof:
- a request goes to a contact REGISTERED for that issuer (registered out of band, not taken from the document or the holder);
- a response counts only if it comes from a registered contact, carries the request's nonce, is within the deadline, and its
  signature verifies against the issuer's registered key (a spoofed sender, a replayed or unsigned answer is refused, GLB-14);
- accepting a response needs two different reviewers, neither the person who raised the request (dual control);
- a request nobody answers EXPIRES at its deadline; that is `UNVERIFIABLE`, never a finding about the holder, and the holder can
  appeal or supply other evidence (GLB-13).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any


class OutreachError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class State(StrEnum):
    PENDING = "PENDING"
    RESPONDED = "RESPONDED"  # an authenticated answer waits for dual review
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class Request:
    request_id: str
    issuer_id: str
    claim_type: str
    nonce_hash: bytes  # only the hash is stored; the nonce travels in the request
    requested_by: str
    created_at: datetime
    deadline: datetime
    state: State = State.PENDING
    response_digest: bytes | None = None
    outcome: str | None = None  # CONFIRMED | DENIED | NOT_HELD, the issuer's answer, once accepted


@dataclass(frozen=True, slots=True)
class Response:
    sender: str  # the address or identity the answer arrived from
    nonce: str
    outcome: str  # CONFIRMED | DENIED | NOT_HELD
    payload: bytes  # the exact bytes the signature covers
    signature: bytes
    received_at: datetime


OUTCOMES = frozenset({"CONFIRMED", "DENIED", "NOT_HELD"})
Verifier = Callable[
    [bytes, bytes], bool
]  # (payload, signature) against the issuer's registered key


def nonce_hash(nonce: str) -> bytes:
    return hashlib.sha256(nonce.encode()).digest()


def open_request(
    *,
    request_id: str,
    issuer_id: str,
    claim_type: str,
    nonce: str,
    requested_by: str,
    now: datetime,
    deadline: datetime,
    registered_contacts: Collection[str],
) -> Request:
    if not registered_contacts:
        raise OutreachError(
            "NO_REGISTERED_CONTACT", issuer_id
        )  # never fall back to a contact taken from the document
    if deadline <= now:
        raise OutreachError("DEADLINE_INVALID")
    return Request(
        request_id, issuer_id, claim_type, nonce_hash(nonce), requested_by, now, deadline
    )


def receive(
    request: Request, response: Response, *, registered_contacts: Collection[str], verify: Verifier
) -> Request:
    """Authenticate an answer. A bad one raises and leaves the request exactly as it was (it stays PENDING)."""
    if request.state is not State.PENDING:
        raise OutreachError("REQUEST_NOT_PENDING", request.state.value)
    if response.received_at > request.deadline:
        raise OutreachError("RESPONSE_AFTER_DEADLINE")
    if response.sender.casefold() not in {c.casefold() for c in registered_contacts}:
        raise OutreachError("SENDER_NOT_REGISTERED")  # GLB-14
    if not hmac.compare_digest(nonce_hash(response.nonce), request.nonce_hash):
        raise OutreachError("NONCE_MISMATCH")
    if response.outcome not in OUTCOMES:
        raise OutreachError("OUTCOME_UNRECOGNIZED")
    if not verify(response.payload, response.signature):
        raise OutreachError("SIGNATURE_INVALID")
    return replace(
        request,
        state=State.RESPONDED,
        response_digest=hashlib.sha256(response.payload).digest(),
        outcome=response.outcome,
    )


def review(request: Request, approvals: Mapping[str, bool]) -> Request:
    """Dual control: two DIFFERENT reviewers, neither the requester, must approve; any rejection rejects."""
    if request.state is not State.RESPONDED:
        raise OutreachError("NOTHING_TO_REVIEW", request.state.value)
    if request.requested_by in approvals:
        raise OutreachError("REQUESTER_CANNOT_REVIEW")
    if any(not ok for ok in approvals.values()):
        return replace(request, state=State.REJECTED)
    if len(approvals) < 2:
        raise OutreachError("SECOND_REVIEWER_REQUIRED")
    return replace(request, state=State.ACCEPTED)


def expire(request: Request, now: datetime) -> Request:
    if request.state is State.PENDING and now > request.deadline:
        return replace(request, state=State.EXPIRED)
    return request


def verdict(request: Request) -> tuple[str, tuple[str, ...]]:
    """What the outreach means for the claim: an issuer's confirmation counts; silence is UNVERIFIABLE, not a denial."""
    if request.state is State.ACCEPTED and request.outcome == "CONFIRMED":
        return "CONFIRMED", ()
    if request.state is State.ACCEPTED and request.outcome in ("DENIED", "NOT_HELD"):
        return "NOT_CONFIRMED", (f"ISSUER_{request.outcome}",)
    if request.state is State.EXPIRED:
        return "UNVERIFIABLE", ("ISSUER_DID_NOT_ANSWER",)
    if request.state is State.REJECTED:
        return "UNVERIFIABLE", ("RESPONSE_REJECTED_BY_REVIEW",)
    return "PENDING", ()


def hmac_verifier(key: bytes) -> Verifier:
    """A verifier for issuers that registered a shared secret; issuers with a public key use their own Verifier."""

    def check(payload: bytes, signature: bytes) -> bool:
        return hmac.compare_digest(hmac.new(key, payload, hashlib.sha256).digest(), signature)

    return check


def summary(request: Request) -> dict[str, Any]:
    return {
        "request_id": request.request_id,
        "issuer_id": request.issuer_id,
        "state": request.state.value,
    }

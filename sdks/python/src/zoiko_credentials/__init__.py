"""Zoiko iD credential-trust client and consumer packs (ZID-GAP-001 v2.0 GID-063; CR-069).

One client, one API: the Zoikorum and Zoiko Rooms packs are thin wrappers over `CredentialsClient`, not separate stacks. A pack
can only NARROW what the client does; it never widens authorization, and the server enforces every rule regardless:
- a Zoikorum decision is signed for the consumer "zoikorum"; `verify_decision` checks the signature offline against pinned keys
  and the Rooms pack refuses it (a decision for one consumer is not usable by another);
- a disclosure grant made through a pack is always for that pack's consumer;
- every call names its scope; an answer is `data` of the platform's envelope, an error becomes `CredentialsError(code, status)`.

Standard library plus `cryptography` (through `zoiko_tool_server.verify_signed`). `transport` is injectable for tests.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from zoiko_tool_server import GrantError, verify_signed

__all__ = [
    "ASSERTION_TYP",
    "DECISION_TYP",
    "CredentialsClient",
    "CredentialsError",
    "RoomsPack",
    "ScopeError",
    "ZoikorumDecision",
    "ZoikorumPack",
    "assertion_digest",
    "verify_assertion",
]

DECISION_TYP = "zid-decision+jwt"
ASSERTION_TYP = "zid-assertion+jwt"
_NOT_PART_OF_THE_BODY = ("signature", "superseded")
CHECK_PATHS = {
    "academic": "academic",
    "license": "professional-licenses",
    "certification": "certifications",
    "employment": "experience",
    "cpd": "continuing-education",
    "insurance": "insurance",
}
Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, bytes]]


def assertion_digest(assertion: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical body of a credential assertion: sorted keys, no spaces, UTF-8, without `signature` and
    `superseded`. Identical to the server's digest (a pinned test vector keeps them equal)."""
    body = {k: v for k, v in assertion.items() if k not in _NOT_PART_OF_THE_BODY}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_assertion(assertion: Mapping[str, Any], jwks: Mapping[str, Any]) -> Mapping[str, Any]:
    """Check offline that an assertion is signed by the platform and that its body has not changed. Returns the assertion, or
    raises `ScopeError` (ASSERTION_UNSIGNED, ASSERTION_SIGNATURE_INVALID, ASSERTION_TAMPERED). Verifying the body, not just the
    signature, is the point: the signature covers a digest of every field."""
    token = assertion.get("signature")
    if not isinstance(token, str) or not token:
        raise ScopeError("ASSERTION_UNSIGNED")
    try:
        claims = verify_signed(token, jwks, typ=ASSERTION_TYP)
    except GrantError as e:
        raise ScopeError("ASSERTION_SIGNATURE_INVALID", str(e)) from e
    if (
        claims.get("typ") != "zid-assertion"
        or claims.get("check_id") != assertion.get("check_id")
        or claims.get("assertion_id") != assertion.get("assertion_id")
        or claims.get("body_sha256") != assertion_digest(assertion)
    ):
        raise ScopeError("ASSERTION_TAMPERED")
    return assertion


class CredentialsError(Exception):
    """The platform refused or failed a call. `code` is the platform's stable code (for example PACK_UNAVAILABLE)."""

    def __init__(self, code: str, status: int, message: str = "", retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.status, self.message, self.retryable = code, status, message, retryable


class ScopeError(Exception):
    """A pack was asked to do something outside its consumer's scope. Raised before any network call."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class CredentialsClient:
    def __init__(
        self, base_url: str, access_token: str, *, transport: Transport | None = None, timeout: float = 15.0
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = access_token
        self._transport = transport or _urllib_transport
        self._timeout = timeout

    # ------------------------------------------------------------------------------------------------ plumbing

    def _call(self, method: str, path: str, body: Mapping[str, Any] | None = None, *, auth: bool = True) -> Any:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        status, raw = self._transport(method, self._base + path, headers, data, self._timeout)
        try:
            doc = json.loads(raw) if raw else {}
        except ValueError:
            doc = {}
        if status >= 400 or doc.get("success") is False:
            err = doc.get("error") or {}
            raise CredentialsError(
                str(err.get("code", "HTTP_ERROR")),
                status,
                str(err.get("message", "")),
                bool(err.get("retryable", False)),
            )
        return doc.get("data", doc)

    # ------------------------------------------------------------------------------------------------ checks

    def check(
        self, kind: str, claim: Mapping[str, Any], jurisdiction: Mapping[str, str], **opts: Any
    ) -> dict[str, Any]:
        """Check a claim against a source. `kind` is one of academic, license, certification, employment, cpd, insurance."""
        if kind not in CHECK_PATHS:
            raise ScopeError("CLAIM_KIND_UNKNOWN", kind)
        body = {"claim": dict(claim), "jurisdiction": dict(jurisdiction), **opts}
        return self._call("POST", f"/v1/credentials/{CHECK_PATHS[kind]}/checks", body)  # type: ignore[no-any-return]

    def disciplinary(
        self, regulator: str, jurisdiction: Mapping[str, str], reviewer_principal_id: str, **opts: Any
    ) -> dict[str, Any]:
        """A sensitive check: it needs `consent_ref` or `legal_basis_ref` in `opts` and a reviewer other than you."""
        body = {
            "regulator": regulator,
            "jurisdiction": dict(jurisdiction),
            "reviewer_principal_id": reviewer_principal_id,
            **opts,
        }
        return self._call("POST", "/v1/credentials/disciplinary/checks", body)  # type: ignore[no-any-return]

    def verify_signed_credential(
        self, credential: str, holder_proof: str | None = None, jurisdiction: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"credential": credential}
        if holder_proof:
            body["holder_proof"] = holder_proof
        if jurisdiction:
            body["jurisdiction"] = jurisdiction
        return self._call("POST", "/v1/credentials/vc/verify", body)  # type: ignore[no-any-return]

    def assertion(self, check_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/credentials/assertions/{check_id}")  # type: ignore[no-any-return]

    # ------------------------------------------------------------------------------------------------ decisions

    def evaluate_eligibility(
        self, country: str, profession_code: str, service_code: str, subdivision: str | None = None
    ) -> dict[str, Any]:
        body = {"country": country, "profession_code": profession_code, "service_code": service_code}
        if subdivision:
            body["subdivision"] = subdivision
        return self._call("POST", "/v1/eligibility/professional/evaluate", body)  # type: ignore[no-any-return]

    def evaluate_engagement(
        self, country: str, profession_code: str, service_code: str, subdivision: str | None = None, **opts: Any
    ) -> dict[str, Any]:
        body = {"country": country, "profession_code": profession_code, "service_code": service_code, **opts}
        if subdivision:
            body["subdivision"] = subdivision
        return self._call("POST", "/v1/eligibility/engagement/evaluate", body)  # type: ignore[no-any-return]

    def packs(self, country: str, profession: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/credential-packs/{country}/{profession}")  # type: ignore[no-any-return]

    # ------------------------------------------------------------------------------------------------ disclosure

    def create_disclosure(
        self,
        recipient: str,
        consumer: str,
        purpose: str,
        fields: Sequence[str],
        days: int = 30,
        parent_grant_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "recipient": recipient,
            "consumer": consumer,
            "purpose": purpose,
            "fields": list(fields),
            "days": days,
        }
        if parent_grant_id:
            body["parent_grant_id"] = parent_grant_id
        return self._call("POST", "/v1/credentials/disclosures", body)  # type: ignore[no-any-return]

    def withdraw_disclosure(self, grant_id: str) -> dict[str, Any]:
        return self._call("POST", f"/v1/credentials/disclosures/{grant_id}:withdraw", {})  # type: ignore[no-any-return]

    def read_disclosure(
        self, grant_id: str, check_id: str, recipient: str, consumer: str, purpose: str
    ) -> dict[str, Any]:
        body = {"check_id": check_id, "recipient": recipient, "consumer": consumer, "purpose": purpose}
        return self._call("POST", f"/v1/credentials/disclosures/{grant_id}:read", body)  # type: ignore[no-any-return]

    def verify_public(self, tenant_id: str, check_id: str, grant: str) -> dict[str, Any]:
        """The bounded public view; needs no account, only a PUBLIC_VERIFIER grant the holder shared."""
        return self._call("GET", f"/v1/credentials/assertions/{tenant_id}.{check_id}/verify?grant={grant}", auth=False)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------------------------------------ cases

    def appeal(self, check_id: str, reason: str, correction: Mapping[str, str] | None = None) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/credentials/cases/{check_id}/appeals",
            {"reason": reason, "correction": dict(correction or {})},
        )  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------------------------------- Zoikorum


@dataclass(frozen=True, slots=True)
class ZoikorumDecision:
    decision_id: str
    engagement: str  # READY | HOLD | BLOCKED
    tier: str  # A | B | C | NONE
    dimensions: Mapping[str, str]  # five separate statuses, never one badge
    reason_codes: tuple[str, ...]
    consumer: str
    token: str  # the signed decision, to pass on as proof

    @property
    def ready(self) -> bool:
        """READY and nothing else. A tier or a public badge is never an authorization."""
        return self.engagement == "READY"


class ZoikorumPack:
    """Zoikorum's view: professional engagement decisions, signed and bound to the consumer "zoikorum"."""

    CONSUMER = "zoikorum"

    def __init__(self, client: CredentialsClient, jwks: Mapping[str, Any]) -> None:
        self._client = client
        self._jwks = jwks

    def decide(
        self, country: str, profession_code: str, service_code: str, subdivision: str | None = None, **opts: Any
    ) -> ZoikorumDecision:
        body: dict[str, Any] = {
            "country": country,
            "profession_code": profession_code,
            "service_code": service_code,
            **opts,
        }
        if subdivision:
            body["subdivision"] = subdivision
        out = self._client._call("POST", "/v1/consumers/zoikorum/decisions", body)
        return self.verify_decision(out["signature"], self._jwks)

    @staticmethod
    def verify_decision(token: str, jwks: Mapping[str, Any]) -> ZoikorumDecision:
        """Check the signature offline against pinned keys and that the decision is for Zoikorum. Never trusts the body of a
        response over its signed claims."""
        try:
            claims = verify_signed(token, jwks, typ=DECISION_TYP)
        except GrantError as e:
            raise ScopeError("DECISION_SIGNATURE_INVALID", str(e)) from e
        if claims.get("consumer") != ZoikorumPack.CONSUMER:
            raise ScopeError("DECISION_FOR_ANOTHER_CONSUMER", str(claims.get("consumer")))
        return ZoikorumDecision(
            decision_id=str(claims["decision_id"]),
            engagement=str(claims["engagement"]),
            tier=str(claims["tier"]),
            dimensions=dict(claims.get("dimensions") or {}),
            reason_codes=tuple(claims.get("reason_codes") or ()),
            consumer=str(claims["consumer"]),
            token=token,
        )

    def share(self, recipient: str, purpose: str, fields: Sequence[str], days: int = 30) -> dict[str, Any]:
        return self._client.create_disclosure(recipient, self.CONSUMER, purpose, fields, days)


# ------------------------------------------------------------------------------------------------------- Rooms


class RoomsPack:
    """Zoiko Rooms' view: property and tenancy workflows. It can share credentials for "rooms" only and never accepts a
    professional decision made for Zoikorum."""

    CONSUMER = "rooms"

    def __init__(self, client: CredentialsClient) -> None:
        self._client = client

    def share(self, recipient: str, purpose: str, fields: Sequence[str], days: int = 30) -> dict[str, Any]:
        return self._client.create_disclosure(recipient, self.CONSUMER, purpose, fields, days)

    @staticmethod
    def accept_decision(decision: ZoikorumDecision) -> None:
        """Always refuses: a Zoikorum professional decision is not usable for a tenancy (GLB-33)."""
        raise ScopeError("DECISION_FOR_ANOTHER_CONSUMER", decision.consumer)

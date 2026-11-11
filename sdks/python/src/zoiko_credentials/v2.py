"""The /v2 global trust API client (ZID-INT-002, Document 02 v2.0). Standard library only; `transport` is injectable for tests.

Differences from the /v1 client, all from the document:
- errors are RFC 9457 problems (`code`, `status`, `detail`, `trace_id`), raised as `V2Error`;
- every POST that makes something takes a REQUIRED `idempotency_key`: a retry must reuse the same key, so the key is the caller's, never
  generated behind their back (the server answers a replay with the stored result and a changed body under one key with
  `idempotency_conflict`);
- answers are the plain JSON document (no envelope);
- the SDK never holds a provider secret and never contacts a source itself: it calls this platform, nothing else.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, bytes]]


class V2Error(Exception):
    """An RFC 9457 problem. `code` is the document's public code (for example `unsupported_market`, `idempotency_conflict`)."""

    def __init__(
        self, code: str, status: int, detail: str = "", trace_id: str | None = None, retryable: bool = False
    ) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.status, self.detail, self.trace_id, self.retryable = code, status, detail, trace_id, retryable


def _urllib_transport(
    method: str, url: str, headers: Mapping[str, str], data: bytes | None, timeout: float
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class V2Client:
    def __init__(
        self, base_url: str, access_token: str, *, transport: Transport | None = None, timeout: float = 15.0
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = access_token
        self._transport = transport or _urllib_transport
        self._timeout = timeout

    # ------------------------------------------------------------------------------------------------ plumbing

    def _call(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        raw: bytes | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any:
        headers = {"Accept": "application/json, application/problem+json", "Authorization": f"Bearer {self._token}"}
        data: bytes | None = None
        if raw is not None:
            data, headers["Content-Type"] = raw, "application/octet-stream"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        headers.update(extra_headers or {})
        status, payload = self._transport(method, self._base + path, headers, data, self._timeout)
        try:
            doc = json.loads(payload) if payload else {}
        except ValueError:
            doc = {}
        if status >= 400:
            raise V2Error(
                str(doc.get("code", "http_error")),
                status,
                str(doc.get("detail", "")),
                doc.get("trace_id"),
                bool(doc.get("retryable", False)),
            )
        return doc

    # ------------------------------------------------------------------------------------------------ discovery

    def capabilities(self) -> dict[str, Any]:
        return self._call("GET", "/v2/capabilities")  # type: ignore[no-any-return]

    def coverage(self, country: str, profession: str | None = None) -> dict[str, Any]:
        q = f"?country={country}" + (f"&profession={profession}" if profession else "")
        return self._call("GET", "/v2/coverage" + q)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------------------------------------ credentials

    def verify_credential(
        self,
        credential_type: str,
        issuer_claim: Mapping[str, Any],
        credential_claim: Mapping[str, Any],
        jurisdiction: Mapping[str, str],
        *,
        idempotency_key: str,
        **opts: Any,
    ) -> dict[str, Any]:
        """One credential check. `opts` may carry subject_ref, evidence_refs, purpose, consent_ref and consumer."""
        body = {
            "credential_type": credential_type,
            "issuer_claim": dict(issuer_claim),
            "credential_claim": dict(credential_claim),
            "jurisdiction": dict(jurisdiction),
            **opts,
        }
        return self._call("POST", "/v2/credentials/verify", body, idempotency_key=idempotency_key)  # type: ignore[no-any-return]

    def credential(self, credential_ref: str) -> dict[str, Any]:
        return self._call("GET", f"/v2/credentials/{credential_ref}")  # type: ignore[no-any-return]

    # ------------------------------------------------------------------------------------------------ eligibility

    def evaluate_eligibility(
        self,
        jurisdiction: Mapping[str, str],
        profession_code: str,
        service_code: str,
        *,
        idempotency_key: str,
        subject_ref: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "jurisdiction": dict(jurisdiction),
            "profession_code": profession_code,
            "service_code": service_code,
        }
        if subject_ref:
            body["subject_ref"] = subject_ref
        return self._call("POST", "/v2/eligibility/evaluate", body, idempotency_key=idempotency_key)  # type: ignore[no-any-return]

    def eligibility(self, eligibility_ref: str) -> dict[str, Any]:
        return self._call("GET", f"/v2/eligibility/{eligibility_ref}")  # type: ignore[no-any-return]

    def engagement_check(
        self,
        professional_subject_ref: str,
        engagement_ref: str,
        profession_code: str,
        requested_services: Sequence[str],
        jurisdiction: Mapping[str, str],
        *,
        idempotency_key: str,
        **opts: Any,
    ) -> dict[str, Any]:
        """Zoikorum engagement eligibility per service, signed for Zoikorum (verify the `signature` offline with the SDK's decision check)."""
        body = {
            "professional_subject_ref": professional_subject_ref,
            "engagement_ref": engagement_ref,
            "profession_code": profession_code,
            "requested_services": list(requested_services),
            "jurisdiction": dict(jurisdiction),
            **opts,
        }
        return self._call("POST", "/v2/zoikorum/engagement-checks", body, idempotency_key=idempotency_key)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------------------------------------ cases

    def open_case(
        self,
        purpose: str,
        jurisdiction: Mapping[str, str],
        workflow: Mapping[str, str],
        requested_checks: Sequence[str],
        *,
        idempotency_key: str,
        **opts: Any,
    ) -> dict[str, Any]:
        body = {
            "purpose": purpose,
            "jurisdiction": dict(jurisdiction),
            "workflow": dict(workflow),
            "requested_checks": list(requested_checks),
            **opts,
        }
        return self._call("POST", "/v2/verifications", body, idempotency_key=idempotency_key)  # type: ignore[no-any-return]

    def case(self, case_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v2/verifications/{case_id}")  # type: ignore[no-any-return]

    def submit_task(self, case_id: str, check: str, body: Mapping[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        return self._call(
            "POST", f"/v2/verifications/{case_id}/tasks/{check}", dict(body), idempotency_key=idempotency_key
        )  # type: ignore[no-any-return]

    def cancel_case(self, case_id: str) -> dict[str, Any]:
        return self._call("POST", f"/v2/verifications/{case_id}/cancel")  # type: ignore[no-any-return]

    # ------------------------------------------------------------------------------------------------ evidence

    def upload_evidence(
        self, content: bytes, declared_type: str, *, idempotency_key: str, subject_ref: str | None = None
    ) -> dict[str, Any]:
        """Announce, then send the bytes with the short-lived token. The SHA-256 and size are computed here so they match exactly.
        Returns the manifest of the stored upload; a rejected upload raises `V2Error` (for example `evidence_rejected_infected`)."""
        announce: dict[str, Any] = {
            "declared_type": declared_type,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if subject_ref:
            announce["subject_ref"] = subject_ref
        made = self._call("POST", "/v2/evidence/uploads", announce, idempotency_key=idempotency_key)
        return self._call(  # type: ignore[no-any-return]
            "PUT", made["upload_url"], raw=content, extra_headers={"X-Upload-Token": made["upload_token"]}
        )

    def evidence_manifest(self, evidence_ref: str) -> dict[str, Any]:
        return self._call("GET", f"/v2/evidence/{evidence_ref}/manifest")  # type: ignore[no-any-return]

    def delete_evidence(self, evidence_ref: str) -> dict[str, Any]:
        return self._call("DELETE", f"/v2/evidence/{evidence_ref}")  # type: ignore[no-any-return]

    def validate_document(
        self,
        declared_type: str,
        checks: Sequence[str],
        *,
        evidence_ref: str | None = None,
        content: bytes | None = None,
        **opts: Any,
    ) -> dict[str, Any]:
        """Exactly one of `evidence_ref` (a stored upload) and `content` (checked in memory and discarded)."""
        if (evidence_ref is None) == (content is None):
            raise V2Error("invalid_request", 0, "send exactly one of evidence_ref and content")
        import base64

        body: dict[str, Any] = {"declared_type": declared_type, "checks": list(checks), **opts}
        if evidence_ref is not None:
            body["evidence_ref"] = evidence_ref
        else:
            body["content_base64"] = base64.b64encode(content or b"").decode()
        return self._call("POST", "/v2/documents/validate", body)  # type: ignore[no-any-return]

    def document_result(self, validation_ref: str) -> dict[str, Any]:
        return self._call("GET", f"/v2/documents/{validation_ref}")  # type: ignore[no-any-return]

    # ------------------------------------------------------------------------------------------------ issuers, presentations, events

    def open_issuer_check(
        self, issuer_ref: str, credential_type: str, *, idempotency_key: str, days: int | None = None
    ) -> dict[str, Any]:
        """The returned `nonce` is shown ONCE: keep it, because a replay of this call will not return it again."""
        body: dict[str, Any] = {"issuer_ref": issuer_ref, "credential_type": credential_type}
        if days is not None:
            body["days"] = days
        return self._call("POST", "/v2/issuer-checks", body, idempotency_key=idempotency_key)  # type: ignore[no-any-return]

    def issuer_check(self, issuer_check_ref: str) -> dict[str, Any]:
        return self._call("GET", f"/v2/issuer-checks/{issuer_check_ref}")  # type: ignore[no-any-return]

    def request_presentation(
        self,
        configuration_id: str,
        recipient_ref: str,
        purpose: str,
        requested_claims: Sequence[str],
        jurisdiction: Mapping[str, str],
        *,
        idempotency_key: str,
        expires_in_seconds: int | None = None,
        **opts: Any,
    ) -> dict[str, Any]:
        """`expires_in_seconds` can only SHORTEN the request's life (at most 600)."""
        body: dict[str, Any] = {
            "configuration_id": configuration_id,
            "recipient_ref": recipient_ref,
            "purpose": purpose,
            "requested_claims": list(requested_claims),
            "jurisdiction": dict(jurisdiction),
            **opts,
        }
        if expires_in_seconds is not None:
            body["expires_in_seconds"] = expires_in_seconds
        return self._call("POST", "/v2/presentations/requests", body, idempotency_key=idempotency_key)  # type: ignore[no-any-return]

    def presentation(self, presentation_ref: str) -> dict[str, Any]:
        return self._call("GET", f"/v2/presentations/{presentation_ref}")  # type: ignore[no-any-return]

    def register_webhook(
        self, url: str, event_types: Sequence[str], *, idempotency_key: str, max_inflight: int | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"url": url, "event_types": list(event_types)}
        if max_inflight is not None:
            body["max_inflight"] = max_inflight
        return self._call("POST", "/v2/webhook-endpoints", body, idempotency_key=idempotency_key)  # type: ignore[no-any-return]

    def webhook_endpoints(self) -> dict[str, Any]:
        return self._call("GET", "/v2/webhook-endpoints")  # type: ignore[no-any-return]

    def event(self, event_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v2/events/{event_id}")  # type: ignore[no-any-return]

    def replay_event(self, event_id: str) -> dict[str, Any]:
        return self._call("POST", f"/v2/events/{event_id}/replay")  # type: ignore[no-any-return]


# every route the client calls, for the parity check against the TypeScript client and the server's published contract
PATHS = (
    "GET /v2/capabilities",
    "GET /v2/coverage",
    "POST /v2/credentials/verify",
    "GET /v2/credentials/{ref}",
    "POST /v2/eligibility/evaluate",
    "GET /v2/eligibility/{ref}",
    "POST /v2/zoikorum/engagement-checks",
    "POST /v2/verifications",
    "GET /v2/verifications/{id}",
    "POST /v2/verifications/{id}/tasks/{check}",
    "POST /v2/verifications/{id}/cancel",
    "POST /v2/evidence/uploads",
    "PUT /v2/evidence/uploads/{id}/content",
    "GET /v2/evidence/{ref}/manifest",
    "DELETE /v2/evidence/{ref}",
    "POST /v2/documents/validate",
    "GET /v2/documents/{ref}",
    "POST /v2/issuer-checks",
    "GET /v2/issuer-checks/{ref}",
    "POST /v2/presentations/requests",
    "GET /v2/presentations/{ref}",
    "POST /v2/webhook-endpoints",
    "GET /v2/webhook-endpoints",
    "GET /v2/events/{id}",
    "POST /v2/events/{id}/replay",
)

__all__ = ["PATHS", "V2Client", "V2Error"]

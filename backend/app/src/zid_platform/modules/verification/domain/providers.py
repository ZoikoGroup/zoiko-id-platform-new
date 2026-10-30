"""Identity-proofing providers: Veriff and Sumsub (ZID-EDR-005). Pure domain, no I/O.

A provider answers with its own vocabulary; Zoiko iD turns that into the normalized `ProviderResult` the decision engine
already understands, and owns the decision. Rules:
- the callback is authenticated on the raw body before it is read, and an unauthenticated one decides nothing;
- an unknown status, a missing field or a half-understood event is PENDING, never an approval ("pending or error is never
  approval", EDR-005 section 6);
- a provider "approved" is evidence, not authorization: it produces claims, and the profile decides whether they are enough;
- risk labels the provider attaches always reach the engine as fraud signals, so they send an approved session to review;
- the UK-certified product is a different flow: only an adapter configured as certified, in the country it is certified
  for, carries that marker, so the global flow can never emit it (EDR-005 section 4).

What each translation rests on, so nobody mistakes a guess for a specification:
- Veriff (devdocs.veriff.com): the decision payload (`status`, `verification` with `id`, `code`, `status`, `person`, `reason`,
  `reasonCode`, `decisionTime`, `riskLabels`), the statuses approved, declined, resubmission_requested, expired and
  abandoned, and the signature (`x-hmac-signature`: HMAC-SHA256, hex, over the raw body, keyed with the shared secret, with
  the API key in `x-auth-client`) are CONFIRMED against the published guide. The numeric decision codes are not used.
- Sumsub (docs.sumsub.com): the request signing (`X-App-Token`, `X-App-Access-Ts`, `X-App-Access-Sig`) and the
  `applicantReviewed` payload (`reviewStatus`, `reviewResult.reviewAnswer` GREEN or RED, `reviewRejectType` RETRY or FINAL,
  `rejectLabels`) are CONFIRMED. The webhook signature header (`X-Payload-Digest` with `X-Payload-Digest-Alg`), the applicant
  data call that supplies the name, and which reject labels mean fraud are NOT confirmed and must be checked in a sandbox.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from zid_platform.modules.verification.domain.verification import (
    ClaimResult,
    ProviderResult,
    VerificationError,
)


class ProviderKind(StrEnum):
    GENERIC = "GENERIC"  # a provider that speaks Zoiko's own signed-JSON protocol
    VERIFF = "VERIFF"
    SUMSUB = "SUMSUB"


_SAFE = re.compile(r"[^A-Z0-9_]")


def _code(prefix: str, value: object) -> str:
    return f"{prefix}_{_SAFE.sub('_', str(value).upper())}"[:64]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _lower(headers: Mapping[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _hex_hmac(secret: str, data: bytes, digest: Any = hashlib.sha256) -> str:
    return hmac.new(secret.encode(), data, digest).hexdigest()


def _commitment(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def _name(person: Mapping[str, Any]) -> str:
    return " ".join(str(person.get(k) or "").strip() for k in ("firstName", "lastName")).strip()


# ---- Veriff ----------------------------------------------------------------------------------------------------------


def veriff_sign(body: bytes, shared_secret: str) -> str:
    """The `x-hmac-signature` of a request or webhook body."""
    return _hex_hmac(shared_secret, body)


def veriff_signature_ok(
    raw: bytes, headers: Mapping[str, str], *, api_key: str, shared_secret: str
) -> bool:
    """Both headers, checked in constant time, on the raw bytes. Anything missing or different is a refusal."""
    h = _lower(headers)
    got_key, got_sig = h.get("x-auth-client", ""), h.get("x-hmac-signature", "")
    if not api_key or not shared_secret or not got_key or not got_sig:
        return False
    return hmac.compare_digest(got_key, api_key) and hmac.compare_digest(
        got_sig.lower(), veriff_sign(raw, shared_secret)
    )


def veriff_decision_signature(session_id: str, shared_secret: str) -> str:
    """The `x-hmac-signature` of a session lookup: the session id signed with the shared secret (CONFIRMED to be the session id;
    the exact input form is to be confirmed against the HMAC guide)."""
    return _hex_hmac(shared_secret, session_id.encode())


def _verification(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    v = payload.get("verification")
    if not isinstance(v, Mapping) or not isinstance(v.get("id"), str) or not v["id"]:
        raise VerificationError("CALLBACK_SCHEMA_INVALID", "no verification id")
    return v


def veriff_ref(payload: Mapping[str, Any]) -> str:
    return str(_verification(payload)["id"])


def veriff_event_id(payload: Mapping[str, Any]) -> str:
    """Veriff sends no event id, and delivers at least once and out of order: the same decision always gets the same id."""
    v = _verification(payload)
    return _commitment(
        "veriff", v["id"], v.get("attemptId"), v.get("status"), v.get("decisionTime"), v.get("code")
    )


def veriff_result(payload: Mapping[str, Any], *, uk_certified: bool = False) -> ProviderResult:
    v = _verification(payload)
    status = str(v.get("status") or "").lower()
    raw_labels = v.get("riskLabels")
    labels: list[Any] = raw_labels if isinstance(raw_labels, list) else []
    fraud = tuple(
        _code("VERIFF_RISK", lab.get("label"))
        for lab in labels
        if isinstance(lab, Mapping) and lab.get("label")
    )
    marker = ("UK_DVS_CERTIFIED_FLOW",) if uk_certified else ()
    commit = _commitment("veriff", v["id"], v.get("decisionTime"))
    if status == "approved":
        person = _mapping(v.get("person"))
        claims = []
        if _name(person):
            claims.append(
                ClaimResult(
                    "legal_name",
                    True,
                    _name(person),
                    "document+biometric",
                    ("GOVERNMENT_ID", "BIOMETRIC_BINDING"),
                )
            )
        if person.get("dateOfBirth"):
            claims.append(
                ClaimResult(
                    "birthdate", True, str(person["dateOfBirth"]), "document", ("GOVERNMENT_ID",)
                )
            )
        # an approved Veriff session has passed document checks and a live face match; the profile still decides what that is worth
        return ProviderResult("COMPLETED", tuple(claims), "BIOMETRIC", True, fraud, marker, commit)
    if status == "declined":
        rc = v.get("reasonCode")
        return ProviderResult(
            "FAILED",
            (),
            None,
            None,
            fraud,
            (_code("VERIFF_REASON", rc) if rc is not None else "VERIFF_DECLINED", *marker),
            commit,
        )
    if status == "resubmission_requested":
        return ProviderResult(
            "PENDING", (), None, None, fraud, ("RESUBMISSION_REQUIRED", *marker), commit
        )
    if status in ("expired", "abandoned"):
        return ProviderResult(
            "FAILED", (), None, None, fraud, (_code("PROVIDER_SESSION", status), *marker), commit
        )
    if status == "review":
        return ProviderResult(
            "PENDING", (), None, None, fraud, ("PROVIDER_MANUAL_REVIEW", *marker), commit
        )
    return ProviderResult(
        "PENDING", (), None, None, fraud, ("UNKNOWN_PROVIDER_STATUS", *marker), commit
    )


# ---- Sumsub ----------------------------------------------------------------------------------------------------------

# Labels Sumsub documents as a forged or fraudulent submission. NOT CONFIRMED: check against the current label list.
SUMSUB_FRAUD_LABELS = frozenset(
    {"FORGERY", "FRAUDULENT_PATTERNS", "FRAUDULENT_LIVENESS", "DIGITAL_FORGERY"}
)
_DIGESTS = {
    "HMAC_SHA256_HEX": hashlib.sha256,
    "HMAC_SHA512_HEX": hashlib.sha512,
}  # HMAC_SHA1_HEX is refused


def sumsub_sign(secret: str, ts: int, method: str, path: str, body: bytes = b"") -> str:
    """`X-App-Access-Sig`: HMAC-SHA256 hex over the timestamp, the upper-case method, the path with its query, and the body."""
    return _hex_hmac(secret, str(ts).encode() + method.upper().encode() + path.encode() + body)


def sumsub_digest_ok(raw: bytes, headers: Mapping[str, str], *, webhook_secret: str) -> bool:
    """The webhook's `X-Payload-Digest` over the raw body, with a digest the header names. SHA-1 and unknown digests are refused."""
    h = _lower(headers)
    given, alg = h.get("x-payload-digest", ""), h.get("x-payload-digest-alg", "HMAC_SHA256_HEX")
    digest = _DIGESTS.get(alg.upper())
    if not webhook_secret or not given or digest is None:
        return False
    return hmac.compare_digest(given.lower(), _hex_hmac(webhook_secret, raw, digest))


def _sumsub_ref(payload: Mapping[str, Any]) -> str:
    ref = payload.get("applicantId")
    if not isinstance(ref, str) or not ref:
        raise VerificationError("CALLBACK_SCHEMA_INVALID", "no applicant id")
    return ref


def sumsub_ref(payload: Mapping[str, Any]) -> str:
    return _sumsub_ref(payload)


def sumsub_event_id(payload: Mapping[str, Any]) -> str:
    rr = _mapping(payload.get("reviewResult"))
    return _commitment(
        "sumsub", _sumsub_ref(payload), payload.get("type"), payload.get("correlationId"),
        payload.get("reviewStatus"), rr.get("reviewAnswer"), payload.get("createdAtMs"),
    )  # fmt: skip


def sumsub_needs_applicant(payload: Mapping[str, Any]) -> bool:
    """A GREEN review carries no name: the applicant's data must be read to produce claims."""
    rr = _mapping(payload.get("reviewResult"))
    return (
        payload.get("type") == "applicantReviewed"
        and payload.get("reviewStatus") == "completed"
        and rr.get("reviewAnswer") == "GREEN"
    )


def sumsub_result(
    payload: Mapping[str, Any], *, info: Mapping[str, Any] | None, biometric_level: bool
) -> ProviderResult:
    """`info` is the applicant's data read with a signed call; `biometric_level` says the configured level includes a liveness
    and face match (it is configuration, because the webhook does not say)."""
    ref = _sumsub_ref(payload)
    commit = _commitment("sumsub", ref, payload.get("correlationId"), payload.get("createdAtMs"))
    if payload.get("type") != "applicantReviewed":
        return ProviderResult(
            "PENDING", (), None, None, (), (_code("IGNORED_EVENT", payload.get("type")),), commit
        )
    rr = _mapping(payload.get("reviewResult"))
    if payload.get("reviewStatus") != "completed":
        return ProviderResult(
            "PENDING",
            (),
            None,
            None,
            (),
            (_code("REVIEW_STATUS", payload.get("reviewStatus")),),
            commit,
        )
    answer = rr.get("reviewAnswer")
    labels = [str(x) for x in rr.get("rejectLabels") or [] if isinstance(x, str)]
    fraud = tuple(_code("SUMSUB_RISK", x) for x in labels if x.upper() in SUMSUB_FRAUD_LABELS)
    if answer == "GREEN":
        data = info or {}
        claims = []
        if _name(data):
            claims.append(
                ClaimResult("legal_name", True, _name(data), "document+biometric" if biometric_level else "document",
                            ("GOVERNMENT_ID", "BIOMETRIC_BINDING") if biometric_level else ("GOVERNMENT_ID",))
            )  # fmt: skip
        if data.get("dob"):
            claims.append(
                ClaimResult("birthdate", True, str(data["dob"]), "document", ("GOVERNMENT_ID",))
            )
        return ProviderResult(
            "COMPLETED",
            tuple(claims),
            "BIOMETRIC" if biometric_level else None,
            True if biometric_level else None,
            fraud,
            (),
            commit,
        )
    if answer == "RED":
        if rr.get("reviewRejectType") == "RETRY":
            return ProviderResult(
                "PENDING", (), None, None, fraud, ("RESUBMISSION_REQUIRED",), commit
            )
        codes = tuple(_code("SUMSUB_REJECT", x) for x in labels) or ("SUMSUB_REJECTED",)
        return ProviderResult("FAILED", (), None, None, fraud, codes, commit)
    return ProviderResult("PENDING", (), None, None, fraud, ("UNKNOWN_PROVIDER_STATUS",), commit)


_REFS = {
    ProviderKind.VERIFF: ("api_key_ref", "shared_secret_ref"),
    ProviderKind.SUMSUB: ("app_token_ref", "secret_key_ref", "webhook_secret_ref", "level_name"),
}
_REF = re.compile(r"^[a-z0-9][a-z0-9._-]{1,80}$")


def validate_provider_config(
    kind: ProviderKind, config: Mapping[str, Any], jurisdictions: list[str]
) -> dict[str, Any]:
    """A provider adapter names its secrets and settings, never carries them. The UK-certified flow is one adapter, for GB only."""
    if kind is ProviderKind.GENERIC:
        if config:
            raise VerificationError(
                "PROFILE_INVALID", "a generic adapter takes no provider settings"
            )
        return {}
    clean: dict[str, Any] = {}
    for key in _REFS[kind]:
        value = config.get(key)
        if not isinstance(value, str) or not (
            _REF.match(value) or key == "level_name" and value.strip()
        ):
            raise VerificationError("PROFILE_INVALID", f"provider setting {key}")
        clean[key] = value
    for flag in ("biometric_level", "uk_certified"):
        if flag in config:
            if not isinstance(config[flag], bool):
                raise VerificationError("PROFILE_INVALID", f"provider setting {flag}")
            clean[flag] = config[flag]
    cb = config.get("callback_url")
    if cb is not None:
        if not isinstance(cb, str) or not cb.startswith("https://"):
            raise VerificationError("PROFILE_INVALID", "provider setting callback_url")
        clean["callback_url"] = cb
    if clean.get("uk_certified") and kind is not ProviderKind.VERIFF:
        raise VerificationError(
            "PROFILE_INVALID", "only a Veriff adapter can be the UK-certified flow"
        )
    if clean.get("uk_certified") and sorted(jurisdictions) != ["GB"]:
        raise VerificationError(
            "PROFILE_INVALID", "the UK-certified flow serves GB only, and no other country"
        )
    unknown = set(config) - set(clean)
    if unknown:
        raise VerificationError("PROFILE_INVALID", f"unknown provider settings {sorted(unknown)}")
    return clean

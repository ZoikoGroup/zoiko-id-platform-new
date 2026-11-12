"""EUDI Wallet relying-party obligations, attestation classes and interoperability conformance (ZID-RCG-003 RCG-G11; section 02 EU/EEA row; CR-080).
Pure domain, no I/O.

The OID4VP verifier (`application/verifier.py`) already requests and verifies presentations to the HAIP profile, and refuses a tenant's relying party
that is not registered for a purpose. An EUDI Wallet request adds what EU law asks of Zoiko itself as a wallet-relying party. Rules, each with a test:
- registration: Zoiko asks an EUDI Wallet for attributes in a Member State only under an ACTIVE, in-date registration there, for an intended use that
  registration lists, and only for attributes it lists for that attestation type. Asking for more is refused, never trimmed silently;
- access certificate: the request must be signed under the access certificate the registration names (the verifier's client id is the hash of its
  signing certificate, `x509_hash:`), so a request cannot be made under some other identity;
- classes: PID, QEAA, PuB-EAA and EAA are told apart. QEAA is claimed only when an official-register attestation (CR-073) shows the issuer holding
  qualified status for that service on the issuance date; otherwise it is an EAA, and saying so is part of the answer. A PID needs a listed PID provider;
- conformance: the signed request object is checked against the interoperability profile (encrypted direct_post.jwt response, x509 client id with the
  certificate chain in the header, DCQL with dc+sd-jwt or mso_mdoc only, a fresh nonce, a short life, an https response URI, an encryption key).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

# EU and EEA states: where an EUDI Wallet request is a request to a European Digital Identity Wallet
MEMBER_STATES = frozenset(
    [
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "HU",
        "IE",
        "IT",
        "LV",
        "LT",
        "LU",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
        "IS",
        "LI",
        "NO",
    ]
)
EUDI_TYPE = re.compile(r"^(eu\.europa\.ec\.eudi\.|urn:eudi:|urn:eu\.europa\.ec\.eudi:)")
PID_TYPE = re.compile(
    r"(^eu\.europa\.ec\.eudi\.pid\.|^urn:eudi:pid:|^urn:eu\.europa\.ec\.eudi:pid:)"
)
FORMATS = frozenset({"dc+sd-jwt", "mso_mdoc"})
MAX_REQUEST_LIFETIME_S = 600
MIN_NONCE_CHARS = 22  # 128 bits in base64url


class RegistrationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class AttestationClass(StrEnum):
    PID = "PID"
    QEAA = "QEAA"
    PUB_EAA = "PUB_EAA"
    EAA = "EAA"
    NOT_TRUSTED = "NOT_TRUSTED"


class EudiError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class Registration:
    """Zoiko's registration as a wallet-relying party in one Member State."""

    member_state: str
    registration_id: str
    status: RegistrationStatus
    valid_from: datetime
    valid_until: datetime | None
    access_certificate_sha256: (
        str  # base64url sha-256 of the access certificate's DER: the `x509_hash:` client id
    )
    registration_certificate_ref: str
    # intended use (the purpose code) -> attestation type -> the attributes registered for it
    intended_uses: Mapping[str, Mapping[str, frozenset[str]]] = field(default_factory=dict)


def is_eudi_type(credential_type: str) -> bool:
    return bool(EUDI_TYPE.match(credential_type))


def check_request(
    registrations: Iterable[Registration],
    *,
    member_state: str,
    intended_use: str,
    attestation_type: str,
    claims: Iterable[str],
    client_id: str,
    at: datetime,
) -> list[str]:
    """Every reason this wallet request may not be made (empty: it may)."""
    ms = member_state.upper()
    if ms not in MEMBER_STATES:
        return ["NOT_A_MEMBER_STATE"]
    mine = [r for r in registrations if r.member_state == ms]
    if not mine:
        return ["WALLET_RP_NOT_REGISTERED"]
    live = [
        r
        for r in mine
        if r.status is RegistrationStatus.ACTIVE
        and r.valid_from <= at
        and (r.valid_until is None or at < r.valid_until)
    ]
    if not live:
        return ["WALLET_RP_REGISTRATION_NOT_ACTIVE"]
    reg = max(live, key=lambda r: r.valid_from)
    reasons: list[str] = []
    if not client_id.startswith("x509_hash:"):
        reasons.append("ACCESS_CERTIFICATE_MISSING")
    elif client_id.removeprefix("x509_hash:") != reg.access_certificate_sha256:
        reasons.append("ACCESS_CERTIFICATE_MISMATCH")
    use = reg.intended_uses.get(intended_use)
    if use is None:
        reasons.append("INTENDED_USE_NOT_REGISTERED")
        return reasons
    allowed = use.get(attestation_type)
    if allowed is None:
        reasons.append("ATTESTATION_TYPE_NOT_REGISTERED")
        return reasons
    if set(claims) - allowed:
        reasons.append(
            "ATTRIBUTE_NOT_REGISTERED"
        )  # asking for more is refused, never trimmed silently
    return reasons


def classify(
    attestation_type: str,
    *,
    issuer_is_public_body: bool,
    qualified_at_issuance: bool | None,
    pid_provider_listed: bool | None = None,
) -> tuple[AttestationClass, tuple[str, ...]]:
    """What an attestation IS, said honestly. `qualified_at_issuance`: whether an official-register attestation shows the issuer qualified for this
    service on the issuance date (None: nobody looked, which is not qualified). `pid_provider_listed`: the same for a PID provider."""
    if PID_TYPE.match(attestation_type):
        if pid_provider_listed is True:
            return AttestationClass.PID, ()
        return AttestationClass.NOT_TRUSTED, ("PID_PROVIDER_NOT_LISTED",)
    if qualified_at_issuance is True:
        return AttestationClass.QEAA, ()
    if issuer_is_public_body:
        return AttestationClass.PUB_EAA, (
            "QEAA_STATUS_NOT_ESTABLISHED",
        ) if qualified_at_issuance is False else ()
    return AttestationClass.EAA, ("QEAA_STATUS_NOT_ESTABLISHED",)


def conformance(
    header: Mapping[str, Any], payload: Mapping[str, Any], *, now_epoch: int | None = None
) -> list[str]:
    """The signed request object against the EUDI / HAIP interoperability profile. Every failure is listed."""
    out: list[str] = []
    if payload.get("response_mode") != "direct_post.jwt":
        out.append("RESPONSE_NOT_ENCRYPTED")
    cid = str(payload.get("client_id") or "")
    if not cid.startswith(("x509_hash:", "x509_san_dns:")):
        out.append("CLIENT_ID_NOT_X509")
    if not header.get("x5c"):
        out.append("CERTIFICATE_CHAIN_MISSING")
    if payload.get("response_type") != "vp_token":
        out.append("RESPONSE_TYPE_INVALID")
    dcql = payload.get("dcql_query") or {}
    creds = dcql.get("credentials") if isinstance(dcql, Mapping) else None
    if not creds:
        out.append("DCQL_MISSING")
    elif any(c.get("format") not in FORMATS for c in creds):
        out.append("FORMAT_NOT_INTEROPERABLE")
    if len(str(payload.get("nonce") or "")) < MIN_NONCE_CHARS:
        out.append("NONCE_TOO_SHORT")
    iat, exp = payload.get("iat"), payload.get("exp")
    if (
        not isinstance(iat, int)
        or not isinstance(exp, int)
        or exp <= iat
        or exp - iat > MAX_REQUEST_LIFETIME_S
    ):
        out.append("REQUEST_LIFETIME_INVALID")
    elif now_epoch is not None and now_epoch >= exp:
        out.append("REQUEST_EXPIRED")
    if not str(payload.get("response_uri") or "").startswith("https://"):
        out.append("RESPONSE_URI_NOT_HTTPS")
    meta = payload.get("client_metadata") or {}
    keys = ((meta.get("jwks") or {}).get("keys") or []) if isinstance(meta, Mapping) else []
    if not any(k.get("use") == "enc" for k in keys):
        out.append("ENCRYPTION_KEY_MISSING")
    return out


def _when(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise EudiError("EUDI_REGISTRATIONS_INVALID", "times carry a time zone")
    return parsed


def parse(doc: Mapping[str, Any]) -> list[Registration]:
    out: list[Registration] = []
    try:
        for raw in doc.get("registrations") or []:
            ms = str(raw["member_state"]).upper()
            if ms not in MEMBER_STATES:
                raise EudiError("EUDI_REGISTRATIONS_INVALID", f"{ms} is not an EU or EEA state")
            valid_from = _when(raw["valid_from"])
            assert valid_from is not None
            out.append(
                Registration(
                    member_state=ms,
                    registration_id=str(raw["registration_id"]),
                    status=RegistrationStatus(str(raw.get("status", "ACTIVE")).upper()),
                    valid_from=valid_from,
                    valid_until=_when(raw.get("valid_until")),
                    access_certificate_sha256=str(raw["access_certificate_sha256"]),
                    registration_certificate_ref=str(raw["registration_certificate_ref"]),
                    intended_uses={
                        str(use): {
                            str(t): frozenset(str(c) for c in claims)
                            for t, claims in (types or {}).items()
                        }
                        for use, types in (raw.get("intended_uses") or {}).items()
                    },
                )
            )
    except EudiError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError, AssertionError) as e:
        raise EudiError("EUDI_REGISTRATIONS_INVALID", str(e)[:80]) from e
    return out

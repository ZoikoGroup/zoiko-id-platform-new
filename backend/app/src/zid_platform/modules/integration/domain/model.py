"""Application registry and enterprise connector framework (ZID-OVERLAY-001 §14-16, §24, §29; G-W2).

Pure rules, no I/O.

Applications (§14): exact allowlisted redirect/ACS endpoints, protected resource ids, a credential
profile (private_key_jwt / mTLS / workload identity; a shared secret only as a recorded exception),
a PEP profile (the obligations the enforcement point can enforce; W4 negotiates against it), risk
and evidence floors, and an owner. DRAFT -> TESTING -> ACTIVE <-> SUSPENDED -> RETIRED.

Connectors (§15): every connector has an owner, a capability contract, a source-of-truth scope, an
auth profile, a health state, a freshness bound and a disable/quarantine path. A QUARANTINED
connector stops updating trust state at once and is never fallen back to (§20 "Do not fall back to
untrusted connector"); facts it supplied become unusable for high-risk decisions once stale.
Credentials (§16): write-only, rotated with a bounded overlap, expiry alerts at 30/14/7/1 days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import urlsplit


class IntegrationError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


# ----------------------------------------------------------------------------------- applications


class Environment(StrEnum):
    DEV = "DEV"
    TEST = "TEST"
    STAGE = "STAGE"
    PROD = "PROD"


class Protocol(StrEnum):
    OIDC = "OIDC"
    SAML = "SAML"
    SCIM = "SCIM"
    AUTHZEN = "AUTHZEN"
    WEBHOOK = "WEBHOOK"
    API = "API"
    MCP = "MCP"
    OTHER = "OTHER"


class CredentialProfile(StrEnum):
    PRIVATE_KEY_JWT = "private_key_jwt"
    MTLS = "mtls"
    WORKLOAD_IDENTITY = "workload_identity"
    SECRET_EXCEPTION = "secret_exception"  # §16: only with a recorded justification


class AppStatus(StrEnum):
    DRAFT = "DRAFT"
    TESTING = "TESTING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


APP_TRANSITIONS: dict[AppStatus, frozenset[AppStatus]] = {
    AppStatus.DRAFT: frozenset({AppStatus.TESTING, AppStatus.RETIRED}),
    AppStatus.TESTING: frozenset({AppStatus.ACTIVE, AppStatus.DRAFT, AppStatus.RETIRED}),
    AppStatus.ACTIVE: frozenset({AppStatus.SUSPENDED, AppStatus.RETIRED}),
    AppStatus.SUSPENDED: frozenset({AppStatus.ACTIVE, AppStatus.RETIRED}),
}

# Obligations a PEP may declare it enforces: exactly the PEP-side obligation types the PDP emits
# (decision/domain/pdp.py). DECISION_RECEIPT is fulfilled by the platform itself, so it is never
# the PEP's to enforce (onboarding.domain.plan.PLATFORM_FULFILLED).
OBLIGATIONS = frozenset(
    {"COMMIT_RECHECK", "MANDATE_RESERVE", "TOOL_ALLOWLIST", "HUMAN_APPROVAL", "STEP_UP"}
)


# AuthZEN context facts a PEP profile may declare as required (decision/api/authzen.py
# EvaluationContext); R2/R3 requests without them are refused as PEP_CONTEXT_INCOMPLETE (§12).
CONTEXT_FIELDS = frozenset(
    {
        "purpose",
        "represented_principal_id",
        "mandate_id",
        "transaction_context_id",
        "tool_id",
        "value",
    }
)


def check_context_fields(fields: frozenset[str]) -> frozenset[str]:
    unknown = fields - CONTEXT_FIELDS
    if unknown:
        raise IntegrationError("VALIDATION_FAILED", f"unknown context fields {sorted(unknown)}")
    return fields


def check_app_transition(current: AppStatus, target: AppStatus) -> AppStatus:
    if target not in APP_TRANSITIONS.get(current, frozenset()):
        raise IntegrationError("APPLICATION_STATE_INVALID", f"{current} cannot become {target}.")
    return target


def check_endpoint(url: str, env: Environment) -> str:
    """Exact allowlisted endpoints: https with a host, no fragment, no wildcard; plain http only for
    a loopback host of a DEV application."""
    parts = urlsplit(url)
    loopback = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.fragment or "*" in url or not parts.hostname:
        raise IntegrationError("VALIDATION_FAILED", f"{url!r} is not an exact endpoint.")
    if parts.scheme != "https" and not (
        parts.scheme == "http" and loopback and env is Environment.DEV
    ):
        raise IntegrationError("VALIDATION_FAILED", f"{url!r} must use https.")
    return url


def check_credential_profile(profile: CredentialProfile, justification: str | None) -> None:
    if profile is CredentialProfile.SECRET_EXCEPTION and not (
        justification and len(justification) >= 20
    ):
        raise IntegrationError(
            "VALIDATION_FAILED",
            "A shared-secret credential is an exception and needs a recorded justification.",
        )


def check_obligations(declared: frozenset[str]) -> frozenset[str]:
    unknown = declared - OBLIGATIONS
    if unknown:
        raise IntegrationError(
            "PEP_OBLIGATION_UNSUPPORTED", f"unknown obligations {sorted(unknown)}"
        )
    return declared


# ------------------------------------------------------------------------------------- connectors


class ConnectorType(StrEnum):
    OIDC_IDP = "OIDC_IDP"
    SAML_IDP = "SAML_IDP"
    SCIM = "SCIM"
    HRIS = "HRIS"
    SIGNALS = "SIGNALS"
    WEBHOOK = "WEBHOOK"
    DIRECTORY = "DIRECTORY"


class Capability(StrEnum):
    AUTHN = "AUTHN"
    PROVISIONING = "PROVISIONING"
    GROUPS = "GROUPS"
    HR_LIFECYCLE = "HR_LIFECYCLE"
    SIGNALS = "SIGNALS"
    WEBHOOK = "WEBHOOK"
    DIRECTORY_LOOKUP = "DIRECTORY_LOOKUP"


class AuthProfile(StrEnum):
    OAUTH_CLIENT = "OAUTH_CLIENT"
    MTLS = "MTLS"
    WORKLOAD_IDENTITY = "WORKLOAD_IDENTITY"
    SECRET = "SECRET"


class Health(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"


class DisableBehavior(StrEnum):
    FAIL_CLOSED_HIGH_RISK = "FAIL_CLOSED_HIGH_RISK"  # stale facts deny R2/R3, R0/R1 within policy
    SUSPEND_DEPENDENTS = "SUSPEND_DEPENDENTS"  # dependent principals/sessions suspended


# source_ref prefix each connector type must point at (the module that does the actual work)
SOURCE_PREFIX = {
    ConnectorType.OIDC_IDP: "federation_connection:",
    ConnectorType.SAML_IDP: "federation_connection:",
    ConnectorType.SCIM: "provisioning_source:",
    ConnectorType.HRIS: "provisioning_source:",
}
DEFAULT_FRESHNESS = {
    ConnectorType.SCIM: timedelta(hours=1),
    ConnectorType.HRIS: timedelta(hours=24),
}
FAILURES_TO_DEGRADED = 1
FAILURES_TO_FAILED = 5
EXPIRY_ALERT_DAYS = (30, 14, 7, 1)
MAX_OVERLAP = timedelta(days=7)


def check_source_ref(kind: ConnectorType, source_ref: str | None) -> str | None:
    prefix = SOURCE_PREFIX.get(kind)
    if prefix is None:
        return source_ref
    if not source_ref or not source_ref.startswith(prefix):
        raise IntegrationError("VALIDATION_FAILED", f"{kind} connectors reference a {prefix}<id>.")
    return source_ref


def health_after(current: Health, *, ok: bool, consecutive_failures: int) -> Health:
    """Interaction outcomes move health; QUARANTINED is left only by a governed release."""
    if current is Health.QUARANTINED:
        return current
    if ok:
        return Health.HEALTHY
    if consecutive_failures >= FAILURES_TO_FAILED:
        return Health.FAILED
    return Health.DEGRADED if consecutive_failures >= FAILURES_TO_DEGRADED else current


def fresh(fresh_until: datetime | None, now: datetime) -> bool:
    return fresh_until is not None and now < fresh_until


def expiry_alert(not_after: datetime | None, now: datetime, already: frozenset[int]) -> int | None:
    """The tightest 30/14/7/1-day threshold crossed and not yet alerted (§29)."""
    if not_after is None:
        return None
    left = (not_after - now).total_seconds() / 86400
    due = [d for d in EXPIRY_ALERT_DAYS if left <= d and d not in already]
    return min(due) if due else None


@dataclass(frozen=True, slots=True)
class CredentialMeta:
    version: int
    status: str  # ACTIVE | RETIRING | REVOKED
    fingerprint: str  # commitment, never the value
    not_after: datetime | None
    overlap_until: datetime | None

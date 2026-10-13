"""Typed settings. Environment variables use the ZID_ prefix; no secret defaults (DEC-F5)."""

from typing import Literal

from zid_app import ServiceSettings

from .roles import Role


class Settings(ServiceSettings):
    service_name: str = "zoiko-id"
    role: Role = Role.CELL
    cell_uuid: str | None = None  # ZID_CELL_UUID: this cell's id in agent.writer_fence (DEC-C5)
    token_issuer: str | None = None  # ZID_TOKEN_ISSUER: exact `iss` of accepted access tokens
    token_audience: str = "https://api.zoiko.id"  # ZID_TOKEN_AUDIENCE: this resource server
    public_base_url: str | None = None  # ZID_PUBLIC_BASE_URL: canonical external URL for DPoP `htu`
    webauthn_rp_id: str | None = None  # ZID_WEBAUTHN_RP_ID: explicit relying-party id (never Host)
    webauthn_rp_name: str = "Zoiko iD"
    webauthn_origins: tuple[str, ...] = ()  # ZID_WEBAUTHN_ORIGINS: exact allowlist (JSON list)
    # ZID_WEBAUTHN_RELATED_ORIGINS: governed Related Origin Requests list (FR-AUT-010)
    webauthn_related_origins: tuple[str, ...] = ()
    # ZID_MTLS_CLIENT_CERT_HEADER: header carrying the URL-encoded PEM client certificate from the
    # TLS terminator (e.g. x-client-cert). Set only when the edge strips it from inbound requests.
    mtls_client_cert_header: str | None = None
    # ZID_FEDERATION_AUTHORITY_HINTS: superiors listed in our OpenID Federation entity configuration
    federation_authority_hints: tuple[str, ...] = ()
    # ZID_DEV_SIGNING_KEYS (local/test only): purpose -> PEM, so local API and consumer processes
    # share development keys (keys.PURPOSES). Deployed cells get KMS-backed signers instead.
    dev_signing_keys: dict[str, str] = {}  # noqa: RUF012 - pydantic copies defaults
    # ZID_VERIFIER_CLIENT_ID_PREFIX: OID4VP client identifier. x509_hash (HAIP) needs the verifier
    # certificate chain (ZID_VERIFIER_CERTIFICATE_CHAIN, PEM, leaf first, for the verifier key);
    # local/test without one use a development self-signed certificate.
    verifier_client_id_prefix: Literal["openid_federation", "x509_hash"] = "openid_federation"
    verifier_certificate_chain: tuple[str, ...] = ()
    consent_url: str | None = None  # ZID_CONSENT_URL: consent UI for third-party OAuth clients

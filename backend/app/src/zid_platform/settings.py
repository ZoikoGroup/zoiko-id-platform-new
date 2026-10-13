"""Typed settings. Environment variables use the ZID_ prefix; no secret defaults (DEC-F5)."""

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
    # ZID_MTLS_CLIENT_CERT_HEADER: header carrying the URL-encoded PEM client certificate from the
    # TLS terminator (e.g. x-client-cert). Set only when the edge strips it from inbound requests.
    mtls_client_cert_header: str | None = None
    # ZID_FEDERATION_AUTHORITY_HINTS: superiors listed in our OpenID Federation entity configuration
    federation_authority_hints: tuple[str, ...] = ()

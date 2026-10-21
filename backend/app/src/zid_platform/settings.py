"""Typed settings. Environment variables use the ZID_ prefix; no secret defaults (DEC-F5)."""

from datetime import timedelta
from typing import Any, Literal

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
    # --- Keys and secrets (DEC-F5; zid_crypto broker, see crypto_runtime.py) ---------------------
    # ZID_KEY_PROVIDER: local (development KEKs; local/test only) | aws-kms (deployed cells)
    key_provider: Literal["local", "aws-kms"] = "local"
    # ZID_LOCAL_KEYSTORE (local only): JSON file so local processes share development KEKs;
    # unset = in-memory keys, regenerated on every start.
    local_keystore: str | None = None
    kms_region: str | None = None  # ZID_KMS_REGION: the cell's KMS region (default: AWS SDK chain)
    # ZID_KMS_KEY_IDS (JSON): KeyPurpose -> KMS key id/ARN/alias, e.g. {"DATA_KEK": "alias/..."};
    # purposes not listed use ZID_KMS_KEY_ALIAS_PATTERN ({cell} = ZID_CELL_ID, {purpose} lower-case)
    kms_key_ids: dict[str, str] = {}  # noqa: RUF012 - pydantic copies defaults
    kms_key_alias_pattern: str = "alias/zid/{cell}/{purpose}"
    kms_pending_window_days: int = 30  # ZID_KMS_PENDING_WINDOW_DAYS: ScheduleKeyDeletion (7-30)
    # ZID_EVIDENCE_SIGNING_KID: published key id of the cell's KMS evidence key (default
    # evidence-<cell>); historic public keys stay published for the life of the evidence (DEC-F2 §5)
    evidence_signing_kid: str | None = None
    # ZID_EVIDENCE_HISTORIC_KEYS (JSON list of public JWKs): retired evidence keys still trusted to
    # verify older checkpoints and exports; never taken from the database next to the signature
    evidence_historic_keys: tuple[dict[str, Any], ...] = ()
    # Checkpoint cadence (DEC-F2 §2): partitions with unsealed E3 entries are sealed sooner
    evidence_checkpoint_interval: timedelta = timedelta(minutes=5)
    evidence_checkpoint_e3_interval: timedelta = timedelta(seconds=30)
    # ZID_EVIDENCE_TSA_URL: optional RFC 3161 TSA; ZID_EVIDENCE_TSA_REQUIRED blocks sealing without it
    evidence_tsa_url: str | None = None
    # ZID_EVIDENCE_RETENTION_DAYS (JSON {"E1": 400, ...}): the DPO's retention per evidence class;
    # unset classes are kept (the ledger entry is never purged, only the record body)
    evidence_retention_days: dict[str, int] = {}  # noqa: RUF012 - pydantic copies defaults
    # ZID_TENANT_KEK_IDS (JSON tenant -> KMS key id/ARN): DEDICATED_TENANT_KEY / BYOK / HYOK
    # SUBJECT_ERASURE KEKs (DEC-F5 §4); tenants not listed use the cell's platform KEK
    tenant_kek_ids: dict[str, str] = {}  # noqa: RUF012 - pydantic copies defaults
    evidence_tsa_required: bool = False
    # --- Operator plane (DEC-F4; operator_access) -------------------------------------------------
    # ZID_OPERATOR_TENANT_ID: the dedicated operator tenant; unset = the operator plane is disabled
    operator_tenant_id: str | None = None
    # ZID_OPERATOR_CLIENT_IDS: first-party Operator Console OIDC clients (never the customer web app)
    operator_client_ids: tuple[str, ...] = ("zoiko-operator",)
    # ZID_OPERATOR_MAX_MINUTES (JSON capability -> minutes): may only lower the catalog ceilings
    operator_max_minutes: dict[str, int] = {}  # noqa: RUF012 - pydantic copies defaults
    operator_break_glass_max: timedelta = timedelta(minutes=60)
    # --- Workload identity (DEC-F7; zid_context.spiffe) ------------------------------------------
    # ZID_SPIFFE_TRUST_DOMAIN: this cell+environment's governed trust domain (default
    # <cell>.<environment>.id.zoiko); ZID_SPIFFE_BUNDLE_FILES (JSON): trust domain -> PEM bundle
    # file; ZID_SPIFFE_FEDERATED_DOMAINS: explicitly federated domains (no trust-on-first-use);
    # ZID_MESH_TRUSTED_PROXIES: local mesh data-plane addresses allowed to forward a peer SVID.
    spiffe_trust_domain: str | None = None
    # ZID_PRIVACY_ENFORCEMENT: enforce | audit (default: enforce, audit in local/test) - see
    # privacy.ports.enforce_processing; audit records every would-be refusal as evidence
    privacy_enforcement: Literal["enforce", "audit"] | None = None
    spiffe_bundle_files: dict[str, str] = {}  # noqa: RUF012 - pydantic copies defaults
    spiffe_federated_domains: tuple[str, ...] = ()
    mesh_trusted_proxies: tuple[str, ...] = ()
    # ZID_SECRET_BROKER: env (ZID_SECRET__<REF>; local/test only) | openbao (deployed cells)
    secret_broker: Literal["env", "openbao"] = "env"
    openbao_addr: str | None = None  # ZID_OPENBAO_ADDR: the cell's OpenBao, e.g. https://bao:8200
    openbao_kv_mount: str = "secret"  # ZID_OPENBAO_KV_MOUNT: KV v2 mount holding service secrets
    openbao_namespace: str | None = None  # ZID_OPENBAO_NAMESPACE
    # ZID_OPENBAO_AUTH: jwt (workload JWT-SVID, DEC-F7) | token. Credentials are read from FILES
    # (ZID_OPENBAO_JWT_FILE / ZID_OPENBAO_TOKEN_FILE), never from environment values.
    openbao_auth: Literal["jwt", "token"] = "jwt"
    openbao_jwt_mount: str = "jwt"
    openbao_jwt_role: str | None = None  # ZID_OPENBAO_JWT_ROLE: role bound to this workload
    openbao_jwt_file: str | None = None
    openbao_token_file: str | None = None

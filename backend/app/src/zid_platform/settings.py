"""Typed settings. Environment variables use the ZID_ prefix; no secret defaults (DEC-F5)."""

from datetime import timedelta
from typing import Any, Literal

from pydantic import Field
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
    # ZID_TLS_GROUP_HEADER: header the TLS terminator sets to the key-exchange group it negotiated (and strips
    # from inbound requests). ZID_TLS_HYBRID_REQUIRED_PREFIXES (JSON list): path prefixes that are refused
    # unless a hybrid post-quantum group was negotiated (CRYPTO-001 §12.1 HYBRID_REQUIRED).
    tls_group_header: str = "x-tls-group"
    tls_hybrid_required_prefixes: tuple[str, ...] = ()
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
    # ZID_BILLING_RETURN_ORIGIN: the console origin Stripe's hosted card page returns to (default: the origin of
    # ZID_CONSENT_URL, which is the console); set it when the console and the consent page are on different hosts
    billing_return_origin: str | None = None
    # --- Keys and secrets (DEC-F5; zid_crypto broker, see crypto_runtime.py) ---------------------
    # ZID_KEY_PROVIDER: local (development KEKs; local/test only) | aws-kms | gcp-kms (deployed cells)
    key_provider: Literal["local", "aws-kms", "gcp-kms"] = "local"
    # ZID_LOCAL_KEYSTORE (local only): JSON file so local processes share development KEKs;
    # unset = in-memory keys, regenerated on every start.
    local_keystore: str | None = None
    kms_region: str | None = None  # ZID_KMS_REGION: the cell's KMS region (default: AWS SDK chain)
    # ZID_KMS_KEY_IDS (JSON): KeyPurpose -> KMS key id/ARN/alias, e.g. {"DATA_KEK": "alias/..."};
    # purposes not listed use ZID_KMS_KEY_ALIAS_PATTERN ({cell} = ZID_CELL_ID, {purpose} lower-case)
    kms_key_ids: dict[str, str] = {}  # noqa: RUF012 - pydantic copies defaults
    kms_key_alias_pattern: str = "alias/zid/{cell}/{purpose}"
    kms_pending_window_days: int = 30  # ZID_KMS_PENDING_WINDOW_DAYS: ScheduleKeyDeletion (7-30)
    # gcp-kms: ZID_KMS_KEY_IDS values are CryptoKey resource names (projects/P/locations/L/keyRings/R/cryptoKeys/K);
    # purposes not listed are built from the project, location and key ring below ({cell} = ZID_CELL_ID).
    gcp_kms_project: str | None = None  # ZID_GCP_KMS_PROJECT
    gcp_kms_location: str | None = None  # ZID_GCP_KMS_LOCATION, e.g. europe-west2
    gcp_kms_key_ring: str = "zid-{cell}"  # ZID_GCP_KMS_KEY_RING
    # ZID_GCP_KMS_HSM: the keys were created with protection level HSM (FIPS 140-2 Level 3)
    gcp_kms_hsm: bool = False
    # ZID_SIGNING_RETIRING_KEYS (JSON {"token": [public JWK, ...]}): signing keys rotated out of a KeyRings purpose
    # but still inside their verification window, published next to the active key
    signing_retiring_keys: dict[str, tuple[dict[str, Any], ...]] = {}  # noqa: RUF012 - pydantic copies defaults
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
    # ZID_OPERATOR_MODE: governed (default: just-in-time access with approvals, dual control) | direct (a registered
    # operator holds every capability that does not touch customer data and needs no second approver; see
    # operator_access/application/service.py). Direct mode is for a small team that runs the platform with one account.
    operator_mode: Literal["governed", "direct"] = "governed"
    # ZID_OPERATOR_DIRECT_ACK: a deployed cell (anything but local/test/dev) refuses direct mode unless this holds the
    # exact sentence below, so removing dual control is a deliberate, reviewed act and never a stray setting.
    operator_direct_ack: str | None = None
    # First operator account from configuration (entrypoints/bootstrap_operator.py; one-shot, idempotent):
    # ZID_OPERATOR_BOOTSTRAP_PRINCIPAL_ID (uuid of the operator's principal), ZID_OPERATOR_ORIGIN (the Operator
    # Console's public origin; its redirect is <origin>/auth/callback unless ZID_OPERATOR_REDIRECT_URIS lists them),
    # ZID_OPERATOR_JURISDICTION (ISO country of the operator), ZID_OPERATOR_BOOTSTRAP_ROLES (default all three) and
    # ZID_OPERATOR_BOOTSTRAP_TICKET_MINUTES (how long the single-use passkey enrollment link lives).
    operator_bootstrap_principal_id: str | None = None
    operator_origin: str | None = None
    operator_redirect_uris: tuple[str, ...] = ()
    operator_jurisdiction: str = "GB"
    operator_bootstrap_roles: tuple[str, ...] = ("REQUESTER", "APPROVER", "REVIEWER")
    operator_bootstrap_ticket_minutes: int = 60
    # --- Onboarding approvals (CR-036) ---------------------------------------------------------------------------
    # ZID_ONBOARDING_APPROVAL: second_admin (default: approving a plan, starting a pilot, activating the company and
    # activating a pilot scope each need an independent second administrator) | owner (the company's OWNER approves
    # these steps themself, with no second person). A deployed cell also needs ZID_ONBOARDING_OWNER_ACK.
    onboarding_approval: Literal["second_admin", "owner"] = "second_admin"
    onboarding_owner_ack: str | None = None
    # --- Tenant onboarding (G-W0; ZID-OVERLAY-001 §4) -------------------------------------------
    # ZID_TENANT_BOOTSTRAP_TENANT_ID: the signup tenant whose principals may create new tenants;
    # unset = tenant self-service creation is disabled in this cell.
    tenant_bootstrap_tenant_id: str | None = None
    # ZID_TENANT_CONSOLE_REDIRECT_URIS (JSON list): exact redirect URIs of the tenant console; when
    # set, bootstrap registers each new tenant's first-party console OAuth client with them.
    tenant_console_redirect_uris: tuple[str, ...] = ()
    # ZID_TENANT_CONSOLE_SCOPES (JSON list): scopes the console client may request
    tenant_console_scopes: tuple[str, ...] = (
        "openid",
        "verification:read",
        "verification:execute",
        "presentation:request",
    )
    # ZID_TENANT_SIGNUP_ENABLED: public company sign-up (POST /v1/signup); needs the console
    # redirect URIs above. Off by default.
    tenant_signup_enabled: bool = False
    # ZID_TENANT_SIGNUP_DEV_VERIFY (local/test only): a signed-up company skips the registry gate
    # with a synthetic, self-labelled development root so it can be administered at once.
    tenant_signup_dev_verify: bool = False
    # ZID_DEV_SIMULATORS: development identity-provider and register simulators (local/test only)
    dev_simulators: bool = False
    # ZID_TENANT_SIGNUP_NOTICE_VERSION: the privacy notice a registrant must acknowledge (PRIV-001)
    tenant_signup_notice_version: str = "2026-10-22"
    # --- Global locator and discovery (G-W6; DEC-G3) ---------------------------------------------
    # ZID_CELL_ENDPOINTS (JSON cell -> base URL): where each cell's API is (discovery answers and
    # the CELL_ROUTING_STALE / CELL_NOT_AUTHORITATIVE location hint)
    cell_endpoints: dict[str, str] = {}  # noqa: RUF012 - pydantic copies defaults
    # ZID_CELL_UUIDS: cell id -> its agent.writer_fence uuid (default: derived from the cell id)
    cell_uuids: dict[str, str] = {}  # noqa: RUF012 - pydantic copies defaults
    # ZID_OPS_CATALOGUE_PATH: the service catalogue (default: backend/config/operations; ZID_PLATFORM_CONFIG moves the root)
    ops_catalogue_path: str | None = None
    # Global role. ZID_LOCATOR_CELL_KEYS (JSON cell -> [public JWK]): each cell's pinned publication
    # keys; local/test without it trust this machine's development "federation" key for any cell.
    locator_cell_keys: dict[str, list[dict[str, Any]]] = {}  # noqa: RUF012 - pydantic copies defaults
    discovery_ttl_s: int = 300  # ZID_DISCOVERY_TTL_S: how long a client may cache an answer
    discovery_kms_key_id: str | None = None  # ZID_DISCOVERY_KMS_KEY_ID: deployed signing key
    # Webhooks (EVENTS-001 §14-20). ZID_WEBHOOK_KMS_KEY_ID: the deployed RFC 9421 signing key (local/test
    # use the development key). ZID_WEBHOOK_ALLOW_HTTP: accept http:// endpoints, local/test only.
    webhook_kms_key_id: str | None = None
    company_evidence_kms_key_id: str | None = (
        None  # ZID_COMPANY_EVIDENCE_KMS_KEY_ID: signs company-verification and screening evidence
    )
    # ZID_WEBHOOK_RETIRING_KEYS (JSON): keys still published during a signing-key rotation, as
    # [{"jwk": {...public key with its kid...}, "retire_at": "2026-11-30T00:00:00+00:00"}]; at most 30 days of overlap
    webhook_retiring_keys: list[dict[str, Any]] = []  # noqa: RUF012 - pydantic copies defaults
    # ZID_API_RATE_LIMIT_PER_MINUTE: authenticated requests per tenant per minute (0 = off). The edge limiter
    # (DEC-G1) is the production control for abuse; this one keeps one tenant from starving the others.
    api_rate_limit_per_minute: int = 600
    webhook_allow_http: bool = False
    # ZID_COMMERCIAL_ENFORCEMENT (CR-024): off | shadow | enforce. `shadow` (default) reports what the plan
    # would refuse without blocking; turn on `enforce` only once a price book is approved.
    commercial_enforcement: Literal["off", "shadow", "enforce"] = "shadow"
    # ZID-ENG-DEC-009: shadow reports a missing or bad step-up proof without blocking; enforce refuses it
    step_up_enforcement: Literal["off", "shadow", "enforce"] = "shadow"
    # ZID_COMMERCIAL_ISSUING_ENTITY (CR-027): the seller of record's invoice series prefix.
    commercial_issuing_entity: str = "ZID"
    # ZID_STRIPE_SECRET_KEY_FILE: path of a file holding the Stripe secret (or restricted) key. A file, never an
    # inline value (DEC-F5). Set, the cell collects payments and calculates tax through Stripe; unset, tax
    # answers "not configured" and a deployed cell refuses to collect. ZID_STRIPE_API_BASE: test doubles only.
    stripe_secret_key_file: str | None = None
    stripe_api_base: str = "https://api.stripe.com"
    # ZID_STRIPE_WEBHOOK_SECRET_FILE (CR-040): path of a file holding the endpoint's signing secret (`whsec_...`).
    # Set, POST /v1/webhooks/stripe accepts Stripe's signed events; unset, the endpoint answers 404.
    stripe_webhook_secret_file: str | None = None
    # --- Email (Communication Estate; CR-031). Off by default: nothing is sent until a person turns it on.
    # ZID_EMAIL_ENABLED: master switch. ZID_EMAIL_ENABLED_TEMPLATES: JSON list of template ids, or ["*"] for
    # every template whose event is wired and whose links have a route. ZID_EMAIL_FROM: "Name <address>" of a
    # sender whose domain has SPF, DKIM and DMARC (release decision register). ZID_SMTP_PASSWORD_FILE: a file,
    # never an inline value (DEC-F5). ZID_EMAIL_ROUTES: JSON object of extra link-field -> path routes.
    email_enabled: bool = False
    email_enabled_templates: list[str] = Field(default_factory=list)
    email_from: str | None = None
    email_reply_to: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password_file: str | None = None
    email_public_base_url: str | None = None
    email_routes: dict[str, str] = Field(default_factory=dict)
    email_flood_per_template_hour: int = 5
    email_flood_per_recipient_hour: int = 20
    email_legal_entity: str | None = None
    email_mailing_address: str | None = None
    # Cell role. ZID_GLOBAL_LOCATOR_URL: the global plane the worker publishes records to;
    # ZID_DIRECTORY_KMS_KEY_ID: the cell's deployed record-signing key (local/test: dev "federation").
    global_locator_url: str | None = None
    directory_kms_key_id: str | None = None
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
    # ZID_ATTESTOR_KEYS_FILE (CR-042): a JWK set (`{"keys": [...]}`) of the attestors this cell trusts to sign runtime
    # attestation statements. Public keys only. A key may carry `zid_max_level` (1 to 3, default 2): the highest
    # level that attestor may vouch for; level 3 only for an attestor that checks hardware evidence. Unset, POST /v1/machine/attestations:verify answers 404 and no workload
    # can reach an attestation level above 0.
    attestor_keys_file: str | None = None
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

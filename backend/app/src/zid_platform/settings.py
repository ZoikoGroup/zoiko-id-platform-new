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
    # db = software keys held in the database, wrapped by a master key outside it (CR-060; standard assurance, not FIPS-validated)
    key_provider: Literal["local", "aws-kms", "gcp-kms", "db"] = "local"
    # ZID_KEY_MASTER_FILE (db): file of master keys, one `<id> <base64 32 bytes>` per line, first is current; root-only, never in the database
    key_master_file: str | None = None
    # ZID_KEY_PROVIDER_DB_ACK (db, deployed): must equal "accept-software-keys"; the operator accepts that a host compromise exposes keys
    key_provider_db_ack: str | None = None
    # ZID_KEY_STORE_DATABASE_URL (db): override for the key store connection (default: this process's database)
    key_store_database_url: str | None = None
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
    # ZID_EVIDENCE_TSA_TRUST_FILE: PEM file of the TSA root certificates a timestamp must chain to; required whenever a TSA URL is set
    evidence_tsa_trust_file: str | None = None
    # ZID_EVIDENCE_TSA_REVOCATION: off | if_available (a revoked TSA certificate fails; a missing CRL is noted) | required (a missing CRL fails too)
    evidence_tsa_revocation: Literal["off", "if_available", "required"] = "if_available"
    # ZID_UPLOAD_RETENTION_DAYS (JSON {"E1": 400, ...}): the DPO's retention per evidence class;
    # unset classes are kept (the ledger entry is never purged, only the record body)
    evidence_retention_days: dict[str, int] = {}  # noqa: RUF012 - pydantic copies defaults
    # ZID_TENANT_KEK_IDS (JSON tenant -> KMS key id/ARN): DEDICATED_TENANT_KEY / BYOK / HYOK
    # SUBJECT_ERASURE KEKs (DEC-F5 §4); tenants not listed use the cell's platform KEK
    tenant_kek_ids: dict[str, str] = {}  # noqa: RUF012 - pydantic copies defaults
    evidence_tsa_required: bool = False
    # ZID_EVIDENCE_VERIFY_INTERVAL (seconds, 0 = off) and ZID_EVIDENCE_VERIFY_BATCH: the worker re-verifies each evidence partition's chain,
    # checkpoints, signatures and timestamps on this schedule, at most `batch` partitions per pass; a failure raises evidence.integrity.failed
    evidence_verify_interval: timedelta = timedelta(hours=6)
    evidence_verify_batch: int = 2
    # ZID_EVIDENCE_TSA_WAIVER=accept-no-external-timestamps: with software keys a deployed cell needs a timestamp authority unless this is
    # set; it records that the owners accept that a rewritten history could not be told apart from the original (CR-060)
    evidence_tsa_waiver: str | None = None
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
    # ZID_IDV_FACE_ROUTE_ACK: the owner's acknowledgement that a market's face-match route (CR-129) decides without a liveness test, an evaluated engine or a check that
    # the document is genuine. It must equal `accept-no-liveness-no-evaluation`; without it that route refuses every request.
    idv_face_route_ack: str | None = None
    # ZID_IDV_GLOBAL_POLICY_ACK: the owners' written acceptance of the built-in global identity policy (config/idv/global-policy.yaml): the values it states for photograph
    # retention, how long an approval counts, the biometric basis and the expired-document rule. It must equal `accept-global-idv-policy`. Without it a country that has no policy of
    # its own is refused, exactly as before; with it that country is served by the global policy. A country policy in market-policies.yaml always wins.
    idv_global_policy_ack: str | None = None
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
    # Operator Console email + password sign-in (the only password sign-in; authentication/api/operator_password.py).
    # ZID_OPERATOR_PASSWORD_LOGINS (JSON) and/or ZID_OPERATOR_LOGINS_FILE (path to a JSON file, e.g. a mounted secret):
    # operator email -> {"principal_id": <uuid of an operator-tenant principal>, "password_hash": <scrypt hash from
    # entrypoints/hash_operator_password.py>}. A plain "password" is accepted in local/test only. Empty = no password route.
    operator_password_logins: dict[str, dict[str, str]] = {}  # noqa: RUF012 - pydantic copies defaults
    operator_logins_file: str | None = None
    # ZID_OPERATOR_EMAIL + ZID_OPERATOR_PASSWORD: the first operator's sign-in, straight from the environment. The
    # password is hashed in memory when the cell starts (never stored or logged), and `entrypoints/migrate.py` creates
    # the operator principal from the same two values after the schema migration. The principal is
    # ZID_OPERATOR_BOOTSTRAP_PRINCIPAL_ID when set, otherwise one derived from the operator tenant and the email.
    operator_email: str | None = None
    operator_password: str | None = None
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
    # ZID_ORG_CLAIM_DEV_AUTOVERIFY: LOCAL/TEST ONLY. A company claim reads as verified as soon as the claimant's own identity check is complete, with no register
    # lookup, screening, authority check or review, and the organization is established from it as `development-unverified` (the same synthetic root as
    # ZID_TENANT_SIGNUP_DEV_VERIFY). The app refuses to start with it on in any other environment.
    org_claim_dev_autoverify: bool = False
    # ZID_DEV_SIMULATORS: development identity-provider and register simulators (local/test only)
    dev_simulators: bool = False
    # ZID_TENANT_SIGNUP_NOTICE_VERSION: the privacy notice a registrant must acknowledge (PRIV-001)
    tenant_signup_notice_version: str = "2026-11-03"
    # ZID_REGISTRATION_TENANT_ID: this cell's registration tenant (WF-013A): where person-first sign-up requests and their email log
    # sit before an account exists. Unset means person-first sign-up is off. Seed it with `entrypoints.seed_registration_tenant`.
    registration_tenant_id: str | None = None
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
    # ZID_PROOF_SCANNER_ADDRESS: host[:port] of the clamd malware scanner that checks uploaded proof documents; a deployed
    # environment refuses uploads without one (nothing is stored unscanned), local and test record NOT_SCANNED
    proof_scanner_address: str | None = None
    # ZID_SELF_SERVE_REGION: the released cell name a company's self-serve plan purchase is quoted in (CR-147). Unset means self-serve purchase is off and a
    # plan is still agreed with the team. The owners switch it on by naming a region whose SRE gate has passed (commercial.region_release).
    self_serve_region: str | None = None
    # ZID_UPLOAD_RETENTION_DAYS: how long an uploaded evidence document is kept before its bytes are erased. The privacy officer
    # decides it; unset means the cell accepts no evidence upload (a retention is never invented). Uploads are scanned by the same clamd
    # as proof documents (ZID_PROOF_SCANNER_ADDRESS).
    upload_retention_days: int | None = None
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
    # ZID_ACTIVATION_PAYMENT (CR-062): REQUIRED (default) holds an activated order until the company has paid the first month's platform fee on Stripe;
    # OFF switches the plan on activation, as before. REQUIRED with no payment provider configured refuses the activation rather than switch without payment.
    activation_payment: Literal["REQUIRED", "OFF"] = "REQUIRED"
    # ZID-ENG-DEC-009: shadow reports a missing or bad step-up proof without blocking; enforce refuses it
    step_up_enforcement: Literal["off", "shadow", "enforce"] = "shadow"
    # ZID_COMMERCIAL_ISSUING_ENTITY (CR-027): the seller of record's invoice series prefix.
    commercial_issuing_entity: str = "ZID"
    # ZID_STRIPE_SECRET_KEY_FILE: path of a file holding the Stripe secret (or restricted) key. A file, never an
    # inline value (DEC-F5). Set, the cell collects payments and calculates tax through Stripe; unset, tax
    # answers "not configured" and a deployed cell refuses to collect. ZID_STRIPE_API_BASE: test doubles only.
    stripe_secret_key_file: str | None = None
    # ZID_SCREENING_SIGNAL_PEPPER_FILE: a deployment secret mixed into the hashes of the fraud and velocity signals (CR-068). Without one the
    # hashes are still tenant-scoped but a short value (a phone number) could be guessed, so a deployed cell sets it.
    screening_signal_pepper_file: str | None = None
    # ZID_SCREENING_VELOCITY_MODE: off | shadow | review (CR-068). `shadow` (the default) counts attempts and logs which would have been flagged, with
    # no review item and no notification, so the proposed thresholds can be read against real traffic first. `review` opens an item per breach.
    screening_velocity_mode: Literal["off", "shadow", "review"] = "shadow"
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
    # ZID_SMTP_INSECURE_LOCAL (local/test only): talk to a local mail sink (for example Mailpit) without TLS. Refused anywhere else.
    smtp_insecure_local: bool = False
    email_public_base_url: str | None = None
    # ZID-CP-WF01 hosted verification lifetimes (owner decision ZID-EXT-IDV-001 D09; these defaults stand until it is ratified). ZID_HOSTED_VERIFY_*:
    # the business's one-use link, a cross-device handoff link, the person's session credential, and the session itself.
    hosted_verify_launch_minutes: int = Field(default=15, ge=1, le=24 * 60)
    hosted_verify_handoff_minutes: int = Field(default=5, ge=1, le=60)
    hosted_verify_access_minutes: int = Field(default=60, ge=5, le=24 * 60)
    hosted_verify_session_hours: int = Field(default=24, ge=1, le=30 * 24)
    # ZID-CPS-WF-02 Command Center policy (owner decision D-01; these are the document's proposals until ratified): the smallest group a figure may
    # describe (ZID_VERIFY_ANALYTICS_MIN_GROUP), how old the newest ingested event may be before figures are provisional, and how long facts are kept.
    verify_analytics_min_group: int = Field(default=20, ge=2, le=1000)
    verify_analytics_stale_minutes: int = Field(default=15, ge=1, le=24 * 60)
    verify_analytics_retention_days: int = Field(default=400, ge=30, le=3650)
    # reviewed languages the hosted page may offer (BCP 47); a language is listed only once its legal notice and copy are reviewed, never machine-translated
    hosted_verify_languages: list[str] = Field(default_factory=lambda: ["en"])
    email_routes: dict[str, str] = Field(default_factory=dict)
    email_flood_per_template_hour: int = 5
    email_flood_per_recipient_hour: int = 20
    email_legal_entity: str | None = None
    email_mailing_address: str | None = None
    # Cell role. ZID_GLOBAL_LOCATOR_URL: the global plane the worker publishes records to;
    # ZID_DIRECTORY_KMS_KEY_ID: the cell's deployed record-signing key (local/test: dev "federation").
    global_locator_url: str | None = None
    # ZID_SERVE_GLOBAL_PLANE: this cell process also serves the global plane (locator + discovery) on its own port,
    # in the same database, instead of a second `global` process on another port. Cell role only.
    serve_global_plane: bool = False
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
    # ZID_MESH_PROXY_SECRET_FILE: a file holding the secret the workload listener adds to every request it forwards. With it set, a forwarded
    # client certificate is honored only from a trusted proxy that also presents it. A deployed environment with trusted proxies requires it.
    mesh_proxy_secret_file: str | None = None
    # ZID_AGENT_BUILDS (CR-064): must a blueprint release run an approved build? auto = yes everywhere but local, test and dev;
    # enforce = always; off = never, which a deployed environment refuses at start-up.
    agent_builds: Literal["auto", "off", "enforce"] = "auto"
    # ZID_ACCESS_READINESS_ENABLED (CR-091, ZID-AG-WF-001): the Delegated Authority & Access Readiness agent. Default OFF: every route answers 404 and
    # no run executes. ZID_ACCESS_READINESS_REGIONS (JSON list of cell names, e.g. ["uk-1"]): the regions approved for it; empty means none, and a
    # cell outside the list fails closed with REGION_UNSUPPORTED. Turning the switch off is also the global kill switch for new runs.
    access_readiness_enabled: bool = False
    access_readiness_regions: tuple[str, ...] = ()
    # The database role the worker runs each queued agent run AS (ZID_AGENT_RUN_ROLE). The worker connects as a role that bypasses row-level security so it can
    # find work across companies; the run itself should not have that privilege. Set it to the runtime role (zid_runtime) in a deployment, and grant the worker's
    # login membership of it (deploy/gcp/db/01_roles.sql). Unset, a run executes on the worker's own connection, as before.
    agent_run_role: str | None = None
    # API-based agents (ZID_API_AGENTS): auto is on outside production and OFF in production until a named decision switches it on (CR-144).
    api_agents: Literal["auto", "on", "off"] = "auto"
    # ZID_ACCESS_READINESS_AI_EXPLAIN (CR-091; sections 06, 09 step 8): may a setup opt in to the optional plain-language explanation of a run? Default
    # OFF. Even on, a setup must opt in (its approval signs the flag) and only codes and counts leave the rules; without an approved model gateway
    # handed to the app the fixed deterministic text is shown. The rule result is the same either way.
    access_readiness_ai_explain: bool = False
    # ZID_FAIRNESS_CHECK (ZID-RCG-003 T44, CR-092): run the disaggregated disparity check on automated name matches? off (default) never runs it and
    # holds nothing. on = the worker pass holds, for a person, the automated "different person" answers of a name cohort with a material disparity,
    # and, until the owners approve a threshold in config/credential-trust/fairness-thresholds.yaml (none ships), of EVERY cohort with outcomes.
    fairness_check: Literal["off", "on"] = "off"
    # ZID_REGULATORY_PACKS (ZID-RCG-003, CR-067): must a regulated capability that is ENABLED in the country registry also have an approved policy
    # pack for the market? shadow (default) = the check runs and a would-be refusal is only logged; enforce = it is refused before any provider is
    # called; off = never, which a deployed environment refuses at start-up. The country registry stays the baseline either way.
    regulatory_packs: Literal["off", "shadow", "enforce"] = "shadow"
    # ZID_MARKET_RELEASES (ZID-RCG-003 RCG-G10, CR-072): must an ENABLED market also have a LAUNCHED market release (seven evidence gates, four
    # approvals) that still covers the packs in force and whose supplier certifications are certified on fresh evidence? Same meaning as above:
    # shadow (default) only logs, enforce refuses with MARKET_NOT_RELEASED, off never checks and a deployed environment refuses it at start-up.
    market_releases: Literal["off", "shadow", "enforce"] = "shadow"
    # ZID_PROVIDER_ENTITLEMENTS (ZID-RCG-003 RCG-G03, CR-074): must the provider the registry names for an ENABLED capability hold a GRANTED
    # entitlement for that country, capability and purpose? Same meaning: shadow (default) only logs, enforce refuses with PROVIDER_NOT_ENTITLED,
    # off is refused outside local, test and dev.
    provider_entitlements: Literal["off", "shadow", "enforce"] = "shadow"
    # ZID_PRIVACY_OVERLAY (ZID-RCG-003 RCG-G09, CR-079): must an ENABLED country's verification start pass its approved privacy overlay (provider
    # region and lawful transfer, biometric permission and consent, DPIA)? shadow (default) only logs; enforce refuses with CROSS_BORDER_ROUTE_DENIED,
    # BIOMETRIC_ROUTE_NOT_PERMITTED (naming the alternate profiles) or PRIVACY_OVERLAY_UNAVAILABLE; off is refused outside local, test and dev.
    privacy_overlay: Literal["off", "shadow", "enforce"] = "shadow"
    # ZID_EUDI_WALLET (ZID-RCG-003 RCG-G11, CR-080): must a request to an EUDI Wallet (a PID or an EU attestation type) be made under Zoiko's
    # registration as a wallet-relying party in that Member State, for a registered use and attributes, signed with the registered access
    # certificate, and conform to the interoperability profile? shadow (default) only logs; enforce refuses; off is refused outside local, test and dev.
    eudi_wallet: Literal["off", "shadow", "enforce"] = "shadow"
    # ZID_MONITORING_SCOPE (ZID-RCG-003 RCG-G12, CR-081): does ongoing monitoring (today the scheduled sanctions re-screen; later PEP, adverse
    # media, transaction and crypto monitoring) run only where a Legal and Compliance approval covers the country, kind and sector? shadow (default)
    # only logs; enforce holds everything not approved (no global background monitoring); off is refused outside local, test and dev.
    monitoring_scope: Literal["off", "shadow", "enforce"] = "shadow"
    # ZID_AGENT_GOVERNANCE (ZID-AG-WF-002, CR-105): must every agent tool call and run pass the governance gates (an active policy version with
    # an effective approval for its exact hash, an unexpired lease for this tool, resource, purpose and region, the company baseline, quotas and
    # budget)? shadow (default) records what enforce would refuse in the agent evidence stream and lets the call through; enforce refuses with
    # the section 13 decision codes; off skips the checks (refused outside local, test and dev). An emergency freeze refuses in every mode.
    agent_governance: Literal["off", "shadow", "enforce"] = "shadow"
    # ZID_DAARA_ENABLED (CR-106, retired by the engineering owner's instruction of 2026-11-16, ADR adr-two-implementations-of-wf-001): the DAARA agent's own
    # routes (/v1/machine/agents/{id}/daara..., /v1/machine/agent-review...) answer 404 and its worker steps do nothing unless this is true. The Access Readiness
    # agent (CR-091) is the one WF-001 agent. Nothing is deleted: the code, the tables and every record stay, so turning this on brings it back unchanged.
    daara_enabled: bool = False
    # ZID_SELF_RUN_ENABLED: self-run identity verification (CR-095, CR-102): the platform's own chip check with no external provider. Off until the owners accept the path;
    # while off, every route answers 404 and nothing else changes.
    self_run_enabled: bool = False
    # ZID_BUILD_REGISTRIES (CR-065): exact host names the console may look an image's digest up in (for example ["ghcr.io"]); a registry's token service
    # host must be listed too. Empty (default) switches the lookup off and nothing is ever contacted. ZID_BUILD_REGISTRIES_PRIVATE names the few of those
    # that may resolve to a private address (a registry on the same network); everything else resolving privately is refused.
    build_registries: tuple[str, ...] = ()
    build_registries_private: tuple[str, ...] = ()
    # ZID_BUILD_IMAGES (JSON list): the image repositories people register builds from, e.g. ["ghcr.io/acme/invoice-bot"]. The console offers them to pick
    # (then pick a version), so nobody types an image name. Repositories already registered are offered too. Needs the host in ZID_BUILD_REGISTRIES.
    build_images: tuple[str, ...] = ()
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

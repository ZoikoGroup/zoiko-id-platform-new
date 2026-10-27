"""Commercial catalogue, plans, price books and entitlements (ZID-COM-001 §5, §6, §29; CR-024).

Revision ID: commercial_0001
Revises:
Create Date: 2026-10-24

The catalogue tables (`sku`, `capability`, `plan`, `plan_capability`, `price_book`, `price_book_line`) are
platform data, not tenant data: one set per cell, read by every tenant, written only by the operator plane.
`entitlement` and `entitlement_override` are per tenant under RLS.

Seeded from ZID-COM-001: the SKU registry (§5), the five plans (§6), the plan capability matrix (§29) and ONE
price book in status DRAFT (§6, §7). Nothing here is an approved price: a DRAFT book is invisible to
`GET /v1/catalog` and prices nothing until a different operator approves it. A capability is only ever
granted when `capability.released` is true; the seed marks released only what the platform implements.

Immutability is enforced in the database: an APPROVED or SUPERSEDED price book and its lines cannot change,
and an entitlement can only change its state and end date (history stays).
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0001"
down_revision = None
branch_labels = None
depends_on = None

# (sku_id, family, unit, meter_id, consumption, released, description)
SKUS = [
    (
        "ZID-BASE",
        "Platform",
        "tenant-month",
        None,
        "FLAT",
        True,
        "Identity control plane; plan fee",
    ),
    ("ZID-HUM", "Human CIAM", "MAH-month", "MAH", "METERED", True, "Unique active human principal"),
    (
        "ZID-AUTH",
        "Authentication",
        "1k events",
        "AUTH_EVENT",
        "METERED",
        True,
        "Successful authentication completions",
    ),
    (
        "ZID-FED",
        "Federation",
        "connection-month",
        None,
        "FLAT",
        True,
        "Production enterprise SAML/OIDC connection",
    ),
    (
        "ZID-SCIM",
        "Directory",
        "connection-month",
        None,
        "FLAT",
        True,
        "Production SCIM or managed directory connection",
    ),
    (
        "ZID-PDP",
        "Policy",
        "10k decisions",
        "POLICY_DECISION",
        "METERED",
        True,
        "Authorized policy decision evaluations",
    ),
    (
        "ZID-M2M",
        "Machine / agents",
        "1k grants",
        "M2M_GRANT",
        "METERED",
        True,
        "Machine credential or token issuance events",
    ),
    ("ZID-VCI", "Credentials", "issue", "VC_ISSUED", "METERED", True, "Credentials issued"),
    (
        "ZID-VCP",
        "Credentials",
        "presentation",
        "VC_PRESENTED",
        "METERED",
        True,
        "Presentations cryptographically checked",
    ),
    (
        "ZID-IDV",
        "Verification",
        "vendor matrix",
        None,
        "PASSTHROUGH",
        False,
        "Identity document, liveness, data-source checks",
    ),
    (
        "ZID-KYB",
        "Business verification",
        "vendor matrix",
        None,
        "PASSTHROUGH",
        False,
        "Entity registry, directors and ownership checks",
    ),
    (
        "ZID-RISK",
        "Risk services",
        "provider check",
        None,
        "PASSTHROUGH",
        False,
        "Screening, re-screening and enhanced risk checks",
    ),
    (
        "ZID-MSG",
        "Messaging",
        "delivered attempt",
        None,
        "PASSTHROUGH",
        False,
        "SMS and messaging routes by country",
    ),
    (
        "ZID-EVD",
        "Evidence",
        "archive capacity",
        None,
        "CONTRACT",
        True,
        "Extended retention, signed export or archival tier",
    ),
    (
        "ZID-DPL",
        "Deployment",
        "environment-region",
        None,
        "CONTRACT",
        False,
        "Dedicated region or private cell",
    ),
    (
        "ZID-PS",
        "Professional services",
        "milestone or day",
        None,
        "CONTRACT",
        True,
        "Migration, configuration, advisory",
    ),
    (
        "ZID-SUP",
        "Support",
        "monthly uplift",
        None,
        "CONTRACT",
        True,
        "24x7, named engineer, enhanced response",
    ),
]

# (capability key, label, released, sku)
CAPABILITIES = [
    ("human_iam", "Human IAM and passwordless", True, "ZID-HUM"),
    ("organizations", "Organizations and tenant roles", True, None),
    ("passkeys_mfa", "Passkeys and MFA", True, None),
    ("oidc_oauth", "OIDC and OAuth integration", True, None),
    ("saml_sso", "SAML enterprise SSO", True, "ZID-FED"),
    ("scim", "SCIM and directory sync", True, "ZID-SCIM"),
    ("policy_engine", "Policy decision engine", True, "ZID-PDP"),
    ("consent_receipts", "Consent and delegation receipts", True, None),
    ("credential_issue_verify", "Credential issue and verify", True, "ZID-VCI"),
    ("idv_kyb", "Identity and business verification", False, "ZID-IDV"),
    ("audit_export", "Audit export and retention", True, "ZID-EVD"),
    ("agent_principal", "Agent and machine principals", True, "ZID-M2M"),
    ("dedicated_region", "Dedicated region and HSM", False, "ZID-DPL"),
    ("operational_sla", "Operational SLA", False, "ZID-SUP"),
]

# plan, label, sort, fee, fee_is_floor, mah, auth, pdp, m2m, sso, environments, support
# NULL means negotiated (Enterprise).
PLANS = [
    ("DEVELOPER", "Developer", 1, "0", False, 2500, 25000, 50000, 0, 0, 1, "Community"),
    ("LAUNCH", "Launch", 2, "299", False, 10000, 150000, 500000, 50000, 0, 1, "Business hours"),
    ("BUSINESS", "Business", 3, "1499", False, 50000, 1000000, 5000000, 250000, 1, 2, "Priority"),
    (
        "SCALE",
        "Scale",
        4,
        "4999",
        False,
        250000,
        5000000,
        25000000,
        1000000,
        3,
        3,
        "Priority plus review",
    ),
    (
        "ENTERPRISE",
        "Enterprise",
        5,
        "12500",
        True,
        None,
        None,
        None,
        None,
        None,
        None,
        "Negotiated 24x7",
    ),
]

# capability -> (developer, launch, business, scale, enterprise) as mode[:tier]
MATRIX = {
    "human_iam": ("INCLUDED:core", "INCLUDED:core", "INCLUDED:core", "INCLUDED:core", "CONTRACT"),
    "organizations": (
        "INCLUDED:limited",
        "INCLUDED:standard",
        "INCLUDED:advanced",
        "INCLUDED:advanced",
        "INCLUDED:advanced",
    ),
    "passkeys_mfa": (
        "INCLUDED:standard",
        "INCLUDED:standard",
        "INCLUDED:advanced",
        "INCLUDED:advanced",
        "CONTRACT",
    ),
    "oidc_oauth": ("INCLUDED", "INCLUDED", "INCLUDED", "INCLUDED", "INCLUDED"),
    "saml_sso": ("NONE", "ADD_ON", "INCLUDED", "INCLUDED", "CONTRACT"),
    "scim": ("NONE", "ADD_ON", "ADD_ON", "ADD_ON", "CONTRACT"),
    "policy_engine": ("INCLUDED:basic", "INCLUDED", "INCLUDED", "INCLUDED", "CONTRACT"),
    "consent_receipts": (
        "SANDBOX",
        "INCLUDED",
        "INCLUDED:advanced",
        "INCLUDED:advanced",
        "CONTRACT",
    ),
    "credential_issue_verify": ("SANDBOX", "METERED", "METERED", "METERED", "CONTRACT"),
    "idv_kyb": ("SANDBOX", "QUALIFIED", "QUALIFIED", "QUALIFIED", "QUALIFIED"),
    "audit_export": (
        "INCLUDED:standard",
        "INCLUDED:standard",
        "INCLUDED:extended",
        "INCLUDED:extended",
        "CONTRACT",
    ),
    "agent_principal": ("SANDBOX", "METERED", "METERED", "METERED", "CONTRACT"),
    "dedicated_region": ("NONE", "NONE", "ADD_ON", "ADD_ON", "CONTRACT"),
    "operational_sla": ("NONE", "NONE", "QUALIFIED", "QUALIFIED", "CONTRACT"),
}
PLAN_ORDER = ("DEVELOPER", "LAUNCH", "BUSINESS", "SCALE", "ENTERPRISE")

# included connection counts the plan carries for a capability
INCLUDED_COUNT = {("saml_sso", "BUSINESS"): 1, ("saml_sso", "SCALE"): 3}

# §7 overage rates: sku -> (launch, business, scale, divisor)
OVERAGE = {
    "ZID-HUM": ("0.025", "0.015", "0.009", 1),
    "ZID-AUTH": ("0.40", "0.25", "0.15", 1000),
    "ZID-PDP": ("0.60", "0.45", "0.25", 10000),
    "ZID-M2M": ("0.50", "0.35", "0.20", 1000),
    "ZID-FED": ("149", "149", "125", 1),
    "ZID-SCIM": ("149", "149", "125", 1),
    "ZID-VCI": ("0.08", "0.05", "0.03", 1),
    "ZID-VCP": ("0.025", "0.015", "0.010", 1),
}


def _q(v: object) -> str:
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


def _n(v: object) -> str:
    return "NULL" if v is None else str(v)


def _seed() -> str:
    out: list[str] = []
    for sku, family, unit, meter, cons, released, desc in SKUS:
        out.append(
            "INSERT INTO commercial.sku (sku_id, family, unit, meter_id, consumption, released, description) "
            f"VALUES ({_q(sku)}, {_q(family)}, {_q(unit)}, {_q(meter)}, {_q(cons)}, {str(released).lower()}, {_q(desc)});"
        )
    for plan, label, sort, fee, floor, mah, auth, pdp, m2m, sso, envs, support in PLANS:
        out.append(
            "INSERT INTO commercial.plan (plan_code, label, sort_order, platform_fee, fee_is_floor, mah_included, "
            "auth_events_included, policy_decisions_included, m2m_grants_included, sso_included, "
            "production_environments, support_level) "
            f"VALUES ({_q(plan)}, {_q(label)}, {sort}, {fee}, {str(floor).lower()}, {_n(mah)}, {_n(auth)}, "
            f"{_n(pdp)}, {_n(m2m)}, {_n(sso)}, {_n(envs)}, {_q(support)});"
        )
    for key, label, released, sku in CAPABILITIES:
        out.append(
            "INSERT INTO commercial.capability (capability_key, label, released, sku_id) "
            f"VALUES ({_q(key)}, {_q(label)}, {str(released).lower()}, {_q(sku)});"
        )
    for key, modes in MATRIX.items():
        for plan, cell in zip(PLAN_ORDER, modes, strict=True):
            mode, _, tier = cell.partition(":")
            out.append(
                "INSERT INTO commercial.plan_capability (plan_code, capability_key, mode, tier, included_count) "
                f"VALUES ({_q(plan)}, {_q(key)}, {_q(mode)}, {_q(tier or None)}, {_n(INCLUDED_COUNT.get((key, plan)))});"
            )
    out.append(
        "INSERT INTO commercial.price_book (price_book_id, version, currency, status, note) VALUES "
        "('00000000-0000-4000-8000-0000000000b1', 1, 'USD', 'DRAFT', "
        "'ZID-COM-001 proposal rates; not approved. Finance must approve before any customer sees a price.');"
    )
    book = "'00000000-0000-4000-8000-0000000000b1'"
    for plan, _label, _sort, fee, *_ in PLANS:
        out.append(
            "INSERT INTO commercial.price_book_line (price_book_id, sku_id, plan_code, unit_rate, unit_divisor) "
            f"VALUES ({book}, 'ZID-BASE', {_q(plan)}, {fee}, 1);"
        )
    for sku, (launch, business, scale, divisor) in OVERAGE.items():
        for plan, rate in zip(
            ("LAUNCH", "BUSINESS", "SCALE"), (launch, business, scale), strict=True
        ):
            out.append(
                "INSERT INTO commercial.price_book_line (price_book_id, sku_id, plan_code, unit_rate, unit_divisor) "
                f"VALUES ({book}, {_q(sku)}, {_q(plan)}, {rate}, {divisor});"
            )
    return "\n".join(out)


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.sku (
            sku_id       text PRIMARY KEY CHECK (sku_id ~ '^ZID-[A-Z0-9]{2,8}$'),
            family       text NOT NULL,
            unit         text NOT NULL,
            meter_id     text,
            consumption  text NOT NULL CHECK (consumption IN ('FLAT', 'METERED', 'PASSTHROUGH', 'CONTRACT')),
            released     boolean NOT NULL DEFAULT false,
            description  text NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            retired_at   timestamptz
        );

        CREATE TABLE commercial.capability (
            capability_key text PRIMARY KEY CHECK (capability_key ~ '^[a-z][a-z0-9_]{1,40}$'),
            label          text NOT NULL,
            released       boolean NOT NULL DEFAULT false,
            sku_id         text REFERENCES commercial.sku (sku_id)
        );

        CREATE TABLE commercial.plan (
            plan_code                 text PRIMARY KEY
                CHECK (plan_code IN ('DEVELOPER', 'LAUNCH', 'BUSINESS', 'SCALE', 'ENTERPRISE')),
            label                     text NOT NULL,
            sort_order                integer NOT NULL,
            platform_fee              numeric(18, 6) NOT NULL CHECK (platform_fee >= 0),
            fee_is_floor              boolean NOT NULL DEFAULT false,
            mah_included              bigint CHECK (mah_included >= 0),
            auth_events_included      bigint CHECK (auth_events_included >= 0),
            policy_decisions_included bigint CHECK (policy_decisions_included >= 0),
            m2m_grants_included       bigint CHECK (m2m_grants_included >= 0),
            sso_included              integer CHECK (sso_included >= 0),
            production_environments   integer CHECK (production_environments >= 0),
            support_level             text NOT NULL,
            published                 boolean NOT NULL DEFAULT true
        );

        CREATE TABLE commercial.plan_capability (
            plan_code       text NOT NULL REFERENCES commercial.plan (plan_code),
            capability_key  text NOT NULL REFERENCES commercial.capability (capability_key),
            mode            text NOT NULL CHECK (mode IN
                ('INCLUDED', 'ADD_ON', 'METERED', 'CONTRACT', 'SANDBOX', 'QUALIFIED', 'NONE')),
            tier            text,
            included_count  integer CHECK (included_count >= 0),
            PRIMARY KEY (plan_code, capability_key)
        );

        CREATE TABLE commercial.price_book (
            price_book_id  uuid PRIMARY KEY,
            version        integer NOT NULL UNIQUE CHECK (version >= 1),
            currency       text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            status         text NOT NULL CHECK (status IN ('DRAFT', 'APPROVED', 'SUPERSEDED')),
            effective_from timestamptz,
            created_by     uuid,
            created_at     timestamptz NOT NULL DEFAULT now(),
            approved_by    uuid,
            approved_at    timestamptz,
            note           text,
            CHECK (status = 'DRAFT' OR (approved_at IS NOT NULL AND effective_from IS NOT NULL)),
            CHECK (approved_by IS NULL OR created_by IS NULL OR approved_by <> created_by)
        );

        CREATE TABLE commercial.price_book_line (
            price_book_id     uuid NOT NULL REFERENCES commercial.price_book (price_book_id),
            sku_id            text NOT NULL REFERENCES commercial.sku (sku_id),
            plan_code         text NOT NULL REFERENCES commercial.plan (plan_code),
            unit_rate         numeric(18, 6) NOT NULL CHECK (unit_rate >= 0),
            unit_divisor      integer NOT NULL DEFAULT 1 CHECK (unit_divisor >= 1),
            included_quantity bigint CHECK (included_quantity >= 0),
            minimum_amount    numeric(18, 6) CHECK (minimum_amount >= 0),
            PRIMARY KEY (price_book_id, sku_id, plan_code)
        );

        CREATE FUNCTION commercial.price_book_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'DRAFT' THEN
                    RAISE EXCEPTION 'an approved price book cannot be deleted' USING ERRCODE = 'check_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status = 'DRAFT' THEN
                RETURN NEW;
            END IF;
            IF OLD.status = 'APPROVED' AND NEW.status = 'SUPERSEDED'
               AND (NEW.price_book_id, NEW.version, NEW.currency, NEW.effective_from, NEW.approved_by,
                    NEW.approved_at, NEW.created_by, NEW.created_at)
                IS NOT DISTINCT FROM
                   (OLD.price_book_id, OLD.version, OLD.currency, OLD.effective_from, OLD.approved_by,
                    OLD.approved_at, OLD.created_by, OLD.created_at) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'an approved price book is immutable' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER price_book_guard BEFORE UPDATE OR DELETE ON commercial.price_book
            FOR EACH ROW EXECUTE FUNCTION commercial.price_book_guard();

        CREATE FUNCTION commercial.price_book_line_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            book uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.price_book_id ELSE NEW.price_book_id END;
            st text;
        BEGIN
            SELECT status INTO st FROM commercial.price_book WHERE price_book_id = book;
            IF st IS NOT NULL AND st <> 'DRAFT' THEN
                RAISE EXCEPTION 'the lines of an approved price book are immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$;
        CREATE TRIGGER price_book_line_guard BEFORE INSERT OR UPDATE OR DELETE ON commercial.price_book_line
            FOR EACH ROW EXECUTE FUNCTION commercial.price_book_line_guard();

        CREATE TABLE commercial.entitlement (
            entitlement_id  uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            plan_code       text NOT NULL REFERENCES commercial.plan (plan_code),
            price_book_id   uuid REFERENCES commercial.price_book (price_book_id),
            state           text NOT NULL CHECK (state IN ('PENDING', 'ACTIVE', 'SUSPENDED', 'ENDED')),
            effective_from  timestamptz NOT NULL,
            effective_to    timestamptz,
            source          text NOT NULL CHECK (source IN ('OPERATOR', 'ORDER')),
            source_order_id uuid,
            version         integer NOT NULL DEFAULT 1 CHECK (version >= 1),
            created_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CHECK (effective_to IS NULL OR effective_to > effective_from)
        );
        CREATE UNIQUE INDEX entitlement_one_active ON commercial.entitlement (tenant_id)
            WHERE state IN ('ACTIVE', 'SUSPENDED');
        CREATE INDEX entitlement_by_tenant ON commercial.entitlement (tenant_id, effective_from DESC);

        CREATE FUNCTION commercial.entitlement_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'entitlement history is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.entitlement_id, NEW.tenant_id, NEW.plan_code, NEW.price_book_id, NEW.effective_from,
                NEW.source, NEW.source_order_id, NEW.created_by, NEW.created_at)
               IS DISTINCT FROM
               (OLD.entitlement_id, OLD.tenant_id, OLD.plan_code, OLD.price_book_id, OLD.effective_from,
                OLD.source, OLD.source_order_id, OLD.created_by, OLD.created_at) THEN
                RAISE EXCEPTION 'an entitlement can only change its state and end date'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state = 'ENDED' THEN
                RAISE EXCEPTION 'an ended entitlement is final' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER entitlement_guard BEFORE UPDATE OR DELETE ON commercial.entitlement
            FOR EACH ROW EXECUTE FUNCTION commercial.entitlement_guard();

        CREATE TABLE commercial.entitlement_override (
            override_id    uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            capability_key text NOT NULL REFERENCES commercial.capability (capability_key),
            grant_mode     text CHECK (grant_mode IN ('INCLUDED', 'ADD_ON', 'METERED', 'CONTRACT', 'QUALIFIED')),
            extra_count    integer CHECK (extra_count >= 0),
            reason         text NOT NULL CHECK (length(reason) BETWEEN 3 AND 500),
            approved_by    uuid NOT NULL,
            created_by     uuid NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            expires_at     timestamptz,
            revoked_at     timestamptz,
            CHECK (approved_by <> created_by),
            CHECK (grant_mode IS NOT NULL OR extra_count IS NOT NULL)
        );
        CREATE INDEX entitlement_override_by_tenant ON commercial.entitlement_override (tenant_id, capability_key);

        ALTER TABLE commercial.entitlement ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.entitlement FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.entitlement
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE commercial.entitlement_override ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.entitlement_override FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.entitlement_override
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)
    _run(_seed())


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.entitlement_override;
        DROP TABLE commercial.entitlement;
        DROP FUNCTION commercial.entitlement_guard();
        DROP TABLE commercial.price_book_line;
        DROP TABLE commercial.price_book;
        DROP FUNCTION commercial.price_book_line_guard();
        DROP FUNCTION commercial.price_book_guard();
        DROP TABLE commercial.plan_capability;
        DROP TABLE commercial.plan;
        DROP TABLE commercial.capability;
        DROP TABLE commercial.sku;
    """)

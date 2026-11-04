"""Finance authority: named, dated appointments, a signed policy, and requests with approvals and one execution (ZID-COM-CL-003, ZID-COM-CL-004,
ZID-UX-COM-009; slice L-14b, CR-054).

Revision ID: commercial_0017
Revises: commercial_0016
Create Date: 2026-11-03

commercial_role_holder becomes the appointment table of Zoiko's internal roles: a person holds a role from a date to a date (or until revoked). Rows that exist keep working: they start when they were granted and do not end. A
person holds one live appointment of a role per scope; ending one never edits history, it sets revoked_at.
finance_policy_signature: who signed which version of the finance policy file, by its content hash. A policy counts only while the current file's hash
is signed by a Founder and a CFO who are different people; changing the file makes it unsigned again.
finance_request: one money instrument (a credit note, a pilot credit) asked for, with the facts frozen in a snapshot and its hash. The facts never change
after insert; only the state moves. A material change is a new request, so no approval is ever carried over.
finance_approval: one decision per required role per request, bound to the snapshot hash, append-only; a person signs a request once.
finance_execution: the single right to apply an approved request, once (the request id is the key), append-only.
finance_pilot_credit: a pilot benefit an approved request granted: scoped, expiring, not cash, never converted automatically.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0017"
down_revision = "commercial_0016"
branch_labels = None
depends_on = None

ROLES = (
    "AE",
    "CCO",
    "CFO",
    "FOUNDER",
    "GROUP_CFO",
    "REVOPS",
    "FINANCE_OPS_LEAD",
    "CONTROLLER",
    "DISPUTES_SPECIALIST",
    "FINANCE_EXECUTOR",
)


def upgrade() -> None:
    roles = ", ".join(f"'{r}'" for r in ROLES)
    _run(f"""
        ALTER TABLE commercial.commercial_role_holder DROP CONSTRAINT commercial_role_holder_pkey;
        ALTER TABLE commercial.commercial_role_holder DROP CONSTRAINT commercial_role_holder_role_check;
        ALTER TABLE commercial.commercial_role_holder
            ADD COLUMN appointment_id uuid NOT NULL DEFAULT gen_random_uuid(),
            ADD COLUMN effective_from timestamptz NOT NULL DEFAULT now(),
            ADD COLUMN effective_to   timestamptz,
            ADD COLUMN revoked_at     timestamptz,
            ADD COLUMN revoked_by     uuid;
        ALTER TABLE commercial.commercial_role_holder ADD PRIMARY KEY (appointment_id);
        ALTER TABLE commercial.commercial_role_holder ADD CONSTRAINT commercial_role_holder_role_check CHECK (role IN ({roles}));
        ALTER TABLE commercial.commercial_role_holder ADD CONSTRAINT commercial_role_holder_window
            CHECK (effective_to IS NULL OR effective_to > effective_from);
        CREATE UNIQUE INDEX commercial_role_holder_live ON commercial.commercial_role_holder
            (principal_id, role) WHERE revoked_at IS NULL;

        CREATE TABLE commercial.finance_policy_signature (
            content_hash     text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{{64}}$'),
            policy_version   text NOT NULL,
            signer_principal uuid NOT NULL,
            signer_role      text NOT NULL CHECK (signer_role IN ('FOUNDER', 'CFO')),
            signed_at        timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (content_hash, signer_principal),
            UNIQUE (content_hash, signer_role)
        );

        CREATE TABLE commercial.finance_request (
            request_id       uuid PRIMARY KEY,
            kind             text NOT NULL CHECK (kind IN ('CREDIT_NOTE', 'PILOT_CREDIT')),
            tenant_id        uuid NOT NULL,
            invoice_id       uuid,
            currency         text NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
            amount_minor     bigint NOT NULL CHECK (amount_minor > 0),
            usd_minor        bigint NOT NULL CHECK (usd_minor > 0),
            exposure_usd_minor bigint NOT NULL CHECK (exposure_usd_minor >= usd_minor),
            category         text NOT NULL CHECK (category IN ('CORRECTION', 'GOODWILL', 'PILOT')),
            reason           text NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
            snapshot         jsonb NOT NULL,
            snapshot_hash    text NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{{64}}$'),
            policy_version   text NOT NULL,
            policy_hash      text NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{{64}}$'),
            required_roles   text[] NOT NULL CHECK (cardinality(required_roles) > 0),
            state            text NOT NULL CHECK (state IN ('PENDING_APPROVAL', 'APPROVED', 'APPLYING', 'APPLIED', 'APPLICATION_FAILED',
                                                            'REJECTED', 'EXPIRED', 'CANCELED')),
            requested_by     uuid NOT NULL,
            created_at       timestamptz NOT NULL,
            expires_at       timestamptz NOT NULL,
            approved_at      timestamptz,
            approval_expires_at timestamptz,
            result           jsonb
        );
        CREATE INDEX finance_request_tenant ON commercial.finance_request (tenant_id, created_at);
        CREATE INDEX finance_request_open ON commercial.finance_request (state, expires_at);
        CREATE FUNCTION commercial.finance_request_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'finance requests are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.request_id, NEW.kind, NEW.tenant_id, NEW.invoice_id, NEW.currency, NEW.amount_minor, NEW.usd_minor,
                NEW.exposure_usd_minor, NEW.category, NEW.reason, NEW.snapshot, NEW.snapshot_hash, NEW.policy_version, NEW.policy_hash,
                NEW.required_roles, NEW.requested_by, NEW.created_at, NEW.expires_at)
               IS DISTINCT FROM
               (OLD.request_id, OLD.kind, OLD.tenant_id, OLD.invoice_id, OLD.currency, OLD.amount_minor, OLD.usd_minor,
                OLD.exposure_usd_minor, OLD.category, OLD.reason, OLD.snapshot, OLD.snapshot_hash, OLD.policy_version, OLD.policy_hash,
                OLD.required_roles, OLD.requested_by, OLD.created_at, OLD.expires_at) THEN
                RAISE EXCEPTION 'a finance request is immutable; a material change is a new request' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state IN ('APPLIED', 'REJECTED', 'EXPIRED', 'CANCELED') AND NEW.state <> OLD.state THEN
                RAISE EXCEPTION 'finance request % is final', OLD.state USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER finance_request_guard BEFORE UPDATE OR DELETE ON commercial.finance_request
            FOR EACH ROW EXECUTE FUNCTION commercial.finance_request_guard();

        CREATE TABLE commercial.finance_approval (
            request_id    uuid NOT NULL REFERENCES commercial.finance_request (request_id),
            role          text NOT NULL,
            principal_id  uuid NOT NULL,
            decision      text NOT NULL CHECK (decision IN ('APPROVE', 'REJECT')),
            snapshot_hash text NOT NULL,
            reason        text CHECK (reason IS NULL OR length(reason) <= 500),
            decided_at    timestamptz NOT NULL,
            PRIMARY KEY (request_id, role),
            UNIQUE (request_id, principal_id)
        );
        CREATE TABLE commercial.finance_execution (
            request_id      uuid PRIMARY KEY REFERENCES commercial.finance_request (request_id),
            executed_by     uuid NOT NULL,
            idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
            outcome         text NOT NULL CHECK (outcome IN ('APPLIED', 'APPLICATION_FAILED')),
            result          jsonb,
            executed_at     timestamptz NOT NULL
        );
        CREATE FUNCTION commercial.finance_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'finance approvals and executions are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER finance_approval_append_only BEFORE UPDATE OR DELETE ON commercial.finance_approval
            FOR EACH ROW EXECUTE FUNCTION commercial.finance_append_only();
        CREATE TRIGGER finance_execution_append_only BEFORE UPDATE OR DELETE ON commercial.finance_execution
            FOR EACH ROW EXECUTE FUNCTION commercial.finance_append_only();
        CREATE TRIGGER finance_policy_signature_append_only BEFORE UPDATE OR DELETE ON commercial.finance_policy_signature
            FOR EACH ROW EXECUTE FUNCTION commercial.finance_append_only();

        CREATE TABLE commercial.finance_pilot_credit (
            pilot_credit_id uuid PRIMARY KEY,
            request_id      uuid NOT NULL UNIQUE REFERENCES commercial.finance_request (request_id),
            tenant_id       uuid NOT NULL,
            currency        text NOT NULL,
            amount_minor    bigint NOT NULL CHECK (amount_minor > 0),
            classification  text NOT NULL DEFAULT 'PILOT_NON_BILLABLE' CHECK (classification = 'PILOT_NON_BILLABLE'),
            starts_at       timestamptz NOT NULL,
            ends_at         timestamptz NOT NULL,
            created_at      timestamptz NOT NULL,
            CHECK (ends_at > starts_at)
        );
        CREATE TRIGGER finance_pilot_credit_append_only BEFORE UPDATE OR DELETE ON commercial.finance_pilot_credit
            FOR EACH ROW EXECUTE FUNCTION commercial.finance_append_only();
    """)


def downgrade() -> None:
    pass

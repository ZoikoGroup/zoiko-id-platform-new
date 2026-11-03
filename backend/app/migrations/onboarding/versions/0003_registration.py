"""Person-first sign-up: registration intents, the account index and the invitation index (ZID-UX-WF-013A; slice L-11, CR-048).

Revision ID: onboarding_0003
Revises: onboarding_0002
Create Date: 2026-11-02

registration_intent: one request to create an account. It is kept in the cell chosen for the person's country, inside the cell's dedicated
registration tenant (so row-level security applies and the email log has a tenant to sit under). The email address is held in clear only
until it is proved or expires, then nulled; afterwards only a keyed hash remains. The activation token is stored as a hash.
registration_account: keyed email hash to the account it created (the "is this address already registered" answer, never shown to a caller).
registration_invitation_index: keyed email hash to a pending invitation, so a verified address can be routed to its invitation without
any pre-authentication disclosure of which organization invited it.
"""

from zid_platform.migrations import execute_script as _run

revision = "onboarding_0003"
down_revision = "onboarding_0002"
branch_labels = None
depends_on = None

_RLS = """
    ALTER TABLE platform.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE platform.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON platform.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""
TABLES = ("registration_intent", "registration_account", "registration_invitation_index")


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.registration_intent (
            intent_id            uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            request_ref          text NOT NULL UNIQUE CHECK (length(request_ref) BETWEEN 16 AND 64),
            country              char(2) NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            journey              text NOT NULL CHECK (journey IN ('PERSONAL', 'ORGANIZATION')),
            locale               text NOT NULL CHECK (length(locale) <= 35),
            notice_version       text NOT NULL CHECK (length(notice_version) <= 40),
            email_hash           text NOT NULL CHECK (length(email_hash) BETWEEN 16 AND 128),
            email_pending        text CHECK (email_pending IS NULL OR length(email_pending) <= 254),
            state                text NOT NULL CHECK (state IN ('INITIATED', 'VERIFICATION_SENT', 'VERIFIED', 'COMPLETED',
                'EXPIRED', 'RISK_HOLD')),
            token_hash           bytea CHECK (token_hash IS NULL OR octet_length(token_hash) = 32),
            token_expires_at     timestamptz,
            resend_count         integer NOT NULL DEFAULT 0 CHECK (resend_count >= 0),
            last_sent_at         timestamptz,
            confirm_attempts     integer NOT NULL DEFAULT 0 CHECK (confirm_attempts >= 0),
            idempotency_key      text CHECK (idempotency_key IS NULL OR length(idempotency_key) <= 128),
            home_cell_id         text NOT NULL,
            existing_account     boolean NOT NULL DEFAULT false,
            account_tenant_id    uuid,
            account_principal_id uuid,
            invitation_tenant_id uuid,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            expires_at           timestamptz NOT NULL
        );
        CREATE UNIQUE INDEX registration_intent_idem ON platform.registration_intent (tenant_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL;
        CREATE INDEX registration_intent_by_address ON platform.registration_intent (tenant_id, email_hash, created_at);
        CREATE INDEX registration_intent_open ON platform.registration_intent (tenant_id, state, expires_at);

        CREATE FUNCTION platform.registration_intent_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'registration requests are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.intent_id, NEW.tenant_id, NEW.request_ref, NEW.country, NEW.journey, NEW.email_hash, NEW.home_cell_id,
                NEW.notice_version, NEW.created_at)
               IS DISTINCT FROM
               (OLD.intent_id, OLD.tenant_id, OLD.request_ref, OLD.country, OLD.journey, OLD.email_hash, OLD.home_cell_id,
                OLD.notice_version, OLD.created_at) THEN
                RAISE EXCEPTION 'a registration request cannot be rewritten' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state IN ('COMPLETED', 'EXPIRED', 'RISK_HOLD') AND NEW.state <> OLD.state THEN
                RAISE EXCEPTION 'registration request % is final', OLD.state USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.email_pending IS NOT NULL AND NEW.email_pending IS DISTINCT FROM OLD.email_pending THEN
                RAISE EXCEPTION 'the address is written once, then only cleared' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.account_tenant_id IS NOT NULL AND NEW.account_tenant_id IS DISTINCT FROM OLD.account_tenant_id THEN
                RAISE EXCEPTION 'an account is bound once' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER registration_intent_guard BEFORE UPDATE OR DELETE ON platform.registration_intent
            FOR EACH ROW EXECUTE FUNCTION platform.registration_intent_guard();

        CREATE FUNCTION platform.registration_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'registration records are append-only' USING ERRCODE = 'check_violation';
        END $$;

        CREATE TABLE platform.registration_account (
            tenant_id            uuid NOT NULL,
            email_hash           text NOT NULL,
            account_tenant_id    uuid NOT NULL,
            account_principal_id uuid NOT NULL,
            country              char(2) NOT NULL,
            created_at           timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, email_hash)
        );
        CREATE TRIGGER registration_account_append_only BEFORE UPDATE OR DELETE ON platform.registration_account
            FOR EACH ROW EXECUTE FUNCTION platform.registration_append_only();

        CREATE TABLE platform.registration_invitation_index (
            tenant_id            uuid NOT NULL,
            email_hash           text NOT NULL,
            invite_tenant_id     uuid NOT NULL,
            principal_id         uuid NOT NULL,
            role                 text NOT NULL,
            status               text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'ROUTED')),
            created_at           timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (invite_tenant_id, principal_id)
        );
        CREATE INDEX registration_invitation_by_address ON platform.registration_invitation_index (tenant_id, email_hash, status);
    """)
    for t in TABLES:
        _run(_RLS.format(t=t))


def downgrade() -> None:
    _run("""
        DROP TABLE platform.registration_invitation_index;
        DROP TABLE platform.registration_account;
        DROP TABLE platform.registration_intent;
        DROP FUNCTION platform.registration_intent_guard();
        DROP FUNCTION platform.registration_append_only();
    """)

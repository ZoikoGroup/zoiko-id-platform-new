"""Invoices, credit notes, payments and the billing state machine (ZID-COM-001 §11, §13, §30; CR-027).

Revision ID: commercial_0004
Revises: commercial_0003
Create Date: 2026-10-24

- `invoice` / `invoice_line`: built from one COMPUTED rating run. A DRAFT can be voided; an ISSUED invoice and
  its lines can never change (database triggers). Numbers are assigned at issue from `invoice_sequence`, one
  row per issuing entity locked for the issue, so numbering is gap-free.
- `invoice_chain`: per issuing entity, (sequence, document digest, chain hash) of every issued invoice. It holds
  digests only, no tenant data, so the whole chain can be verified without reading any tenant's invoices.
- `credit_note`: always against an ISSUED invoice; never more than what remains of it.
- `payment_attempt`: one row per attempt with an idempotency key; the platform never holds card data.
- `billing_account_state` + `billing_state_history`: the state machine and its insert-only history.
- `dunning_policy`: (days after due, state) rows. It ships EMPTY: grace periods are a commercial decision,
  so until one is configured, dunning moves nobody.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0004"
down_revision = "commercial_0003"
branch_labels = None
depends_on = None

STATES = (
    "('DRAFT', 'CONFIRMED', 'PROVISIONING', 'ACTIVE', 'PAST_DUE', 'RESTRICTED', 'SUSPENDED', "
    "'TERMINATING', 'CLOSED')"
)
TENANT_TABLES = (
    "invoice",
    "invoice_line",
    "credit_note",
    "payment_attempt",
    "billing_account_state",
    "billing_state_history",
)


def upgrade() -> None:
    _run(f"""
        CREATE TABLE commercial.invoice_sequence (
            issuing_entity  text PRIMARY KEY CHECK (issuing_entity ~ '^[A-Z0-9-]{{2,24}}$'),
            last_number     bigint NOT NULL DEFAULT 0 CHECK (last_number >= 0),
            last_chain_hash text
        );

        CREATE TABLE commercial.invoice_chain (
            issuing_entity  text NOT NULL REFERENCES commercial.invoice_sequence (issuing_entity),
            sequence_number bigint NOT NULL CHECK (sequence_number >= 1),
            document_digest text NOT NULL CHECK (length(document_digest) = 64),
            chain_hash      text NOT NULL CHECK (length(chain_hash) = 64),
            kind            text NOT NULL CHECK (kind IN ('INVOICE', 'CREDIT_NOTE')),
            issued_at       timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (issuing_entity, sequence_number)
        );

        CREATE TABLE commercial.invoice (
            invoice_id      uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            period          date NOT NULL,
            rating_run_id   uuid NOT NULL REFERENCES commercial.rating_run (run_id),
            issuing_entity  text NOT NULL,
            currency        text NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
            subtotal        numeric(18, 6) NOT NULL CHECK (subtotal >= 0),
            tax_total       numeric(18, 6) NOT NULL DEFAULT 0 CHECK (tax_total >= 0),
            gross           numeric(18, 6) NOT NULL CHECK (gross >= 0),
            tax_status      text NOT NULL CHECK (tax_status IN ('CONFIGURED', 'NOT_CONFIGURED', 'ZERO_TAX_OVERRIDE')),
            status          text NOT NULL CHECK (status IN ('DRAFT', 'ISSUED', 'VOID')),
            sequence_number bigint,
            issued_at       timestamptz,
            due_at          timestamptz,
            issued_by       uuid,
            document_digest text,
            chain_hash      text,
            created_by      uuid NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (issuing_entity, sequence_number),
            CHECK ((status = 'ISSUED') = (sequence_number IS NOT NULL AND issued_at IS NOT NULL
                   AND due_at IS NOT NULL AND document_digest IS NOT NULL AND chain_hash IS NOT NULL)),
            CHECK (issued_by IS NULL OR issued_by <> created_by)
        );
        CREATE UNIQUE INDEX invoice_one_live_per_run ON commercial.invoice (rating_run_id) WHERE status <> 'VOID';

        CREATE TABLE commercial.invoice_line (
            line_id     uuid PRIMARY KEY,
            invoice_id  uuid NOT NULL REFERENCES commercial.invoice (invoice_id),
            tenant_id   uuid NOT NULL,
            seq         integer NOT NULL CHECK (seq >= 1),
            kind        text NOT NULL CHECK (kind IN ('FEE', 'OVERAGE', 'CONNECTION', 'MINIMUM', 'CREDIT')),
            sku         text,
            meter_id    text,
            description text NOT NULL,
            included_units   numeric(30, 10),
            chargeable_units numeric(30, 10),
            unit_rate   numeric(18, 6),
            net         numeric(18, 6) NOT NULL,
            tax_code    text,
            tax_amount  numeric(18, 6) NOT NULL DEFAULT 0,
            gross       numeric(18, 6) NOT NULL,
            region      text,
            UNIQUE (invoice_id, seq)
        );

        CREATE TABLE commercial.credit_note (
            credit_note_id  uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            invoice_id      uuid NOT NULL REFERENCES commercial.invoice (invoice_id),
            issuing_entity  text NOT NULL,
            sequence_number bigint NOT NULL,
            amount          numeric(18, 6) NOT NULL CHECK (amount > 0),
            reason_code     text NOT NULL CHECK (reason_code ~ '^[A-Z][A-Z0-9_]{{2,40}}$'),
            reverses_line   integer,
            document_digest text NOT NULL CHECK (length(document_digest) = 64),
            issued_by       uuid NOT NULL,
            issued_at       timestamptz NOT NULL DEFAULT now(),
            UNIQUE (issuing_entity, sequence_number)
        );

        CREATE TABLE commercial.payment_attempt (
            attempt_id      uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            invoice_id      uuid NOT NULL REFERENCES commercial.invoice (invoice_id),
            idempotency_key text NOT NULL,
            amount          numeric(18, 6) NOT NULL CHECK (amount > 0),
            currency        text NOT NULL,
            status          text NOT NULL CHECK (status IN ('CAPTURED', 'FAILED')),
            provider_ref    text,
            failure_code    text,
            attempted_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, idempotency_key)
        );

        CREATE TABLE commercial.billing_account_state (
            tenant_id   uuid PRIMARY KEY,
            state       text NOT NULL CHECK (state IN {STATES}),
            reason_code text NOT NULL,
            safety_hold boolean NOT NULL DEFAULT false,
            safety_reason text,
            since       timestamptz NOT NULL DEFAULT now(),
            version     integer NOT NULL DEFAULT 1 CHECK (version >= 1),
            CHECK (safety_hold = (safety_reason IS NOT NULL))
        );

        CREATE TABLE commercial.billing_state_history (
            entry_id    uuid PRIMARY KEY,
            tenant_id   uuid NOT NULL,
            from_state  text NOT NULL,
            to_state    text NOT NULL,
            reason_code text NOT NULL,
            actor       uuid,
            at          timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE commercial.dunning_policy (
            days_after_due integer PRIMARY KEY CHECK (days_after_due >= 0),
            target_state   text NOT NULL CHECK (target_state IN ('PAST_DUE', 'RESTRICTED', 'SUSPENDED'))
        );

        CREATE FUNCTION commercial.invoice_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'invoices are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'DRAFT' THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'an issued invoice is immutable' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER invoice_guard BEFORE UPDATE OR DELETE ON commercial.invoice
            FOR EACH ROW EXECUTE FUNCTION commercial.invoice_guard();

        CREATE FUNCTION commercial.invoice_line_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            inv uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.invoice_id ELSE NEW.invoice_id END;
        BEGIN
            IF (SELECT status FROM commercial.invoice WHERE invoice_id = inv) <> 'DRAFT' THEN
                RAISE EXCEPTION 'the lines of an issued invoice are immutable' USING ERRCODE = 'check_violation';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$;
        CREATE TRIGGER invoice_line_guard BEFORE INSERT OR UPDATE OR DELETE ON commercial.invoice_line
            FOR EACH ROW EXECUTE FUNCTION commercial.invoice_line_guard();

        CREATE TRIGGER invoice_chain_immutable BEFORE UPDATE OR DELETE ON commercial.invoice_chain
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();
        CREATE TRIGGER credit_note_immutable BEFORE UPDATE OR DELETE ON commercial.credit_note
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();
        CREATE TRIGGER payment_attempt_immutable BEFORE UPDATE OR DELETE ON commercial.payment_attempt
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();
        CREATE TRIGGER billing_state_history_immutable BEFORE UPDATE OR DELETE ON commercial.billing_state_history
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();
    """)
    for t in TENANT_TABLES:
        _run(f"""
            ALTER TABLE commercial.{t} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE commercial.{t} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON commercial.{t}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.dunning_policy;
        DROP TABLE commercial.billing_state_history;
        DROP TABLE commercial.billing_account_state;
        DROP TABLE commercial.payment_attempt;
        DROP TABLE commercial.credit_note;
        DROP TABLE commercial.invoice_line;
        DROP TABLE commercial.invoice;
        DROP TABLE commercial.invoice_chain;
        DROP TABLE commercial.invoice_sequence;
        DROP FUNCTION commercial.invoice_line_guard();
        DROP FUNCTION commercial.invoice_guard();
    """)

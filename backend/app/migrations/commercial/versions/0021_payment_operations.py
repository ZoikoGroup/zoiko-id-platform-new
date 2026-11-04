"""Refunds, disputes and reconciliation with period close (ZID-UX-COM-009 F03 and F04; slice L-14f, CR-058).

refund_case / refund_approval: a refund is requested and approved in Zoiko iD and executed through the processor by a worker. Request approval, processor acceptance and
customer receipt are different facts, so the state says which one is known. The case pins its source payment, its amount and the digest every approval signs.
dispute_case / dispute_event: one case per processor dispute (dispute, merchant and livemode), created from verified events; the outcome comes from the processor, never from
a person's say-so. recon_leg / recon_exception / recon_period / recon_close_approval: processor payout lines, bank lines and ledger journals imported by the Controller,
the exceptions the match raises, and the period close, which locks a period once two different people have signed it.
None of these tables is tenant-scoped: they belong to the operator plane. A closed period is immutable (triggers below); a refund or a dispute row only moves forward.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0021"
down_revision = "commercial_0020"
branch_labels = None
depends_on = None

REFUND_STATES = "'REQUESTED', 'APPROVED', 'EXECUTION_PENDING', 'PROCESSOR_PENDING', 'PARTIAL', 'SUCCEEDED', 'FAILED', 'UNKNOWN', 'REJECTED', 'EXPIRED', 'CANCELED'"
DISPUTE_STATES = "'OPEN', 'INVESTIGATING', 'DECISION_PENDING', 'ACCEPTED', 'CONTESTING', 'SUBMISSION_RECORDED', 'SUBMISSION_VERIFIED', 'CLOSED_WON', 'CLOSED_LOST', 'CLOSED_ACCEPTED', 'CLOSED_OTHER'"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE commercial.refund_case (
            refund_id            uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            invoice_id           uuid NOT NULL REFERENCES commercial.invoice (invoice_id),
            payment_attempt_id   uuid NOT NULL REFERENCES commercial.payment_attempt (attempt_id),
            issuing_entity       text NOT NULL,
            currency             text NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
            amount_minor         bigint NOT NULL CHECK (amount_minor > 0),
            usd_minor            bigint NOT NULL CHECK (usd_minor > 0),
            exposure_usd_minor   bigint NOT NULL CHECK (exposure_usd_minor >= usd_minor),
            category             text NOT NULL CHECK (category IN ('DUPLICATE_PAYMENT', 'CORRECTION', 'CANCELLATION', 'CONTRACT_RIGHT', 'GOODWILL')),
            reason               text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 500),
            evidence_ref         text CHECK (evidence_ref IS NULL OR char_length(evidence_ref) <= 200),
            credit_note_id       uuid REFERENCES commercial.credit_note (credit_note_id),
            snapshot             jsonb NOT NULL,
            snapshot_hash        text NOT NULL CHECK (length(snapshot_hash) = 64),
            policy_version       text NOT NULL,
            required_roles       text[] NOT NULL CHECK (cardinality(required_roles) >= 1),
            state                text NOT NULL CHECK (state IN ({REFUND_STATES})),
            requested_by         uuid NOT NULL,
            created_at           timestamptz NOT NULL DEFAULT now(),
            expires_at           timestamptz NOT NULL,
            approved_at          timestamptz,
            approval_expires_at  timestamptz,
            executed_by          uuid,
            executed_at          timestamptz,
            idempotency_key      text NOT NULL UNIQUE CHECK (char_length(idempotency_key) BETWEEN 8 AND 120),
            provider_refund_ref  text,
            provider_state       text,
            provider_amount_minor bigint,
            last_verified_at     timestamptz,
            failure_code         text CHECK (failure_code IS NULL OR char_length(failure_code) <= 80),
            attempts             integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            updated_at           timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX refund_case_provider_ref ON commercial.refund_case (provider_refund_ref) WHERE provider_refund_ref IS NOT NULL;
        CREATE INDEX refund_case_payment ON commercial.refund_case (payment_attempt_id);
        CREATE INDEX refund_case_work ON commercial.refund_case (state, updated_at);

        CREATE TABLE commercial.refund_approval (
            refund_id     uuid NOT NULL REFERENCES commercial.refund_case (refund_id),
            role          text NOT NULL,
            principal_id  uuid NOT NULL,
            decision      text NOT NULL CHECK (decision IN ('APPROVE', 'REJECT')),
            snapshot_hash text NOT NULL,
            reason        text CHECK (reason IS NULL OR char_length(reason) <= 500),
            decided_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (refund_id, role)
        );

        CREATE FUNCTION commercial.refund_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'a refund case is never deleted'; END IF;
            IF (NEW.refund_id, NEW.tenant_id, NEW.invoice_id, NEW.payment_attempt_id, NEW.currency, NEW.amount_minor, NEW.snapshot_hash, NEW.idempotency_key, NEW.requested_by)
               IS DISTINCT FROM (OLD.refund_id, OLD.tenant_id, OLD.invoice_id, OLD.payment_attempt_id, OLD.currency, OLD.amount_minor, OLD.snapshot_hash, OLD.idempotency_key, OLD.requested_by) THEN
                RAISE EXCEPTION 'what a refund case asked for is never changed';
            END IF;
            IF OLD.state IN ('SUCCEEDED', 'REJECTED', 'EXPIRED', 'CANCELED') AND NEW.state <> OLD.state THEN
                RAISE EXCEPTION 'a final refund case never moves';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER refund_case_guard BEFORE UPDATE OR DELETE ON commercial.refund_case FOR EACH ROW EXECUTE FUNCTION commercial.refund_guard();
        CREATE TRIGGER refund_approval_immutable BEFORE UPDATE OR DELETE ON commercial.refund_approval
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();

        CREATE TABLE commercial.dispute_case (
            dispute_id            uuid PRIMARY KEY,
            provider_dispute_ref  text NOT NULL CHECK (char_length(provider_dispute_ref) BETWEEN 3 AND 80),
            merchant_ref          text NOT NULL DEFAULT '' CHECK (char_length(merchant_ref) <= 80),
            livemode              boolean NOT NULL,
            tenant_id             uuid,
            invoice_id            uuid,
            issuing_entity        text,
            payment_attempt_id    uuid,
            payment_intent_ref    text,
            reason                text NOT NULL DEFAULT 'unknown' CHECK (char_length(reason) <= 80),
            amount_minor          bigint NOT NULL DEFAULT 0 CHECK (amount_minor >= 0),
            currency              text NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{{3}}$'),
            provider_status       text NOT NULL DEFAULT 'unknown' CHECK (char_length(provider_status) <= 60),
            evidence_due_by       timestamptz,
            deadline_zone         text NOT NULL DEFAULT 'UTC',
            state                 text NOT NULL CHECK (state IN ({DISPUTE_STATES})),
            owner                 uuid,
            strategy              text CHECK (strategy IN ('ACCEPT', 'CONTEST')),
            strategy_by           uuid,
            strategy_at           timestamptz,
            strategy_reason       text CHECK (strategy_reason IS NULL OR char_length(strategy_reason) <= 500),
            evidence_version      integer NOT NULL DEFAULT 0,
            evidence              jsonb NOT NULL DEFAULT '{{}}',
            submission_ref        text CHECK (submission_ref IS NULL OR char_length(submission_ref) <= 120),
            submitted_by          uuid,
            submission_recorded_at timestamptz,
            submission_verified_at timestamptz,
            outcome               text CHECK (outcome IS NULL OR char_length(outcome) <= 60),
            fee_minor             bigint,
            funds_withdrawn_minor bigint NOT NULL DEFAULT 0,
            last_synced_at        timestamptz NOT NULL DEFAULT now(),
            created_at            timestamptz NOT NULL DEFAULT now(),
            updated_at            timestamptz NOT NULL DEFAULT now(),
            UNIQUE (provider_dispute_ref, merchant_ref, livemode)
        );
        CREATE INDEX dispute_case_open ON commercial.dispute_case (state, evidence_due_by);
        CREATE INDEX dispute_case_payment ON commercial.dispute_case (payment_intent_ref);

        CREATE TABLE commercial.dispute_event (
            event_id    uuid PRIMARY KEY,
            dispute_id  uuid NOT NULL REFERENCES commercial.dispute_case (dispute_id),
            kind        text NOT NULL CHECK (kind ~ '^[A-Z][A-Z_]{{2,40}}$'),
            actor       uuid,
            detail      jsonb NOT NULL DEFAULT '{{}}',
            at          timestamptz NOT NULL DEFAULT now()
        );
        CREATE TRIGGER dispute_event_immutable BEFORE UPDATE OR DELETE ON commercial.dispute_event
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();

        -- the money events the platform says happened, for every organization, readable without a tenant: a payment captured, a refund the processor confirmed, money a
        -- dispute withdrew. Reconciliation reads this; it never reads another organization's invoices.
        CREATE TABLE commercial.recon_item (
            item_id        uuid PRIMARY KEY,
            kind           text NOT NULL CHECK (kind IN ('PAYMENT', 'REFUND', 'DISPUTE')),
            tenant_id      uuid NOT NULL,
            issuing_entity text NOT NULL,
            currency       text NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
            amount_minor   bigint NOT NULL,
            ref            text NOT NULL CHECK (char_length(ref) BETWEEN 1 AND 120),
            journal_ref    text NOT NULL DEFAULT '',
            occurred_on    date NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (kind, ref)
        );
        CREATE INDEX recon_item_period ON commercial.recon_item (issuing_entity, occurred_on);

        CREATE FUNCTION commercial.minor_units(amount numeric, currency text) RETURNS bigint LANGUAGE sql IMMUTABLE AS $$
            SELECT round(amount * CASE
                WHEN currency IN ('BIF','CLP','DJF','GNF','JPY','KMF','KRW','MGA','PYG','RWF','UGX','VND','VUV','XAF','XOF','XPF') THEN 1
                WHEN currency IN ('BHD','IQD','JOD','KWD','LYD','OMR','TND') THEN 1000
                ELSE 100 END)::bigint
        $$;

        CREATE FUNCTION commercial.note_captured_payment() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = commercial, pg_temp AS $$
        DECLARE inv record;
        BEGIN
            IF NEW.status <> 'CAPTURED' OR NEW.provider_ref IS NULL THEN RETURN NEW; END IF;
            SELECT issuing_entity, CASE WHEN sequence_number IS NULL THEN '' ELSE issuing_entity || '-' || lpad(sequence_number::text, 8, '0') END AS number INTO inv FROM commercial.invoice WHERE invoice_id = NEW.invoice_id;
            IF NOT FOUND THEN RETURN NEW; END IF;
            INSERT INTO commercial.recon_item (item_id, kind, tenant_id, issuing_entity, currency, amount_minor, ref, journal_ref, occurred_on)
            VALUES (NEW.attempt_id, 'PAYMENT', NEW.tenant_id, inv.issuing_entity, NEW.currency, commercial.minor_units(NEW.amount, NEW.currency),
                    NEW.provider_ref, coalesce(inv.number, ''), (NEW.attempted_at AT TIME ZONE 'UTC')::date)
            ON CONFLICT (kind, ref) DO NOTHING;
            RETURN NEW;
        END $$;
        CREATE TRIGGER payment_attempt_recon AFTER INSERT OR UPDATE ON commercial.payment_attempt
            FOR EACH ROW EXECUTE FUNCTION commercial.note_captured_payment();

        CREATE TABLE commercial.recon_leg (
            leg_id         uuid PRIMARY KEY,
            leg_type       text NOT NULL CHECK (leg_type IN ('PAYOUT_LINE', 'BANK_LINE', 'GL_JOURNAL')),
            issuing_entity text NOT NULL,
            currency       text NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
            amount_minor   bigint NOT NULL,
            ref            text NOT NULL CHECK (char_length(ref) BETWEEN 1 AND 120),
            link_ref       text NOT NULL DEFAULT '' CHECK (char_length(link_ref) <= 120),
            occurred_on    date NOT NULL,
            source_hash    text NOT NULL CHECK (length(source_hash) = 64),
            imported_by    uuid NOT NULL,
            imported_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (leg_type, issuing_entity, ref, link_ref)
        );
        CREATE INDEX recon_leg_link ON commercial.recon_leg (issuing_entity, link_ref);
        CREATE TRIGGER recon_leg_immutable BEFORE UPDATE OR DELETE ON commercial.recon_leg
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();

        CREATE TABLE commercial.recon_period (
            issuing_entity text NOT NULL,
            period         text NOT NULL CHECK (period ~ '^[0-9]{{4}}-(0[1-9]|1[0-2])$'),
            state          text NOT NULL CHECK (state IN ('OPEN', 'READY_FOR_CLOSE', 'CLOSED', 'REOPEN_REQUESTED')),
            close_hash     text,
            receipt        jsonb,
            closed_at      timestamptz,
            reopen_reason  text,
            reopen_impact  text,
            reopen_requested_by uuid,
            updated_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (issuing_entity, period)
        );

        CREATE TABLE commercial.recon_close_approval (
            approval_id    uuid PRIMARY KEY,
            issuing_entity text NOT NULL,
            period         text NOT NULL,
            kind           text NOT NULL CHECK (kind IN ('CLOSE', 'REOPEN')),
            role           text NOT NULL,
            principal_id   uuid NOT NULL,
            snapshot_hash  text NOT NULL,
            decided_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (issuing_entity, period, kind, role, snapshot_hash)
        );
        CREATE TRIGGER recon_close_approval_immutable BEFORE UPDATE OR DELETE ON commercial.recon_close_approval
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();

        CREATE TABLE commercial.recon_exception (
            exception_id   uuid PRIMARY KEY,
            issuing_entity text NOT NULL,
            period         text NOT NULL,
            source_kind    text NOT NULL CHECK (source_kind IN ('PAYMENT', 'REFUND', 'DISPUTE')),
            source_id      uuid NOT NULL,
            reason         text NOT NULL CHECK (char_length(reason) <= 200),
            owner          uuid,
            resolution     text CHECK (resolution IS NULL OR char_length(resolution) <= 500),
            resolved_by    uuid,
            resolved_at    timestamptz,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (source_kind, source_id, period)
        );

        CREATE FUNCTION commercial.recon_period_lock() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE p text; s text;
        BEGIN
            IF TG_TABLE_NAME = 'recon_leg' THEN p := to_char(NEW.occurred_on, 'YYYY-MM'); ELSE p := NEW.period; END IF;
            SELECT state INTO s FROM commercial.recon_period WHERE issuing_entity = NEW.issuing_entity AND period = p;
            IF s = 'CLOSED' THEN
                RAISE EXCEPTION 'period % is closed and never edited: reopen it with two approvals first', p;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER recon_leg_period_lock BEFORE INSERT ON commercial.recon_leg FOR EACH ROW EXECUTE FUNCTION commercial.recon_period_lock();
        CREATE TRIGGER recon_exception_period_lock BEFORE INSERT OR UPDATE ON commercial.recon_exception FOR EACH ROW EXECUTE FUNCTION commercial.recon_period_lock();

        -- payments captured before this change are items too
        SET LOCAL row_security = off;
        INSERT INTO commercial.recon_item (item_id, kind, tenant_id, issuing_entity, currency, amount_minor, ref, journal_ref, occurred_on)
        SELECT a.attempt_id, 'PAYMENT', a.tenant_id, i.issuing_entity, a.currency, commercial.minor_units(a.amount, a.currency), a.provider_ref,
               CASE WHEN i.sequence_number IS NULL THEN '' ELSE i.issuing_entity || '-' || lpad(i.sequence_number::text, 8, '0') END, (a.attempted_at AT TIME ZONE 'UTC')::date
          FROM commercial.payment_attempt a JOIN commercial.invoice i ON i.invoice_id = a.invoice_id
         WHERE a.status = 'CAPTURED' AND a.provider_ref IS NOT NULL
        ON CONFLICT (kind, ref) DO NOTHING;
    """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER payment_attempt_recon ON commercial.payment_attempt;
        DROP FUNCTION commercial.note_captured_payment();
        DROP FUNCTION commercial.minor_units(numeric, text);
        DROP TABLE commercial.recon_item;
        DROP TABLE commercial.recon_exception;
        DROP TABLE commercial.recon_close_approval;
        DROP TABLE commercial.recon_period;
        DROP TABLE commercial.recon_leg;
        DROP TABLE commercial.dispute_event;
        DROP TABLE commercial.dispute_case;
        DROP TABLE commercial.refund_approval;
        DROP TABLE commercial.refund_case;
        DROP FUNCTION commercial.recon_period_lock();
        DROP FUNCTION commercial.refund_guard();
    """)

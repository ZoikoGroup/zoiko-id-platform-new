"""The partner channel: partner profile and agreement, permitted child tenants, and settlement entries (ZID-COM-WF-2026-11 WF-I06, P01, CW-12, INV-09).

Revision ID: commercial_0032
Revises: commercial_0031
Create Date: 2026-11-13

A partner (reseller, referral or embedded ISV) is recorded with a REFERENCE to its signed channel agreement, the territory it may sell into, and who the bill-to is
(the partner or the child, never both). It is created PROPOSED and activated by a different person. A child tenant belongs to at most one live partner, so an event
can never be billed through two channels. Nothing here prices anything, takes a commission by itself or pays anyone out: a settlement entry is only a proposal that a
second person approves, and only where a signed settlement arrangement is on file.

Platform configuration, not tenant data: no row-level security on these tables. The operator plane may read the billable ledger across companies, read only,
to reconcile a partner's attributed usage.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0032"
down_revision = "commercial_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.partner (
            partner_id        uuid PRIMARY KEY,
            name              text NOT NULL CHECK (char_length(btrim(name)) BETWEEN 2 AND 160),
            kind              text NOT NULL CHECK (kind IN ('RESELLER', 'REFERRAL', 'EMBEDDED_ISV')),
            parent_tenant_id  uuid NOT NULL,
            bill_to           text NOT NULL CHECK (bill_to IN ('PARTNER', 'CHILD')),
            territory         text[] NOT NULL CHECK (cardinality(territory) BETWEEN 1 AND 250),
            agreement_ref     text NOT NULL CHECK (char_length(btrim(agreement_ref)) BETWEEN 1 AND 255),
            brand_terms_ref   text CHECK (brand_terms_ref IS NULL OR char_length(btrim(brand_terms_ref)) BETWEEN 1 AND 255),
            settlement_ref    text CHECK (settlement_ref IS NULL OR char_length(btrim(settlement_ref)) BETWEEN 1 AND 255),
            status            text NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'ACTIVE', 'SUSPENDED', 'TERMINATED')),
            effective_from    timestamptz,
            effective_until   timestamptz,
            created_by        uuid NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            activated_by      uuid,
            activated_at      timestamptz,
            CHECK (status IN ('PROPOSED', 'TERMINATED') OR (activated_by IS NOT NULL AND effective_from IS NOT NULL)),
            CHECK (activated_by IS NULL OR activated_by <> created_by),
            CHECK (effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from)
        );

        CREATE FUNCTION commercial.partner_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a partner is never deleted; terminate it' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'TERMINATED' THEN
                RAISE EXCEPTION 'a terminated partner is final' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status <> 'PROPOSED'
               AND (NEW.partner_id, NEW.name, NEW.kind, NEW.parent_tenant_id, NEW.bill_to, NEW.territory, NEW.agreement_ref, NEW.created_by, NEW.created_at)
                   IS DISTINCT FROM
                   (OLD.partner_id, OLD.name, OLD.kind, OLD.parent_tenant_id, OLD.bill_to, OLD.territory, OLD.agreement_ref, OLD.created_by, OLD.created_at) THEN
                RAISE EXCEPTION 'the terms of a partner are fixed once it has been activated' USING ERRCODE = 'check_violation';
            END IF;
            IF NOT (
                (OLD.status = NEW.status)
                OR (OLD.status = 'PROPOSED' AND NEW.status IN ('ACTIVE', 'TERMINATED'))
                OR (OLD.status = 'ACTIVE' AND NEW.status IN ('SUSPENDED', 'TERMINATED'))
                OR (OLD.status = 'SUSPENDED' AND NEW.status IN ('ACTIVE', 'TERMINATED'))
            ) THEN
                RAISE EXCEPTION 'that partner status change is not allowed' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER partner_guard BEFORE UPDATE OR DELETE ON commercial.partner
            FOR EACH ROW EXECUTE FUNCTION commercial.partner_guard();

        CREATE TABLE commercial.partner_child (
            partner_id              uuid NOT NULL REFERENCES commercial.partner (partner_id),
            child_tenant_id         uuid NOT NULL,
            permitted_by            uuid NOT NULL,
            permitted_at            timestamptz NOT NULL DEFAULT now(),
            downstream_consent_ref  text CHECK (downstream_consent_ref IS NULL OR char_length(btrim(downstream_consent_ref)) BETWEEN 1 AND 255),
            usage_visible           boolean NOT NULL DEFAULT false,
            revoked_at              timestamptz,
            PRIMARY KEY (partner_id, child_tenant_id)
        );
        -- one live channel per child: an operation can never be attributed to two partners
        CREATE UNIQUE INDEX partner_child_one_live ON commercial.partner_child (child_tenant_id) WHERE revoked_at IS NULL;
        CREATE FUNCTION commercial.partner_child_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a partner child link is never deleted; revoke it' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL
               AND (NEW.partner_id, NEW.child_tenant_id, NEW.permitted_by, NEW.permitted_at, NEW.downstream_consent_ref, NEW.usage_visible)
                   IS NOT DISTINCT FROM
                   (OLD.partner_id, OLD.child_tenant_id, OLD.permitted_by, OLD.permitted_at, OLD.downstream_consent_ref, OLD.usage_visible) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'a partner child link changes only by being revoked' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER partner_child_guard BEFORE UPDATE OR DELETE ON commercial.partner_child
            FOR EACH ROW EXECUTE FUNCTION commercial.partner_child_guard();

        CREATE TABLE commercial.partner_settlement (
            settlement_id  uuid PRIMARY KEY,
            partner_id     uuid NOT NULL REFERENCES commercial.partner (partner_id),
            period         date NOT NULL CHECK (period = date_trunc('month', period)::date),
            kind           text NOT NULL CHECK (kind IN ('COMMISSION', 'BILLING_REPORT', 'CREDIT_NOTE')),
            amount         numeric(18, 6) NOT NULL CHECK (amount > 0),
            currency       text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            status         text NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'APPROVED', 'REJECTED')),
            proposed_by    uuid NOT NULL,
            proposed_at    timestamptz NOT NULL DEFAULT now(),
            decided_by     uuid,
            decided_at     timestamptz,
            reason         text CHECK (reason IS NULL OR char_length(btrim(reason)) BETWEEN 3 AND 500),
            CHECK ((status = 'PROPOSED') = (decided_by IS NULL)),
            CHECK (decided_by IS NULL OR decided_by <> proposed_by)
        );
        CREATE FUNCTION commercial.partner_settlement_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NOT EXISTS (SELECT 1 FROM commercial.partner WHERE partner_id = NEW.partner_id AND settlement_ref IS NOT NULL AND status = 'ACTIVE') THEN
                    RAISE EXCEPTION 'a settlement needs an active partner with a signed settlement arrangement on file' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.status = 'PROPOSED' AND NEW.status IN ('APPROVED', 'REJECTED')
               AND (NEW.settlement_id, NEW.partner_id, NEW.period, NEW.kind, NEW.amount, NEW.currency, NEW.proposed_by, NEW.proposed_at)
                   IS NOT DISTINCT FROM
                   (OLD.settlement_id, OLD.partner_id, OLD.period, OLD.kind, OLD.amount, OLD.currency, OLD.proposed_by, OLD.proposed_at) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'a settlement entry is decided once and never changed or deleted' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER partner_settlement_guard BEFORE INSERT OR UPDATE OR DELETE ON commercial.partner_settlement
            FOR EACH ROW EXECUTE FUNCTION commercial.partner_settlement_guard();

        -- the operator plane reads the ledger across companies, read only, to reconcile what a partner brought in
        CREATE POLICY operator_read ON commercial.billable_event FOR SELECT
            USING (current_setting('app.commercial_scope', true) = 'operator');
    """)


def downgrade() -> None:
    _run("""
        DROP POLICY operator_read ON commercial.billable_event;
        DROP TABLE commercial.partner_settlement;
        DROP TABLE commercial.partner_child;
        DROP TABLE commercial.partner;
        DROP FUNCTION commercial.partner_settlement_guard();
        DROP FUNCTION commercial.partner_child_guard();
        DROP FUNCTION commercial.partner_guard();
    """)

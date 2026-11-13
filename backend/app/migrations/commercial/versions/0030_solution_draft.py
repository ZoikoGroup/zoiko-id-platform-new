"""Saved solution configurations and the quote requests made from them (ZID-COM-WF-2026-11 WF-C02, B06).

Revision ID: commercial_0030
Revises: commercial_0029
Create Date: 2026-11-13

A draft is what a buyer configured in the solution builder: purpose, families, markets and expected volumes. It is NOT an entitlement and it prices nothing. A
draft is immutable once saved (save again for a new one); the only change allowed is the single move to QUOTE_REQUESTED, which freezes the scope, the
assumptions and the market exceptions seen at that moment for the deal desk. A draft expires 30 days after it is saved and cannot then be sent. Tenant rows
are isolated; the operator plane can read them across tenants to work the quote requests.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0030"
down_revision = "commercial_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.solution_draft (
            draft_id            uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            created_by          uuid NOT NULL,
            purpose             text NOT NULL CHECK (purpose IN ('ONBOARDING', 'COMPLIANCE', 'PROFESSIONAL_HIRING', 'REGULATED_TRANSACTION', 'MARKETPLACE')),
            families            text[] NOT NULL CHECK (cardinality(families) BETWEEN 1 AND 16),
            subject_country     text CHECK (subject_country ~ '^[A-Z]{2}$'),
            payer_country       text CHECK (payer_country ~ '^[A-Z]{2}$'),
            operating_region    text CHECK (char_length(operating_region) BETWEEN 2 AND 40),
            volumes             jsonb NOT NULL DEFAULT '{}'::jsonb,
            scope_hash          text NOT NULL CHECK (scope_hash ~ '^[0-9a-f]{64}$'),
            status              text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'QUOTE_REQUESTED')),
            created_at          timestamptz NOT NULL DEFAULT now(),
            expires_at          timestamptz NOT NULL DEFAULT now() + interval '30 days',
            quote_snapshot      jsonb,
            quote_requested_by  uuid,
            quote_requested_at  timestamptz,
            CHECK ((status = 'QUOTE_REQUESTED') = (quote_snapshot IS NOT NULL AND quote_requested_by IS NOT NULL AND quote_requested_at IS NOT NULL))
        );
        CREATE INDEX solution_draft_tenant ON commercial.solution_draft (tenant_id, created_at DESC);
        CREATE INDEX solution_draft_requested ON commercial.solution_draft (quote_requested_at) WHERE status = 'QUOTE_REQUESTED';

        CREATE FUNCTION commercial.solution_draft_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a saved solution configuration is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'DRAFT' AND NEW.status = 'QUOTE_REQUESTED'
               AND (NEW.draft_id, NEW.tenant_id, NEW.created_by, NEW.purpose, NEW.families, NEW.subject_country, NEW.payer_country, NEW.operating_region,
                    NEW.volumes, NEW.scope_hash, NEW.created_at, NEW.expires_at)
                   IS NOT DISTINCT FROM
                   (OLD.draft_id, OLD.tenant_id, OLD.created_by, OLD.purpose, OLD.families, OLD.subject_country, OLD.payer_country, OLD.operating_region,
                    OLD.volumes, OLD.scope_hash, OLD.created_at, OLD.expires_at) THEN
                IF now() > OLD.expires_at THEN
                    RAISE EXCEPTION 'the draft has expired' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'a saved solution configuration is immutable' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER solution_draft_guard BEFORE UPDATE OR DELETE ON commercial.solution_draft
            FOR EACH ROW EXECUTE FUNCTION commercial.solution_draft_guard();

        ALTER TABLE commercial.solution_draft ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.solution_draft FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.solution_draft
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        CREATE POLICY operator_scope ON commercial.solution_draft
            USING (current_setting('app.commercial_scope', true) = 'operator');
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.solution_draft;
        DROP FUNCTION commercial.solution_draft_guard();
    """)

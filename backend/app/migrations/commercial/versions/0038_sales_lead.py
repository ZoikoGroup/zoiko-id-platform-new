"""Public sales inquiries (ZID-UX-PRICE-004 WF-P07; QA P28, P38, P39).

Revision ID: commercial_0038
Revises: commercial_0037
Create Date: 2026-11-13

A lead is a request for a conversation from the public pricing page. The table references NO tenant, entitlement, order, payment customer or mandate: nothing a lead does can
create one. It records the least Sales needs, the privacy notice version the person saw, and how far the conversation has got. Status moves forward one step at a time or ends
as CLOSED or SPAM; nothing moves backwards, nothing but status changes, and nothing is deleted. One OPEN inquiry per person, company and kind: a repeat returns the same one.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0038"
down_revision = "commercial_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.sales_lead (
            lead_id         uuid PRIMARY KEY,
            kind            text NOT NULL CHECK (kind IN ('ENTERPRISE', 'PARTNER')),
            partner_type    text CHECK (partner_type IN ('RESELLER', 'REFERRAL', 'EMBEDDED_ISV')),
            contact_name    text NOT NULL CHECK (char_length(contact_name) BETWEEN 2 AND 120),
            work_email      text NOT NULL CHECK (char_length(work_email) BETWEEN 6 AND 254),
            company         text NOT NULL CHECK (char_length(company) BETWEEN 2 AND 160),
            country         text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            region          text CHECK (region IS NULL OR char_length(region) BETWEEN 2 AND 40),
            capabilities    text[] NOT NULL DEFAULT '{}' CHECK (cardinality(capabilities) <= 16),
            volume_band     text NOT NULL CHECK (volume_band IN ('UNDER_1K', 'FROM_1K_TO_10K', 'FROM_10K_TO_100K', 'OVER_100K', 'UNSURE')),
            entities        integer CHECK (entities IS NULL OR entities BETWEEN 1 AND 10000),
            referral_source text CHECK (referral_source IS NULL OR char_length(referral_source) <= 80),
            security_needs  text CHECK (security_needs IS NULL OR char_length(security_needs) <= 500),
            notice_version  text NOT NULL CHECK (char_length(notice_version) BETWEEN 1 AND 40),
            dedupe_key      text NOT NULL CHECK (dedupe_key ~ '^[0-9a-f]{64}$'),
            status          text NOT NULL DEFAULT 'RECEIVED' CHECK (status IN ('RECEIVED', 'QUALIFIED', 'SCOPED', 'REVIEW', 'OFFER_SENT', 'CLOSED', 'SPAM')),
            status_reason   text CHECK (status_reason IS NULL OR char_length(status_reason) BETWEEN 3 AND 500),
            decided_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CHECK ((kind = 'PARTNER') = (partner_type IS NOT NULL)),
            CHECK ((status IN ('CLOSED', 'SPAM')) = (status_reason IS NOT NULL))
        );
        CREATE UNIQUE INDEX sales_lead_one_open ON commercial.sales_lead (dedupe_key) WHERE status NOT IN ('CLOSED', 'SPAM');
        CREATE INDEX sales_lead_email_day ON commercial.sales_lead (work_email, created_at);
        CREATE INDEX sales_lead_status ON commercial.sales_lead (status, created_at DESC);

        CREATE FUNCTION commercial.sales_lead_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a sales inquiry is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.lead_id, NEW.kind, NEW.partner_type, NEW.contact_name, NEW.work_email, NEW.company, NEW.country, NEW.region, NEW.capabilities, NEW.volume_band,
                NEW.entities, NEW.referral_source, NEW.security_needs, NEW.notice_version, NEW.dedupe_key, NEW.created_at)
               IS DISTINCT FROM
               (OLD.lead_id, OLD.kind, OLD.partner_type, OLD.contact_name, OLD.work_email, OLD.company, OLD.country, OLD.region, OLD.capabilities, OLD.volume_band,
                OLD.entities, OLD.referral_source, OLD.security_needs, OLD.notice_version, OLD.dedupe_key, OLD.created_at) THEN
                RAISE EXCEPTION 'only the status of a sales inquiry changes' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status IN ('CLOSED', 'SPAM') THEN
                RAISE EXCEPTION 'a closed inquiry is final' USING ERRCODE = 'check_violation';
            END IF;
            IF NOT (
                OLD.status = NEW.status
                OR (OLD.status = 'RECEIVED' AND NEW.status IN ('QUALIFIED', 'CLOSED', 'SPAM'))
                OR (OLD.status = 'QUALIFIED' AND NEW.status IN ('SCOPED', 'CLOSED', 'SPAM'))
                OR (OLD.status = 'SCOPED' AND NEW.status IN ('REVIEW', 'CLOSED', 'SPAM'))
                OR (OLD.status = 'REVIEW' AND NEW.status IN ('OFFER_SENT', 'CLOSED', 'SPAM'))
                OR (OLD.status = 'OFFER_SENT' AND NEW.status IN ('CLOSED', 'SPAM'))
            ) THEN
                RAISE EXCEPTION 'that inquiry status change is not allowed' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER sales_lead_guard BEFORE UPDATE OR DELETE ON commercial.sales_lead
            FOR EACH ROW EXECUTE FUNCTION commercial.sales_lead_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.sales_lead;
        DROP FUNCTION commercial.sales_lead_guard();
    """)

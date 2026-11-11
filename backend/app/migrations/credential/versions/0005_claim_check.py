"""Claim checks: one immutable, source-backed assertion per check (GID-037, CR-069).

Revision ID: credential_0005
Revises: credential_0004
Create Date: 2026-11-11

ZID-GAP-001 v2.0 sections 04.1 and 08.1. `cred.claim_check` records the outcome of verifying an academic, license,
certificate, CPD, employment or insurance claim against a source: the normalized assertion (five separate facts, no
document and no raw claimed value), a digest of what was claimed, which service pack allowed it and which source answered.
Rows are never edited: a later check supersedes an earlier one (`superseded_at`), so the history stays.
Single database: the table lives in the existing `cred` schema of the cell database, like every other credential table.
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0005"
down_revision = "credential_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE cred.claim_check (
            check_id             uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            subject_principal_id uuid NOT NULL,
            claim_type           text NOT NULL CHECK (claim_type IN (
                'ACADEMIC_AWARD', 'PROFESSIONAL_LICENSE', 'INDUSTRY_CERTIFICATE', 'CPD_RECORD',
                'EMPLOYMENT_ASSERTION', 'INSURANCE_ASSERTION', 'DISCIPLINARY_FINDING')),
            decision             text NOT NULL CHECK (decision IN (
                'VERIFIED', 'NOT_VERIFIED', 'REVIEW', 'SOURCE_UNAVAILABLE', 'UNVERIFIABLE')),
            assurance_tier       text NOT NULL CHECK (assurance_tier IN (
                'SELF_DECLARED', 'DOCUMENT_INTEGRITY', 'ISSUER_AUTHENTICATED', 'PRIMARY_SOURCE_CONFIRMED')),
            pack_id              text NOT NULL,
            pack_version         integer NOT NULL CHECK (pack_version >= 1),
            jurisdiction         jsonb NOT NULL DEFAULT '{}'::jsonb,
            claimed_digest       bytea NOT NULL CHECK (length(claimed_digest) = 32),
            source_id            text NOT NULL,
            source_reference     text,
            source_result        text NOT NULL CHECK (source_result IN ('RESPONDED', 'UNAVAILABLE', 'NO_RECORD')),
            source_checked_at    timestamptz NOT NULL,
            assertion            jsonb NOT NULL,
            created_at           timestamptz NOT NULL DEFAULT now(),
            superseded_at        timestamptz
        );
        CREATE INDEX claim_check_subject ON cred.claim_check (tenant_id, subject_principal_id, claim_type, created_at DESC);
        CREATE FUNCTION cred.claim_check_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'claim checks are not deleted' USING ERRCODE = '23514';
            END IF;
            IF (NEW.check_id, NEW.tenant_id, NEW.subject_principal_id, NEW.claim_type, NEW.decision, NEW.assurance_tier,
                NEW.pack_id, NEW.pack_version, NEW.jurisdiction, NEW.claimed_digest, NEW.source_id, NEW.source_reference,
                NEW.source_result, NEW.source_checked_at, NEW.assertion, NEW.created_at)
               IS DISTINCT FROM
               (OLD.check_id, OLD.tenant_id, OLD.subject_principal_id, OLD.claim_type, OLD.decision, OLD.assurance_tier,
                OLD.pack_id, OLD.pack_version, OLD.jurisdiction, OLD.claimed_digest, OLD.source_id, OLD.source_reference,
                OLD.source_result, OLD.source_checked_at, OLD.assertion, OLD.created_at)
               OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at) THEN
                RAISE EXCEPTION 'a claim check is immutable; only superseded_at may be set once' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER claim_check_guard BEFORE UPDATE OR DELETE ON cred.claim_check
            FOR EACH ROW EXECUTE FUNCTION cred.claim_check_guard();
        ALTER TABLE cred.claim_check ENABLE ROW LEVEL SECURITY;
        ALTER TABLE cred.claim_check FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON cred.claim_check
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER claim_check_guard ON cred.claim_check;
        DROP FUNCTION cred.claim_check_guard();
        DROP TABLE cred.claim_check;
    """)

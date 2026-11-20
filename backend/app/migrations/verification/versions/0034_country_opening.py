"""Opening a country for sign-up from the operator console (ZID-EDR-001 section 4; CR-127).

Revision ID: verification_0034
Revises: verification_0033
Create Date: 2026-11-19

A country opens for REGISTRATION when an operator records the six go/no-go approvals (each with its reference), the date it takes effect and the cell that
holds its accounts. Each decision is a new row: the table is append-only, so every opening and closing stays on record, and the newest version of a country
is the one in force. Platform configuration, not tenant data: no row-level security. The registry file still names the countries and holds everything else;
this table can only open or close REGISTRATION for a country that file lists.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0034"
down_revision = "verification_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.country_opening (
            country        text    NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            version        integer NOT NULL CHECK (version >= 1),
            status         text    NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
            effective_from timestamptz,
            approvals      jsonb   NOT NULL DEFAULT '{}'::jsonb,
            home_cells     text[]  NOT NULL DEFAULT '{}',
            reason         text    CHECK (reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500),
            recorded_by    text    NOT NULL CHECK (length(btrim(recorded_by)) BETWEEN 1 AND 128),
            recorded_at    timestamptz NOT NULL,
            PRIMARY KEY (country, version),
            CONSTRAINT country_opening_open_complete CHECK (
                status <> 'OPEN' OR (
                    effective_from IS NOT NULL
                    AND cardinality(home_cells) >= 1
                    AND approvals ?& ARRAY['legal_assessment', 'supplier_coverage', 'privacy_and_data',
                                           'security_and_resilience', 'local_support', 'signoff']
                )
            )
        );
        CREATE FUNCTION verify.country_opening_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'country openings are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER country_opening_guard BEFORE UPDATE OR DELETE ON verify.country_opening
            FOR EACH ROW EXECUTE FUNCTION verify.country_opening_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.country_opening;
        DROP FUNCTION verify.country_opening_guard();
    """)

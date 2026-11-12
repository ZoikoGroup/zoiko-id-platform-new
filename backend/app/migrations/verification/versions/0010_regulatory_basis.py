"""The regulatory basis of a verification session (ZID-RCG-003 section 09.1 RegulatoryDecision; CR-082).

Revision ID: verification_0010
Revises: verification_0009
Create Date: 2026-11-12

`verify.verification_session.regulatory_basis` records, at start, what the regulatory checks went on: the governing policy pack (id, version, hash),
the launched market release (id, version, hash), the provider and its entitlement outcome, whether the kill switch was checked, and the switch modes.
It is written once and never changed: a trigger refuses any later edit, so a decision can always be traced to the rules in force when it began.
`decision_basis` (verification_0002) stays what it is: the provider's normalized facts at decision time.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0010"
down_revision = "verification_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.verification_session ADD COLUMN regulatory_basis jsonb;

        CREATE FUNCTION verify.regulatory_basis_write_once() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.regulatory_basis IS NOT NULL AND NEW.regulatory_basis IS DISTINCT FROM OLD.regulatory_basis THEN
                RAISE EXCEPTION 'the regulatory basis of a verification is written once' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER regulatory_basis_write_once BEFORE UPDATE OF regulatory_basis ON verify.verification_session
            FOR EACH ROW EXECUTE FUNCTION verify.regulatory_basis_write_once();
    """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER regulatory_basis_write_once ON verify.verification_session;
        DROP FUNCTION verify.regulatory_basis_write_once();
        ALTER TABLE verify.verification_session DROP COLUMN regulatory_basis;
    """)

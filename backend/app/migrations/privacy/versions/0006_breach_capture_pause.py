"""Breach containment: pause biometric capture (ZID-DTV-WF-04 B09, QA-033, QA-034; CR-108).

Revision ID: privacy_0006
Revises: privacy_0005
Create Date: 2026-11-17

A breach case can stop new biometric processing for its tenant in the jurisdictions it names, at once, without waiting for an operator: the
biometric eligibility check refuses every method there while the case is open and its capture state is PAUSED. The state moves NOT_PAUSED -> PAUSED
-> RELEASED only; a release is by someone other than whoever paused it, with a reason. Closing the case ends the pause. The table repeats those rules.
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0006"
down_revision = "privacy_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE privacy.breach_case
            ADD COLUMN capture_state          text NOT NULL DEFAULT 'NOT_PAUSED'
                                              CHECK (capture_state IN ('NOT_PAUSED', 'PAUSED', 'RELEASED')),
            ADD COLUMN capture_paused_by      uuid,
            ADD COLUMN capture_paused_at      timestamptz,
            ADD COLUMN capture_released_by    uuid,
            ADD COLUMN capture_released_at    timestamptz,
            ADD COLUMN capture_release_reason text CHECK (capture_release_reason IS NULL OR length(btrim(capture_release_reason)) BETWEEN 10 AND 1000),
            ADD CONSTRAINT breach_capture_paused_fields
                CHECK ((capture_state = 'NOT_PAUSED') = (capture_paused_by IS NULL AND capture_paused_at IS NULL)),
            ADD CONSTRAINT breach_capture_released_fields
                CHECK ((capture_state = 'RELEASED') = (capture_released_by IS NOT NULL AND capture_released_at IS NOT NULL
                                                       AND capture_release_reason IS NOT NULL)),
            ADD CONSTRAINT breach_capture_released_by_other
                CHECK (capture_released_by IS NULL OR capture_released_by <> capture_paused_by);
        CREATE INDEX breach_case_capture_paused ON privacy.breach_case (tenant_id) WHERE capture_state = 'PAUSED' AND state <> 'CLOSED';

        CREATE FUNCTION privacy.breach_capture_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.capture_state IS DISTINCT FROM OLD.capture_state AND NOT (
                   (OLD.capture_state = 'NOT_PAUSED' AND NEW.capture_state = 'PAUSED')
                OR (OLD.capture_state = 'PAUSED' AND NEW.capture_state = 'RELEASED')) THEN
                RAISE EXCEPTION 'capture moves NOT_PAUSED -> PAUSED -> RELEASED only' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.capture_state IS DISTINCT FROM OLD.capture_state AND OLD.state = 'CLOSED' THEN
                RAISE EXCEPTION 'a closed breach case does not change its capture state' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER breach_capture_guard BEFORE UPDATE ON privacy.breach_case
            FOR EACH ROW EXECUTE FUNCTION privacy.breach_capture_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER breach_capture_guard ON privacy.breach_case;
        DROP FUNCTION privacy.breach_capture_guard();
        DROP INDEX privacy.breach_case_capture_paused;
        ALTER TABLE privacy.breach_case
            DROP CONSTRAINT breach_capture_released_by_other,
            DROP CONSTRAINT breach_capture_released_fields,
            DROP CONSTRAINT breach_capture_paused_fields,
            DROP COLUMN capture_release_reason,
            DROP COLUMN capture_released_at,
            DROP COLUMN capture_released_by,
            DROP COLUMN capture_paused_at,
            DROP COLUMN capture_paused_by,
            DROP COLUMN capture_state;
    """)

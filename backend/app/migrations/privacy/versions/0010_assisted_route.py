"""The assisted route's back office (ZID-DTV-WF-04 B02; QA-009, QA-010; CR-110).

Revision ID: privacy_0010
Revises: privacy_0009
Create Date: 2026-11-17

A person who chose a non-biometric alternative is waiting for a person. The session records who took it and what came of it: WAITING ->
IN_PROGRESS (claimed) -> DONE (with the reference of the verification or review that did the work) or NOT_COMPLETED (with a short reason code).
No disability, medical or other reason for choosing the alternative is ever stored. The staff member is never the person the session is about.
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0010"
down_revision = "privacy_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE privacy.verification_session
            ADD COLUMN assisted_state text CHECK (assisted_state IS NULL OR assisted_state IN ('WAITING', 'IN_PROGRESS', 'DONE', 'NOT_COMPLETED')),
            ADD COLUMN assisted_by uuid,
            ADD COLUMN assisted_at timestamptz,
            ADD COLUMN assisted_outcome_ref text CHECK (assisted_outcome_ref IS NULL OR length(btrim(assisted_outcome_ref)) BETWEEN 2 AND 200),
            ADD COLUMN assisted_reason_code text CHECK (assisted_reason_code IS NULL OR assisted_reason_code ~ '^[A-Z][A-Z0-9_]{1,63}$'),
            ADD CONSTRAINT assisted_staff_not_subject CHECK (assisted_by IS NULL OR assisted_by IS DISTINCT FROM subject_principal_id),
            ADD CONSTRAINT assisted_done_has_ref CHECK (assisted_state IS DISTINCT FROM 'DONE' OR assisted_outcome_ref IS NOT NULL),
            ADD CONSTRAINT assisted_not_completed_has_reason CHECK (assisted_state IS DISTINCT FROM 'NOT_COMPLETED' OR assisted_reason_code IS NOT NULL);
        CREATE INDEX verification_session_assisted ON privacy.verification_session (assisted_state, created_at) WHERE assisted_state IS NOT NULL;
    """)


def downgrade() -> None:
    _run("""
        DROP INDEX privacy.verification_session_assisted;
        ALTER TABLE privacy.verification_session
            DROP CONSTRAINT assisted_not_completed_has_reason,
            DROP CONSTRAINT assisted_done_has_ref,
            DROP CONSTRAINT assisted_staff_not_subject,
            DROP COLUMN assisted_reason_code,
            DROP COLUMN assisted_outcome_ref,
            DROP COLUMN assisted_at,
            DROP COLUMN assisted_by,
            DROP COLUMN assisted_state;
    """)

"""A reversal of a contested decision needs a checker (ZID-DTV-WF-04 B08; QA-020, QA-031, QA-032; CR-109).

Revision ID: privacy_0008
Revises: privacy_0007
Create Date: 2026-11-17

A reviewer who upholds a decision closes the review. A reviewer who overturns or corrects it only PROPOSES that outcome: the review waits in
AWAITING_CHECKER until a third person, neither the requester nor the reviewer, confirms it (the outcome then takes effect and is announced) or
sends it back (OPEN again, the proposal on the audit record). The table repeats the rules. The new "a reversal has its checker" rule is NOT VALID
so reviews decided before this change are not rewritten; every new or changed row must meet it.
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0008"
down_revision = "privacy_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE privacy.decision_review DROP CONSTRAINT IF EXISTS decision_review_state_check;
        ALTER TABLE privacy.decision_review
            ADD CONSTRAINT decision_review_state_check
                CHECK (state IN ('OPEN', 'AWAITING_CHECKER', 'UPHELD', 'OVERTURNED', 'CORRECTED')),
            ADD COLUMN proposed_outcome text CHECK (proposed_outcome IS NULL OR proposed_outcome IN ('OVERTURNED', 'CORRECTED')),
            ADD COLUMN checker          uuid,
            ADD COLUMN checked_at       timestamptz,
            ADD CONSTRAINT decision_review_awaiting_fields
                CHECK ((state = 'AWAITING_CHECKER') = (proposed_outcome IS NOT NULL AND checker IS NULL)),
            ADD CONSTRAINT decision_review_checker_independent
                CHECK (checker IS NULL OR (checker <> requested_by AND checker <> reviewer));
        ALTER TABLE privacy.decision_review
            ADD CONSTRAINT decision_review_reversal_checked
                CHECK (state NOT IN ('OVERTURNED', 'CORRECTED') OR (checker IS NOT NULL AND checked_at IS NOT NULL)) NOT VALID;
        CREATE INDEX decision_review_state ON privacy.decision_review (state, due_at);
    """)


def downgrade() -> None:
    _run("""
        DROP INDEX privacy.decision_review_state;
        ALTER TABLE privacy.decision_review
            DROP CONSTRAINT decision_review_reversal_checked,
            DROP CONSTRAINT decision_review_checker_independent,
            DROP CONSTRAINT decision_review_awaiting_fields,
            DROP COLUMN checked_at,
            DROP COLUMN checker,
            DROP COLUMN proposed_outcome,
            DROP CONSTRAINT decision_review_state_check;
        ALTER TABLE privacy.decision_review
            ADD CONSTRAINT decision_review_state_check CHECK (state IN ('OPEN', 'UPHELD', 'OVERTURNED', 'CORRECTED'));
    """)

"""Approval case state NEEDS_CHANGES (ZID-UX-WF-013C section 09; slice L-13, CR-049).

Revision ID: tenant_0011
Revises: tenant_0010
Create Date: 2026-11-02

An approver who asks for changes ends the current request version (every vote on it is void) and the requester revises it into a new one.
The state is final for that version, like REJECTED, so the guard treats it so.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0011"
down_revision = "tenant_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE platform.approval_case DROP CONSTRAINT approval_case_state_check;
        ALTER TABLE platform.approval_case ADD CONSTRAINT approval_case_state_check CHECK (state IN ('PENDING_APPROVAL',
            'PARTIALLY_APPROVED', 'APPROVED_READY', 'EXECUTED', 'FAILED', 'REJECTED', 'EXPIRED', 'INVALIDATED', 'CANCELLED',
            'NEEDS_CHANGES'));
        CREATE OR REPLACE FUNCTION platform.approval_case_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'approval cases are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.case_id, NEW.tenant_id, NEW.version, NEW.supersedes_case_id, NEW.action_id, NEW.tier, NEW.target_id,
                NEW.payload_json, NEW.request_hash, NEW.policy_version, NEW.region, NEW.requester_principal_id,
                NEW.requester_person_id, NEW.editor_persons, NEW.beneficiary_principal_id, NEW.beneficiary_person_id,
                NEW.summary, NEW.reason, NEW.expires_at, NEW.created_at)
               IS DISTINCT FROM
               (OLD.case_id, OLD.tenant_id, OLD.version, OLD.supersedes_case_id, OLD.action_id, OLD.tier, OLD.target_id,
                OLD.payload_json, OLD.request_hash, OLD.policy_version, OLD.region, OLD.requester_principal_id,
                OLD.requester_person_id, OLD.editor_persons, OLD.beneficiary_principal_id, OLD.beneficiary_person_id,
                OLD.summary, OLD.reason, OLD.expires_at, OLD.created_at) THEN
                RAISE EXCEPTION 'an approval request is immutable; a material change is a new case'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state IN ('EXECUTED', 'REJECTED', 'EXPIRED', 'INVALIDATED', 'CANCELLED', 'NEEDS_CHANGES')
               AND NEW.state <> OLD.state THEN
                RAISE EXCEPTION 'approval case % is final', OLD.state USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.state = 'EXECUTED' AND OLD.state NOT IN ('APPROVED_READY', 'FAILED') THEN
                RAISE EXCEPTION 'a case runs only from APPROVED_READY or after a failed attempt'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
    """)


def downgrade() -> None:
    pass  # the wider state list is harmless to keep

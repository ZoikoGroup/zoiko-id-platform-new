"""Let the three "a different person decides" table rules yield when the deployment has chosen single-approver mode.

Revision ID: verify_api_0019
Revises: verify_api_0018
Create Date: 2026-11-26

The go-live request, a proposed price and a production-credential approval each repeat the rule "the person who decides is not the person who asked" in a CHECK constraint, so the rule holds even for
a caller that skips the application code. ZID_VERIFY_SINGLE_APPROVER (the deployment's named decision that one person may ask and decide) relaxed only the application code, so the table still refused:
approving one's own go-live request failed with a check violation.

Each rule now also passes when the transaction carries `app.verify_single_approver = 'on'`. Only the cell sets it, and only in the approve paths, and only when the setting is on. With the setting off (the
default) nothing sets it, so the table refuses exactly as before. A decline or a withdrawal never needed it. Replacing a CHECK constraint with a wider one is additive.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0019"
down_revision = "verify_api_0018"
branch_labels = None
depends_on = None

_FLAG = "coalesce(current_setting('app.verify_single_approver', true), '') = 'on'"


def upgrade() -> None:
    _run(f"""
        ALTER TABLE verify.api_go_live_request DROP CONSTRAINT api_go_live_request_check;
        ALTER TABLE verify.api_go_live_request ADD CONSTRAINT api_go_live_request_check
            CHECK (decided_by IS NULL OR reviewer_id IS NULL OR decided_by <> reviewer_id OR status = ANY (ARRAY['DECLINED','WITHDRAWN']) OR {_FLAG});

        ALTER TABLE verify.api_price DROP CONSTRAINT api_price_check1;
        ALTER TABLE verify.api_price ADD CONSTRAINT api_price_check1
            CHECK (decided_by IS NULL OR decided_by <> proposed_by OR {_FLAG});

        ALTER TABLE verify.api_credential_approval DROP CONSTRAINT api_credential_approval_check1;
        ALTER TABLE verify.api_credential_approval ADD CONSTRAINT api_credential_approval_check1
            CHECK (decided_by IS NULL OR decided_by <> requested_by OR {_FLAG});
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE verify.api_go_live_request DROP CONSTRAINT api_go_live_request_check;
        ALTER TABLE verify.api_go_live_request ADD CONSTRAINT api_go_live_request_check
            CHECK (decided_by IS NULL OR reviewer_id IS NULL OR decided_by <> reviewer_id OR status = ANY (ARRAY['DECLINED','WITHDRAWN']));

        ALTER TABLE verify.api_price DROP CONSTRAINT api_price_check1;
        ALTER TABLE verify.api_price ADD CONSTRAINT api_price_check1 CHECK (decided_by IS NULL OR decided_by <> proposed_by);

        ALTER TABLE verify.api_credential_approval DROP CONSTRAINT api_credential_approval_check1;
        ALTER TABLE verify.api_credential_approval ADD CONSTRAINT api_credential_approval_check1 CHECK (decided_by IS NULL OR decided_by <> requested_by);
    """)

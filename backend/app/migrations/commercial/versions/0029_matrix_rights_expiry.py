"""Provider rights and regulated status in a market-matrix row carry an expiry date (ZID-COM-WF-2026-11 WF-I01 rights register, CAT-02).

Revision ID: commercial_0029
Revises: commercial_0028
Create Date: 2026-11-13

"Proof IDs and expiry, no assumed worldwide licensing": a reference to provider rights or a regulated status now needs the date it stops being valid. A right
past that date no longer supports a sale, so an expired right is denied rather than silently rerouted. The constraints are NOT VALID so rows recorded before
this revision are not rewritten; every new or changed row is checked.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0029"
down_revision = "commercial_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE commercial.market_matrix_row
            ADD COLUMN provider_rights_expires date,
            ADD COLUMN regulated_status_expires date,
            ADD CONSTRAINT market_matrix_row_provider_expiry CHECK (provider_rights_ref IS NULL OR provider_rights_expires IS NOT NULL) NOT VALID,
            ADD CONSTRAINT market_matrix_row_regulated_expiry CHECK (regulated_status_ref IS NULL OR regulated_status_expires IS NOT NULL) NOT VALID;
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE commercial.market_matrix_row
            DROP CONSTRAINT market_matrix_row_provider_expiry, DROP CONSTRAINT market_matrix_row_regulated_expiry,
            DROP COLUMN provider_rights_expires, DROP COLUMN regulated_status_expires;
    """)

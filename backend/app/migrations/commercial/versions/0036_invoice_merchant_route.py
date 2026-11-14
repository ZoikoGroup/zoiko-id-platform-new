"""An issued invoice records the merchant route and market matrix version it was issued under (ZID-COM-WF-2026-11 INV-04, section 05.1).

Revision ID: commercial_0036
Revises: commercial_0035
Create Date: 2026-11-13

The invoice already pins the issuing entity (seller), the payer, the currency, the per-line tax code and the charge basis. This adds the merchant route and the version of
the market matrix it came from, set once at issue and then immutable with the rest of the issued invoice. NULL means "the matrix did not say unambiguously", never a guess:
a route is recorded only when the active matrix has exactly one for this seller, buyer country and currency. The document and its hash chain are not changed.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0036"
down_revision = "commercial_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE commercial.invoice
            ADD COLUMN merchant_route text CHECK (merchant_route IS NULL OR char_length(btrim(merchant_route)) BETWEEN 1 AND 160),
            ADD COLUMN market_matrix_version integer CHECK (market_matrix_version IS NULL OR market_matrix_version >= 1);
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE commercial.invoice DROP COLUMN merchant_route, DROP COLUMN market_matrix_version;
    """)

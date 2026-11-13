"""Which document-issuing countries a market-matrix row covers (ZID-UX-PRICE-004 WF-P06; ZID-COM-WF-2026-11 section 05.1 "service / document / credential type").

Revision ID: commercial_0040
Revises: commercial_0039
Create Date: 2026-11-13

A row says where a service is sold. For a document-based service (identity, document, qualification, licence, employment) what can actually be checked also depends on which country issued the document or
credential. A row can now list the issuing countries it covers. An empty list means "not recorded": it supports no issuing country, so a buyer who names one is told it is not covered, never assumed to be.
Nothing changes for a buyer who does not name an issuing country. The list is part of the signed matrix: editable only in a DRAFT, immutable once active.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0040"
down_revision = "commercial_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE commercial.market_matrix_row
            ADD COLUMN document_issuer_countries text[] NOT NULL DEFAULT '{}'
                CHECK (cardinality(document_issuer_countries) <= 250
                       AND array_to_string(document_issuer_countries, ',') ~ '^([A-Z]{2}(,[A-Z]{2})*)?$');
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE commercial.market_matrix_row DROP COLUMN document_issuer_countries;
    """)

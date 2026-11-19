"""A country opens one capability at a time (ZID-EDR-001 section 4; CR-127).

Revision ID: verification_0035
Revises: verification_0034
Create Date: 2026-11-19

Every regulated capability is its own go/no-go: REGISTRATION (sign-up), INDIVIDUAL_VERIFICATION, BUSINESS_VERIFICATION (organization claims),
CREDENTIAL_ISSUANCE and ENTERPRISE_USE. A row now names its capability; the rows already written are sign-up decisions. The newest version of a
(country, capability) is the one in force. Only REGISTRATION needs a home cell: it decides which cell holds the account.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0035"
down_revision = "verification_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.country_opening ADD COLUMN capability text NOT NULL DEFAULT 'REGISTRATION'
            CHECK (capability IN ('REGISTRATION', 'INDIVIDUAL_VERIFICATION', 'BUSINESS_VERIFICATION', 'CREDENTIAL_ISSUANCE', 'ENTERPRISE_USE'));
        ALTER TABLE verify.country_opening DROP CONSTRAINT country_opening_pkey;
        ALTER TABLE verify.country_opening ADD PRIMARY KEY (country, capability, version);
        ALTER TABLE verify.country_opening DROP CONSTRAINT country_opening_open_complete;
        ALTER TABLE verify.country_opening ADD CONSTRAINT country_opening_open_complete CHECK (
            status <> 'OPEN' OR (
                effective_from IS NOT NULL
                AND (capability <> 'REGISTRATION' OR cardinality(home_cells) >= 1)
                AND approvals ?& ARRAY['legal_assessment', 'supplier_coverage', 'privacy_and_data',
                                       'security_and_resilience', 'local_support', 'signoff']
            )
        );
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE verify.country_opening DISABLE TRIGGER country_opening_guard;
        DELETE FROM verify.country_opening WHERE capability <> 'REGISTRATION';
        ALTER TABLE verify.country_opening ENABLE TRIGGER country_opening_guard;
        ALTER TABLE verify.country_opening DROP CONSTRAINT country_opening_open_complete;
        ALTER TABLE verify.country_opening DROP CONSTRAINT country_opening_pkey;
        ALTER TABLE verify.country_opening DROP COLUMN capability;
        ALTER TABLE verify.country_opening ADD PRIMARY KEY (country, version);
        ALTER TABLE verify.country_opening ADD CONSTRAINT country_opening_open_complete CHECK (
            status <> 'OPEN' OR (
                effective_from IS NOT NULL
                AND cardinality(home_cells) >= 1
                AND approvals ?& ARRAY['legal_assessment', 'supplier_coverage', 'privacy_and_data',
                                       'security_and_resilience', 'local_support', 'signoff']
            )
        );
    """)

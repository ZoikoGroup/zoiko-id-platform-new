"""The tenant's current signed entitlement snapshot, shared by every process (ZID-ENG-DEC-008 section 05; CR-045).

Revision ID: commercial_0016
Revises: commercial_0015
Create Date: 2026-10-30

entitlement_snapshot: one row per tenant holding the latest signed snapshot, so a second process or a restarted one reads what was
already built instead of building its own. A reader trusts it only if it has not expired, its signature verifies under the current
key and its fingerprint equals the tenant's commercial state at that moment; a row that fails any of the three is replaced. `revision`
rises by one each time the fingerprint changes (a real change of plan, suspension, billing or override), never on a plain refresh, and is
the version of the `commercial.entitlement.snapshot_published` event. The table holds decisions about features, no personal data.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0016"
down_revision = "commercial_0015"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE commercial.entitlement_snapshot (
            tenant_id       uuid PRIMARY KEY,
            revision        integer NOT NULL CHECK (revision >= 1),
            fingerprint     text NOT NULL CHECK (char_length(fingerprint) BETWEEN 1 AND 500),
            catalog_version text NOT NULL,
            issued_at       timestamptz NOT NULL,
            expires_at      timestamptz NOT NULL,
            doc             jsonb NOT NULL,
            signed          text NOT NULL CHECK (signed ~ '^[0-9a-f]{{64}}$'),
            published_at    timestamptz NOT NULL DEFAULT now(),
            CHECK (expires_at > issued_at)
        );
        ALTER TABLE commercial.entitlement_snapshot ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.entitlement_snapshot FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.entitlement_snapshot
            USING ({TENANT}) WITH CHECK ({TENANT});
    """)


def downgrade() -> None:
    _run("DROP TABLE commercial.entitlement_snapshot;")

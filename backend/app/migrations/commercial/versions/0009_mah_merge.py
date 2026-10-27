"""Identity merges for the monthly active human count (ZID-COM-001 §7, §31; CR-025).

Revision ID: commercial_0009
Revises: commercial_0008
Create Date: 2026-10-25

`mah_merge` records that one principal was merged into another inside a billed organization, so a person
whose identities were merged counts once: activity of the merged-away principal is credited to the surviving
one, before or after the merge. The row is insert-only and tenant-scoped like `mah_activity`; a principal can
be merged away only once, and a merge can never point back at itself or create a loop (checked in the
service, enforced here for the self-merge case).
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0009"
down_revision = "commercial_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.mah_merge (
            tenant_id         uuid NOT NULL,
            from_principal_id uuid NOT NULL,
            into_principal_id uuid NOT NULL CHECK (into_principal_id <> from_principal_id),
            merged_at         timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, from_principal_id)
        );
        CREATE TRIGGER mah_merge_immutable BEFORE UPDATE OR DELETE ON commercial.mah_merge
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();
        ALTER TABLE commercial.mah_merge ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.mah_merge FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.mah_merge
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.mah_merge;
    """)

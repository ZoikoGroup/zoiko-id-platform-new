"""Recorded ownership graphs, one immutable version at a time (ZID-GAP-001 v2.0 DTV-24; CR-102).

Revision ID: organization_0008
Revises: organization_0007
Create Date: 2026-11-16

ownership_graph: the entities (opaque ids and a kind, never a name) and the recorded ownership and control links of one target company, as one version,
with the threshold it was read at and a hash of its content. A version never changes and is never deleted: a correction is the next version, so what a
reviewer saw on a given day can always be shown. The candidates and gaps are not stored: they are worked out from the version, deterministically, each
time. Row-level security by company.
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0008"
down_revision = "organization_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE org.ownership_graph (
            graph_id      uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            target        text NOT NULL CHECK (target ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'),
            version       integer NOT NULL CHECK (version >= 1),
            entities      jsonb NOT NULL,
            links         jsonb NOT NULL,
            threshold     numeric(5, 2) NOT NULL CHECK (threshold > 0 AND threshold <= 100),
            content_hash  text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            author        uuid NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, target, version)
        );
        CREATE INDEX ownership_graph_recent ON org.ownership_graph (tenant_id, created_at DESC);

        CREATE FUNCTION org.ownership_graph_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'a recorded ownership graph never changes and is never deleted; record a new version' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER ownership_graph_append_only BEFORE UPDATE OR DELETE ON org.ownership_graph
            FOR EACH ROW EXECUTE FUNCTION org.ownership_graph_append_only();

        ALTER TABLE org.ownership_graph ENABLE ROW LEVEL SECURITY;
        ALTER TABLE org.ownership_graph FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON org.ownership_graph
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE org.ownership_graph;
        DROP FUNCTION org.ownership_graph_append_only();
    """)

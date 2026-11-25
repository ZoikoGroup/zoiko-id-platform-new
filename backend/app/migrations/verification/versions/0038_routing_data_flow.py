"""A provider's registered data-flow graph (ZID-CP-WF09 v2.0 WF-09.10; CR-172).

Revision ID: verification_0038
Revises: verification_0037
Create Date: 2026-11-25

One row per version of a provider's graph: every place its data enters, travels, rests, is reached by support, analysed, measured or backed up, and every
onward party, each with a region and a data class. The graph has a digest. A legal determination names the digest it was made about, so any later change to
the graph (a new subprocessor, a new region) stops routing to that provider until a person looks again. Append-only, platform configuration.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0038"
down_revision = "verification_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.route_data_flow (
            provider_id text    NOT NULL CHECK (provider_id ~ '^[a-z0-9][a-z0-9_.-]{1,62}$'),
            version     integer NOT NULL CHECK (version >= 1),
            hops        jsonb   NOT NULL CHECK (jsonb_typeof(hops) = 'array' AND jsonb_array_length(hops) BETWEEN 1 AND 200),
            graph_digest text   NOT NULL CHECK (graph_digest ~ '^sha-256:[0-9a-f]{64}$'),
            recorded_by text    NOT NULL CHECK (length(btrim(recorded_by)) BETWEEN 1 AND 128),
            recorded_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (provider_id, version)
        );
        CREATE TRIGGER route_data_flow_append_only BEFORE UPDATE OR DELETE ON verify.route_data_flow
            FOR EACH ROW EXECUTE FUNCTION verify.route_append_only();
    """)


def downgrade() -> None:
    _run("DROP TABLE verify.route_data_flow;")

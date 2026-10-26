"""Bulk evidence export with expiry (ZID-API-001 API-13; EVIDENCE-001 §17-18).

Revision ID: evidence_0004
Revises: evidence_0003
Create Date: 2026-10-26

One row per bulk export: who asked, why, when it stops being downloadable, and the signed package itself. When it
expires the package is erased in place (the row stays, as the record that the export happened) and every later
download is refused.
"""

from zid_platform.migrations import execute_script as _run

revision = "evidence_0004"
down_revision = "evidence_0003"
branch_labels = None
depends_on = None

DIGEST = r"'^sha-(256|384|512):[0-9a-f]+$'"
RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE evidence.bulk_export (
            export_id           uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            requested_by_handle text NOT NULL,
            purpose             text NOT NULL,
            case_commitment     text NOT NULL CHECK (case_commitment ~ {DIGEST}),
            export_mode         text NOT NULL,
            receipt_count       integer NOT NULL CHECK (receipt_count BETWEEN 1 AND 100),
            package_hash        text NOT NULL CHECK (package_hash ~ {DIGEST}),
            package             jsonb,
            status              text NOT NULL DEFAULT 'AVAILABLE' CHECK (status IN ('AVAILABLE', 'EXPIRED')),
            downloads           integer NOT NULL DEFAULT 0,
            created_at          timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at          timestamptz NOT NULL,
            erased_at           timestamptz,
            CHECK (expires_at > created_at),
            CHECK ((status = 'EXPIRED') = (package IS NULL))
        );
        CREATE INDEX bulk_export_expiry ON evidence.bulk_export (expires_at) WHERE status = 'AVAILABLE';
        ALTER TABLE evidence.bulk_export ENABLE ROW LEVEL SECURITY;
        ALTER TABLE evidence.bulk_export FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON evidence.bulk_export USING {RULE} WITH CHECK {RULE};
    """)


def downgrade() -> None:
    _run("DROP TABLE evidence.bulk_export;")

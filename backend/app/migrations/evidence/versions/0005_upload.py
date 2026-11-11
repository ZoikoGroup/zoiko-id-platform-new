"""Evidence uploads held in the cell database (ZID-INT-002 sections 05 and 09; CR-071).

Revision ID: evidence_0005
Revises: evidence_0004
Create Date: 2026-11-11

A document a person or an organization uploads as evidence. The bytes live in this table (one database, no object store), so the
database itself enforces the rule that matters: bytes exist ONLY for an upload that is READY and scanned CLEAN. An infected, unscanned,
rejected, deleted or expired upload has no bytes at all; the CHECK below makes that impossible to violate from application code.

Retention is not invented here: `expires_at` is set from a deployment setting the privacy officer owns, and an upload is refused if none
is configured. Deleting or expiring an upload erases the bytes and keeps the row as the record that it existed.
"""

from zid_platform.migrations import execute_script as _run

revision = "evidence_0005"
down_revision = "evidence_0004"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE evidence.upload (
            upload_id            uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            subject_principal_id uuid NOT NULL,
            uploaded_by          uuid NOT NULL,
            declared_type        text NOT NULL CHECK (declared_type IN ('application/pdf', 'image/png', 'image/jpeg')),
            sniffed_type         text,
            size_bytes           integer NOT NULL CHECK (size_bytes BETWEEN 1 AND 5242880),
            declared_sha256      text NOT NULL CHECK (declared_sha256 ~ '^[0-9a-f]{{64}}$'),
            stored_sha256        text CHECK (stored_sha256 ~ '^[0-9a-f]{{64}}$'),
            exif_stripped        boolean NOT NULL DEFAULT false,
            state                text NOT NULL DEFAULT 'AWAITING_CONTENT' CHECK (state IN (
                'AWAITING_CONTENT', 'READY', 'REJECTED', 'DELETED', 'EXPIRED')),
            reject_reason        text,
            scan_state           text NOT NULL DEFAULT 'NOT_SCANNED' CHECK (scan_state IN (
                'NOT_SCANNED', 'CLEAN', 'INFECTED', 'SCAN_ERROR')),
            scanner              text,
            content              bytea,
            created_at           timestamptz NOT NULL DEFAULT now(),
            token_expires_at     timestamptz NOT NULL,
            expires_at           timestamptz NOT NULL,
            content_at           timestamptz,
            erased_at            timestamptz,
            CHECK (expires_at > created_at),
            -- the rule that matters: bytes exist only for a READY upload that scanned CLEAN
            CHECK ((content IS NOT NULL) = (state = 'READY' AND scan_state = 'CLEAN')),
            CHECK (state <> 'READY' OR stored_sha256 IS NOT NULL),
            CHECK (state <> 'REJECTED' OR reject_reason IS NOT NULL)
        );
        CREATE INDEX upload_subject ON evidence.upload (tenant_id, subject_principal_id, created_at DESC);
        CREATE INDEX upload_expiry ON evidence.upload (expires_at) WHERE state = 'READY';
        ALTER TABLE evidence.upload ENABLE ROW LEVEL SECURITY;
        ALTER TABLE evidence.upload FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON evidence.upload USING {RULE} WITH CHECK {RULE};
    """)


def downgrade() -> None:
    _run("DROP TABLE evidence.upload;")

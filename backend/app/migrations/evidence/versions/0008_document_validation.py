"""Stored document validation results (ZID-INT-002 section 09: `GET /v2/documents/{id}`; CR-071).

Revision ID: evidence_0008
Revises: evidence_0007
Create Date: 2026-11-11

Only a validation of an UPLOADED document is kept (a pasted document is checked in memory and discarded). The row holds the per-check
results (statuses, reason codes, details), the digest and who asked, never the document or any text from it. It belongs to its upload:
erasing a person's uploads removes their validations with them (cascade), and a validation can never outlive the upload it is about.
"""

from zid_platform.migrations import execute_script as _run

revision = "evidence_0008"
down_revision = "evidence_0007"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE evidence.document_validation (
            validation_id  uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            upload_id      uuid NOT NULL REFERENCES evidence.upload (upload_id) ON DELETE CASCADE,
            requested_by   uuid NOT NULL,
            checks         text[] NOT NULL CHECK (cardinality(checks) BETWEEN 1 AND 4),
            sha256         text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
            results        jsonb NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX document_validation_upload ON evidence.document_validation (upload_id);
        ALTER TABLE evidence.document_validation ENABLE ROW LEVEL SECURITY;
        ALTER TABLE evidence.document_validation FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON evidence.document_validation USING {RULE} WITH CHECK {RULE};
    """)


def downgrade() -> None:
    _run("DROP TABLE evidence.document_validation;")

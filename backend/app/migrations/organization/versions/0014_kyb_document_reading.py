"""What the platform's OCR read from a stored company certificate (CR-132).

Revision ID: organization_0014
Revises: organization_0013
Create Date: 2026-11-20

kyb_document_reading: one row per stored certificate that was read, with the verdict (CONSISTENT, CONTRADICTS, NOT_ENOUGH), the per-field results and the incorporation date read. Never the text. A
CONSISTENT reading of a stored certificate completes the claim's company check (see onboarding claims status), so the row is what a reviewer or auditor sees as the basis. Append-only; row-level security by company.
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0014"
down_revision = "organization_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE org.kyb_document_reading (
            reading_id         uuid PRIMARY KEY,
            tenant_id          uuid NOT NULL,
            case_id            uuid NOT NULL REFERENCES org.kyb_case (case_id),
            proof_id           uuid NOT NULL REFERENCES org.kyb_proof (proof_id),
            verdict            text NOT NULL CHECK (verdict IN ('CONSISTENT', 'CONTRADICTS', 'NOT_ENOUGH')),
            name_result        text NOT NULL,
            number_result      text NOT NULL,
            incorporation_date date,
            read_at            timestamptz NOT NULL,
            UNIQUE (proof_id)
        );
        CREATE INDEX kyb_document_reading_case ON org.kyb_document_reading (tenant_id, case_id, verdict);
        CREATE FUNCTION org.kyb_document_reading_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'kyb document readings are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER kyb_document_reading_append_only BEFORE UPDATE OR DELETE ON org.kyb_document_reading
            FOR EACH ROW EXECUTE FUNCTION org.kyb_document_reading_guard();
        ALTER TABLE org.kyb_document_reading ENABLE ROW LEVEL SECURITY;
        ALTER TABLE org.kyb_document_reading FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON org.kyb_document_reading
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE org.kyb_document_reading; DROP FUNCTION org.kyb_document_reading_guard();")

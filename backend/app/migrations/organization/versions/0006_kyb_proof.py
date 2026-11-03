"""Proof documents for a company claim (WF-13B section 06, AT-12; ZID-ED-03; CR-043).

Revision ID: organization_0006
Revises: organization_0005
Create Date: 2026-11-02

kyb_proof: the record of one document an applicant supplied for a claim. The document itself is in the evidence vault (encrypted,
retention-governed, erasable); this row holds only what a reviewer needs to find and judge it: kind, sniffed type, size, content hash,
the plain label, the scan result and the policy version. Append-only: a proof is never edited or removed, so what a reviewer saw is
what was recorded. A claim can hold the same content only once.
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0006"
down_revision = "organization_0005"
branch_labels = None
depends_on = None

TABLES = ("kyb_proof",)
_RLS = """
    ALTER TABLE org.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE org.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON org.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""


def upgrade() -> None:
    _run("""
        CREATE TABLE org.kyb_proof (
            proof_id         uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            case_id          uuid NOT NULL REFERENCES org.kyb_case (case_id),
            kind             text NOT NULL,
            media_type       text NOT NULL,
            size_bytes       integer NOT NULL CHECK (size_bytes > 0),
            content_hash     text NOT NULL CHECK (content_hash ~ '^sha-256:[0-9a-f]{64}$'),
            object_id        uuid NOT NULL,
            label            text NOT NULL,
            scan_status      text NOT NULL CHECK (scan_status IN ('CLEAN', 'NOT_SCANNED')),
            policy_version   text NOT NULL,
            idempotency_key  text NOT NULL,
            uploaded_by      uuid NOT NULL,
            created_at       timestamptz NOT NULL,
            UNIQUE (tenant_id, idempotency_key),
            UNIQUE (case_id, content_hash)
        );
        CREATE INDEX kyb_proof_case ON org.kyb_proof (tenant_id, case_id, created_at);
        CREATE FUNCTION org.kyb_proof_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'kyb proof records are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER kyb_proof_append_only BEFORE UPDATE OR DELETE ON org.kyb_proof
            FOR EACH ROW EXECUTE FUNCTION org.kyb_proof_guard();
    """)
    for table in TABLES:
        _run(_RLS.format(t=table))


def downgrade() -> None:
    _run("DROP TABLE org.kyb_proof; DROP FUNCTION org.kyb_proof_guard();")

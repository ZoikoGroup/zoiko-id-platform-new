"""Documents attached to an approval request (ZID-UX-WF-013C AT-12; slice L-13, CR-051).

Revision ID: onboarding_0006
Revises: onboarding_0005
Create Date: 2026-11-03

approval_attachment: the record of one document the requester supplied for an approval request. The document itself is in the evidence
vault (encrypted, retention-governed, erasable); this row holds only what an approver needs to find and judge it: kind, sniffed type,
size, content hash, the plain label, the scan result, the policy version and the moment access ends (the request's own expiry). Append-only:
an attachment is never edited or removed, so what an approver saw is what was recorded. A request can hold the same content only once.
"""

from zid_platform.migrations import execute_script as _run

revision = "onboarding_0006"
down_revision = "onboarding_0005"
branch_labels = None
depends_on = None

TABLES = ("approval_attachment",)
_RLS = """
    ALTER TABLE platform.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE platform.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON platform.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.approval_attachment (
            attachment_id    uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            case_id          uuid NOT NULL,
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
            expires_at       timestamptz NOT NULL,
            created_at       timestamptz NOT NULL,
            UNIQUE (tenant_id, idempotency_key),
            UNIQUE (case_id, content_hash)
        );
        CREATE INDEX approval_attachment_case ON platform.approval_attachment (tenant_id, case_id, created_at);
        CREATE FUNCTION platform.approval_attachment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'approval attachment records are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER approval_attachment_append_only BEFORE UPDATE OR DELETE ON platform.approval_attachment
            FOR EACH ROW EXECUTE FUNCTION platform.approval_attachment_guard();
    """)
    for table in TABLES:
        _run(_RLS.format(t=table))


def downgrade() -> None:
    _run(
        "DROP TABLE platform.approval_attachment; DROP FUNCTION platform.approval_attachment_guard();"
    )

"""Idempotency records for retryable mutation APIs (ZID-API-001 §1.1, §4; proposed table, CR-007).

Revision ID: outbox_0002
Revises: outbox_0001
Create Date: 2026-10-12

One row per (tenant, client, endpoint, key hash): the first request's canonical payload hash and its
stored response. A retry with the same payload replays the response; the same key with a different
payload is IDEMPOTENCY_KEY_REUSED (409). Keys are stored only as hashes.
"""

from zid_platform.migrations import execute_script as _run

revision = "outbox_0002"
down_revision = "outbox_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.idempotency_record (
            tenant_id       uuid NOT NULL,
            client_id       text NOT NULL,
            endpoint        text NOT NULL,
            key_hash        bytea NOT NULL,
            request_hash    bytea NOT NULL,
            status          text NOT NULL CHECK (status IN ('IN_PROGRESS', 'COMPLETED')),
            response_status integer,
            response_body   jsonb,
            created_at      timestamptz NOT NULL DEFAULT now(),
            expires_at      timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, client_id, endpoint, key_hash)
        );
        CREATE INDEX idempotency_expiry ON platform.idempotency_record (expires_at);
        ALTER TABLE platform.idempotency_record ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.idempotency_record FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.idempotency_record
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE platform.idempotency_record;")

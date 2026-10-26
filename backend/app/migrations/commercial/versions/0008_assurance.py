"""Commercial change log, assurance exceptions and month-end close (ZID-COM-001 §11, §24; CR-030).

Revision ID: commercial_0008
Revises: commercial_0007
Create Date: 2026-10-24

- `change_log`: append-only hash chain of every commercial change (price book, entitlement, override, order,
  invoice, credit note, billing state, vendor rate, usage adjustment), with actor, reason and the digests of
  what changed. `change_log_head` is the single row locked to append, so the chain is gap-free.
  Not tenant-scoped (it is the platform's commercial audit trail); it carries a tenant column for filtering.
- `assurance_exception`: detector findings with an owner, one per (kind, reference) until resolved.
- `close_run`: one row per tenant and month recording which close steps are done, so a close resumes.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0008"
down_revision = "commercial_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.change_log_head (
            id        boolean PRIMARY KEY DEFAULT true CHECK (id),
            last_seq  bigint NOT NULL DEFAULT 0,
            last_hash text
        );
        INSERT INTO commercial.change_log_head (id) VALUES (true);

        CREATE TABLE commercial.change_log (
            seq          bigint PRIMARY KEY CHECK (seq >= 1),
            kind         text NOT NULL CHECK (kind ~ '^[A-Z][A-Z_]{2,40}$'),
            ref          text NOT NULL CHECK (length(ref) BETWEEN 1 AND 200),
            tenant_id    uuid,
            actor        uuid,
            reason       text,
            detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
            entry_digest text NOT NULL CHECK (length(entry_digest) = 64),
            chain_hash   text NOT NULL CHECK (length(chain_hash) = 64),
            at           timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX change_log_by_tenant ON commercial.change_log (tenant_id, at);
        CREATE TRIGGER change_log_immutable BEFORE UPDATE OR DELETE ON commercial.change_log
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();

        CREATE TABLE commercial.assurance_exception (
            exception_id uuid PRIMARY KEY,
            kind         text NOT NULL,
            ref          text NOT NULL,
            tenant_id    uuid,
            detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
            status       text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
            owner        uuid,
            raised_at    timestamptz NOT NULL DEFAULT now(),
            resolved_at  timestamptz,
            resolution   text
        );
        CREATE UNIQUE INDEX assurance_exception_open ON commercial.assurance_exception (kind, ref) WHERE status = 'OPEN';

        CREATE TABLE commercial.close_run (
            tenant_id   uuid NOT NULL,
            period      date NOT NULL CHECK (period = date_trunc('month', period)::date),
            frozen_at   timestamptz,
            rated_at    timestamptz,
            drafted_at  timestamptz,
            invoice_id  uuid,
            rating_run_id uuid,
            updated_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, period)
        );
        ALTER TABLE commercial.close_run ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.close_run FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.close_run
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.close_run;
        DROP TABLE commercial.assurance_exception;
        DROP TABLE commercial.change_log;
        DROP TABLE commercial.change_log_head;
    """)

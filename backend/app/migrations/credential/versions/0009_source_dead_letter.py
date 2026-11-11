"""Dead-letter store for source status events that could not be applied (GID-060; CR-069).

Revision ID: credential_0009
Revises: credential_0008
Create Date: 2026-11-11

A source can tell us about a check we do not hold, or one that a newer check has already replaced. That event must not be lost and
must not be a 404 the source retries forever: it is kept here, once per (check, event), until a person replays or discards it.
The row holds only the event's own facts (ids, status, time, sequence) and a reason code, never the source's raw payload, and it
has no foreign key, so it does not depend on the check existing and carries no personal data of its own.
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0009"
down_revision = "credential_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE cred.source_dead_letter (
            letter_id    uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            check_id     uuid NOT NULL,
            event_id     text NOT NULL CHECK (length(event_id) BETWEEN 1 AND 200),
            status       text NOT NULL CHECK (status IN ('ACTIVE', 'SUSPENDED', 'REVOKED', 'EXPIRED', 'UNKNOWN')),
            observed_at  timestamptz NOT NULL,
            sequence     bigint,
            reason       text NOT NULL CHECK (reason IN ('CHECK_NOT_FOUND', 'CHECK_SUPERSEDED')),
            attempts     integer NOT NULL DEFAULT 1,
            state        text NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'REPLAYED', 'DISCARDED')),
            received_at  timestamptz NOT NULL DEFAULT now(),
            resolved_at  timestamptz,
            resolved_by  uuid,
            CHECK ((state = 'OPEN') = (resolved_at IS NULL)),
            UNIQUE (tenant_id, check_id, event_id)
        );
        CREATE INDEX source_dead_letter_open ON cred.source_dead_letter (tenant_id, state, received_at);

        ALTER TABLE cred.source_dead_letter ENABLE ROW LEVEL SECURITY;
        ALTER TABLE cred.source_dead_letter FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON cred.source_dead_letter
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE cred.source_dead_letter;
    """)

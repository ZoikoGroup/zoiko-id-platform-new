"""Access requests: a person asks an owner or administrator for a permission or a spending limit.

Revision ID: authority_0006
Revises: authority_0005
Create Date: 2026-11-23

Until now only an owner or administrator could start a grant, so a Member who needed one had no way to ask inside the product. A request holds what the person
asks for and why; it grants nothing. An owner or administrator reads it, gives the permission through the ordinary grant checks (or declines with a note) and
marks the request done. Each person sees only their own requests.
"""

from zid_platform.migrations import execute_script as _run

revision = "authority_0006"
down_revision = "authority_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE authz.access_request (
            request_id     uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            requester_id   uuid NOT NULL,
            actions        text[] NOT NULL CHECK (cardinality(actions) BETWEEN 1 AND 20),
            value_amount   numeric CHECK (value_amount IS NULL OR value_amount >= 0),
            value_currency text CHECK (value_currency IS NULL OR value_currency ~ '^[A-Z]{3}$'),
            reason         text NOT NULL CHECK (char_length(reason) BETWEEN 5 AND 500),
            status         text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'FULFILLED', 'DECLINED', 'WITHDRAWN')),
            decided_by     uuid,
            decided_at     timestamptz,
            decision_note  text CHECK (decision_note IS NULL OR char_length(decision_note) <= 300),
            created_at     timestamptz NOT NULL DEFAULT now(),
            CHECK ((value_amount IS NULL) = (value_currency IS NULL)),
            CHECK ((status = 'OPEN') = (decided_at IS NULL))
        );
        CREATE INDEX access_request_requester ON authz.access_request (tenant_id, requester_id, created_at DESC);
        CREATE INDEX access_request_open ON authz.access_request (tenant_id, created_at DESC) WHERE status = 'OPEN';
        ALTER TABLE authz.access_request ENABLE ROW LEVEL SECURITY;
        ALTER TABLE authz.access_request FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON authz.access_request
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE authz.access_request;")

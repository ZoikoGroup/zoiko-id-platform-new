"""A company asks for a different plan (ZID-COM-001 §10 "Expand"; CR-032).

Revision ID: commercial_0011
Revises: commercial_0010
Create Date: 2026-10-26

A company on the default plan cannot switch itself to a paid plan: a plan comes from a signed order. What it can do
is ask. `plan_request` is that ask: which plan, why, who asked, and the commercial team's answer. Asking changes
nothing about entitlements; the order is still raised and approved by people with the authority to do it.

Tenant-scoped under RLS. The operator plane reads and answers requests across companies through a second policy that
only opens inside a transaction the operator surface has put into operator scope.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0011"
down_revision = "commercial_0010"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
OPERATOR = "current_setting('app.commercial_scope', true) = 'operator'"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE commercial.plan_request (
            request_id      uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            plan_code       text NOT NULL CHECK (plan_code ~ '^[A-Z_]{{3,24}}$'),
            current_plan    text NOT NULL CHECK (current_plan ~ '^[A-Z_]{{3,24}}$'),
            note            text CHECK (note IS NULL OR char_length(note) <= 1000),
            requested_by    uuid NOT NULL,
            status          text NOT NULL DEFAULT 'OPEN'
                                CHECK (status IN ('OPEN', 'ACCEPTED', 'DECLINED', 'WITHDRAWN')),
            created_at      timestamptz NOT NULL DEFAULT now(),
            decided_by      uuid,
            decided_at      timestamptz,
            decision_note   text CHECK (decision_note IS NULL OR char_length(decision_note) <= 1000),
            CHECK ((status IN ('ACCEPTED', 'DECLINED')) = (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
            CHECK (plan_code <> current_plan)
        );
        CREATE UNIQUE INDEX plan_request_one_open ON commercial.plan_request (tenant_id) WHERE status = 'OPEN';
        CREATE INDEX plan_request_open_by_time ON commercial.plan_request (created_at) WHERE status = 'OPEN';

        ALTER TABLE commercial.plan_request ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.plan_request FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.plan_request
            USING ({TENANT}) WITH CHECK ({TENANT});
        CREATE POLICY operator_scope ON commercial.plan_request
            USING ({OPERATOR}) WITH CHECK ({OPERATOR});
    """)


def downgrade() -> None:
    _run("DROP TABLE commercial.plan_request;")

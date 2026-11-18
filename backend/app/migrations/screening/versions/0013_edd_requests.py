"""EDD information requests on a screening case (ZID-DTV-WF-06 section 10; CR-121).

Revision ID: screening_0013
Revises: screening_0012
Create Date: 2026-11-18

screening_edd_request: a reviewer's request to the customer for named items (a fixed catalogue), with a due date, what has been provided, the reminders sent and the review. At most one request is open
(REQUESTED or RECEIVED) for a case at a time. The customer is never told why; the wording is `domain.edd_request.customer_text`. Expiry clears nothing.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0013"
down_revision = "screening_0012"
branch_labels = None
depends_on = None

TABLES = ("screening_edd_request",)
_RLS = """
    ALTER TABLE verify.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE verify.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON verify.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.screening_edd_request (
            edd_id        uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            request_id    uuid NOT NULL REFERENCES verify.screening_request (request_id),
            items         jsonb NOT NULL,
            requested_by  uuid NOT NULL,
            opened_at     timestamptz NOT NULL,
            due_at        timestamptz NOT NULL,
            state         text NOT NULL CHECK (state IN ('REQUESTED', 'RECEIVED', 'ACCEPTED', 'INSUFFICIENT', 'EXPIRED')),
            provided      jsonb NOT NULL DEFAULT '[]',
            reminders     jsonb NOT NULL DEFAULT '[]',
            reviewed_by   uuid,
            review_code   text,
            updated_at    timestamptz NOT NULL,
            CHECK (due_at > opened_at),
            CHECK (reviewed_by IS NULL OR reviewed_by <> requested_by)
        );
        CREATE UNIQUE INDEX screening_edd_one_open ON verify.screening_edd_request (request_id) WHERE state IN ('REQUESTED', 'RECEIVED');
        CREATE INDEX screening_edd_due ON verify.screening_edd_request (state, due_at);
    """)
    for table in TABLES:
        _run(_RLS.format(t=table))
    _run(
        "CREATE POLICY reviewer_read ON verify.screening_edd_request FOR SELECT USING (current_setting('app.screening_reviewer', true) = 'on');"
    )


def downgrade() -> None:
    _run("DROP TABLE verify.screening_edd_request;")

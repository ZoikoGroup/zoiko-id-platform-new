"""The last outcome of a company claim, so a change of outcome is announced once (ZID-UX-WF-013B events; slice L-12, CR-052).

Revision ID: onboarding_0008
Revises: onboarding_0007
Create Date: 2026-11-03

The outcome of a claim is derived from four checks that change in different places (a reviewer, the worker, the applicant, the passing of
time). This row remembers the last one that was seen, so that when it changes the platform says so exactly once, whoever caused it.
"""

from zid_platform.migrations import execute_script as _run

revision = "onboarding_0008"
down_revision = "onboarding_0007"
branch_labels = None
depends_on = None

TABLES = ("org_claim_outcome",)
_RLS = """
    ALTER TABLE platform.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE platform.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON platform.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.org_claim_outcome (
            tenant_id   uuid NOT NULL,
            claim_id    uuid NOT NULL,
            outcome     text NOT NULL CHECK (length(outcome) BETWEEN 1 AND 24),
            checks      jsonb NOT NULL DEFAULT '{}',
            since       timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, claim_id)
        );
    """)
    for table in TABLES:
        _run(_RLS.format(t=table))


def downgrade() -> None:
    pass

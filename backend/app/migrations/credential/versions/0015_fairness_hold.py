"""A held automated path for a cohort, from the disparity check (conformance T44; CR-092).

Revision ID: credential_0015
Revises: credential_0014
Create Date: 2026-11-14

fairness_hold: one row per cohort (the writing system of a name) saying whether its automated "different person" answer is held for a person, why,
and the counts and rates the check saw. Aggregate counts only: no tenant, no person, no name. Platform state, not tenant data: no tenant_id and
no row-level security. Rows are rewritten by each evaluation; the outbox event and the alert are the history.
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0015"
down_revision = "credential_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE cred.fairness_hold (
            cohort        text PRIMARY KEY CHECK (cohort ~ '^[A-Z]{2,16}$'),
            held          boolean NOT NULL,
            reason        text CHECK (reason IN ('NO_APPROVED_THRESHOLD', 'MATERIAL_DISPARITY')),
            detail        jsonb NOT NULL,
            evaluated_at  timestamptz NOT NULL,
            held_since    timestamptz,
            CHECK (held = (reason IS NOT NULL)),
            CHECK (held = (held_since IS NOT NULL))
        );
    """)


def downgrade() -> None:
    _run("DROP TABLE cred.fairness_hold;")

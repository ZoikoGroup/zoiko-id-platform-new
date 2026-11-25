"""A usage meter for hosted identity verifications (ZID-CP-WF01 S08, Q33): one submitted session.

Revision ID: commercial_0046
Revises: commercial_0045
Create Date: 2026-11-24

The billable meters are a closed set held in check constraints. This widens them to carry IDV_SUBMISSION, recorded once when a person makes the one final submission of a hosted
session (the key is the session, so a repeat, a resume or a retake is the same event). It is a usage fact only, like the screening and credential meters: no SKU row and no price
is added, so nothing is rated or invoiced until the owners define the unit and approve a price. Existing events, windows and budgets are untouched.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0046"
down_revision = "commercial_0045"
branch_labels = None
depends_on = None

OLD = (
    "('MAH', 'AUTH_EVENT', 'POLICY_DECISION', 'M2M_GRANT', 'VC_ISSUED', 'VC_PRESENTED', 'SCREEN_CHECK', 'SCREEN_MONITOR_PERIOD', "
    "'CREDENTIAL_CHECK', 'CREDENTIAL_SENSITIVE_CHECK', 'VERIFY_SESSION_START', 'VERIFY_CHECK_COMPLETE')"
)
NEW = (
    "('MAH', 'AUTH_EVENT', 'POLICY_DECISION', 'M2M_GRANT', 'VC_ISSUED', 'VC_PRESENTED', 'SCREEN_CHECK', 'SCREEN_MONITOR_PERIOD', "
    "'CREDENTIAL_CHECK', 'CREDENTIAL_SENSITIVE_CHECK', 'VERIFY_SESSION_START', 'VERIFY_CHECK_COMPLETE', 'IDV_SUBMISSION')"
)
TABLES = ("billable_event", "billable_adjustment", "meter_window", "budget")


def _swap(to: str) -> str:
    out = []
    for t in TABLES:
        out.append(f"""
        DO $$
        DECLARE c record;
        BEGIN
            FOR c IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'commercial.{t}'::regclass AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%meter_id%' AND pg_get_constraintdef(oid) LIKE '%VC_PRESENTED%'
            LOOP
                EXECUTE format('ALTER TABLE commercial.{t} DROP CONSTRAINT %I', c.conname);
            END LOOP;
            ALTER TABLE commercial.{t} ADD CONSTRAINT {t}_meter_known CHECK (meter_id IN {to});
        END $$;
        """)
    return "".join(out)


def upgrade() -> None:
    _run(_swap(NEW))


def downgrade() -> None:
    _run(_swap(OLD))

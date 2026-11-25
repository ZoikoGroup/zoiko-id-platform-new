"""Two usage meters for the Verify API (CR-154; ZID-EXT-IDV-001 epic E17): a session started and a check completed, in production.

Revision ID: commercial_0045
Revises: commercial_0044
Create Date: 2026-11-24

The billable meters are a closed set held in check constraints. This widens them to carry VERIFY_SESSION_START (one production session started) and VERIFY_CHECK_COMPLETE (one production session that reached a
final decision). They are usage facts only, exactly like the screening and credential-check meters: no SKU row and no price is added, so nothing is rated or invoiced until the owners ratify the billable-event
policy (D12) and approve a price. Existing events, windows and budgets are untouched.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0045"
down_revision = "commercial_0044"
branch_labels = None
depends_on = None

OLD = (
    "('MAH', 'AUTH_EVENT', 'POLICY_DECISION', 'M2M_GRANT', 'VC_ISSUED', 'VC_PRESENTED', 'SCREEN_CHECK', 'SCREEN_MONITOR_PERIOD', "
    "'CREDENTIAL_CHECK', 'CREDENTIAL_SENSITIVE_CHECK')"
)
NEW = (
    "('MAH', 'AUTH_EVENT', 'POLICY_DECISION', 'M2M_GRANT', 'VC_ISSUED', 'VC_PRESENTED', 'SCREEN_CHECK', 'SCREEN_MONITOR_PERIOD', "
    "'CREDENTIAL_CHECK', 'CREDENTIAL_SENSITIVE_CHECK', 'VERIFY_SESSION_START', 'VERIFY_CHECK_COMPLETE')"
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

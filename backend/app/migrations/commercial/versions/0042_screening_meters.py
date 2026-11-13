"""Two usage meters for screening (CR-090; ZID-UX-PRICE-004 P23): a screening check and a monitoring period.

Revision ID: commercial_0042
Revises: commercial_0041
Create Date: 2026-11-13

The billable meters are a closed set held in check constraints. This widens them to carry SCREEN_CHECK (one automated screening decision of one case) and SCREEN_MONITOR_PERIOD (one case under continuous
monitoring for one UTC month). They are usage facts only: no SKU row and no price is added, so nothing is rated or invoiced until the owners approve a unit and a price. Existing events, windows and budgets are untouched.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0042"
down_revision = "commercial_0041"
branch_labels = None
depends_on = None

OLD = "('MAH', 'AUTH_EVENT', 'POLICY_DECISION', 'M2M_GRANT', 'VC_ISSUED', 'VC_PRESENTED')"
NEW = "('MAH', 'AUTH_EVENT', 'POLICY_DECISION', 'M2M_GRANT', 'VC_ISSUED', 'VC_PRESENTED', 'SCREEN_CHECK', 'SCREEN_MONITOR_PERIOD')"
TABLES = ("billable_event", "billable_adjustment", "meter_window", "budget")


def _swap(frm: str, to: str) -> str:
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
    _run(_swap(OLD, NEW))


def downgrade() -> None:
    _run(_swap(NEW, OLD))

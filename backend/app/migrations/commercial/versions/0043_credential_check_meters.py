"""Two usage meters for credential checks (CR-070; ZID-GAP-001 v2.0 GID-064): a claim check and a sensitive-category check.

Revision ID: commercial_0043
Revises: commercial_0042
Create Date: 2026-11-16

The billable meters are a closed set held in check constraints. This widens them to carry CREDENTIAL_CHECK (one completed claim check that reached a source or an issuer outreach concluded) and
CREDENTIAL_SENSITIVE_CHECK (one completed sensitive-category check). They are usage facts only, exactly like the screening meters of commercial_0042: no SKU row and no price is added, so nothing is
rated or invoiced until the owners approve a unit and a price. Existing events, windows and budgets are untouched.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0043"
down_revision = "commercial_0042"
branch_labels = None
depends_on = None

OLD = "('MAH', 'AUTH_EVENT', 'POLICY_DECISION', 'M2M_GRANT', 'VC_ISSUED', 'VC_PRESENTED', 'SCREEN_CHECK', 'SCREEN_MONITOR_PERIOD')"
NEW = (
    "('MAH', 'AUTH_EVENT', 'POLICY_DECISION', 'M2M_GRANT', 'VC_ISSUED', 'VC_PRESENTED', 'SCREEN_CHECK', 'SCREEN_MONITOR_PERIOD', "
    "'CREDENTIAL_CHECK', 'CREDENTIAL_SENSITIVE_CHECK')"
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

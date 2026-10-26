"""Budgets and threshold alerts (ZID-COM-001 §7 anti-bill-shock; CR-029).

Revision ID: commercial_0006
Revises: commercial_0005
Create Date: 2026-10-24

`budget` holds a tenant's own monthly targets: the whole bill in money, or one meter in units. `budget_alert`
records each threshold reached, at most once per (alert key, month, threshold), so a crossing notifies once
however often evaluation runs. Included-quota warnings use the same table with a `QUOTA:<meter>` key. Neither
table can block anything: a budget only notifies. Tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0006"
down_revision = "commercial_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.budget (
            budget_id   uuid PRIMARY KEY,
            tenant_id   uuid NOT NULL,
            meter_id    text CHECK (meter_id IN
                ('MAH', 'AUTH_EVENT', 'POLICY_DECISION', 'M2M_GRANT', 'VC_ISSUED', 'VC_PRESENTED')),
            monthly_limit numeric(18, 6) NOT NULL CHECK (monthly_limit >= 0),
            currency    text CHECK (currency ~ '^[A-Z]{3}$'),
            thresholds  integer[] NOT NULL DEFAULT '{50,80,100}',
            active      boolean NOT NULL DEFAULT true,
            created_by  uuid NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            CHECK ((meter_id IS NULL) = (currency IS NOT NULL))
        );
        CREATE UNIQUE INDEX budget_one_active_per_target ON commercial.budget (tenant_id, coalesce(meter_id, 'BILL'))
            WHERE active;

        CREATE TABLE commercial.budget_alert (
            tenant_id  uuid NOT NULL,
            alert_key  text NOT NULL CHECK (length(alert_key) BETWEEN 3 AND 80),
            period     date NOT NULL,
            threshold  integer NOT NULL CHECK (threshold BETWEEN 1 AND 200),
            observed   numeric(30, 6) NOT NULL,
            limit_value numeric(30, 6) NOT NULL,
            raised_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, alert_key, period, threshold)
        );
        CREATE TRIGGER budget_alert_immutable BEFORE UPDATE OR DELETE ON commercial.budget_alert
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();

        ALTER TABLE commercial.budget ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.budget FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.budget
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE commercial.budget_alert ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.budget_alert FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.budget_alert
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE commercial.budget_alert; DROP TABLE commercial.budget;")

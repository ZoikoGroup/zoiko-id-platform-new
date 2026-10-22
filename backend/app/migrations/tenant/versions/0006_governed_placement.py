"""A home cell moves only inside a governed recovery operation (TENANCY-001 §15; RESILIENCE §15;
G-W7; CR-016).

Revision ID: tenant_0006
Revises: tenant_0005
Create Date: 2026-10-22

tenant_guard already refuses a home-cell change once a tenant is past CONFIGURING. The resilience
module's ADVANCE_EPOCHS step marks its own transaction with `app.governed_placement = 'on'` (a
transaction-local setting the API never exposes); only then may a live tenant's home cell change.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0006"
down_revision = "tenant_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE OR REPLACE FUNCTION platform.tenant_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.configuration_revision < OLD.configuration_revision
               OR NEW.security_revision < OLD.security_revision THEN
                RAISE EXCEPTION 'tenant revisions only increase' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'CLOSED' THEN
                RAISE EXCEPTION 'a CLOSED tenant is terminal' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.home_cell_id <> OLD.home_cell_id
               AND OLD.status NOT IN ('DRAFT', 'ORGANIZATION_VERIFYING', 'CONFIGURING', 'BLOCKED')
               AND coalesce(current_setting('app.governed_placement', true), '') <> 'on' THEN
                RAISE EXCEPTION 'home cell moves only through a governed migration'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
    """)


def downgrade() -> None:
    pass  # the stricter guard of tenant_0002 is restored by re-running that function body

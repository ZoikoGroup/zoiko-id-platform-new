"""Mandate terms for scope checks and sub-agent delegation (ZID-MANDATE-001 §9-10, §17; MAND-06/07/18-20).

Revision ID: mandate_0002
Revises: mandate_0001
Create Date: 2026-10-12

Adds what ZID-MANDATE-001 §9 requires beyond ZID-DATA-001 §8.2 (CR-007): parent mandate, delegation
flag/depth, budget mode, and the tool / jurisdiction / resource-scope sets the gateway checks.
Database guards: a sub-mandate's depth is strictly lower than its parent's; scope columns are
immutable (a change is a superseding mandate, §10); terminal states never move.
"""

from zid_platform.migrations import execute_script as _run

revision = "mandate_0002"
down_revision = "mandate_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE agent.mandate
            ADD COLUMN parent_mandate_id uuid REFERENCES agent.mandate (mandate_id),
            ADD COLUMN delegation_allowed boolean NOT NULL DEFAULT false,
            ADD COLUMN max_delegation_depth smallint NOT NULL DEFAULT 0 CHECK (max_delegation_depth >= 0),
            ADD COLUMN budget_mode text CHECK (budget_mode IN ('ALLOCATED_SUB_BUDGET', 'SHARED_PARENT_LEDGER')),
            ADD COLUMN tools text[] NOT NULL DEFAULT '{}',
            ADD COLUMN jurisdictions text[] NOT NULL DEFAULT '{}',
            ADD COLUMN resource_scopes text[] NOT NULL DEFAULT '{}',
            ADD COLUMN allocation_reservation_id uuid,
            ADD CONSTRAINT sub_mandate_has_mode CHECK (parent_mandate_id IS NULL OR budget_mode IS NOT NULL),
            ADD CONSTRAINT not_own_parent CHECK (parent_mandate_id IS NULL OR parent_mandate_id <> mandate_id);
        CREATE INDEX mandate_parent ON agent.mandate (parent_mandate_id);
        CREATE INDEX mandate_grant ON agent.mandate (tenant_id, authority_grant_id, status);

        CREATE FUNCTION agent.mandate_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_depth smallint;
        BEGIN
            IF TG_OP = 'INSERT' AND NEW.parent_mandate_id IS NOT NULL THEN
                SELECT max_delegation_depth INTO parent_depth FROM agent.mandate WHERE mandate_id = NEW.parent_mandate_id;
                IF parent_depth IS NULL OR NEW.max_delegation_depth >= parent_depth THEN
                    RAISE EXCEPTION 'sub-mandate depth must strictly decrease' USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF OLD.status IN ('EXHAUSTED', 'COMPLETED', 'EXPIRED', 'REVOKED') AND NEW.status <> OLD.status THEN
                    RAISE EXCEPTION 'terminal mandate states are final' USING ERRCODE = 'check_violation';
                END IF;
                IF (NEW.authority_grant_id, NEW.represented_principal_id, NEW.executor_principal_id, NEW.purpose_id,
                    NEW.allowed_actions, NEW.tools, NEW.jurisdictions, NEW.resource_scopes, NEW.value_limit,
                    NEW.per_transaction_value_limit, NEW.currency, NEW.count_limit, NEW.data_limit_bytes,
                    NEW.valid_from, NEW.valid_to, NEW.parent_mandate_id, NEW.max_delegation_depth)
                   IS DISTINCT FROM
                   (OLD.authority_grant_id, OLD.represented_principal_id, OLD.executor_principal_id, OLD.purpose_id,
                    OLD.allowed_actions, OLD.tools, OLD.jurisdictions, OLD.resource_scopes, OLD.value_limit,
                    OLD.per_transaction_value_limit, OLD.currency, OLD.count_limit, OLD.data_limit_bytes,
                    OLD.valid_from, OLD.valid_to, OLD.parent_mandate_id, OLD.max_delegation_depth) THEN
                    RAISE EXCEPTION 'mandate terms are immutable; create a superseding mandate'
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER mandate_guard BEFORE INSERT OR UPDATE ON agent.mandate
            FOR EACH ROW EXECUTE FUNCTION agent.mandate_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER mandate_guard ON agent.mandate;
        DROP FUNCTION agent.mandate_guard();
        ALTER TABLE agent.mandate
            DROP CONSTRAINT not_own_parent, DROP CONSTRAINT sub_mandate_has_mode,
            DROP COLUMN allocation_reservation_id, DROP COLUMN resource_scopes, DROP COLUMN jurisdictions,
            DROP COLUMN tools, DROP COLUMN budget_mode, DROP COLUMN max_delegation_depth,
            DROP COLUMN delegation_allowed, DROP COLUMN parent_mandate_id;
    """)

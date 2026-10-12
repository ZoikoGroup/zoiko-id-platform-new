"""Agent, blueprint, workload binding and tool registry (ZID-DATA-001 §8.1; ZID-MACHINE-001 §2-3, §15-16).

Revision ID: machine_0001
Revises:
Create Date: 2026-10-12

agent.agent_profile follows ZID-MACHINE-001 §2 (wider than ZID-DATA-001 §8.1: DRAFT/PENDING_APPROVAL
states, owning organization, blueprint version, max sub-agent depth, allowed tools). Proposed tables
(CR-007): blueprint_release, tool_capability; workload_binding is the CR-003 table.
Database guards: an ACTIVE agent has a sponsor and owning organization (MACH-02); REVOKED agents
never return; security_revision never decreases.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ("agent_profile", "blueprint_release", "workload_binding", "tool_capability")


def upgrade() -> None:
    _run("""
        CREATE TYPE agent.agent_status AS ENUM ('DRAFT', 'PENDING_APPROVAL', 'ACTIVE', 'SUSPENDED', 'REVOKED', 'RETIRED');

        CREATE TABLE agent.blueprint_release (
            tenant_id               uuid NOT NULL,
            blueprint_id            text NOT NULL,
            version                 text NOT NULL,
            artifact_digest         text NOT NULL,
            tool_manifest_digest    text NOT NULL,
            publisher_principal_id  uuid NOT NULL,
            review_status           text NOT NULL CHECK (review_status IN ('DRAFT', 'APPROVED', 'REVOKED')),
            effective_at            timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, blueprint_id, version)
        );

        CREATE TABLE agent.agent_profile (
            agent_principal_id          uuid PRIMARY KEY,
            tenant_id                   uuid NOT NULL,
            agent_name                  text NOT NULL,
            blueprint_id                text NOT NULL,
            blueprint_version           text NOT NULL,
            publisher_principal_id      uuid NOT NULL,
            operator_principal_id       uuid NOT NULL,
            sponsor_principal_id        uuid,
            owning_organization_id      uuid,
            parent_agent_principal_id   uuid REFERENCES agent.agent_profile (agent_principal_id),
            model_provider              text NOT NULL DEFAULT '',
            allowed_tools               text[] NOT NULL DEFAULT '{}',
            max_subagent_depth          smallint NOT NULL DEFAULT 0 CHECK (max_subagent_depth >= 0),
            status                      agent.agent_status NOT NULL,
            security_revision           bigint NOT NULL DEFAULT 0 CHECK (security_revision >= 0),
            created_at                  timestamptz NOT NULL DEFAULT now(),
            retired_at                  timestamptz,
            CHECK (status <> 'ACTIVE' OR (sponsor_principal_id IS NOT NULL AND owning_organization_id IS NOT NULL)),
            CHECK (parent_agent_principal_id IS NULL OR parent_agent_principal_id <> agent_principal_id),
            FOREIGN KEY (tenant_id, blueprint_id, blueprint_version)
                REFERENCES agent.blueprint_release (tenant_id, blueprint_id, version)
        );

        CREATE FUNCTION agent.agent_profile_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status = 'REVOKED' AND NEW.status <> 'REVOKED' THEN
                RAISE EXCEPTION 'revoked agents never return' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.security_revision < OLD.security_revision THEN
                RAISE EXCEPTION 'security_revision may not decrease' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.agent_principal_id <> OLD.agent_principal_id OR NEW.tenant_id <> OLD.tenant_id THEN
                RAISE EXCEPTION 'agent identity is immutable' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER agent_profile_guard BEFORE UPDATE ON agent.agent_profile
            FOR EACH ROW EXECUTE FUNCTION agent.agent_profile_guard();

        CREATE TABLE agent.workload_binding (
            binding_id              uuid PRIMARY KEY,
            tenant_id               uuid NOT NULL,
            logical_principal_id    uuid NOT NULL REFERENCES agent.agent_profile (agent_principal_id),
            workload_identity_id    text NOT NULL,
            blueprint_version       text NOT NULL,
            artifact_digest         text NOT NULL,
            environment_id          text NOT NULL,
            attestation_required    smallint NOT NULL DEFAULT 0 CHECK (attestation_required BETWEEN 0 AND 3),
            status                  text NOT NULL CHECK (status IN ('ACTIVE', 'SUSPENDED', 'REVOKED', 'EXPIRED')),
            valid_from              timestamptz NOT NULL,
            valid_to                timestamptz NOT NULL,
            security_revision       bigint NOT NULL DEFAULT 0,
            CHECK (valid_to > valid_from)
        );
        -- One live binding per runtime identity: a workload can't be bound to two agents at once (§16).
        CREATE UNIQUE INDEX binding_one_live_per_workload ON agent.workload_binding (tenant_id, workload_identity_id)
            WHERE status = 'ACTIVE';

        CREATE TABLE agent.tool_capability (
            tenant_id               uuid NOT NULL,
            tool_id                 text NOT NULL,
            version                 text NOT NULL,
            server_principal_id     uuid NOT NULL,
            canonical_action        text NOT NULL,
            default_risk_class      smallint NOT NULL CHECK (default_risk_class BETWEEN 0 AND 3),
            side_effect             text NOT NULL CHECK (side_effect IN ('NONE', 'REVERSIBLE', 'IRREVERSIBLE',
                                                                          'EXTERNAL_TRANSACTION')),
            requires_mandate        boolean NOT NULL,
            requires_human_approval boolean NOT NULL,
            status                  text NOT NULL CHECK (status IN ('DRAFT', 'ACTIVE', 'SUSPENDED', 'RETIRED')),
            PRIMARY KEY (tenant_id, tool_id)
        );
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE agent.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE agent.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON agent.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE agent.tool_capability;
        DROP TABLE agent.workload_binding;
        DROP TRIGGER agent_profile_guard ON agent.agent_profile;
        DROP FUNCTION agent.agent_profile_guard();
        DROP TABLE agent.agent_profile;
        DROP TABLE agent.blueprint_release;
        DROP TYPE agent.agent_status;
    """)

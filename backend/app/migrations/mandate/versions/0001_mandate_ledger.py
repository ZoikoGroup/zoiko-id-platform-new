"""Mandate ledger: mandate, balance, reservation, writer fence (ZID-DATA-001 §8.2-8.3; ZID-MANDATE-001 §9-19; DEC-C5).

Revision ID: mandate_0001
Revises:
Create Date: 2026-10-12

Differences from ZID-DATA-001 v1.0, required by ZID-MANDATE-001 (raised as CR-006):
- mandate_status adds PENDING_APPROVAL (MANDATE §10)
- reservation_status adds RECONCILIATION_REQUIRED (MANDATE §14)
- agent.mandate adds per_transaction_value_limit (MANDATE §9)
- agent.mandate_reservation adds transaction_context_hash (+ algorithm) (MANDATE §12.1, §20)
agent.writer_fence is the CR-003 proposed table (per tenant, per cell epoch; DEC-C5).

Guards live in the database as well as the domain code: non-negative CHECKs, one reservation per
idempotency key, and FORCE ROW LEVEL SECURITY on every tenant table.
"""

from zid_platform.migrations import execute_script as _run

revision = "mandate_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ("mandate", "mandate_balance", "mandate_reservation", "writer_fence")


def upgrade() -> None:
    _run("""
        CREATE TYPE agent.mandate_status AS ENUM (
            'PENDING_APPROVAL', 'ACTIVE', 'PAUSED', 'EXHAUSTED', 'COMPLETED', 'EXPIRED', 'REVOKED');
        CREATE TYPE agent.reservation_status AS ENUM (
            'RESERVED', 'COMMITTED', 'RELEASED', 'EXPIRED', 'RECONCILIATION_REQUIRED');

        CREATE TABLE agent.mandate (
            mandate_id                  uuid PRIMARY KEY,
            tenant_id                   uuid NOT NULL,
            authority_grant_id          uuid NOT NULL,
            represented_principal_id    uuid NOT NULL,
            executor_principal_id       uuid NOT NULL,
            purpose_id                  uuid NOT NULL,
            resource_scope_id           uuid NOT NULL,
            allowed_actions             text[] NOT NULL,
            allowed_toolset_id          uuid,
            value_limit                 numeric(38,9) CHECK (value_limit >= 0),
            per_transaction_value_limit numeric(38,9) CHECK (per_transaction_value_limit >= 0),
            currency                    char(3),
            count_limit                 bigint CHECK (count_limit >= 0),
            data_limit_bytes            bigint CHECK (data_limit_bytes >= 0),
            home_cell_id                uuid NOT NULL,
            status                      agent.mandate_status NOT NULL,
            valid_from                  timestamptz NOT NULL,
            valid_to                    timestamptz NOT NULL,
            row_version                 bigint NOT NULL DEFAULT 0,
            created_by_principal_id     uuid NOT NULL,
            created_at                  timestamptz NOT NULL DEFAULT now(),
            CHECK (valid_to > valid_from),
            CHECK ((value_limit IS NULL AND per_transaction_value_limit IS NULL) OR currency IS NOT NULL),
            UNIQUE (tenant_id, mandate_id)
        );
        CREATE INDEX mandate_tenant_status ON agent.mandate (tenant_id, status);

        CREATE TABLE agent.mandate_balance (
            mandate_id      uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            committed_value numeric(38,9) NOT NULL DEFAULT 0 CHECK (committed_value >= 0),
            reserved_value  numeric(38,9) NOT NULL DEFAULT 0 CHECK (reserved_value >= 0),
            committed_count bigint NOT NULL DEFAULT 0 CHECK (committed_count >= 0),
            reserved_count  bigint NOT NULL DEFAULT 0 CHECK (reserved_count >= 0),
            committed_bytes bigint NOT NULL DEFAULT 0 CHECK (committed_bytes >= 0),
            reserved_bytes  bigint NOT NULL DEFAULT 0 CHECK (reserved_bytes >= 0),
            ledger_version  bigint NOT NULL DEFAULT 0,
            FOREIGN KEY (tenant_id, mandate_id) REFERENCES agent.mandate (tenant_id, mandate_id)
        );

        CREATE TABLE agent.mandate_reservation (
            reservation_id              uuid PRIMARY KEY,
            tenant_id                   uuid NOT NULL,
            mandate_id                  uuid NOT NULL,
            idempotency_key_hash        bytea NOT NULL,
            transaction_context_hash    bytea NOT NULL,
            hash_algorithm              text NOT NULL DEFAULT 'sha256',
            requested_value             numeric(38,9) NOT NULL DEFAULT 0 CHECK (requested_value >= 0),
            requested_count             bigint NOT NULL DEFAULT 0 CHECK (requested_count >= 0),
            requested_bytes             bigint NOT NULL DEFAULT 0 CHECK (requested_bytes >= 0),
            currency                    char(3),
            reservation_status          agent.reservation_status NOT NULL,
            expires_at                  timestamptz NOT NULL,
            execution_id                text,
            decision_id                 uuid,
            created_at                  timestamptz NOT NULL DEFAULT now(),
            settled_at                  timestamptz,
            FOREIGN KEY (tenant_id, mandate_id) REFERENCES agent.mandate (tenant_id, mandate_id),
            UNIQUE (mandate_id, idempotency_key_hash)
        );
        CREATE INDEX mandate_reservation_sweep ON agent.mandate_reservation (reservation_status, expires_at);

        CREATE TABLE agent.writer_fence (
            tenant_id   uuid PRIMARY KEY,
            cell_id     uuid NOT NULL,
            epoch       bigint NOT NULL CHECK (epoch >= 1),
            updated_at  timestamptz NOT NULL DEFAULT now()
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
        DROP TABLE agent.writer_fence;
        DROP TABLE agent.mandate_reservation;
        DROP TABLE agent.mandate_balance;
        DROP TABLE agent.mandate;
        DROP TYPE agent.reservation_status;
        DROP TYPE agent.mandate_status;
    """)

"""Cell state, recovery readiness, recovery operations and DR exercises (ZID-RESILIENCE-001 §4, §11,
§15-20, §25-30; DEC-G1; G-W7; CR-016).

Revision ID: resilience_0001
Revises:
Create Date: 2026-10-22

Operational metadata of the cell, not tenant content (no PII), so not tenant-scoped:
- cell_state: the operator-set state of each cell (ACTIVE / DEGRADED / DRAINING / QUARANTINED).
- cell_readiness: the latest telemetry-reported recovery readiness of each cell (replica lag, backup
  age, recovery-key readiness, one-AZ-loss headroom) that the ASSESS gate reads.
- recovery_operation / recovery_step: one governed failover, migration, failback or restore and its
  strictly ordered, append-only step log (who, what, evidence reference, gate report).
- dr_exercise: exercise results with observed vs target RTO/RPO (RES-30, SRE-20).
"""

from zid_platform.migrations import execute_script as _run

revision = "resilience_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.cell_state (
            cell_id       text PRIMARY KEY CHECK (cell_id ~ '^[a-z0-9-]{1,40}$'),
            status        text NOT NULL CHECK (status IN ('ACTIVE', 'DEGRADED', 'DRAINING', 'QUARANTINED')),
            reason_code   text,
            changed_by    uuid,
            changed_at    timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE platform.cell_readiness (
            cell_id                text PRIMARY KEY CHECK (cell_id ~ '^[a-z0-9-]{1,40}$'),
            replica_lag_s          numeric(12, 3),
            backup_age_s           numeric(12, 3),
            recovery_keys_ready    boolean NOT NULL DEFAULT false,
            one_az_loss_headroom   numeric(5, 4),
            last_restore_verified  timestamptz,
            observed_at            timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE platform.recovery_operation (
            operation_id     uuid PRIMARY KEY,
            tenant_id        uuid,
            kind             text NOT NULL CHECK (kind IN ('FAILOVER', 'MIGRATION', 'FAILBACK', 'RESTORE')),
            source_cell_id   text NOT NULL,
            target_cell_id   text NOT NULL,
            status           text NOT NULL DEFAULT 'OPEN'
                             CHECK (status IN ('OPEN', 'BLOCKED', 'COMPLETED', 'ABORTED')),
            steps_done       integer NOT NULL DEFAULT 0 CHECK (steps_done >= 0),
            blocked_code     text,
            declared_by      uuid NOT NULL,
            jit_grant_id     uuid,
            epoch_before     integer,
            epoch_after      integer,
            declared_at      timestamptz NOT NULL DEFAULT now(),
            completed_at     timestamptz,
            observed_rto_s   numeric(12, 3),
            CHECK (kind = 'RESTORE' OR tenant_id IS NOT NULL),
            CHECK ((status = 'BLOCKED') = (blocked_code IS NOT NULL))
        );
        -- one open operation per tenant: no two concurrent moves of the same writer
        CREATE UNIQUE INDEX recovery_operation_one_open ON platform.recovery_operation (tenant_id)
            WHERE status IN ('OPEN', 'BLOCKED') AND tenant_id IS NOT NULL;

        CREATE TABLE platform.recovery_step (
            operation_id   uuid NOT NULL REFERENCES platform.recovery_operation (operation_id),
            seq            integer NOT NULL,
            step           text NOT NULL,
            outcome        text NOT NULL CHECK (outcome IN ('DONE', 'BLOCKED', 'ABORTED')),
            actor          uuid,
            evidence_ref   text,
            report         jsonb NOT NULL DEFAULT '[]',
            at             timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (operation_id, seq)
        );
        CREATE FUNCTION platform.recovery_step_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'the recovery step log is append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER recovery_step_append_only BEFORE UPDATE OR DELETE ON platform.recovery_step
            FOR EACH ROW EXECUTE FUNCTION platform.recovery_step_guard();

        CREATE TABLE platform.dr_exercise (
            exercise_id      uuid PRIMARY KEY,
            kind             text NOT NULL CHECK (kind IN ('FAILOVER', 'MIGRATION', 'FAILBACK', 'RESTORE',
                             'AZ_LOSS', 'PROCESS_KILL', 'BROKER_DEGRADATION', 'KMS_OUTAGE',
                             'CONTROL_PLANE_LOSS', 'PARTITION')),
            cell_id          text NOT NULL,
            scenario         text NOT NULL,
            operation_id     uuid REFERENCES platform.recovery_operation (operation_id),
            observed_rto_s   numeric(12, 3),
            observed_rpo_s   numeric(12, 3),
            target_rto_s     numeric(12, 3),
            target_rpo_s     numeric(12, 3),
            passed           boolean NOT NULL,
            findings         jsonb NOT NULL DEFAULT '[]',
            recorded_by      uuid NOT NULL,
            started_at       timestamptz NOT NULL,
            completed_at     timestamptz NOT NULL
        );
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.dr_exercise;
        DROP TABLE platform.recovery_step;
        DROP FUNCTION platform.recovery_step_guard();
        DROP TABLE platform.recovery_operation;
        DROP TABLE platform.cell_readiness;
        DROP TABLE platform.cell_state;
    """)

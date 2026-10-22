"""Onboarding plans, pilot stages and shadow observations (ZID-OVERLAY-001 §17-20; G-W4; CR-015).

Revision ID: onboarding_0001
Revises:
Create Date: 2026-10-22

- onboarding_plan: versioned (a new version supersedes; an APPROVED version is never edited -
  trigger), with scope, target mode, exit criteria, cutover windows, rollback triggers, support,
  data-migration and residency notes, approval and the rollback-tested proof.
- onboarding_stage: progressive enforcement scopes (applications, actions, risk ceiling,
  population); only ACTIVE stages enforce during PILOT.
- shadow_observation: one minimized row per evaluated request in SHADOW/PILOT - Zoiko and incumbent
  outcomes, mismatch class, context completeness, latency; the subject only as a commitment and no
  request content (§18 "PII").
"""

from zid_platform.migrations import execute_script as _run

revision = "onboarding_0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = ("onboarding_plan", "onboarding_stage", "shadow_observation")


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.onboarding_plan (
            plan_id               uuid PRIMARY KEY,
            tenant_id             uuid NOT NULL,
            version               integer NOT NULL CHECK (version >= 1),
            supersedes            uuid REFERENCES platform.onboarding_plan (plan_id),
            status                text NOT NULL DEFAULT 'DRAFT'
                                  CHECK (status IN ('DRAFT', 'APPROVED', 'SUPERSEDED')),
            target_mode           text NOT NULL CHECK (target_mode IN ('O0', 'O1', 'O2', 'O3')),
            scope                 jsonb NOT NULL,
            exit_criteria         jsonb NOT NULL,
            cutover_windows       jsonb NOT NULL DEFAULT '[]',
            rollback_triggers     jsonb NOT NULL DEFAULT '{}',
            support               jsonb NOT NULL DEFAULT '{}',
            data_migration        text NOT NULL DEFAULT 'NONE',
            acceptance_suite      text[] NOT NULL DEFAULT '{}',
            author_principal_id   uuid NOT NULL,
            approved_by           uuid,
            approved_at           timestamptz,
            shadow_started_at     timestamptz,
            pilot_started_at      timestamptz,
            rollback_tested_at    timestamptz,
            created_at            timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, version),
            CHECK (status <> 'APPROVED' OR (approved_by IS NOT NULL AND approved_by <> author_principal_id))
        );
        CREATE UNIQUE INDEX onboarding_plan_one_approved ON platform.onboarding_plan (tenant_id)
            WHERE status = 'APPROVED';

        CREATE FUNCTION platform.onboarding_plan_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'DRAFT' AND (NEW.scope IS DISTINCT FROM OLD.scope
               OR NEW.exit_criteria IS DISTINCT FROM OLD.exit_criteria
               OR NEW.rollback_triggers IS DISTINCT FROM OLD.rollback_triggers
               OR NEW.target_mode <> OLD.target_mode) THEN
                RAISE EXCEPTION 'an approved onboarding plan is immutable; create a new version'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER onboarding_plan_guard BEFORE UPDATE ON platform.onboarding_plan
            FOR EACH ROW EXECUTE FUNCTION platform.onboarding_plan_guard();

        CREATE TABLE platform.onboarding_stage (
            stage_id         uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            plan_id          uuid NOT NULL REFERENCES platform.onboarding_plan (plan_id),
            name             text NOT NULL,
            scope            jsonb NOT NULL,
            max_risk         smallint NOT NULL CHECK (max_risk BETWEEN 0 AND 3),
            status           text NOT NULL DEFAULT 'PLANNED'
                             CHECK (status IN ('PLANNED', 'ACTIVE', 'ROLLED_BACK', 'COMPLETED')),
            created_by       uuid NOT NULL,
            approved_by      uuid,
            activated_at     timestamptz,
            ended_at         timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now(),
            CHECK (status <> 'ACTIVE' OR approved_by IS NOT NULL)
        );

        CREATE TABLE platform.shadow_observation (
            observation_id     uuid PRIMARY KEY,
            tenant_id          uuid NOT NULL,
            plan_id            uuid,
            decision_id        text NOT NULL,
            application_id     uuid,
            action             text NOT NULL,
            risk_class         smallint NOT NULL,
            zoiko_outcome      text NOT NULL,
            zoiko_allow        boolean NOT NULL,
            incumbent_outcome  text CHECK (incumbent_outcome IN ('ALLOW', 'DENY')),
            mismatch           text NOT NULL CHECK (mismatch IN ('MATCH', 'FALSE_ALLOW', 'FALSE_DENY',
                               'NO_INCUMBENT')),
            context_complete   boolean NOT NULL,
            enforcement        text NOT NULL CHECK (enforcement IN ('ENFORCE', 'EVALUATE_ONLY')),
            subject_commitment text NOT NULL,
            latency_ms         numeric(10, 3) NOT NULL,
            observed_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX shadow_observation_window ON platform.shadow_observation (tenant_id, observed_at);
    """)
    for table in TABLES:
        _run(f"""
            ALTER TABLE platform.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE platform.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON platform.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.shadow_observation;
        DROP TABLE platform.onboarding_stage;
        DROP TABLE platform.onboarding_plan;
        DROP FUNCTION platform.onboarding_plan_guard();
    """)

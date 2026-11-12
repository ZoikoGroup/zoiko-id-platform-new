"""Model inventory, independent evaluations and drift holds (ZID-PRI-004 PR-16; AT-015 to AT-017; CR-072).

Revision ID: privacy_0004
Revises: privacy_0003
Create Date: 2026-11-12

A model version is registered with its LIVE features (a protected characteristic or proxy is refused), evaluated by an independent evaluator,
promoted only on that evidence, pinned while in service, and held when anything else is reported. Evaluations and observations are append-only.
Metrics are aggregate rates with confidence intervals; no person, image or template is stored.
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0004"
down_revision = "privacy_0003"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE privacy.model_version (
            model_id        uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            model_ref       text NOT NULL CHECK (model_ref ~ '^[a-z0-9][a-z0-9_.-]{{1,62}}$'),
            version         text NOT NULL CHECK (char_length(version) BETWEEN 1 AND 64),
            kind            text NOT NULL CHECK (kind IN ('face_match','pad','fraud','document')),
            provider_service_ref text,
            artifact_sha256 text CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{{64}}$'),
            live_features   text[] NOT NULL DEFAULT '{{}}',
            state           text NOT NULL CHECK (state IN ('REGISTERED','ACTIVE','HELD','SUSPENDED','RETIRED')),
            state_reason    text,
            promoted_by     uuid,
            promoted_at     timestamptz,
            created_by      uuid NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, model_ref, version)
        );
        CREATE UNIQUE INDEX model_one_active ON privacy.model_version (tenant_id, model_ref) WHERE state = 'ACTIVE';
        CREATE TABLE privacy.model_evaluation (
            evaluation_id   uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            model_id        uuid NOT NULL REFERENCES privacy.model_version (model_id),
            evaluator_ref   text NOT NULL CHECK (char_length(evaluator_ref) BETWEEN 1 AND 120),
            independent     boolean NOT NULL,
            cohorts         integer NOT NULL CHECK (cohorts BETWEEN 0 AND 1000),
            metrics         jsonb NOT NULL,
            accessible_alternative_confirmed boolean NOT NULL,
            audit_attributes text[] NOT NULL DEFAULT '{{}}',
            report_ref      text,
            recorded_by     uuid NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX model_evaluation_model ON privacy.model_evaluation (model_id, created_at DESC);
        CREATE TABLE privacy.model_observation (
            observation_id  uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            model_ref       text NOT NULL,
            pinned_version  text NOT NULL,
            seen_version    text NOT NULL,
            verdict         text NOT NULL CHECK (verdict IN ('OK','DRIFT')),
            observed_by     uuid NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE privacy.model_version ENABLE ROW LEVEL SECURITY;
        ALTER TABLE privacy.model_version FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON privacy.model_version USING {RULE} WITH CHECK {RULE};
        ALTER TABLE privacy.model_evaluation ENABLE ROW LEVEL SECURITY;
        ALTER TABLE privacy.model_evaluation FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON privacy.model_evaluation USING {RULE} WITH CHECK {RULE};
        ALTER TABLE privacy.model_observation ENABLE ROW LEVEL SECURITY;
        ALTER TABLE privacy.model_observation FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON privacy.model_observation USING {RULE} WITH CHECK {RULE};
        CREATE FUNCTION privacy.model_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'evaluations and observations are append-only' USING ERRCODE = '23514';
        END $$;
        CREATE TRIGGER model_evaluation_append_only BEFORE UPDATE OR DELETE ON privacy.model_evaluation
            FOR EACH ROW EXECUTE FUNCTION privacy.model_append_only();
        CREATE TRIGGER model_observation_append_only BEFORE UPDATE OR DELETE ON privacy.model_observation
            FOR EACH ROW EXECUTE FUNCTION privacy.model_append_only();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE privacy.model_observation;
        DROP TABLE privacy.model_evaluation;
        DROP TABLE privacy.model_version;
        DROP FUNCTION privacy.model_append_only();
    """)

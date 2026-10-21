"""Privacy notices, processor/subprocessor registry, automated-decision profiles (ZID-PRIV-001 §20,
§22, §23; CR-013).

Revision ID: privacy_0002
Revises: privacy_0001
Create Date: 2026-10-21

- notice: immutable published notice versions (content hash); consent cites `CODE@version`
  (PRIV-26: consent evidence resolves the exact notice shown).
- processor: processors, subprocessors and recipients with the activities, data categories,
  purposes, locations, transfer mechanism and deletion SLA they are approved for. A data-map entry
  held by a processor must name an APPROVED one (§23: none added without metadata).
- automated_decision_profile: decision classes with significance, solely-automated flag,
  explanation and human-review route; HUMAN_REVIEW / ADMT requests route through them (§20).
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0002"
down_revision = "privacy_0001"
branch_labels = None
depends_on = None

TABLES = ("notice", "processor", "automated_decision_profile")
CODE = r"'^[A-Z][A-Z0-9_]{1,63}$'"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE privacy.notice (
            notice_id                 uuid PRIMARY KEY,
            tenant_id                 uuid NOT NULL,
            notice_code               text NOT NULL CHECK (notice_code ~ {CODE}),
            version                   integer NOT NULL CHECK (version >= 1),
            audience                  text NOT NULL,
            jurisdictions             text[] NOT NULL DEFAULT '{{}}',
            processing_activity_codes text[] NOT NULL DEFAULT '{{}}',
            content_hash              text NOT NULL,
            content_uri               text,
            status                    text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RETIRED')),
            created_by                uuid NOT NULL,
            approved_by               uuid NOT NULL,
            effective_at              timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, notice_code, version),
            CHECK (approved_by <> created_by)
        );

        CREATE TABLE privacy.processor (
            tenant_id                 uuid NOT NULL,
            processor_id              text NOT NULL CHECK (processor_id ~ '^[a-z0-9][a-z0-9_.-]{{0,63}}$'),
            name                      text NOT NULL,
            role                      text NOT NULL CHECK (role IN ('PROCESSOR', 'SUBPROCESSOR',
                                      'INDEPENDENT_CONTROLLER', 'RECIPIENT')),
            processing_activity_codes text[] NOT NULL DEFAULT '{{}}',
            data_categories           text[] NOT NULL,
            purposes                  text[] NOT NULL DEFAULT '{{}}',
            locations                 text[] NOT NULL,
            transfer_mechanism        text,
            deletion_sla_days         integer CHECK (deletion_sla_days >= 0),
            security_assurance        text,
            contract_ref              text,
            status                    text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING',
                                      'APPROVED', 'SUSPENDED', 'RETIRED')),
            created_by                uuid NOT NULL,
            approved_by               uuid,
            revision                  integer NOT NULL DEFAULT 1,
            created_at                timestamptz NOT NULL DEFAULT now(),
            updated_at                timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, processor_id),
            CHECK (data_categories <@ ARRAY['D0','D1','D2','D3','D4']::text[]),
            CHECK (status = 'PENDING' OR approved_by IS NOT NULL),
            CHECK (approved_by IS NULL OR approved_by <> created_by),
            CHECK (role NOT IN ('PROCESSOR', 'SUBPROCESSOR') OR status = 'PENDING' OR contract_ref IS NOT NULL)
        );

        CREATE TABLE privacy.automated_decision_profile (
            profile_id                uuid PRIMARY KEY,
            tenant_id                 uuid NOT NULL,
            decision_class            text NOT NULL,
            version                   integer NOT NULL CHECK (version >= 1),
            purpose_code              text NOT NULL,
            input_data_categories     text[] NOT NULL,
            model_or_rules_ref        text NOT NULL,
            solely_automated          boolean NOT NULL,
            significance_class        text NOT NULL CHECK (significance_class IN ('NONE', 'MATERIAL',
                                      'LEGAL_OR_SIMILARLY_SIGNIFICANT')),
            special_category_use      boolean NOT NULL DEFAULT false,
            explanation_profile       text NOT NULL,
            human_review_route        text NOT NULL,
            jurisdiction_rules        jsonb NOT NULL DEFAULT '{{}}',
            risk_assessment_ref       text,
            status                    text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RETIRED')),
            created_by                uuid NOT NULL,
            created_at                timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, decision_class, version),
            CHECK (significance_class = 'NONE' OR risk_assessment_ref IS NOT NULL)
        );
        CREATE UNIQUE INDEX adm_profile_one_active ON privacy.automated_decision_profile
            (tenant_id, decision_class) WHERE status = 'ACTIVE';

        CREATE FUNCTION privacy.notice_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'status') <> (to_jsonb(OLD) - 'status') OR OLD.status = 'RETIRED' THEN
                RAISE EXCEPTION 'a published notice version is immutable (publish a new version)'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER notice_immutable BEFORE UPDATE OR DELETE ON privacy.notice
            FOR EACH ROW EXECUTE FUNCTION privacy.notice_immutable();
    """)
    for table in TABLES:
        _run(f"""
            ALTER TABLE privacy.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE privacy.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON privacy.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE privacy.automated_decision_profile;
        DROP TABLE privacy.processor;
        DROP TABLE privacy.notice;
        DROP FUNCTION privacy.notice_immutable();
    """)

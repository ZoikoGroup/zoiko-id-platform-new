"""Verification workflows: versions with a maker-checker gate, and runs (ZID-GAP-001 v2.0 DTV-30; CR-099).

Revision ID: verification_0016
Revises: verification_0015
Create Date: 2026-11-16

workflow_version: one definition of one workflow at one version, as data, with its content hash. The table repeats the rules: an APPROVED version never changes
(its definition, hash and author are frozen; the only later move is APPROVED to RETIRED), the person who approves is never the author, and APPROVED needs an
approver. Row-level security on both tables.

workflow_run: one applicant's run, pinned to a version by its content hash. It holds the machine's state (the step, the check results as outcome words, what it
awaits, the outcome, the events). The applicant is a reference; the results are words from a fixed list, never a value. Governed erasure empties the results and
events and keeps the outcome.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0016"
down_revision = "verification_0015"
branch_labels = None
depends_on = None

TENANT_TABLES = ("workflow_version", "workflow_run")


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.workflow_version (
            tenant_id     uuid NOT NULL,
            workflow_id   text NOT NULL CHECK (workflow_id ~ '^[a-z][a-z0-9_]{0,39}$'),
            version       text NOT NULL CHECK (length(version) BETWEEN 1 AND 40),
            definition    jsonb NOT NULL,
            content_hash  text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            status        text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'APPROVED', 'RETIRED')),
            author        uuid NOT NULL,
            approved_by   uuid,
            approved_at   timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, workflow_id, version),
            CHECK ((status = 'DRAFT') = (approved_by IS NULL)),
            CHECK (approved_by IS NULL OR approved_by <> author)
        );

        CREATE FUNCTION verify.workflow_version_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'DRAFT' AND (
                NEW.definition IS DISTINCT FROM OLD.definition OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
                OR NEW.author IS DISTINCT FROM OLD.author OR NEW.workflow_id IS DISTINCT FROM OLD.workflow_id
                OR NEW.version IS DISTINCT FROM OLD.version OR NEW.approved_by IS DISTINCT FROM OLD.approved_by
            ) THEN
                RAISE EXCEPTION 'an approved workflow version never changes; make a new version' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'RETIRED' AND NEW.status <> 'RETIRED' THEN
                RAISE EXCEPTION 'a retired workflow version stays retired' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'APPROVED' AND NEW.status = 'DRAFT' THEN
                RAISE EXCEPTION 'an approved workflow version cannot go back to draft' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER workflow_version_frozen BEFORE UPDATE ON verify.workflow_version
            FOR EACH ROW EXECUTE FUNCTION verify.workflow_version_frozen();
        CREATE FUNCTION verify.workflow_version_no_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'DRAFT' THEN
                RAISE EXCEPTION 'an approved workflow version is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            RETURN OLD;
        END $$;
        CREATE TRIGGER workflow_version_no_delete BEFORE DELETE ON verify.workflow_version
            FOR EACH ROW EXECUTE FUNCTION verify.workflow_version_no_delete();

        CREATE TABLE verify.workflow_run (
            run_id                uuid PRIMARY KEY,
            tenant_id             uuid NOT NULL,
            workflow_id           text NOT NULL,
            version               text NOT NULL,
            content_hash          text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            subject_principal_id  uuid NOT NULL,
            started_by            uuid NOT NULL,
            status                text NOT NULL CHECK (status IN ('WAITING_FOR_CHECK', 'WAITING_FOR_REVIEW', 'ENDED')),
            step                  text NOT NULL,
            results               jsonb NOT NULL DEFAULT '{}'::jsonb,
            awaiting_check        text,
            awaiting_role         text,
            outcome               text CHECK (outcome IN ('APPROVED', 'REFER_TO_PERSON', 'COMPLETED_NO_DECISION')),
            events                jsonb NOT NULL DEFAULT '[]'::jsonb,
            notifications         jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at            timestamptz NOT NULL DEFAULT now(),
            updated_at            timestamptz NOT NULL DEFAULT now(),
            erased_at             timestamptz,
            row_version           integer NOT NULL DEFAULT 1,
            FOREIGN KEY (tenant_id, workflow_id, version) REFERENCES verify.workflow_version (tenant_id, workflow_id, version),
            CHECK ((status = 'ENDED') = (outcome IS NOT NULL))
        );
        CREATE INDEX workflow_run_subject ON verify.workflow_run (tenant_id, subject_principal_id, created_at DESC);
        CREATE INDEX workflow_run_open ON verify.workflow_run (tenant_id, status, created_at);
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE verify.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE verify.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON verify.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.workflow_run;
        DROP TABLE verify.workflow_version;
        DROP FUNCTION verify.workflow_version_frozen();
        DROP FUNCTION verify.workflow_version_no_delete();
    """)

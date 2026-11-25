"""Versioned application scopes, the partner request log and certification runs (ZID-CP-WF10 v2.0, WF-10.03, WF-10.10, WF-10.12; CR-173).

Revision ID: verify_api_0017
Revises: verify_api_0016
Create Date: 2026-11-25

api_scope_change      every change to what an application may do, as a new version with the full resulting scope set. A removal takes effect at once or at a stated later time; an
                      addition to a PRODUCTION application is not effective until a person other than the proposer approves it (the database checks the pair too).
api_scope_approval    one row per approver of a scope change.
api_request_log       one row per call a partner server made to the Verify API with a bearer token: route template (never the path with ids), status, error code, trace id, time taken.
                      No body, no header, no query string, no person data.
api_certification_run a scored synthetic test run (the fixture report) kept with its digest. It is a record of a test, never a go-live approval.
All four are append-only and tenant-isolated.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0017"
down_revision = "verify_api_0016"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
TABLES = ("api_scope_change", "api_scope_approval", "api_request_log", "api_certification_run")


def upgrade() -> None:
    _run(f"""
        CREATE FUNCTION verify.api_dev_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'verify.% is append-only (% refused)', TG_TABLE_NAME, TG_OP USING ERRCODE = 'restrict_violation';
        END $$;

        CREATE TABLE verify.api_scope_change (
            change_id         uuid PRIMARY KEY,
            tenant_id         uuid NOT NULL,
            application_id    uuid NOT NULL REFERENCES verify.api_application (application_id),
            version           integer NOT NULL CHECK (version >= 1),
            kind              text NOT NULL CHECK (kind IN ('ADD', 'REMOVE', 'REPLACE')),
            resulting_scopes  text[] NOT NULL CHECK (cardinality(resulting_scopes) >= 1),
            requires_approval boolean NOT NULL,
            proposed_by       uuid NOT NULL,
            effective_at      timestamptz NOT NULL,
            reason            text NOT NULL CHECK (char_length(reason) BETWEEN 3 AND 300),
            created_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (application_id, version)
        );
        CREATE INDEX api_scope_change_effective ON verify.api_scope_change (application_id, version DESC);
        CREATE TABLE verify.api_scope_approval (
            change_id  uuid NOT NULL REFERENCES verify.api_scope_change (change_id),
            tenant_id  uuid NOT NULL,
            approver   uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (change_id, approver)
        );

        CREATE TABLE verify.api_request_log (
            log_id         uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            application_id uuid NOT NULL,
            environment    text NOT NULL,
            method         text NOT NULL CHECK (char_length(method) <= 10),
            route          text NOT NULL CHECK (char_length(route) <= 120),
            status         integer NOT NULL CHECK (status BETWEEN 100 AND 599),
            error_code     text CHECK (error_code IS NULL OR char_length(error_code) <= 60),
            trace_id       text NOT NULL CHECK (char_length(trace_id) <= 80),
            session_id     text CHECK (session_id IS NULL OR char_length(session_id) <= 64),
            duration_ms    integer NOT NULL CHECK (duration_ms >= 0),
            occurred_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX api_request_log_app ON verify.api_request_log (application_id, occurred_at DESC);
        CREATE INDEX api_request_log_session ON verify.api_request_log (session_id) WHERE session_id IS NOT NULL;
        CREATE INDEX api_request_log_trace ON verify.api_request_log (trace_id);

        CREATE TABLE verify.api_certification_run (
            run_id         uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            application_id uuid NOT NULL REFERENCES verify.api_application (application_id),
            report         jsonb NOT NULL,
            report_digest  text NOT NULL CHECK (report_digest ~ '^sha-256:[0-9a-f]{{64}}$'),
            passed         boolean NOT NULL,
            personas_passed integer NOT NULL CHECK (personas_passed >= 0),
            personas_total integer NOT NULL CHECK (personas_total >= 1),
            run_by         uuid NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            CHECK (NOT passed OR personas_passed = personas_total)
        );
        CREATE INDEX api_certification_run_app ON verify.api_certification_run (application_id, created_at DESC);
    """)
    for t in TABLES:
        _run(f"""
            ALTER TABLE verify.{t} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE verify.{t} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON verify.{t} USING ({TENANT}) WITH CHECK ({TENANT});
            CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON verify.{t} FOR EACH ROW EXECUTE FUNCTION verify.api_dev_append_only();
        """)
    # a scope change must also be readable while a bearer token is being checked (before any tenant is set), exactly as credentials and tokens are
    _run("""
        CREATE POLICY verify_api_auth ON verify.api_scope_change USING (current_setting('app.verify_api_scope', true) = 'auth');
        CREATE POLICY verify_api_auth ON verify.api_scope_approval USING (current_setting('app.verify_api_scope', true) = 'auth');
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.api_certification_run;
        DROP TABLE verify.api_request_log;
        DROP TABLE verify.api_scope_approval;
        DROP TABLE verify.api_scope_change;
        DROP FUNCTION verify.api_dev_append_only();
    """)

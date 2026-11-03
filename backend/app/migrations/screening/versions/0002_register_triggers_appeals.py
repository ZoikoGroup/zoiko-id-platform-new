"""Register evidence, rescreen triggers and appeals for screening (ZID-ED-04 sections 04, 06, 07; CR-044).

Revision ID: screening_0002
Revises: screening_0001
Create Date: 2026-11-02

screening_register_snapshot, screening_register_person: what the official register said about a company's directors and people with
significant control when a case was screened, as numbered snapshots. A person on the register has a name (and sometimes a birth month
and year), so these are restricted records in the tenant's home cell, like the parties a customer declares; they never appear in
events, logs or audit records. Append-only: a changed register is a new snapshot, never an edit.
screening_trigger: a reason to screen a case again that is not a list change or a lapse (the register changed, a privileged role was
granted, a high-risk action was taken). `handled_version` is the evidence version that answered it.
screening_appeal: a customer's appeal or correction request with the statement they wrote (restricted; only its hash goes to audit).
Append-only.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0002"
down_revision = "screening_0001"
branch_labels = None
depends_on = None

TABLES = (
    "screening_register_snapshot",
    "screening_register_person",
    "screening_trigger",
    "screening_appeal",
)
_RLS = """
    ALTER TABLE verify.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE verify.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON verify.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.screening_register_snapshot (
            tenant_id     uuid NOT NULL,
            case_id       uuid NOT NULL REFERENCES verify.screening_case (case_id),
            seq           integer NOT NULL CHECK (seq >= 1),
            status        text NOT NULL CHECK (status IN ('OK', 'UNAVAILABLE', 'NOT_APPLICABLE')),
            content_hash  text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            source        text NOT NULL,
            observed_at   timestamptz NOT NULL,
            PRIMARY KEY (case_id, seq)
        );
        CREATE TABLE verify.screening_register_person (
            tenant_id   uuid NOT NULL,
            case_id     uuid NOT NULL,
            seq         integer NOT NULL,
            ref         text NOT NULL,
            kind        text NOT NULL CHECK (kind IN ('PSC', 'OFFICER')),
            party_kind  text NOT NULL,
            name        text NOT NULL,
            birth       text,
            natures     jsonb NOT NULL DEFAULT '[]'::jsonb,
            ceased      boolean NOT NULL,
            PRIMARY KEY (case_id, seq, ref),
            FOREIGN KEY (case_id, seq) REFERENCES verify.screening_register_snapshot (case_id, seq)
        );
        CREATE TABLE verify.screening_trigger (
            trigger_id       uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            case_id          uuid NOT NULL REFERENCES verify.screening_case (case_id),
            reason           text NOT NULL,
            requested_at     timestamptz NOT NULL,
            handled_version  integer
        );
        CREATE INDEX screening_trigger_open ON verify.screening_trigger (tenant_id, case_id) WHERE handled_version IS NULL;
        CREATE TABLE verify.screening_appeal (
            appeal_id         uuid PRIMARY KEY,
            tenant_id         uuid NOT NULL,
            case_id           uuid NOT NULL REFERENCES verify.screening_case (case_id),
            kind              text NOT NULL CHECK (kind IN ('APPEAL', 'CORRECTION')),
            statement         text NOT NULL,
            statement_hash    text NOT NULL CHECK (statement_hash ~ '^[0-9a-f]{64}$'),
            requested_by      uuid NOT NULL,
            created_at        timestamptz NOT NULL,
            evidence_version  integer NOT NULL
        );
        CREATE FUNCTION verify.screening_register_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'register evidence and appeals are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER screening_register_snapshot_append_only BEFORE UPDATE OR DELETE ON verify.screening_register_snapshot
            FOR EACH ROW EXECUTE FUNCTION verify.screening_register_guard();
        CREATE TRIGGER screening_register_person_append_only BEFORE UPDATE OR DELETE ON verify.screening_register_person
            FOR EACH ROW EXECUTE FUNCTION verify.screening_register_guard();
        CREATE TRIGGER screening_appeal_append_only BEFORE UPDATE OR DELETE ON verify.screening_appeal
            FOR EACH ROW EXECUTE FUNCTION verify.screening_register_guard();
    """)
    for table in TABLES:
        _run(_RLS.format(t=table))


def downgrade() -> None:
    _run("""
        DROP TABLE verify.screening_appeal;
        DROP TABLE verify.screening_trigger;
        DROP TABLE verify.screening_register_person;
        DROP TABLE verify.screening_register_snapshot;
        DROP FUNCTION verify.screening_register_guard();
    """)

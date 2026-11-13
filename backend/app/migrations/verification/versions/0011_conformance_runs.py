"""Independent conformance runs and their reviews (ZID-RCG-003 sections 12 and 12.1; CR-083).

Revision ID: verification_0011
Revises: verification_0010
Create Date: 2026-11-12

conformance_run: one row per run of the 48 acceptance cases: the commit, the environment, the matrix hash, when it ran, who recorded it, the outcome
of every case and the hash over all of it. conformance_review: an independent reviewer's ACCEPTED or REJECTED over that exact hash. Both append-only,
platform records (no tenant_id, no row-level security). The tables repeat the rules: no update or delete; a review signs the stored run hash and is
never by whoever recorded the run; one review per reviewer per run.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0011"
down_revision = "verification_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.conformance_run (
            run_id       text PRIMARY KEY CHECK (run_id ~ '^[a-z0-9][a-z0-9-]{0,127}$'),
            commit_sha   text NOT NULL CHECK (commit_sha ~ '^[0-9a-f]{40}$'),
            environment  text NOT NULL CHECK (length(btrim(environment)) BETWEEN 1 AND 64),
            matrix_hash  text NOT NULL CHECK (matrix_hash ~ '^[0-9a-f]{64}$'),
            started_at   timestamptz NOT NULL,
            finished_at  timestamptz NOT NULL,
            recorded_by  text NOT NULL CHECK (length(btrim(recorded_by)) BETWEEN 1 AND 128),
            results      jsonb NOT NULL,
            run_hash     text NOT NULL CHECK (run_hash ~ '^[0-9a-f]{64}$'),
            recorded_at  timestamptz NOT NULL DEFAULT now(),
            CHECK (finished_at >= started_at),
            CHECK (jsonb_array_length(results) = 48)
        );
        CREATE TABLE verify.conformance_review (
            run_id       text NOT NULL REFERENCES verify.conformance_run (run_id),
            reviewer     text NOT NULL CHECK (length(btrim(reviewer)) BETWEEN 1 AND 128),
            signed_hash  text NOT NULL CHECK (signed_hash ~ '^[0-9a-f]{64}$'),
            decision     text NOT NULL CHECK (decision IN ('ACCEPTED', 'REJECTED')),
            note_ref     text NOT NULL CHECK (length(btrim(note_ref)) BETWEEN 1 AND 256),
            reviewed_at  timestamptz NOT NULL,
            PRIMARY KEY (run_id, reviewer)
        );

        CREATE FUNCTION verify.conformance_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'conformance runs and reviews are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER conformance_run_append_only BEFORE UPDATE OR DELETE ON verify.conformance_run
            FOR EACH ROW EXECUTE FUNCTION verify.conformance_append_only();
        CREATE TRIGGER conformance_review_append_only BEFORE UPDATE OR DELETE ON verify.conformance_review
            FOR EACH ROW EXECUTE FUNCTION verify.conformance_append_only();

        CREATE FUNCTION verify.conformance_review_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            r verify.conformance_run%ROWTYPE;
        BEGIN
            SELECT * INTO r FROM verify.conformance_run WHERE run_id = NEW.run_id;
            IF NEW.reviewer = r.recorded_by THEN
                RAISE EXCEPTION 'whoever recorded a conformance run cannot review it' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.signed_hash <> r.run_hash THEN
                RAISE EXCEPTION 'a review signs the stored hash of the run' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER conformance_review_guard BEFORE INSERT ON verify.conformance_review
            FOR EACH ROW EXECUTE FUNCTION verify.conformance_review_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.conformance_review;
        DROP TABLE verify.conformance_run;
        DROP FUNCTION verify.conformance_review_guard();
        DROP FUNCTION verify.conformance_append_only();
    """)

"""Issuer and accreditation history with provenance (ZID-RCG-003 RCG-G04; CR-075).

Revision ID: credential_0013
Revises: credential_0012
Create Date: 2026-11-12

issuer_statement: one row per dated statement about one issuer (a full issuer record, the official source it came from, the hash of what the source
returned and where that is kept). Append-only, so a past check can be replayed against what was known then. Platform configuration, not tenant data:
no tenant_id, no row-level security. The table repeats the domain rules: no update, no delete, never from the future, sequence numbers that rise by
one per issuer, and each statement newer than the one before it.
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0013"
down_revision = "credential_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE cred.issuer_statement (
            issuer_id        text NOT NULL CHECK (issuer_id ~ '^[a-z0-9][a-z0-9-]{0,127}$'),
            seq              integer NOT NULL CHECK (seq >= 1),
            statement_id     text NOT NULL UNIQUE CHECK (length(statement_id) BETWEEN 1 AND 128),
            kind             text NOT NULL CHECK (kind IN ('UPDATE', 'CORRECTION')),
            reason           text CHECK (reason IS NULL OR length(btrim(reason)) >= 10),
            record           jsonb NOT NULL,
            source_url       text NOT NULL CHECK (source_url ~ '^https://[^[:space:]/]+'),
            source_hash      text NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
            evidence_locator text NOT NULL CHECK (evidence_locator ~ '^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$'),
            recorded_by      text NOT NULL CHECK (length(btrim(recorded_by)) BETWEEN 1 AND 128),
            recorded_at      timestamptz NOT NULL,
            PRIMARY KEY (issuer_id, seq),
            CHECK (kind <> 'CORRECTION' OR reason IS NOT NULL),
            CHECK (record ->> 'issuer_id' = issuer_id)
        );

        CREATE FUNCTION cred.issuer_statement_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            last_seq integer;
            last_at  timestamptz;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'issuer statements are append-only' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.recorded_at > now() + interval '5 minutes' THEN
                RAISE EXCEPTION 'an issuer statement cannot be from the future' USING ERRCODE = 'check_violation';
            END IF;
            SELECT max(seq) INTO last_seq FROM cred.issuer_statement WHERE issuer_id = NEW.issuer_id;
            IF NEW.seq <> coalesce(last_seq, 0) + 1 THEN
                RAISE EXCEPTION 'issuer statements rise by one per issuer' USING ERRCODE = 'check_violation';
            END IF;
            IF last_seq IS NOT NULL THEN
                SELECT recorded_at INTO last_at FROM cred.issuer_statement WHERE issuer_id = NEW.issuer_id AND seq = last_seq;
                IF NEW.recorded_at <= last_at THEN
                    RAISE EXCEPTION 'an issuer statement must be newer than the one before it' USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER issuer_statement_guard BEFORE INSERT OR UPDATE OR DELETE ON cred.issuer_statement
            FOR EACH ROW EXECUTE FUNCTION cred.issuer_statement_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE cred.issuer_statement;
        DROP FUNCTION cred.issuer_statement_guard();
    """)

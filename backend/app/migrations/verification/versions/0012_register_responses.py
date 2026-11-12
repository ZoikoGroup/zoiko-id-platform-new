"""Preserved official-register responses (ZID-RCG-003 RCG-G02, section 05; CR-087).

Revision ID: verification_0012
Revises: verification_0011
Create Date: 2026-11-12

register_response: the exact bytes an official register, trusted list or professional register returned to a connector, keyed by their sha256.
An attestation recorded from a connector carries that hash as its proof and points here, so the proof can be shown later. Append-only platform
records (no tenant_id, no row-level security): public register entries about businesses, not personal data. The table repeats the rules: the key is
the hash of the body, and nothing is updated or deleted.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0012"
down_revision = "verification_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.register_response (
            proof_hash    text PRIMARY KEY CHECK (proof_hash ~ '^[0-9a-f]{64}$'),
            source_id     text NOT NULL CHECK (source_id ~ '^[a-z0-9][a-z0-9-]{0,63}$'),
            register_url  text NOT NULL CHECK (register_url ~ '^https://'),
            body          bytea NOT NULL CHECK (length(body) BETWEEN 1 AND 1048576),
            fetched_at    timestamptz NOT NULL,
            recorded_at   timestamptz NOT NULL DEFAULT now(),
            CHECK (proof_hash = encode(sha256(body), 'hex'))
        );

        CREATE FUNCTION verify.register_response_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'preserved register responses are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER register_response_append_only BEFORE UPDATE OR DELETE ON verify.register_response
            FOR EACH ROW EXECUTE FUNCTION verify.register_response_append_only();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.register_response;
        DROP FUNCTION verify.register_response_append_only();
    """)

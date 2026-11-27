"""Photographs of a partner's customer, kept only when the deployment chose to, and the log of every time one was opened.

Revision ID: verify_api_0020
Revises: verify_api_0019
Create Date: 2026-11-26

A verification started through the Verify API compares a document photograph and a selfie and, by default, keeps neither. When the deployment sets ZID_VERIFY_KEEP_PHOTOS_DAYS, each photograph of an attempt is kept
ENCRYPTED in the evidence vault (under the session's own pseudonymous key, so erasing it erases the photographs), with the date retention ends, and the vault's retention sweep erases the bytes at that date. This
table holds only the pointer: which session, which attempt, which kind, the type and size, and when it expires. It never holds an image.

`api_media_access` is the record of every time a person at the company opened a photograph: who, which one, the reason they typed, and when. It is append-only: a row can be added and never changed or removed.

Both tables are tenant-scoped under row-level security, like the sessions they belong to.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0020"
down_revision = "verify_api_0019"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
TABLES = ("api_session_media", "api_media_access")


def upgrade() -> None:
    policies = "\n".join(
        f"""
        ALTER TABLE verify.{t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.{t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.{t} USING ({TENANT}) WITH CHECK ({TENANT});
        """
        for t in TABLES
    )
    _run(f"""
        CREATE TABLE verify.api_session_media (
            media_id         text PRIMARY KEY CHECK (media_id ~ '^med_[0-9a-f]{{32}}$'),
            session_id       text NOT NULL REFERENCES verify.api_session (session_id),
            tenant_id        uuid NOT NULL,
            kind             text NOT NULL CHECK (kind IN ('DOCUMENT', 'SELFIE')),
            attempt          integer NOT NULL CHECK (attempt >= 1),
            vault_object_id  text NOT NULL,
            content_type     text NOT NULL CHECK (content_type IN ('image/jpeg', 'image/png')),
            byte_length      integer NOT NULL CHECK (byte_length > 0),
            captured_at      timestamptz NOT NULL,
            expires_at       timestamptz NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX api_session_media_by_session ON verify.api_session_media (session_id, attempt, kind);

        CREATE TABLE verify.api_media_access (
            access_id    text PRIMARY KEY CHECK (access_id ~ '^acc_[0-9a-f]{{32}}$'),
            media_id     text NOT NULL REFERENCES verify.api_session_media (media_id),
            session_id   text NOT NULL,
            tenant_id    uuid NOT NULL,
            principal_id uuid NOT NULL,
            reason       text NOT NULL CHECK (char_length(reason) BETWEEN 5 AND 300),
            opened_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX api_media_access_by_session ON verify.api_media_access (session_id, opened_at DESC);
        {policies}

        CREATE FUNCTION verify.api_media_access_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'verify.api_media_access is append-only (% refused)', TG_OP USING ERRCODE = 'restrict_violation';
        END $$;
        CREATE TRIGGER api_media_access_append_only BEFORE UPDATE OR DELETE ON verify.api_media_access FOR EACH ROW EXECUTE FUNCTION verify.api_media_access_append_only();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.api_media_access;
        DROP FUNCTION verify.api_media_access_append_only();
        DROP TABLE verify.api_session_media;
    """)

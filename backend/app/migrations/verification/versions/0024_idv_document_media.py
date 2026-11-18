"""The document photograph a reviewer looks at, and who looked and why (ZID-DTV-WF-03 browser-only route; CR-124).

Revision ID: verification_0024
Revises: verification_0023
Create Date: 2026-11-18

idv_case_media: the photograph of a case on a reviewed (non-chip) route, kept ENCRYPTED in the evidence vault under the applicant's own erasable key; this table holds only the vault reference, the digest, the image checks
that were measured, and the date retention ends. A newer photograph supersedes an older one; nothing here is the image.
idv_media_grant: a reviewer's time-limited permission to read one photograph, with the reason they gave. A grant lasts minutes, reads at most three times, and belongs to one reviewer.
Row-level security by company on both.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0024"
down_revision = "verification_0023"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE verify.idv_case_media (
            media_id        uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            case_id         uuid NOT NULL REFERENCES verify.idv_case (case_id),
            vault_object_id uuid NOT NULL,
            sha256          text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
            content_type    text NOT NULL CHECK (content_type IN ('image/jpeg', 'image/png')),
            byte_length     integer NOT NULL CHECK (byte_length > 0),
            checks          jsonb NOT NULL,
            captured_at     timestamptz NOT NULL,
            expires_at      timestamptz NOT NULL,
            superseded_at   timestamptz,
            created_at      timestamptz NOT NULL
        );
        CREATE INDEX idv_case_media_case ON verify.idv_case_media (case_id, created_at DESC);
        CREATE TABLE verify.idv_media_grant (
            grant_id    uuid PRIMARY KEY,
            tenant_id   uuid NOT NULL,
            media_id    uuid NOT NULL REFERENCES verify.idv_case_media (media_id),
            reviewer    uuid NOT NULL,
            reason      text NOT NULL CHECK (length(btrim(reason)) BETWEEN 10 AND 500),
            created_at  timestamptz NOT NULL,
            expires_at  timestamptz NOT NULL,
            reads       integer NOT NULL DEFAULT 0 CHECK (reads BETWEEN 0 AND 3),
            CHECK (expires_at > created_at)
        );
        CREATE INDEX idv_media_grant_media ON verify.idv_media_grant (media_id, created_at DESC);
        ALTER TABLE verify.idv_case_media ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.idv_case_media FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.idv_case_media USING {RULE} WITH CHECK {RULE};
        ALTER TABLE verify.idv_media_grant ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.idv_media_grant FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.idv_media_grant USING {RULE} WITH CHECK {RULE};
    """)


def downgrade() -> None:
    _run("DROP TABLE verify.idv_media_grant, verify.idv_case_media;")

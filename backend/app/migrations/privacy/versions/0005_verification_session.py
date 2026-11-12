"""The privacy-safe verification session (ZID-PRI-004 section 07; AT-001 to AT-003, AT-012; CR-072).

Revision ID: privacy_0005
Revises: privacy_0004
Create Date: 2026-11-12

A session records WHY capture may or may not happen: the purpose decision taken when it opened, the alternative the person chose and whether it was
cancelled. Whether capture is enabled NOW is never stored: it is recomputed on every read from the notice acknowledgments and the live consent and
restriction records, so a consent withdrawn mid-session stops capture at once. No image, template, document or score is held here.
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0005"
down_revision = "privacy_0004"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE privacy.verification_session (
            session_ref          text PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            subject_principal_id uuid,
            activity_code        text NOT NULL,
            purpose_code         text NOT NULL,
            data_category        text NOT NULL CHECK (data_category IN ('D0','D1','D2','D3','D4')),
            country              text,
            subregion            text,
            assurance_goal       text,
            alternative_mode     text,
            notice_version       text,
            provider_service_ref text,
            decision_id          uuid NOT NULL,
            allowed_at_open      boolean NOT NULL,
            reason_codes         text[] NOT NULL,
            state                text NOT NULL CHECK (state IN ('OPEN','CANCELED')),
            canceled_at          timestamptz,
            created_by           uuid,
            created_at           timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE privacy.verification_session ENABLE ROW LEVEL SECURITY;
        ALTER TABLE privacy.verification_session FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON privacy.verification_session USING {RULE} WITH CHECK {RULE};
    """)


def downgrade() -> None:
    _run("DROP TABLE privacy.verification_session;")

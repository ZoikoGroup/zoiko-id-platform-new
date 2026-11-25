"""The hosted applicant session (ZID-CP-WF01 sections 05, 08, S12).

Revision ID: privacy_0013
Revises: privacy_0012
Create Date: 2026-11-24

verification_session gains what the person's own journey needs: the market it verifies in, the pseudonymous applicant the proofing case is opened for, the case
itself once a method is chosen, the environment it runs in, the business's return address and state, when it expires, when the notice was first presented,
when and in which language the person acknowledged it, whether they agreed to the face comparison, and when they submitted. Nothing here is an image, a
document or a score.

applicant_token: the short-lived credentials the person's browser or phone holds instead of a sign-in. Only a SHA-256 of each token is stored. A LAUNCH token
is one-use (the business's link, a resume link or a cross-device handoff) and is exchanged for an ACCESS token bound to the session.
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0013"
down_revision = "privacy_0012"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        ALTER TABLE privacy.verification_session
            ADD COLUMN market        text,
            ADD COLUMN applicant_id  uuid,
            ADD COLUMN case_id       uuid,
            ADD COLUMN environment   text,
            ADD COLUMN return_url    text,
            ADD COLUMN return_state  text,
            ADD COLUMN expires_at    timestamptz,
            ADD COLUMN presented_at  timestamptz,
            ADD COLUMN acknowledged_at timestamptz,
            ADD COLUMN notice_locale text,
            ADD COLUMN biometric_consent_at timestamptz,
            ADD COLUMN submitted_at  timestamptz,
            ADD COLUMN event_seq     integer NOT NULL DEFAULT 0;
        CREATE TABLE privacy.applicant_token (
            token_hash   text PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            session_ref  text NOT NULL REFERENCES privacy.verification_session (session_ref),
            kind         text NOT NULL CHECK (kind IN ('LAUNCH','ACCESS')),
            expires_at   timestamptz NOT NULL,
            used_at      timestamptz,
            revoked_at   timestamptz,
            created_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX applicant_token_session ON privacy.applicant_token (session_ref);
        ALTER TABLE privacy.applicant_token ENABLE ROW LEVEL SECURITY;
        ALTER TABLE privacy.applicant_token FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON privacy.applicant_token USING {RULE} WITH CHECK {RULE};
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE privacy.applicant_token;
        ALTER TABLE privacy.verification_session
            DROP COLUMN market, DROP COLUMN applicant_id, DROP COLUMN case_id, DROP COLUMN environment, DROP COLUMN return_url,
            DROP COLUMN return_state, DROP COLUMN expires_at, DROP COLUMN presented_at, DROP COLUMN acknowledged_at, DROP COLUMN notice_locale,
            DROP COLUMN biometric_consent_at, DROP COLUMN submitted_at, DROP COLUMN event_seq;
    """)

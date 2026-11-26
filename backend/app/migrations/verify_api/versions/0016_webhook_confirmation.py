"""A partner's confirmation that it received and checked a signed webhook (ZID-CP-WF10 v2.0, WF-10.01; CR-173).

Revision ID: verify_api_0016
Revises: verify_api_0015
Create Date: 2026-11-25

One row each time a partner's server names the event id of a delivery the platform really made and says it verified the signature. It is the only evidence the first-verification
checklist accepts for its last step. Tenant data, append-only: a confirmation is a fact about a moment and is never edited.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0016"
down_revision = "verify_api_0015"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE verify.api_webhook_confirmation (
            confirmation_id uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            application_id  uuid NOT NULL REFERENCES verify.api_application (application_id),
            event_id        uuid NOT NULL,
            confirmed_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (application_id, event_id)
        );
        CREATE FUNCTION verify.api_confirmation_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'verify.api_webhook_confirmation is append-only (% refused)', TG_OP USING ERRCODE = 'restrict_violation';
        END $$;
        CREATE TRIGGER api_webhook_confirmation_append_only BEFORE UPDATE OR DELETE ON verify.api_webhook_confirmation
            FOR EACH ROW EXECUTE FUNCTION verify.api_confirmation_append_only();
        ALTER TABLE verify.api_webhook_confirmation ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.api_webhook_confirmation FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.api_webhook_confirmation USING ({TENANT}) WITH CHECK ({TENANT});
    """)


def downgrade() -> None:
    _run("DROP TABLE verify.api_webhook_confirmation; DROP FUNCTION verify.api_confirmation_append_only();")

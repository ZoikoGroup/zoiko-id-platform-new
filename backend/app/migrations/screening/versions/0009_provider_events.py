"""Events a licensed screening provider sent us (ZID-DTV-WF-06 section 10, ENG-15; CR-113).

Revision ID: screening_0009
Revises: screening_0008
Create Date: 2026-11-18

screening_provider_event: one row for every callback received, accepted or refused. A refused one (a wrong signature, a stale clock, a wrong tenant) is recorded with its code so it can be alerted on,
but it never claims an event id: only an ACCEPTED event holds its (provider, event id), through a partial unique index, so an attacker who has no key cannot use up ids a provider will later send, and a
replay of an accepted event is recognised and changes nothing. The body itself is not kept, only its hash.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0009"
down_revision = "screening_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.screening_provider_event (
            row_id       uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            provider_id  text NOT NULL CHECK (length(provider_id) BETWEEN 1 AND 64),
            event_id     text NOT NULL CHECK (length(event_id) BETWEEN 1 AND 128),
            case_ref     text,
            accepted     boolean NOT NULL,
            outcome      text NOT NULL,
            body_sha256  text NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
            received_at  timestamptz NOT NULL
        );
        CREATE UNIQUE INDEX screening_provider_event_accepted ON verify.screening_provider_event (provider_id, event_id) WHERE accepted;
        CREATE INDEX screening_provider_event_recent ON verify.screening_provider_event (tenant_id, received_at);
        CREATE TRIGGER screening_provider_event_append_only BEFORE UPDATE OR DELETE ON verify.screening_provider_event
            FOR EACH ROW EXECUTE FUNCTION verify.screening_append_only();
        ALTER TABLE verify.screening_provider_event ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.screening_provider_event FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.screening_provider_event
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE verify.screening_provider_event;")

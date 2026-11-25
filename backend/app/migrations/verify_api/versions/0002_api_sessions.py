"""Verify API sessions, decisions and idempotency keys (ZID-EXT-IDV-001 section 9, 10.1, 10.2; CR-150).

Revision ID: verify_api_0002
Revises: verify_api_0001
Create Date: 2026-11-24

`api_session` is one verification of one of a partner's customers: who asked (the application), the partner's own reference for the person, the pinned workflow version, the market and purpose, where it is in
the lifecycle, and the HASH of the launch token (the link a person opens is shown once, when it is created). `api_decision` is what was established, versioned: a later decision supersedes an earlier one and the
earlier one stays. `api_idempotency` makes creating a session safe to repeat: the same key with the same request returns the same session, the same key with a different request is refused.

All three are tenant-scoped under row-level security. The hosted page a person opens has no signed-in company, so it finds its session by the hash of the launch token inside the same `auth` scope the token
endpoint uses (migration verify_api_0001); outside it nothing here is readable across companies.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0002"
down_revision = "verify_api_0001"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
AUTH = "current_setting('app.verify_api_scope', true) = 'auth'"
TABLES = ("api_session", "api_decision", "api_idempotency")
STATES = "'created', 'ready', 'opened', 'capture_started', 'submitted', 'processing', 'manual_review', 'action_required', 'completed', 'inconclusive', 'expired', 'canceled'"


def upgrade() -> None:
    policies = "\n".join(
        f"""
        ALTER TABLE verify.{t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.{t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.{t} USING ({TENANT}) WITH CHECK ({TENANT});
        CREATE POLICY verify_api_auth ON verify.{t} USING ({AUTH}) WITH CHECK ({AUTH});
        """
        for t in TABLES
    )
    _run(f"""
        CREATE TABLE verify.api_session (
            session_id          text PRIMARY KEY CHECK (session_id ~ '^vfs_[0-9a-f]{{32}}$'),
            tenant_id           uuid NOT NULL,
            application_id      uuid NOT NULL REFERENCES verify.api_application (application_id),
            environment         text NOT NULL CHECK (environment IN ('SANDBOX', 'PRODUCTION')),
            customer_reference  text NOT NULL CHECK (char_length(customer_reference) BETWEEN 1 AND 128),
            workflow_id         text NOT NULL,
            workflow_version    text NOT NULL,
            country             text NOT NULL CHECK (country ~ '^[A-Z]{{2}}$'),
            region              text NOT NULL CHECK (region ~ '^[a-z0-9-]{{2,16}}$'),
            purpose_code        text NOT NULL,
            locale              text NOT NULL CHECK (locale ~ '^[a-z]{{2}}(-[A-Z]{{2}})?$'),
            return_origin       text,
            metadata            jsonb NOT NULL DEFAULT '{{}}' CHECK (pg_column_size(metadata) <= 2048),
            status              text NOT NULL CHECK (status IN ({STATES})),
            launch_token_hash   bytea UNIQUE CHECK (launch_token_hash IS NULL OR octet_length(launch_token_hash) = 32),
            launch_expires_at   timestamptz,
            created_at          timestamptz NOT NULL DEFAULT now(),
            opened_at           timestamptz,
            completed_at        timestamptz,
            expires_at          timestamptz NOT NULL
        );
        CREATE INDEX api_session_by_application ON verify.api_session (application_id, created_at DESC, session_id DESC);
        CREATE INDEX api_session_by_reference ON verify.api_session (application_id, customer_reference);

        CREATE TABLE verify.api_decision (
            decision_id             text PRIMARY KEY CHECK (decision_id ~ '^dec_[0-9a-f]{{32}}$'),
            session_id              text NOT NULL REFERENCES verify.api_session (session_id),
            tenant_id               uuid NOT NULL,
            decision_version        integer NOT NULL CHECK (decision_version >= 1),
            supersedes_decision_id  text REFERENCES verify.api_decision (decision_id),
            outcome                 text NOT NULL CHECK (outcome IN ('verified', 'not_verified', 'inconclusive', 'review_required')),
            checks                  jsonb NOT NULL DEFAULT '[]',
            reason_codes            text[] NOT NULL DEFAULT '{{}}',
            sandbox                 boolean NOT NULL,
            policy_version          text NOT NULL,
            region                  text NOT NULL,
            completed_at            timestamptz NOT NULL DEFAULT now(),
            UNIQUE (session_id, decision_version),
            CHECK ((decision_version = 1) = (supersedes_decision_id IS NULL))
        );

        CREATE TABLE verify.api_idempotency (
            application_id  uuid NOT NULL REFERENCES verify.api_application (application_id),
            idem_key        text NOT NULL CHECK (char_length(idem_key) BETWEEN 8 AND 128),
            tenant_id       uuid NOT NULL,
            request_hash    bytea NOT NULL CHECK (octet_length(request_hash) = 32),
            session_id      text NOT NULL REFERENCES verify.api_session (session_id),
            created_at      timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (application_id, idem_key)
        );
        {policies}
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.api_idempotency;
        DROP TABLE verify.api_decision;
        DROP TABLE verify.api_session;
    """)

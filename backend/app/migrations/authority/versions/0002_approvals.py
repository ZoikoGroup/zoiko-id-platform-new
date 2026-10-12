"""Transaction-bound approvals (ZID-MANDATE-001 §16; ZID-AUTHZ-001 §9; MAND-16/17, AUTHZ-15/16).

Revision ID: authority_0002
Revises: authority_0001
Create Date: 2026-10-12

Proposed tables (CR-007). Database guards: one decision per approver per request (two sessions of the
same principal can't vote twice), every decision must carry the request's transaction hash, and
decisions are append-only.
"""

from zid_platform.migrations import execute_script as _run

revision = "authority_0002"
down_revision = "authority_0001"
branch_labels = None
depends_on = None

TENANT_TABLES = ("approval_request", "approval_decision")


def upgrade() -> None:
    _run("""
        CREATE TABLE authz.approval_request (
            approval_request_id         uuid PRIMARY KEY,
            tenant_id                   uuid NOT NULL,
            requester_principal_id      uuid NOT NULL,
            subject_ref                 text NOT NULL,
            transaction_context_hash    bytea NOT NULL,
            mode                        text NOT NULL CHECK (mode IN ('SINGLE', 'ALL_OF', 'K_OF_N', 'ROLE_PAIR',
                                                                       'EXTERNAL_CONFIRMATION')),
            roles                       text[] NOT NULL CHECK (cardinality(roles) > 0),
            k                           smallint NOT NULL DEFAULT 1 CHECK (k >= 1),
            self_approval_forbidden     boolean NOT NULL DEFAULT true,
            allow_shared_grant          boolean NOT NULL DEFAULT false,
            external_confirmation_valid boolean NOT NULL DEFAULT false,
            expires_at                  timestamptz NOT NULL,
            created_at                  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, approval_request_id, transaction_context_hash)
        );

        CREATE TABLE authz.approval_decision (
            approval_decision_id        uuid PRIMARY KEY,
            tenant_id                   uuid NOT NULL,
            approval_request_id         uuid NOT NULL,
            approver_principal_id       uuid NOT NULL,
            approver_roles              text[] NOT NULL,
            authority_grant_id          uuid NOT NULL,
            authority_valid             boolean NOT NULL,
            verdict                     text NOT NULL CHECK (verdict IN ('APPROVE', 'DENY')),
            transaction_context_hash    bytea NOT NULL,
            session_id                  text NOT NULL DEFAULT '',
            decided_at                  timestamptz NOT NULL,
            UNIQUE (approval_request_id, approver_principal_id),
            FOREIGN KEY (tenant_id, approval_request_id, transaction_context_hash)
                REFERENCES authz.approval_request (tenant_id, approval_request_id, transaction_context_hash)
        );

        CREATE FUNCTION authz.approval_decision_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'approval decisions are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER approval_decision_append_only BEFORE UPDATE OR DELETE ON authz.approval_decision
            FOR EACH ROW EXECUTE FUNCTION authz.approval_decision_append_only();
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE authz.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE authz.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON authz.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER approval_decision_append_only ON authz.approval_decision;
        DROP FUNCTION authz.approval_decision_append_only();
        DROP TABLE authz.approval_decision;
        DROP TABLE authz.approval_request;
    """)

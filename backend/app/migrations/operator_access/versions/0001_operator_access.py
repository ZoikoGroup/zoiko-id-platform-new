"""Operator access: JIT grants, approvals, customer lockbox, break-glass review (DEC-F4; CR-012).

Revision ID: operator_access_0001
Revises:
Create Date: 2026-10-21

Proposed tables (DEC-F4, CR-012). Every table is tenant-scoped under row-level security:
- operator-plane rows (operator_profile, jit_grant, operator_access_approval, break_glass_review)
  live in the operator tenant (ZID_OPERATOR_TENANT_ID);
- customer-plane rows (lockbox_request, operator_access_policy) live in the customer tenant, so a
  customer administrator only ever sees their own tenant's lockbox requests.
Database guards: an ACTIVE grant can never outlive its approved duration (no open-ended access);
nobody approves their own request or reviews their own break-glass; approvals are append-only.
"""

from zid_platform.migrations import execute_script as _run

revision = "operator_access_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "operator_profile",
    "jit_grant",
    "operator_access_approval",
    "lockbox_request",
    "operator_access_policy",
    "break_glass_review",
)


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.operator_profile (
            tenant_id                uuid NOT NULL,
            principal_id             uuid NOT NULL,
            jurisdiction             text NOT NULL CHECK (jurisdiction ~ '^[A-Z]{2}$'),
            roles                    text[] NOT NULL DEFAULT '{REQUESTER}'
                                     CHECK (roles <@ ARRAY['REQUESTER', 'APPROVER', 'REVIEWER']::text[]),
            status                   text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
            created_at               timestamptz NOT NULL DEFAULT now(),
            updated_at               timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, principal_id)
        );

        CREATE TABLE platform.jit_grant (
            jit_grant_id             uuid PRIMARY KEY,
            tenant_id                uuid NOT NULL,
            requester_principal_id   uuid NOT NULL,
            capability               text NOT NULL,
            target_tenant_id         uuid,
            environment              text NOT NULL,
            cell_id                  text NOT NULL,
            resource_scope           text NOT NULL,
            case_ref                 text NOT NULL,
            reason                   text NOT NULL,
            risk_class               smallint NOT NULL CHECK (risk_class BETWEEN 0 AND 3),
            evidence_class           smallint NOT NULL CHECK (evidence_class BETWEEN 0 AND 3),
            duration_seconds         integer NOT NULL CHECK (duration_seconds > 0),
            required_approvals       smallint NOT NULL CHECK (required_approvals BETWEEN 0 AND 2),
            lockbox_required         boolean NOT NULL DEFAULT false,
            lockbox_request_id       uuid,
            break_glass              boolean NOT NULL DEFAULT false,
            requester_jurisdiction   text NOT NULL,
            state                    text NOT NULL CHECK (state IN ('REQUESTED', 'APPROVED', 'ACTIVE',
                                                                    'EXPIRED', 'REVOKED', 'DENIED', 'CANCELLED')),
            version                  integer NOT NULL DEFAULT 1,
            created_at               timestamptz NOT NULL,
            request_expires_at       timestamptz NOT NULL,
            approved_at              timestamptz,
            activated_at             timestamptz,
            expires_at               timestamptz,
            ended_at                 timestamptz,
            ended_by                 uuid,
            end_reason               text,
            UNIQUE (jit_grant_id, requester_principal_id),
            CHECK (break_glass = false OR required_approvals = 0),
            CHECK (lockbox_required = false OR lockbox_request_id IS NOT NULL),
            CHECK (state <> 'ACTIVE' OR (activated_at IS NOT NULL AND expires_at IS NOT NULL
                   AND expires_at <= activated_at + duration_seconds * interval '1 second'))
        );
        CREATE INDEX jit_grant_requester ON platform.jit_grant (tenant_id, requester_principal_id, state);
        CREATE INDEX jit_grant_due ON platform.jit_grant (tenant_id, state, expires_at);

        CREATE TABLE platform.operator_access_approval (
            approval_id              uuid PRIMARY KEY,
            tenant_id                uuid NOT NULL,
            jit_grant_id             uuid NOT NULL,
            requester_principal_id   uuid NOT NULL,
            approver_principal_id    uuid NOT NULL,
            verdict                  text NOT NULL CHECK (verdict IN ('APPROVE', 'DENY')),
            decided_at               timestamptz NOT NULL,
            UNIQUE (jit_grant_id, approver_principal_id),
            CHECK (approver_principal_id <> requester_principal_id),
            FOREIGN KEY (jit_grant_id, requester_principal_id)
                REFERENCES platform.jit_grant (jit_grant_id, requester_principal_id)
        );
        CREATE FUNCTION platform.operator_access_approval_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'operator access approvals are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER operator_access_approval_append_only
            BEFORE UPDATE OR DELETE ON platform.operator_access_approval
            FOR EACH ROW EXECUTE FUNCTION platform.operator_access_approval_append_only();

        CREATE TABLE platform.lockbox_request (
            lockbox_request_id       uuid PRIMARY KEY,
            tenant_id                uuid NOT NULL,
            operator_tenant_id       uuid NOT NULL,
            jit_grant_id             uuid NOT NULL,
            requester_principal_id   uuid NOT NULL,
            capability               text NOT NULL,
            resource_scope           text NOT NULL,
            case_ref                 text NOT NULL,
            reason                   text NOT NULL,
            duration_seconds         integer NOT NULL CHECK (duration_seconds > 0),
            state                    text NOT NULL CHECK (state IN ('PENDING', 'APPROVED', 'DENIED', 'REVOKED')),
            version                  integer NOT NULL DEFAULT 1,
            created_at               timestamptz NOT NULL,
            expires_at               timestamptz NOT NULL,
            decided_by               uuid,
            decided_at               timestamptz,
            decision_reason          text,
            CHECK (state = 'PENDING' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
            CHECK (decided_by IS NULL OR decided_by <> requester_principal_id)
        );
        CREATE INDEX lockbox_request_state ON platform.lockbox_request (tenant_id, state, created_at);

        CREATE TABLE platform.operator_access_policy (
            tenant_id                       uuid PRIMARY KEY,
            lockbox_required                boolean NOT NULL DEFAULT true,
            allowed_operator_jurisdictions  text[] NOT NULL DEFAULT '{}',
            revision                        integer NOT NULL DEFAULT 1,
            updated_by                      uuid NOT NULL,
            updated_at                      timestamptz NOT NULL
        );

        CREATE TABLE platform.break_glass_review (
            review_id                uuid PRIMARY KEY,
            tenant_id                uuid NOT NULL,
            jit_grant_id             uuid NOT NULL UNIQUE,
            requester_principal_id   uuid NOT NULL,
            state                    text NOT NULL CHECK (state IN ('REVIEW_PENDING', 'REVIEWED')),
            reviewer_principal_id    uuid,
            outcome                  text CHECK (outcome IN ('JUSTIFIED', 'UNJUSTIFIED')),
            notes                    text,
            created_at               timestamptz NOT NULL,
            reviewed_at              timestamptz,
            CHECK (reviewer_principal_id IS NULL OR reviewer_principal_id <> requester_principal_id),
            CHECK (state = 'REVIEW_PENDING' OR (reviewer_principal_id IS NOT NULL AND outcome IS NOT NULL
                   AND reviewed_at IS NOT NULL)),
            FOREIGN KEY (jit_grant_id, requester_principal_id)
                REFERENCES platform.jit_grant (jit_grant_id, requester_principal_id)
        );
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE platform.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE platform.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON platform.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.break_glass_review;
        DROP TABLE platform.operator_access_policy;
        DROP TABLE platform.lockbox_request;
        DROP TRIGGER operator_access_approval_append_only ON platform.operator_access_approval;
        DROP FUNCTION platform.operator_access_approval_append_only();
        DROP TABLE platform.operator_access_approval;
        DROP TABLE platform.jit_grant;
        DROP TABLE platform.operator_profile;
    """)

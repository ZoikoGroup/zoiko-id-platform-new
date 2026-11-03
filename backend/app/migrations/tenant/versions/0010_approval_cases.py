"""Approval cases, votes, execution attempts and the reviewer registry (ZID-ENG-DEC-010 to 012; slice L-10, CR-047).

Revision ID: tenant_0010
Revises: tenant_0009
Create Date: 2026-10-30

approval_case: one immutable request version. What it asks for (action, target, canonical payload, policy version, region, hash) and who
asked (requester person, every editor, the beneficiary) never change after insert; only the state moves, and a database trigger refuses
a move out of a final state or into EXECUTED from anywhere but a ready or failed case. A material change is a new case that points back at
the one it replaces, so no vote is ever carried over.
approval_case_transition: every state change, append-only.
approval_vote: one row per approving person per case (unique on the PERSON, so two accounts of one human can never be two votes), bound to
the request hash, append-only.
approval_execution: every attempt to apply a case, append-only; at most one attempt may be EXECUTED, and a retry with the same idempotency
key is the same attempt.
governance_reviewer: the organization's appointed independent reviewers (Decision 12), who need not be employees.
approval_consumption: a platform authorization (an executed platform case) is used up exactly once by the operation it authorizes.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0010"
down_revision = "tenant_0009"
branch_labels = None
depends_on = None

_RLS = """
    ALTER TABLE platform.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE platform.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON platform.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""
TABLES = (
    "approval_case",
    "approval_case_transition",
    "approval_vote",
    "approval_execution",
    "governance_reviewer",
    "approval_consumption",
)


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.governance_reviewer (
            reviewer_id            uuid PRIMARY KEY,
            tenant_id              uuid NOT NULL REFERENCES platform.tenant (tenant_id),
            principal_id           uuid NOT NULL,
            person_id              uuid NOT NULL,
            relationship           text NOT NULL CHECK (relationship IN
                ('EXTERNAL_PROFESSIONAL', 'CO_DIRECTOR', 'TRUSTEE', 'AUTHORIZED_DELEGATE')),
            authority_basis        text NOT NULL CHECK (length(authority_basis) BETWEEN 1 AND 500),
            independence_declaration text NOT NULL CHECK (length(independence_declaration) BETWEEN 1 AND 500),
            declared_conflicts     uuid[] NOT NULL DEFAULT '{}',
            region                 text NOT NULL,
            status                 text NOT NULL CHECK (status IN ('PROPOSED', 'ACTIVE', 'REVOKED', 'EXPIRED')),
            expires_at             timestamptz NOT NULL,
            proposed_by            uuid NOT NULL,
            activated_via          text CHECK (activated_via IN ('A1_APPROVAL', 'PLATFORM_VERIFICATION')),
            activation_ref         text CHECK (activation_ref IS NULL OR length(activation_ref) <= 128),
            activated_at           timestamptz,
            revoked_at             timestamptz,
            created_at             timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX governance_reviewer_one_live ON platform.governance_reviewer (tenant_id, person_id)
            WHERE status IN ('PROPOSED', 'ACTIVE');

        CREATE TABLE platform.approval_case (
            case_id                uuid PRIMARY KEY,
            tenant_id              uuid NOT NULL REFERENCES platform.tenant (tenant_id),
            version                integer NOT NULL DEFAULT 1 CHECK (version >= 1),
            supersedes_case_id     uuid REFERENCES platform.approval_case (case_id),
            action_id              text NOT NULL,
            tier                   text NOT NULL CHECK (tier IN ('A1', 'A2')),
            target_id              text NOT NULL CHECK (length(target_id) BETWEEN 1 AND 256),
            payload_json           text NOT NULL,
            request_hash           text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
            policy_version         text NOT NULL,
            region                 text NOT NULL,
            requester_principal_id uuid NOT NULL,
            requester_person_id    uuid NOT NULL,
            editor_persons         uuid[] NOT NULL,
            beneficiary_principal_id uuid,
            beneficiary_person_id  uuid,
            summary                text NOT NULL CHECK (length(summary) BETWEEN 1 AND 500),
            reason                 text NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
            evidence_ref           text CHECK (evidence_ref IS NULL OR length(evidence_ref) <= 128),
            state                  text NOT NULL CHECK (state IN ('PENDING_APPROVAL', 'PARTIALLY_APPROVED', 'APPROVED_READY',
                'EXECUTED', 'FAILED', 'REJECTED', 'EXPIRED', 'INVALIDATED', 'CANCELLED')),
            expires_at             timestamptz NOT NULL,
            created_at             timestamptz NOT NULL DEFAULT now(),
            state_changed_at       timestamptz NOT NULL DEFAULT now(),
            executed_at            timestamptz,
            UNIQUE (tenant_id, case_id)
        );
        CREATE INDEX approval_case_open ON platform.approval_case (tenant_id, state, expires_at);

        CREATE FUNCTION platform.approval_case_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'approval cases are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.case_id, NEW.tenant_id, NEW.version, NEW.supersedes_case_id, NEW.action_id, NEW.tier, NEW.target_id,
                NEW.payload_json, NEW.request_hash, NEW.policy_version, NEW.region, NEW.requester_principal_id,
                NEW.requester_person_id, NEW.editor_persons, NEW.beneficiary_principal_id, NEW.beneficiary_person_id,
                NEW.summary, NEW.reason, NEW.expires_at, NEW.created_at)
               IS DISTINCT FROM
               (OLD.case_id, OLD.tenant_id, OLD.version, OLD.supersedes_case_id, OLD.action_id, OLD.tier, OLD.target_id,
                OLD.payload_json, OLD.request_hash, OLD.policy_version, OLD.region, OLD.requester_principal_id,
                OLD.requester_person_id, OLD.editor_persons, OLD.beneficiary_principal_id, OLD.beneficiary_person_id,
                OLD.summary, OLD.reason, OLD.expires_at, OLD.created_at) THEN
                RAISE EXCEPTION 'an approval request is immutable; a material change is a new case'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state IN ('EXECUTED', 'REJECTED', 'EXPIRED', 'INVALIDATED', 'CANCELLED') AND NEW.state <> OLD.state THEN
                RAISE EXCEPTION 'approval case % is final', OLD.state USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.state = 'EXECUTED' AND OLD.state NOT IN ('APPROVED_READY', 'FAILED') THEN
                RAISE EXCEPTION 'a case runs only from APPROVED_READY or after a failed attempt'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER approval_case_guard BEFORE UPDATE OR DELETE ON platform.approval_case
            FOR EACH ROW EXECUTE FUNCTION platform.approval_case_guard();

        CREATE FUNCTION platform.governance_reviewer_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'reviewers are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.reviewer_id, NEW.tenant_id, NEW.principal_id, NEW.person_id, NEW.relationship, NEW.authority_basis,
                NEW.independence_declaration, NEW.declared_conflicts, NEW.region, NEW.expires_at, NEW.proposed_by)
               IS DISTINCT FROM
               (OLD.reviewer_id, OLD.tenant_id, OLD.principal_id, OLD.person_id, OLD.relationship, OLD.authority_basis,
                OLD.independence_declaration, OLD.declared_conflicts, OLD.region, OLD.expires_at, OLD.proposed_by) THEN
                RAISE EXCEPTION 'a reviewer appointment is immutable; appoint again' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status IN ('REVOKED', 'EXPIRED') AND NEW.status <> OLD.status THEN
                RAISE EXCEPTION 'a ended appointment is final' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER governance_reviewer_guard BEFORE UPDATE OR DELETE ON platform.governance_reviewer
            FOR EACH ROW EXECUTE FUNCTION platform.governance_reviewer_guard();

        CREATE TABLE platform.approval_case_transition (
            transition_id          uuid PRIMARY KEY,
            seq                    integer NOT NULL CHECK (seq >= 1),
            tenant_id              uuid NOT NULL REFERENCES platform.tenant (tenant_id),
            case_id                uuid NOT NULL REFERENCES platform.approval_case (case_id),
            from_state             text NOT NULL,
            to_state               text NOT NULL,
            actor_principal_id     uuid,
            reason_code            text NOT NULL CHECK (length(reason_code) <= 64),
            at                     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (case_id, seq)
        );
        CREATE INDEX approval_case_transition_case ON platform.approval_case_transition (tenant_id, case_id, seq);

        CREATE TABLE platform.approval_vote (
            vote_id                uuid PRIMARY KEY,
            tenant_id              uuid NOT NULL REFERENCES platform.tenant (tenant_id),
            case_id                uuid NOT NULL REFERENCES platform.approval_case (case_id),
            request_hash           text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
            tier                   text NOT NULL CHECK (tier IN ('A1', 'A2')),
            approver_principal_id  uuid NOT NULL,
            approver_person_id     uuid NOT NULL,
            authority_basis        text NOT NULL CHECK (length(authority_basis) <= 128),
            vote                   text NOT NULL CHECK (vote IN ('APPROVE', 'REJECT')),
            step_up_proof_ref      text CHECK (step_up_proof_ref IS NULL OR length(step_up_proof_ref) <= 128),
            auth_context           jsonb NOT NULL DEFAULT '{}',
            decided_at             timestamptz NOT NULL DEFAULT now(),
            UNIQUE (case_id, approver_person_id)
        );
        CREATE TRIGGER approval_vote_append_only BEFORE UPDATE OR DELETE ON platform.approval_vote
            FOR EACH ROW EXECUTE FUNCTION platform.tenant_history_guard();

        CREATE TABLE platform.approval_execution (
            execution_id           uuid PRIMARY KEY,
            tenant_id              uuid NOT NULL REFERENCES platform.tenant (tenant_id),
            case_id                uuid NOT NULL REFERENCES platform.approval_case (case_id),
            attempt                integer NOT NULL CHECK (attempt >= 1),
            idempotency_key        text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
            request_hash           text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
            outcome                text NOT NULL CHECK (outcome IN ('EXECUTED', 'FAILED')),
            reason_code            text CHECK (reason_code IS NULL OR length(reason_code) <= 64),
            actor_principal_id     uuid NOT NULL,
            region                 text NOT NULL,
            quorum                 jsonb NOT NULL DEFAULT '{}',
            executed_at            timestamptz NOT NULL DEFAULT now(),
            UNIQUE (case_id, attempt),
            UNIQUE (case_id, idempotency_key)
        );
        CREATE UNIQUE INDEX approval_execution_once ON platform.approval_execution (case_id) WHERE outcome = 'EXECUTED';
        CREATE TABLE platform.approval_consumption (
            case_id                uuid PRIMARY KEY REFERENCES platform.approval_case (case_id),
            tenant_id              uuid NOT NULL REFERENCES platform.tenant (tenant_id),
            consumer               text NOT NULL CHECK (length(consumer) BETWEEN 1 AND 128),
            consumed_at            timestamptz NOT NULL DEFAULT now()
        );
        CREATE TRIGGER approval_consumption_append_only BEFORE UPDATE OR DELETE ON platform.approval_consumption
            FOR EACH ROW EXECUTE FUNCTION platform.tenant_history_guard();
        CREATE TRIGGER approval_execution_append_only BEFORE UPDATE OR DELETE ON platform.approval_execution
            FOR EACH ROW EXECUTE FUNCTION platform.tenant_history_guard();
        CREATE TRIGGER approval_case_transition_append_only BEFORE UPDATE OR DELETE ON platform.approval_case_transition
            FOR EACH ROW EXECUTE FUNCTION platform.tenant_history_guard();
    """)
    for t in TABLES:
        _run(_RLS.format(t=t))


def downgrade() -> None:
    _run("""
        DROP TABLE platform.approval_consumption;
        DROP TABLE platform.approval_execution;
        DROP TABLE platform.approval_vote;
        DROP TABLE platform.approval_case_transition;
        DROP TABLE platform.approval_case;
        DROP TABLE platform.governance_reviewer;
        DROP FUNCTION platform.approval_case_guard();
        DROP FUNCTION platform.governance_reviewer_guard();
    """)

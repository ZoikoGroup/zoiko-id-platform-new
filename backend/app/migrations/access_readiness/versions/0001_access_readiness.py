"""Delegated Authority & Access Readiness agent: setup, approval, runs, decisions, evidence, proposals (ZID-AG-WF-001; CR-091).

Revision ID: access_readiness_0001
Revises:
Create Date: 2026-11-13

access_review_setup: the agent's scope, kept as a draft until approved (one per agent). access_review_approval: the exact digest an approver signed,
with its expiry; set once, never edited, invalidated once. access_review_run: one row per requested run; terminal states are final.
access_review_policy_decision and access_review_evidence: append-only (decision lineage; digests and references only, never a token, a document,
a biometric or raw personal data). access_review_proposal: what the agent writes, its only write, one per proposal key; after creation only the
human review fields move forward. access_review_handoff: a signed record that a person asked for the governed access workflow, which is
never executed from here (`executes` is always false). All tenant-scoped under row-level security.
"""

from zid_platform.migrations import execute_script as _run

revision = "access_readiness_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "access_review_setup",
    "access_review_approval",
    "access_review_run",
    "access_review_policy_decision",
    "access_review_evidence",
    "access_review_proposal",
    "access_review_handoff",
)
APPEND_ONLY = ["access_review_policy_decision", "access_review_evidence", "access_review_handoff"]


def upgrade() -> None:
    _run("""
        CREATE TABLE agent.access_review_setup (
            setup_id            uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            agent_principal_id  uuid NOT NULL,
            display_name        text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 80),
            purpose             text NOT NULL CHECK (purpose = 'access_posture_review'),
            resource_kinds      text[] NOT NULL CHECK (cardinality(resource_kinds) >= 1),
            max_records         integer NOT NULL CHECK (max_records BETWEEN 1 AND 500),
            warning_window_days integer NOT NULL CHECK (warning_window_days BETWEEN 1 AND 90),
            home_region         text NOT NULL CHECK (length(btrim(home_region)) BETWEEN 1 AND 64),
            rule_set_version    text NOT NULL,
            policy_version      text NOT NULL,
            status              text NOT NULL CHECK (status IN ('DRAFT', 'SANDBOX_READY', 'PENDING_APPROVAL', 'APPROVED', 'PAUSED')),
            draft_revision      integer NOT NULL DEFAULT 1 CHECK (draft_revision >= 1),
            scope_digest        text NOT NULL CHECK (scope_digest ~ '^sha256:[0-9a-f]{64}$'),
            validated_digest    text CHECK (validated_digest ~ '^sha256:[0-9a-f]{64}$'),
            created_by          uuid NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            paused_by           uuid,
            paused_reason       text,
            paused_at           timestamptz,
            UNIQUE (tenant_id, agent_principal_id),
            UNIQUE (tenant_id, setup_id)
        );

        CREATE TABLE agent.access_review_approval (
            approval_id         uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            setup_id            uuid NOT NULL,
            agent_principal_id  uuid NOT NULL,
            scope_digest        text NOT NULL CHECK (scope_digest ~ '^sha256:[0-9a-f]{64}$'),
            requested_by        uuid NOT NULL,
            requested_at        timestamptz NOT NULL DEFAULT now(),
            expires_at          timestamptz NOT NULL,
            approved_by         uuid,
            approved_at         timestamptz,
            invalidated_at      timestamptz,
            invalidated_reason  text,
            FOREIGN KEY (tenant_id, setup_id) REFERENCES agent.access_review_setup (tenant_id, setup_id),
            CHECK (expires_at > requested_at),
            CHECK ((approved_by IS NULL) = (approved_at IS NULL)),
            CHECK (approved_by IS NULL OR approved_by <> requested_by)
        );
        CREATE INDEX access_review_approval_setup ON agent.access_review_approval (tenant_id, setup_id, requested_at DESC);

        CREATE TABLE agent.access_review_run (
            run_id              uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            agent_principal_id  uuid NOT NULL,
            setup_id            uuid NOT NULL,
            idempotency_key_hash text NOT NULL,
            request_hash        text NOT NULL,
            run_definition_hash text NOT NULL CHECK (run_definition_hash ~ '^sha256:[0-9a-f]{64}$'),
            policy_hash         text NOT NULL CHECK (policy_hash ~ '^sha256:[0-9a-f]{64}$'),
            rule_set_version    text NOT NULL,
            mode                text NOT NULL CHECK (mode = 'manual'),
            purpose             text NOT NULL,
            resource_selector   jsonb NOT NULL,
            warning_window_days integer NOT NULL,
            home_region         text NOT NULL,
            state               text NOT NULL CHECK (state IN ('QUEUED', 'PREFLIGHT', 'RUNNING', 'DENIED', 'COMPLETED', 'NEEDS_REVIEW', 'FAILED', 'CANCELED')),
            attempt             integer NOT NULL DEFAULT 0,
            denial_codes        text[] NOT NULL DEFAULT '{}',
            initiated_by        uuid NOT NULL,
            trace_id            text NOT NULL,
            worker_attestation_id uuid,
            summary             jsonb NOT NULL DEFAULT '{}',
            created_at          timestamptz NOT NULL DEFAULT now(),
            started_at          timestamptz,
            terminal_at         timestamptz,
            FOREIGN KEY (tenant_id, setup_id) REFERENCES agent.access_review_setup (tenant_id, setup_id),
            UNIQUE (tenant_id, run_id),
            UNIQUE (tenant_id, agent_principal_id, idempotency_key_hash)
        );
        CREATE INDEX access_review_run_queue ON agent.access_review_run (state, created_at);

        CREATE TABLE agent.access_review_policy_decision (
            decision_id         uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            run_id              uuid,
            actor               text NOT NULL,
            subject             text NOT NULL,
            resource            text NOT NULL,
            action              text NOT NULL,
            purpose             text NOT NULL,
            outcome             text NOT NULL CHECK (outcome IN ('ALLOW', 'DENY')),
            reason_code         text NOT NULL,
            policy_hash         text NOT NULL CHECK (policy_hash ~ '^sha256:[0-9a-f]{64}$'),
            decided_at          timestamptz NOT NULL DEFAULT now(),
            trace_id            text
        );
        CREATE INDEX access_review_decision_run ON agent.access_review_policy_decision (tenant_id, run_id, decided_at);

        CREATE TABLE agent.access_review_evidence (
            event_id            uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            run_id              uuid NOT NULL,
            seq                 integer NOT NULL CHECK (seq >= 1),
            event_type          text NOT NULL,
            source_id           text NOT NULL,
            source_version      text NOT NULL,
            payload_digest      text NOT NULL CHECK (payload_digest ~ '^sha256:[0-9a-f]{64}$'),
            chain_digest        text NOT NULL CHECK (chain_digest ~ '^sha256:[0-9a-f]{64}$'),
            classification      text NOT NULL,
            region              text NOT NULL,
            retention_policy_ref text NOT NULL,
            recorded_at         timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, run_id, seq)
        );

        CREATE TABLE agent.access_review_proposal (
            proposal_id         uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            created_by_agent_id uuid NOT NULL,
            run_id              uuid NOT NULL,
            run_step_id         text NOT NULL,
            proposal_key        text NOT NULL CHECK (proposal_key ~ '^sha256:[0-9a-f]{64}$'),
            target_ref          text NOT NULL,
            rule_id             text NOT NULL,
            rule_version        text NOT NULL,
            finding_code        text NOT NULL,
            severity            text NOT NULL,
            confidence          text NOT NULL,
            evidence_refs       text[] NOT NULL,
            status              text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'ASSIGNED', 'DISMISSED', 'HANDED_OFF')),
            created_at          timestamptz NOT NULL DEFAULT now(),
            reviewed_by         uuid,
            reviewed_at         timestamptz,
            assignee            uuid,
            UNIQUE (tenant_id, proposal_key),
            UNIQUE (tenant_id, run_id, run_step_id),
            UNIQUE (tenant_id, proposal_id)
        );
        CREATE INDEX access_review_proposal_status ON agent.access_review_proposal (tenant_id, status, created_at DESC);

        CREATE TABLE agent.access_review_handoff (
            handoff_id          uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            proposal_id         uuid NOT NULL,
            handed_off_by       uuid NOT NULL,
            target_workflow     text NOT NULL,
            handoff_digest      text NOT NULL CHECK (handoff_digest ~ '^sha256:[0-9a-f]{64}$'),
            signature           text NOT NULL,
            executes            boolean NOT NULL DEFAULT false CHECK (executes = false),
            signed_at           timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (tenant_id, proposal_id) REFERENCES agent.access_review_proposal (tenant_id, proposal_id),
            UNIQUE (tenant_id, proposal_id)
        );

        CREATE FUNCTION agent.access_review_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'access review decisions, evidence and handoffs are append-only' USING ERRCODE = 'check_violation';
        END $$;

        CREATE FUNCTION agent.access_review_approval_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'approvals are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.approval_id, NEW.tenant_id, NEW.setup_id, NEW.agent_principal_id, NEW.scope_digest, NEW.requested_by, NEW.requested_at, NEW.expires_at)
               IS DISTINCT FROM
               (OLD.approval_id, OLD.tenant_id, OLD.setup_id, OLD.agent_principal_id, OLD.scope_digest, OLD.requested_by, OLD.requested_at, OLD.expires_at) THEN
                RAISE EXCEPTION 'an approval request is immutable' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.approved_by IS NOT NULL AND (NEW.approved_by, NEW.approved_at) IS DISTINCT FROM (OLD.approved_by, OLD.approved_at) THEN
                RAISE EXCEPTION 'an approval is set once' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.invalidated_at IS NOT NULL AND (NEW.invalidated_at, NEW.invalidated_reason) IS DISTINCT FROM (OLD.invalidated_at, OLD.invalidated_reason) THEN
                RAISE EXCEPTION 'an invalidation is final' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;

        CREATE FUNCTION agent.access_review_setup_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a setup is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.setup_id, NEW.tenant_id, NEW.agent_principal_id, NEW.created_by, NEW.created_at)
               IS DISTINCT FROM (OLD.setup_id, OLD.tenant_id, OLD.agent_principal_id, OLD.created_by, OLD.created_at) THEN
                RAISE EXCEPTION 'a setup keeps its agent and creator' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.draft_revision < OLD.draft_revision THEN
                RAISE EXCEPTION 'draft revisions only move forward' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;

        CREATE FUNCTION agent.access_review_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a run is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.run_id, NEW.tenant_id, NEW.agent_principal_id, NEW.setup_id, NEW.run_definition_hash, NEW.policy_hash, NEW.resource_selector, NEW.initiated_by, NEW.idempotency_key_hash, NEW.request_hash)
               IS DISTINCT FROM
               (OLD.run_id, OLD.tenant_id, OLD.agent_principal_id, OLD.setup_id, OLD.run_definition_hash, OLD.policy_hash, OLD.resource_selector, OLD.initiated_by, OLD.idempotency_key_hash, OLD.request_hash) THEN
                RAISE EXCEPTION 'a run definition is immutable' USING ERRCODE = 'check_violation';
            END IF;
            -- DENIED, COMPLETED, NEEDS_REVIEW and CANCELED are final; a FAILED attempt may be retried under the same run id
            IF OLD.state IN ('DENIED', 'COMPLETED', 'NEEDS_REVIEW', 'CANCELED') THEN
                RAISE EXCEPTION 'a finished run is final' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;

        CREATE FUNCTION agent.access_review_proposal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a review proposal is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.proposal_id, NEW.tenant_id, NEW.created_by_agent_id, NEW.run_id, NEW.run_step_id, NEW.proposal_key, NEW.target_ref, NEW.rule_id,
                NEW.rule_version, NEW.finding_code, NEW.severity, NEW.confidence, NEW.evidence_refs, NEW.created_at)
               IS DISTINCT FROM
               (OLD.proposal_id, OLD.tenant_id, OLD.created_by_agent_id, OLD.run_id, OLD.run_step_id, OLD.proposal_key, OLD.target_ref, OLD.rule_id,
                OLD.rule_version, OLD.finding_code, OLD.severity, OLD.confidence, OLD.evidence_refs, OLD.created_at) THEN
                RAISE EXCEPTION 'a proposal states what the agent found and never changes' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status IN ('DISMISSED', 'HANDED_OFF') AND NEW.status <> OLD.status THEN
                RAISE EXCEPTION 'a reviewed proposal is final' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;

        CREATE TRIGGER access_review_approval_guard BEFORE UPDATE OR DELETE ON agent.access_review_approval
            FOR EACH ROW EXECUTE FUNCTION agent.access_review_approval_guard();
        CREATE TRIGGER access_review_setup_guard BEFORE UPDATE OR DELETE ON agent.access_review_setup
            FOR EACH ROW EXECUTE FUNCTION agent.access_review_setup_guard();
        CREATE TRIGGER access_review_run_guard BEFORE UPDATE OR DELETE ON agent.access_review_run
            FOR EACH ROW EXECUTE FUNCTION agent.access_review_run_guard();
        CREATE TRIGGER access_review_proposal_guard BEFORE UPDATE OR DELETE ON agent.access_review_proposal
            FOR EACH ROW EXECUTE FUNCTION agent.access_review_proposal_guard();
    """)
    for table in APPEND_ONLY:
        _run(f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON agent.{table}
                FOR EACH ROW EXECUTE FUNCTION agent.access_review_append_only();
        """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE agent.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE agent.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON agent.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE agent.access_review_handoff;
        DROP TABLE agent.access_review_proposal;
        DROP TABLE agent.access_review_evidence;
        DROP TABLE agent.access_review_policy_decision;
        DROP TABLE agent.access_review_run;
        DROP TABLE agent.access_review_approval;
        DROP TABLE agent.access_review_setup;
        DROP FUNCTION agent.access_review_proposal_guard();
        DROP FUNCTION agent.access_review_run_guard();
        DROP FUNCTION agent.access_review_setup_guard();
        DROP FUNCTION agent.access_review_approval_guard();
        DROP FUNCTION agent.access_review_append_only();
    """)

"""The DAARA reference agent (ZID-AG-WF-001) and governance alert state (ZID-AG-WF-002 section 15); CR-106.

Revision ID: machine_0009
Revises: machine_0008
Create Date: 2026-11-14

`daara_instance` is one agent's DAARA setup (template, purpose, thresholds) with the hashes that gate publishing: a change to the
setup changes the configuration hash, which voids a passed validation and a passed sandbox test. `daara_run_result` is what a run
found. `review_proposal` is the one write DAARA is allowed: a row in a human review queue. Its finding is immutable; only the human
decision on it changes, and nothing in this schema or its code reaches a mandate, a grant, a role or a credential. `alert_state` is
the acknowledge / route / SLA record for the computed alerts of governance monitoring. Tenant-scoped under row-level security.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0009"
down_revision = "machine_0008"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
TABLES = ("daara_instance", "daara_run_result", "review_proposal", "alert_state")


def upgrade() -> None:
    _run("""
        CREATE TABLE agent.daara_instance (
            agent_principal_id  uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            template            text NOT NULL CHECK (template = 'DAARA'),
            purpose             text NOT NULL CHECK (purpose = 'access_posture_review'),
            display_label       text NOT NULL CHECK (char_length(display_label) BETWEEN 1 AND 80),
            rationale           text CHECK (char_length(rationale) <= 500),
            warn_days           integer NOT NULL DEFAULT 14 CHECK (warn_days BETWEEN 1 AND 90),
            schedule            text NOT NULL DEFAULT 'MANUAL' CHECK (schedule IN ('MANUAL','DAILY')),
            region              text NOT NULL CHECK (char_length(region) BETWEEN 2 AND 64),
            ai_explain          boolean NOT NULL DEFAULT false,
            config_hash         text NOT NULL CHECK (config_hash ~ '^sha256:[0-9a-f]{64}$'),
            validated_hash      text,
            sandbox_passed_hash text,
            policy_id           uuid,
            created_by          uuid NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE agent.daara_run_result (
            run_id              uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            agent_principal_id  uuid NOT NULL,
            mode                text NOT NULL CHECK (mode IN ('LIVE','DRY_RUN')),
            state               text NOT NULL CHECK (state IN ('COMPLETED','NEEDS_REVIEW','FAILED','DENIED','CANCELED')),
            incomplete          boolean NOT NULL DEFAULT false,
            checked             integer NOT NULL DEFAULT 0 CHECK (checked >= 0),
            issues              integer NOT NULL DEFAULT 0 CHECK (issues >= 0),
            proposals           integer NOT NULL DEFAULT 0 CHECK (proposals >= 0),
            privileges_changed  integer NOT NULL DEFAULT 0 CHECK (privileges_changed = 0),
            rule_set_version    text NOT NULL,
            policy_hash         text,
            summary             jsonb NOT NULL,
            explanation         text,
            explanation_source  text NOT NULL DEFAULT 'FIXED' CHECK (explanation_source IN ('FIXED','MODEL')),
            reason_code         text,
            completed_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX daara_run_result_agent ON agent.daara_run_result (tenant_id, agent_principal_id, completed_at DESC);

        CREATE TABLE agent.review_proposal (
            proposal_id         uuid PRIMARY KEY,
            ref_no              bigint GENERATED ALWAYS AS IDENTITY,
            tenant_id           uuid NOT NULL,
            agent_principal_id  uuid NOT NULL,
            run_id              uuid NOT NULL,
            target_ref          text NOT NULL CHECK (char_length(target_ref) BETWEEN 3 AND 200),
            rule_id             text NOT NULL,
            rule_version        text NOT NULL,
            finding_code        text NOT NULL,
            severity            text NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH')),
            confidence_basis    text NOT NULL DEFAULT 'DETERMINISTIC_RULE',
            evidence_refs       jsonb NOT NULL,
            dedupe_key          text NOT NULL CHECK (dedupe_key ~ '^sha256:[0-9a-f]{64}$'),
            seen_count          integer NOT NULL DEFAULT 1 CHECK (seen_count >= 1),
            last_run_id         uuid NOT NULL,
            status              text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','DISMISSED','ASSIGNED','HANDED_OFF')),
            decided_by          uuid,
            decided_at          timestamptz,
            decision_reason     text,
            assignee            uuid,
            handoff_digest      text,
            created_at          timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, dedupe_key),
            CHECK ((status = 'OPEN') = (decided_by IS NULL))
        );
        CREATE INDEX review_proposal_status ON agent.review_proposal (tenant_id, status, created_at DESC);

        CREATE TABLE agent.alert_state (
            alert_key           text NOT NULL CHECK (char_length(alert_key) BETWEEN 8 AND 200),
            tenant_id           uuid NOT NULL,
            rule                text NOT NULL,
            agent_principal_id  uuid,
            severity            text NOT NULL CHECK (severity IN ('P0','P1','P2')),
            state               text NOT NULL CHECK (state IN ('OPEN','ACKNOWLEDGED','ROUTED','RESOLVED')),
            first_seen          timestamptz NOT NULL,
            last_seen           timestamptz NOT NULL,
            occurrences         integer NOT NULL DEFAULT 1 CHECK (occurrences >= 1),
            sla_due             timestamptz NOT NULL,
            acknowledged_by     uuid,
            acknowledged_at     timestamptz,
            owner               uuid,
            route               text CHECK (route IN ('SECURITY','AUTHORITY','PRIVACY','FINANCE','SRE')),
            routed_at           timestamptz,
            note                text CHECK (char_length(note) <= 500),
            PRIMARY KEY (tenant_id, alert_key)
        );

        CREATE FUNCTION agent.review_proposal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.target_ref <> OLD.target_ref OR NEW.rule_id <> OLD.rule_id OR NEW.rule_version <> OLD.rule_version
               OR NEW.finding_code <> OLD.finding_code OR NEW.severity <> OLD.severity OR NEW.evidence_refs <> OLD.evidence_refs
               OR NEW.dedupe_key <> OLD.dedupe_key OR NEW.run_id <> OLD.run_id OR NEW.agent_principal_id <> OLD.agent_principal_id THEN
                RAISE EXCEPTION 'a review proposal keeps its finding: only a person decides on it' USING ERRCODE = '23514';
            END IF;
            IF OLD.status IN ('DISMISSED','HANDED_OFF') AND NEW.status <> OLD.status THEN
                RAISE EXCEPTION 'a decided proposal is final' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER review_proposal_guard BEFORE UPDATE ON agent.review_proposal
            FOR EACH ROW EXECUTE FUNCTION agent.review_proposal_guard();
        CREATE FUNCTION agent.daara_result_final() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'a run result is a record: it is not edited' USING ERRCODE = '23514';
        END $$;
        CREATE TRIGGER daara_result_final BEFORE UPDATE OR DELETE ON agent.daara_run_result
            FOR EACH ROW EXECUTE FUNCTION agent.daara_result_final();
    """)
    for t in TABLES:
        _run(f"""
            ALTER TABLE agent.{t} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE agent.{t} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON agent.{t} USING {RULE} WITH CHECK {RULE};
        """)


def downgrade() -> None:
    for t in reversed(TABLES):
        _run(f"DROP TABLE agent.{t} CASCADE;")
    _run("DROP FUNCTION agent.review_proposal_guard(); DROP FUNCTION agent.daara_result_final();")

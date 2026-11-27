"""Fraud graph: observations, typed edges, leads, notes, action proposals and the audit chain (ZID-CP-WF05 v2.0; CR-168).

Revision ID: verify_api_0014
Revises: verify_api_0013
Create Date: 2026-11-25

A company's own projection over its own sessions, never a second person store and never shared between companies. `api_fraud_observation` is a sourced fact (a tenant-keyed token or an outcome) with an expiry; an erased or expired one
loses its token. `api_fraud_edge` links two of the company's sessions through one kind of observation, with a strength, a method and a version; an excluded or retracted edge stays as history. `api_fraud_lead` is what a deterministic
rule made of a group of edges: it carries no finding until a person has adjudicated it in the review queue. `api_fraud_action` is an exact-target proposal: a database check refuses the same person as maker and approver, and the
runtime role cannot delete. `api_fraud_event` is the append-only chain (a trigger refuses any update), and it records who looked as well as who changed.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0014"
down_revision = "verify_api_0013"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
TABLES = (
    "api_fraud_observation",
    "api_fraud_edge",
    "api_fraud_lead",
    "api_fraud_note",
    "api_fraud_action",
    "api_fraud_action_target",
    "api_fraud_event",
)


def upgrade() -> None:
    policies = "\n".join(
        f"""
        ALTER TABLE verify.{t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.{t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.{t} USING ({TENANT}) WITH CHECK ({TENANT});
        """
        for t in TABLES
    )
    _run(f"""
        CREATE TABLE verify.api_fraud_observation (
            observation_id    text PRIMARY KEY CHECK (observation_id ~ '^obs_[0-9a-f]{{24}}$'),
            tenant_id         uuid NOT NULL,
            environment       text NOT NULL,
            region            text NOT NULL,
            purpose_code      text NOT NULL,
            session_id        text NOT NULL REFERENCES verify.api_session (session_id),
            kind              text NOT NULL,
            source            text NOT NULL,
            source_event_id   text NOT NULL,
            token             text,
            value             text,
            observed_at       timestamptz NOT NULL,
            ingested_at       timestamptz NOT NULL DEFAULT now(),
            expires_at        timestamptz NOT NULL,
            reliability       text NOT NULL DEFAULT 'COMPANY_SUPPLIED',
            classification    text NOT NULL DEFAULT 'STANDARD' CHECK (classification IN ('STANDARD', 'SENSITIVE')),
            digest            text NOT NULL,
            status            text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'QUARANTINED', 'ERASED', 'EXPIRED')),
            quarantine_reason text,
            UNIQUE (tenant_id, source, kind, source_event_id)
        );
        CREATE INDEX api_fraud_observation_token ON verify.api_fraud_observation (tenant_id, token) WHERE token IS NOT NULL AND status = 'ACTIVE';
        CREATE INDEX api_fraud_observation_session ON verify.api_fraud_observation (session_id);

        CREATE TABLE verify.api_fraud_edge (
            edge_id          text PRIMARY KEY CHECK (edge_id ~ '^edg_[0-9a-f]{{24}}$'),
            tenant_id        uuid NOT NULL,
            node_a           text NOT NULL REFERENCES verify.api_session (session_id),
            node_b           text NOT NULL REFERENCES verify.api_session (session_id),
            edge_type        text NOT NULL,
            strength         text NOT NULL CHECK (strength IN ('strong', 'corroborating', 'weak', 'similarity')),
            method           text NOT NULL,
            confidence_class text NOT NULL,
            rule_version     text NOT NULL,
            evidence         text[] NOT NULL,
            token_group      text,
            first_seen       timestamptz NOT NULL,
            last_seen        timestamptz NOT NULL,
            expires_at       timestamptz NOT NULL,
            status           text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'EXCLUDED', 'RETRACTED')),
            status_reason    text,
            changed_by       text,
            edge_version     integer NOT NULL DEFAULT 1 CHECK (edge_version >= 1),
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            CHECK (node_a < node_b),
            UNIQUE (tenant_id, node_a, node_b, edge_type)
        );
        CREATE INDEX api_fraud_edge_a ON verify.api_fraud_edge (tenant_id, node_a) WHERE status = 'ACTIVE';
        CREATE INDEX api_fraud_edge_b ON verify.api_fraud_edge (tenant_id, node_b) WHERE status = 'ACTIVE';
        CREATE INDEX api_fraud_edge_group ON verify.api_fraud_edge (tenant_id, token_group) WHERE token_group IS NOT NULL;

        CREATE TABLE verify.api_fraud_lead (
            lead_id        text PRIMARY KEY CHECK (lead_id ~ '^led_[0-9a-f]{{24}}$'),
            tenant_id      uuid NOT NULL,
            environment    text NOT NULL,
            rule_id        text NOT NULL,
            rule_version   text NOT NULL,
            group_key      text NOT NULL,
            risk_class     text NOT NULL CHECK (risk_class IN ('LOW', 'MODERATE', 'HIGH')),
            seed_session   text NOT NULL REFERENCES verify.api_session (session_id),
            nodes          text[] NOT NULL,
            edge_ids       text[] NOT NULL,
            snapshot_hash  text NOT NULL,
            uncertain      jsonb NOT NULL DEFAULT '[]',
            summary        jsonb NOT NULL DEFAULT '{{}}',
            status         text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'IN_ANALYSIS', 'ESCALATED', 'CLEARED', 'EXPIRED')),
            finding        text NOT NULL DEFAULT 'UNREVIEWED' CHECK (finding IN ('UNREVIEWED', 'CONFIRMED', 'NO_FINDING', 'INCONCLUSIVE')),
            finding_by     uuid,
            review_case_id text REFERENCES verify.api_review_case (case_id),
            lead_version   integer NOT NULL DEFAULT 1 CHECK (lead_version >= 1),
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            expires_at     timestamptz NOT NULL,
            UNIQUE (tenant_id, rule_id, group_key),
            CHECK (finding = 'UNREVIEWED' OR status = 'ESCALATED')
        );
        CREATE INDEX api_fraud_lead_list ON verify.api_fraud_lead (tenant_id, status, created_at DESC);

        CREATE TABLE verify.api_fraud_note (
            note_id     uuid PRIMARY KEY,
            tenant_id   uuid NOT NULL,
            lead_id     text NOT NULL REFERENCES verify.api_fraud_lead (lead_id),
            edge_id     text REFERENCES verify.api_fraud_edge (edge_id),
            kind        text NOT NULL CHECK (kind IN ('EXCULPATORY', 'NOTE')),
            body        text NOT NULL CHECK (char_length(body) >= 3),
            source_ref  text,
            author      uuid NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX api_fraud_note_lead ON verify.api_fraud_note (lead_id, created_at);

        CREATE TABLE verify.api_fraud_action (
            action_id         text PRIMARY KEY CHECK (action_id ~ '^act_[0-9a-f]{{24}}$'),
            tenant_id         uuid NOT NULL,
            lead_id           text NOT NULL REFERENCES verify.api_fraud_lead (lead_id),
            proposal_type     text NOT NULL,
            targets           text[] NOT NULL,
            basis_ref         text NOT NULL,
            impact_ref        text NOT NULL,
            impact            jsonb NOT NULL,
            snapshot_hash     text NOT NULL,
            manifest_checksum text NOT NULL,
            expires_at        timestamptz NOT NULL,
            status            text NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'APPROVED', 'REJECTED', 'EXPIRED', 'REVOKED')),
            maker             uuid NOT NULL,
            decided_by        uuid,
            decided_at        timestamptz,
            decision_note     text,
            idempotency_key   text NOT NULL,
            action_version    integer NOT NULL DEFAULT 1 CHECK (action_version >= 1),
            created_at        timestamptz NOT NULL DEFAULT now(),
            CHECK (decided_by IS NULL OR decided_by <> maker),
            UNIQUE (tenant_id, idempotency_key)
        );
        CREATE INDEX api_fraud_action_lead ON verify.api_fraud_action (tenant_id, lead_id, created_at DESC);

        CREATE TABLE verify.api_fraud_action_target (
            action_id   text NOT NULL REFERENCES verify.api_fraud_action (action_id),
            tenant_id   uuid NOT NULL,
            position    integer NOT NULL,
            target      text NOT NULL,
            strength    text NOT NULL,
            state       text NOT NULL DEFAULT 'PROPOSED' CHECK (state IN ('PROPOSED', 'APPROVED', 'REJECTED', 'EXPIRED', 'REVOKED')),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (action_id, position)
        );

        CREATE TABLE verify.api_fraud_event (
            scope_id     text NOT NULL,
            seq          integer NOT NULL CHECK (seq >= 1),
            tenant_id    uuid NOT NULL,
            event_type   text NOT NULL,
            actor        text NOT NULL,
            reason       text,
            data         jsonb NOT NULL DEFAULT '{{}}',
            prev_digest  text NOT NULL,
            digest       text NOT NULL,
            created_at   timestamptz NOT NULL,
            PRIMARY KEY (scope_id, seq)
        );
        CREATE FUNCTION verify.api_fraud_event_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'fraud graph events are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER api_fraud_event_no_update BEFORE UPDATE OR DELETE ON verify.api_fraud_event FOR EACH ROW EXECUTE FUNCTION verify.api_fraud_event_immutable();
        {policies}
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.api_fraud_event, verify.api_fraud_action_target, verify.api_fraud_action, verify.api_fraud_note,
            verify.api_fraud_lead, verify.api_fraud_edge, verify.api_fraud_observation;
        DROP FUNCTION verify.api_fraud_event_immutable();
    """)

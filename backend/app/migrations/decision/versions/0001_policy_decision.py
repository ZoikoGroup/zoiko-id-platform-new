"""Online decision record (ZID-DATA-001 §7.4; ZID-AUTHZ-001 §12 step 12, §24).

Revision ID: decision_0001
Revises:
Create Date: 2026-10-12

An operational trace of every decision, not a reusable grant. Durable material evidence (pseudonymous
DecisionReceipt, hash chain) is evidence.decision_receipt in Phase F. Differences from ZID-DATA-001
(CR-007): STATE_UNAVAILABLE outcome; evidence_class, authority_chain_commitment, input commitment.
Rows are append-only; R2/R3 rows must carry policy release and both revisions (AUTHZ §24, INV-15).
"""

from zid_platform.migrations import execute_script as _run

revision = "decision_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TYPE authz.decision_outcome AS ENUM ('ALLOW', 'DENY', 'STEP_UP', 'APPROVAL_REQUIRED', 'STATE_UNAVAILABLE');

        CREATE TABLE authz.policy_decision (
            decision_id                 uuid PRIMARY KEY,
            tenant_id                   uuid NOT NULL,
            subject_principal_id        uuid NOT NULL,
            represented_principal_id    uuid,
            action                      text NOT NULL,
            resource_ref                text NOT NULL,
            risk_class                  text NOT NULL CHECK (risk_class IN ('R0', 'R1', 'R2', 'R3')),
            evidence_class              text NOT NULL CHECK (evidence_class IN ('E0', 'E1', 'E2', 'E3')),
            outcome                     authz.decision_outcome NOT NULL,
            reason_codes                text[] NOT NULL,
            obligations                 jsonb,
            policy_release_id           uuid,
            relation_revision           bigint,
            security_revision           bigint,
            authority_chain_commitment  text,
            mandate_id                  uuid,
            input_commitment            text NOT NULL,
            decided_at                  timestamptz NOT NULL,
            expires_at                  timestamptz,
            CHECK (risk_class IN ('R0', 'R1') OR outcome <> 'ALLOW'
                   OR (policy_release_id IS NOT NULL AND relation_revision IS NOT NULL AND security_revision IS NOT NULL))
        );
        CREATE INDEX decision_subject_time ON authz.policy_decision (tenant_id, subject_principal_id, decided_at DESC);

        CREATE FUNCTION authz.policy_decision_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'decision records are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER policy_decision_append_only BEFORE UPDATE OR DELETE ON authz.policy_decision
            FOR EACH ROW EXECUTE FUNCTION authz.policy_decision_append_only();

        ALTER TABLE authz.policy_decision ENABLE ROW LEVEL SECURITY;
        ALTER TABLE authz.policy_decision FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON authz.policy_decision
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER policy_decision_append_only ON authz.policy_decision;
        DROP FUNCTION authz.policy_decision_append_only();
        DROP TABLE authz.policy_decision;
        DROP TYPE authz.decision_outcome;
    """)

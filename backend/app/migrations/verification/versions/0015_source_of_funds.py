"""Source of funds and wealth cases (ZID-GAP-001 v2.0 DTV-28; CR-096).

Revision ID: verification_0015
Revises: verification_0014
Create Date: 2026-11-16

sof_case: one person's answers to one questionnaire version, with REFERENCES to uploaded evidence (never the evidence). The case records the content hash of
the questionnaire it was answered against. The state is moved only by the service (a person decides; nothing scores). Answers and evidence references are
personal and sensitive financial information, so governed erasure empties them (the privacy adapter) and leaves the state and the decision record.

sof_decision: append-only record of every human decision and the person who made it. It holds reason CODES from a fixed list and no free text, so it carries
no personal data and can stay for the retention the compliance officer sets. The table repeats the rules: it cannot be changed, and a reviewer cannot be the
person who submitted the case (the check lives in the service, which knows who submitted; the table keeps the two ids so the rule can be audited).
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0015"
down_revision = "verification_0014"
branch_labels = None
depends_on = None

TENANT_TABLES = ("sof_case", "sof_decision")


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.sof_case (
            case_id               uuid PRIMARY KEY,
            tenant_id             uuid NOT NULL,
            subject_principal_id  uuid NOT NULL,
            country               text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            questionnaire_code    text NOT NULL,
            questionnaire_version text NOT NULL,
            questionnaire_hash    text NOT NULL CHECK (questionnaire_hash ~ '^[0-9a-f]{64}$'),
            state                 text NOT NULL DEFAULT 'DRAFT' CHECK (state IN (
                'DRAFT', 'INCOMPLETE', 'NEEDS_REVIEW', 'CLEARED', 'NOT_CLEARED_PENDING_APPEAL', 'APPEALED', 'NOT_CLEARED_FINAL', 'CANCELED')),
            answers               jsonb NOT NULL DEFAULT '{}'::jsonb,
            evidence              jsonb NOT NULL DEFAULT '[]'::jsonb,
            problems              text[] NOT NULL DEFAULT '{}',
            created_by            uuid NOT NULL,
            submitted_by          uuid,
            submitted_at          timestamptz,
            first_reviewer        uuid,
            valid_until           timestamptz,
            created_at            timestamptz NOT NULL DEFAULT now(),
            updated_at            timestamptz NOT NULL DEFAULT now(),
            erased_at             timestamptz,
            row_version           integer NOT NULL DEFAULT 1,
            CHECK ((state = 'CLEARED') = (valid_until IS NOT NULL)),
            CHECK (state = 'DRAFT' OR submitted_by IS NOT NULL OR erased_at IS NOT NULL)
        );
        CREATE INDEX sof_case_subject ON verify.sof_case (tenant_id, subject_principal_id, created_at DESC);
        CREATE INDEX sof_case_queue ON verify.sof_case (tenant_id, state, submitted_at);

        CREATE TABLE verify.sof_decision (
            decision_id   uuid PRIMARY KEY,
            case_id       uuid NOT NULL REFERENCES verify.sof_case (case_id),
            tenant_id     uuid NOT NULL,
            decision      text NOT NULL CHECK (decision IN ('CLEAR', 'NOT_CLEAR', 'ASK_FOR_MORE', 'APPEAL_FILED')),
            from_state    text NOT NULL,
            to_state      text NOT NULL,
            reason_codes  text[] NOT NULL DEFAULT '{}',
            decided_by    uuid NOT NULL,
            decided_at    timestamptz NOT NULL DEFAULT now(),
            CHECK (decision = 'APPEAL_FILED' OR cardinality(reason_codes) > 0)
        );
        CREATE INDEX sof_decision_case ON verify.sof_decision (case_id, decided_at);

        CREATE FUNCTION verify.sof_decision_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'source of funds decisions are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER sof_decision_append_only BEFORE UPDATE OR DELETE ON verify.sof_decision
            FOR EACH ROW EXECUTE FUNCTION verify.sof_decision_append_only();
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE verify.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE verify.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON verify.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.sof_decision;
        DROP FUNCTION verify.sof_decision_append_only();
        DROP TABLE verify.sof_case;
    """)

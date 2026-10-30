"""Company verification cases and their signed evidence (ZID-ED-03 Global Company Verification; CR-043).

Revision ID: organization_0005
Revises: organization_0004
Create Date: 2026-10-30

kyb_case: one request to verify one legal entity for a tenant; its status is the latest evidence's decision.
kyb_evidence: append-only. Every decision, refresh and reviewer ruling is a new signed row with its sources,
hashes, policy version, reasons and expiry; a row is never edited or deleted, so history is never rewritten.
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0005"
down_revision = "organization_0004"
branch_labels = None
depends_on = None

TABLES = ("kyb_case", "kyb_evidence")
_RLS = """
    ALTER TABLE org.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE org.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON org.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""


def upgrade() -> None:
    _run("""
        CREATE TABLE org.kyb_case (
            case_id             uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            jurisdiction        text NOT NULL CHECK (jurisdiction ~ '^[A-Z]{2}$'),
            registration_number text NOT NULL CHECK (length(registration_number) BETWEEN 1 AND 40),
            applicant_name      text NOT NULL CHECK (length(applicant_name) BETWEEN 1 AND 300),
            entity_form         text,
            register_id         text,
            lei                 text,
            idempotency_key     text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
            status              text NOT NULL CHECK (status IN ('NOT_STARTED', 'IN_PROGRESS', 'VERIFIED',
                                'NEEDS_REVIEW', 'REJECTED', 'UNAVAILABLE', 'EXPIRED')),
            review_reason       text,
            requested_by        uuid NOT NULL,
            current_version     integer NOT NULL DEFAULT 0,
            expiry_at           timestamptz,
            created_at          timestamptz NOT NULL,
            updated_at          timestamptz NOT NULL,
            UNIQUE (tenant_id, idempotency_key)
        );
        CREATE INDEX kyb_case_entity ON org.kyb_case (tenant_id, jurisdiction, registration_number);

        CREATE TABLE org.kyb_evidence (
            evidence_id     uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            case_id         uuid NOT NULL REFERENCES org.kyb_case (case_id),
            version         integer NOT NULL CHECK (version >= 1),
            decision_status text NOT NULL CHECK (decision_status IN ('VERIFIED', 'NEEDS_REVIEW', 'REJECTED',
                            'UNAVAILABLE')),
            review_reason   text,
            reasons         jsonb NOT NULL,
            body            jsonb NOT NULL,
            evidence_hash   text NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
            jws             text NOT NULL,
            policy_version  text NOT NULL,
            decided_by      uuid,
            rationale       text,
            observed_at     timestamptz NOT NULL,
            expiry_at       timestamptz,
            created_at      timestamptz NOT NULL,
            UNIQUE (case_id, version)
        );
        CREATE FUNCTION org.kyb_evidence_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'kyb evidence is append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER kyb_evidence_append_only BEFORE UPDATE OR DELETE ON org.kyb_evidence
            FOR EACH ROW EXECUTE FUNCTION org.kyb_evidence_guard();
    """)
    for table in TABLES:
        _run(_RLS.format(t=table))


def downgrade() -> None:
    _run(
        "DROP TABLE org.kyb_evidence; DROP FUNCTION org.kyb_evidence_guard(); DROP TABLE org.kyb_case;"
    )

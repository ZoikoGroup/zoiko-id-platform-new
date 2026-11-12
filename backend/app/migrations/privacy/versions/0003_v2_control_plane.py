"""/v2 privacy control plane (ZID-PRI-004 sections 07 to 23): decisions, notice acknowledgments, erasure jobs with provider receipts,
transfer decisions, subprocessor changes, DPIA packages and release gates, decision reviews and breach cases.

Revision ID: privacy_0003
Revises: privacy_0002
Create Date: 2026-11-11

Append-only records (a trigger refuses UPDATE and DELETE): purpose_decision, notice_acknowledgment, transfer_decision.
State-machine records: erasure_job and erasure_task (a job is COMPLETE only when every task is acknowledged), subprocessor_change,
dpia_package, decision_review and breach_case. Everything is tenant-scoped with row-level security, like the rest of the schema.
`privacy.processor.receipt_public_key` is the Ed25519 key a provider signs its deletion receipts with.
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0003"
down_revision = "privacy_0002"
branch_labels = None
depends_on = None

TABLES = (
    "purpose_decision",
    "notice_acknowledgment",
    "erasure_job",
    "erasure_task",
    "transfer_decision",
    "subprocessor_change",
    "dpia_package",
    "decision_review",
    "breach_case",
)


def upgrade() -> None:
    _run("""
        ALTER TABLE privacy.processor ADD COLUMN receipt_public_key text;

        CREATE TABLE privacy.purpose_decision (
            decision_id              uuid PRIMARY KEY,
            tenant_id                uuid NOT NULL,
            subject_ref              text,
            purpose_code             text NOT NULL,
            activity_code            text NOT NULL,
            data_category            text NOT NULL,
            country                  text,
            subregion                text,
            allowed                  boolean NOT NULL,
            reason_codes             text[] NOT NULL,
            alternative_modes        text[] NOT NULL DEFAULT '{}',
            notice_version           text,
            provider_service_ref     text,
            effective_policy_version text NOT NULL,
            requested_by             uuid,
            evaluated_at             timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE privacy.notice_acknowledgment (
            ack_id          uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            session_ref     text NOT NULL,
            subject_ref     text,
            notice_code     text NOT NULL,
            notice_version  integer NOT NULL,
            language        text NOT NULL,
            acknowledged_by uuid,
            acknowledged_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, session_ref, notice_code, notice_version)
        );

        CREATE TABLE privacy.erasure_job (
            job_id         uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            subject_ref    text NOT NULL,
            scope          text NOT NULL CHECK (scope IN ('ALL', 'PROVIDERS_ONLY', 'FIRST_PARTY_ONLY')),
            reason         text NOT NULL,
            legal_basis    text NOT NULL,
            state          text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING', 'PARTIAL', 'COMPLETE', 'FAILED', 'HELD')),
            held_reason    text,
            requested_by   uuid NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            completed_at   timestamptz,
            CHECK ((state = 'COMPLETE') = (completed_at IS NOT NULL))
        );

        CREATE TABLE privacy.erasure_task (
            task_id        uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            job_id         uuid NOT NULL REFERENCES privacy.erasure_job (job_id),
            target_kind    text NOT NULL CHECK (target_kind IN ('PROVIDER', 'FIRST_PARTY_STORE', 'BACKUP')),
            target_ref     text NOT NULL,
            state          text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING', 'ACKNOWLEDGED', 'FAILED', 'HELD')),
            receipt_hash   text,
            object_count   integer,
            failure        text,
            due_at         timestamptz,
            updated_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (job_id, target_kind, target_ref),
            CHECK ((state = 'ACKNOWLEDGED') = (receipt_hash IS NOT NULL))
        );

        CREATE TABLE privacy.transfer_decision (
            decision_id           uuid PRIMARY KEY,
            tenant_id             uuid NOT NULL,
            processor_id          text NOT NULL,
            purpose_code          text NOT NULL,
            data_category         text NOT NULL,
            origin_country        text NOT NULL,
            destination_country   text NOT NULL,
            remote_access_countries text[] NOT NULL DEFAULT '{}',
            mechanism             text,
            allowed               boolean NOT NULL,
            reason_codes          text[] NOT NULL,
            policy_snapshot       jsonb NOT NULL,
            requested_by          uuid,
            evaluated_at          timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE privacy.subprocessor_change (
            change_id      uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            processor_id   text NOT NULL,
            kind           text NOT NULL CHECK (kind IN ('SUBPROCESSOR_ADDED', 'SUBPROCESSOR_REMOVED', 'LOCATION_ADDED', 'LOCATION_REMOVED')),
            detail         text NOT NULL,
            state          text NOT NULL DEFAULT 'PENDING_APPROVAL' CHECK (state IN ('PENDING_APPROVAL', 'APPROVED', 'REJECTED')),
            notified_at    timestamptz NOT NULL,
            requested_by   uuid NOT NULL,
            decided_by     uuid,
            decided_at     timestamptz,
            created_at     timestamptz NOT NULL DEFAULT now(),
            CHECK (state = 'PENDING_APPROVAL' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
            CHECK (decided_by IS NULL OR decided_by <> requested_by)
        );

        CREATE TABLE privacy.dpia_package (
            package_id          uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            market              text NOT NULL,
            product             text NOT NULL,
            version             text NOT NULL,
            controller          text NOT NULL,
            purpose_code        text NOT NULL,
            provider_service_ref text,
            model_version       text,
            risks               jsonb NOT NULL DEFAULT '[]',
            gates               jsonb NOT NULL DEFAULT '{}',
            consultation_ref    text,
            state               text NOT NULL DEFAULT 'DRAFT' CHECK (state IN ('DRAFT', 'IN_REVIEW', 'APPROVED', 'REJECTED', 'SUPERSEDED')),
            approved_until      timestamptz,
            decision_rationale  text,
            decided_by          uuid,
            created_by          uuid NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, market, product, version),
            CHECK (state NOT IN ('APPROVED', 'REJECTED') OR (decided_by IS NOT NULL AND decision_rationale IS NOT NULL)),
            CHECK (decided_by IS NULL OR decided_by <> created_by),
            CHECK (state <> 'APPROVED' OR approved_until IS NOT NULL)
        );

        CREATE TABLE privacy.decision_review (
            review_id      uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            decision_ref   text NOT NULL,
            kind           text NOT NULL CHECK (kind IN ('APPEAL', 'HUMAN_REVIEW')),
            subject_ref    text NOT NULL,
            statement      text NOT NULL CHECK (char_length(statement) BETWEEN 1 AND 2000),
            state          text NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'UPHELD', 'OVERTURNED', 'CORRECTED')),
            requested_by   uuid NOT NULL,
            reviewer       uuid,
            rationale      text,
            correction_ref text,
            created_at     timestamptz NOT NULL DEFAULT now(),
            due_at         timestamptz NOT NULL,
            decided_at     timestamptz,
            CHECK (state = 'OPEN' OR (reviewer IS NOT NULL AND rationale IS NOT NULL AND decided_at IS NOT NULL)),
            CHECK (reviewer IS NULL OR reviewer <> requested_by)
        );

        CREATE TABLE privacy.breach_case (
            breach_id       uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            summary         text NOT NULL,
            severity        text NOT NULL CHECK (severity IN ('LOW', 'MODERATE', 'HIGH', 'CRITICAL')),
            jurisdictions   text[] NOT NULL,
            subjects_estimate integer CHECK (subjects_estimate >= 0),
            detected_at     timestamptz NOT NULL,
            state           text NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'CONTAINED', 'CLOSED')),
            notifications   jsonb NOT NULL DEFAULT '[]',
            opened_by       uuid NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            closed_at       timestamptz,
            CHECK ((state = 'CLOSED') = (closed_at IS NOT NULL))
        );

        CREATE FUNCTION privacy.append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
        END;
        $$;
        CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON privacy.purpose_decision
            FOR EACH ROW EXECUTE FUNCTION privacy.append_only();
        CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON privacy.notice_acknowledgment
            FOR EACH ROW EXECUTE FUNCTION privacy.append_only();
        CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON privacy.transfer_decision
            FOR EACH ROW EXECUTE FUNCTION privacy.append_only();
    """)
    for table in TABLES:
        _run(f"""
            ALTER TABLE privacy.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE privacy.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON privacy.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE privacy.breach_case;
        DROP TABLE privacy.decision_review;
        DROP TABLE privacy.dpia_package;
        DROP TABLE privacy.subprocessor_change;
        DROP TABLE privacy.transfer_decision;
        DROP TABLE privacy.erasure_task;
        DROP TABLE privacy.erasure_job;
        DROP TABLE privacy.notice_acknowledgment;
        DROP TABLE privacy.purpose_decision;
        DROP FUNCTION privacy.append_only();
        ALTER TABLE privacy.processor DROP COLUMN receipt_public_key;
    """)

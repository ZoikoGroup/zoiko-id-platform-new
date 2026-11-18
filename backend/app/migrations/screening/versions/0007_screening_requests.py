"""The WF06 screening case: four result dimensions, restricted candidates, an append-only decision chain (ZID-DTV-WF-06 S01 to S09, section 09; CR-113).

Revision ID: screening_0007
Revises: screening_0006
Create Date: 2026-11-18

screening_request: one screening of one subject for one purpose in one market, tenant scoped. The subject's details are restricted (screening_request_subject): a reviewer sees them, no
event, audit record, log or customer view does. screening_request_source holds what each source said and which exact snapshot it was; screening_request_candidate holds what matched,
which is a candidate and never a finding; screening_request_decision is the append-only chain of dispositions, one revision after another, each citing the one before. screening_request_proposal holds a decision one person proposed and a second has not yet finalised.

The request table also lets a screening reviewer READ across tenants when the session says so (app.screening_reviewer), so the restricted queue can list cases. Everything else, and every
write, is under the case's own tenant.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0007"
down_revision = "screening_0006"
branch_labels = None
depends_on = None

_RLS = """
    ALTER TABLE verify.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE verify.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON verify.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""
TABLES = (
    "screening_request",
    "screening_request_subject",
    "screening_request_source",
    "screening_request_candidate",
    "screening_request_decision",
    "screening_request_proposal",
)


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.screening_request (
            request_id       uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            market           text NOT NULL CHECK (market ~ '^[A-Z]{2}$'),
            region           text NOT NULL CHECK (length(region) BETWEEN 1 AND 64),
            purpose          text NOT NULL CHECK (length(purpose) BETWEEN 1 AND 64),
            subject_type     text NOT NULL CHECK (subject_type IN ('INDIVIDUAL', 'BUSINESS', 'UBO', 'REPRESENTATIVE')),
            subject_ref      text NOT NULL CHECK (length(subject_ref) BETWEEN 1 AND 128),
            idempotency_key  text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
            policy_version   text NOT NULL,
            classes          jsonb NOT NULL,
            state            text NOT NULL CHECK (state IN ('NO_CANDIDATE', 'REVIEW_REQUIRED', 'SOURCE_UNAVAILABLE',
                             'CLOSED_CLEAR_FOR_SCOPE', 'CLOSED_RESTRICTED', 'CLOSED_UNRESOLVED')),
            created_by       uuid NOT NULL,
            created_at       timestamptz NOT NULL,
            screened_at      timestamptz NOT NULL,
            revision         integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
            UNIQUE (tenant_id, idempotency_key)
        );
        CREATE INDEX screening_request_queue ON verify.screening_request (state, created_at);

        CREATE TABLE verify.screening_request_subject (
            request_id         uuid PRIMARY KEY REFERENCES verify.screening_request (request_id),
            tenant_id          uuid NOT NULL,
            kind               text NOT NULL CHECK (kind IN ('PERSON', 'ENTITY', 'OTHER')),
            name               text NOT NULL CHECK (length(name) BETWEEN 1 AND 500),
            birth              text,
            countries          jsonb NOT NULL DEFAULT '[]',
            identifiers        jsonb NOT NULL DEFAULT '[]',
            expired_identifiers jsonb NOT NULL DEFAULT '[]',
            identity_source    text NOT NULL,
            identity_assured_at timestamptz NOT NULL
        );

        CREATE TABLE verify.screening_request_source (
            request_id         uuid NOT NULL REFERENCES verify.screening_request (request_id),
            tenant_id          uuid NOT NULL,
            source_id          text NOT NULL,
            klass              text NOT NULL CHECK (klass IN ('SANCTIONS', 'PEP_RCA', 'ADVERSE_MEDIA', 'OWNERSHIP_CONTROL')),
            tier               text NOT NULL CHECK (tier IN ('OFFICIAL', 'LICENSED')),
            snapshot           text,
            screened_at        timestamptz,
            available          boolean NOT NULL,
            candidates         integer NOT NULL DEFAULT 0 CHECK (candidates >= 0),
            last_validated_at  timestamptz,
            PRIMARY KEY (request_id, source_id)
        );

        CREATE TABLE verify.screening_request_candidate (
            candidate_id   uuid PRIMARY KEY,
            request_id     uuid NOT NULL REFERENCES verify.screening_request (request_id),
            tenant_id      uuid NOT NULL,
            list_id        text NOT NULL,
            list_version   text NOT NULL,
            source_ref     text NOT NULL,
            matched_name   text NOT NULL,
            score          numeric(5, 4) NOT NULL,
            strength       text NOT NULL CHECK (strength IN ('STRONG', 'POSSIBLE')),
            reasons        jsonb NOT NULL,
            programs       jsonb NOT NULL DEFAULT '[]',
            UNIQUE (request_id, list_id, source_ref)
        );

        CREATE TABLE verify.screening_request_decision (
            request_id    uuid NOT NULL REFERENCES verify.screening_request (request_id),
            tenant_id     uuid NOT NULL,
            revision      integer NOT NULL CHECK (revision >= 1),
            body          jsonb NOT NULL,
            case_hash     text NOT NULL CHECK (case_hash ~ '^[0-9a-f]{64}$'),
            decided_at    timestamptz NOT NULL,
            PRIMARY KEY (request_id, revision)
        );
        CREATE TABLE verify.screening_request_proposal (
            proposal_id      uuid PRIMARY KEY,
            request_id       uuid NOT NULL REFERENCES verify.screening_request (request_id),
            tenant_id        uuid NOT NULL,
            maker            text NOT NULL,
            maker_role       text NOT NULL CHECK (maker_role IN ('analyst', 'senior', 'legal')),
            body             jsonb NOT NULL,
            state            text NOT NULL CHECK (state IN ('PENDING', 'FINALISED', 'WITHDRAWN')),
            created_at       timestamptz NOT NULL,
            closed_at        timestamptz
        );
        CREATE INDEX screening_request_proposal_open ON verify.screening_request_proposal (request_id) WHERE state = 'PENDING';
        CREATE TRIGGER screening_request_decision_append_only BEFORE UPDATE OR DELETE ON verify.screening_request_decision
            FOR EACH ROW EXECUTE FUNCTION verify.screening_append_only();
    """)
    for table in TABLES:
        _run(_RLS.format(t=table))
    _run("""
        CREATE POLICY reviewer_read ON verify.screening_request FOR SELECT
            USING (current_setting('app.screening_reviewer', true) = 'on');
        CREATE POLICY reviewer_read ON verify.screening_request_proposal FOR SELECT
            USING (current_setting('app.screening_reviewer', true) = 'on');
        CREATE POLICY reviewer_read ON verify.screening_request_candidate FOR SELECT
            USING (current_setting('app.screening_reviewer', true) = 'on');
    """)


def downgrade() -> None:
    _run(
        "DROP TABLE verify.screening_request_decision, verify.screening_request_candidate, verify.screening_request_source, "
        "verify.screening_request_subject, verify.screening_request;"
    )

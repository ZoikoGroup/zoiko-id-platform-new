"""Global coverage and deterministic provider routing (ZID-CP-WF09 v2.0; CR-171).

Revision ID: verification_0037
Revises: verification_0036
Create Date: 2026-11-25

Five append-only tables. A change is a new row, never an edit, so the past is always what it was:

route_capability        platform configuration: one provider's capability for one method, versioned. APPROVED needs a second person and its dates and legal
                        determination (the database checks it too). No row-level security, like the other platform registries.
route_decision          tenant data: the frozen result of one resolution (inputs and snapshot digests, the reason-coded decision), one per idempotency key.
route_attempt_event     tenant data: every state change of every provider attempt. The first event of an idempotency key is unique, so a retry never makes
                        a second attempt. The current state is the latest event.
route_release           platform configuration: a route release, versioned (DRAFT, PREFLIGHT_PASSED, APPROVED, LIVE, ROLLED_BACK).
route_release_approval  one signature per approver and release, bound to the dependency digest that was approved.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0037"
down_revision = "verification_0036"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
TABLES = (
    "route_capability",
    "route_decision",
    "route_attempt_event",
    "route_release",
    "route_release_approval",
)


def upgrade() -> None:
    _run(f"""
        CREATE FUNCTION verify.route_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'verify.% is append-only (% refused)', TG_TABLE_NAME, TG_OP USING ERRCODE = 'restrict_violation';
        END $$;

        CREATE TABLE verify.route_capability (
            provider_id        text    NOT NULL CHECK (provider_id ~ '^[a-z0-9][a-z0-9_.-]{{1,62}}$'),
            method             text    NOT NULL CHECK (method IN ('DOCUMENT', 'DOCUMENT_FACE', 'NFC_CHIP', 'EID', 'REGISTRY')),
            version            integer NOT NULL CHECK (version >= 1),
            state              text    NOT NULL CHECK (state IN ('DRAFT', 'UNDER_REVIEW', 'APPROVED', 'SUSPENDED', 'EXPIRED', 'RETIRED')),
            manifest           jsonb   NOT NULL,
            manifest_digest    text    NOT NULL CHECK (manifest_digest ~ '^sha-256:[0-9a-f]{{64}}$'),
            qualified_until    timestamptz,
            contract_until     timestamptz,
            legal_determination_ref         text,
            legal_determination_valid_until timestamptz,
            proposed_by        text    NOT NULL CHECK (length(btrim(proposed_by)) BETWEEN 1 AND 128),
            approved_by        text,
            reason             text    CHECK (reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500),
            recorded_at        timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (provider_id, method, version),
            CONSTRAINT route_capability_approved_complete CHECK (
                state <> 'APPROVED' OR (
                    approved_by IS NOT NULL AND approved_by <> proposed_by
                    AND qualified_until IS NOT NULL AND contract_until IS NOT NULL
                    AND legal_determination_ref IS NOT NULL AND legal_determination_valid_until IS NOT NULL
                )
            )
        );

        CREATE TABLE verify.route_decision (
            decision_id       uuid PRIMARY KEY,
            tenant_id         uuid NOT NULL,
            session_id        text NOT NULL CHECK (length(session_id) BETWEEN 1 AND 128),
            idempotency_key   text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 200),
            route_version     text NOT NULL,
            algorithm_version text NOT NULL,
            input_digest      text NOT NULL,
            snapshot_digest   text NOT NULL,
            decision_digest   text NOT NULL,
            selected          text,
            status            text NOT NULL CHECK (status IN ('ROUTE_SELECTED', 'NO_ROUTE')),
            body              jsonb NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, idempotency_key),
            CHECK ((status = 'ROUTE_SELECTED') = (selected IS NOT NULL))
        );
        CREATE INDEX route_decision_session ON verify.route_decision (tenant_id, session_id, created_at DESC);

        CREATE TABLE verify.route_attempt_event (
            event_id              uuid PRIMARY KEY,
            tenant_id             uuid NOT NULL,
            attempt_id            uuid NOT NULL,
            seq                   integer NOT NULL CHECK (seq >= 1),
            decision_id           uuid NOT NULL,
            session_id            text NOT NULL,
            provider_id           text NOT NULL,
            idempotency_key       text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 200),
            external_operation_id text,
            from_state            text,
            to_state              text NOT NULL,
            actor                 text NOT NULL CHECK (actor IN ('SYSTEM', 'PROVIDER', 'VERIFIER', 'OPERATOR')),
            reason                text CHECK (reason IS NULL OR length(reason) <= 300),
            expected_cost_minor   integer CHECK (expected_cost_minor IS NULL OR expected_cost_minor >= 0),
            currency              text CHECK (currency IS NULL OR currency ~ '^[A-Z]{{3}}$'),
            created_at            timestamptz NOT NULL DEFAULT now(),
            UNIQUE (attempt_id, seq),
            CHECK ((seq = 1) = (from_state IS NULL))
        );
        -- the first event of an idempotency key is unique: a repeated request returns the same attempt and never starts another
        CREATE UNIQUE INDEX route_attempt_first_event ON verify.route_attempt_event (tenant_id, session_id, provider_id, idempotency_key) WHERE seq = 1;
        CREATE INDEX route_attempt_decision ON verify.route_attempt_event (tenant_id, decision_id);

        CREATE TABLE verify.route_release (
            release_id        uuid NOT NULL,
            version           integer NOT NULL CHECK (version >= 1),
            state             text NOT NULL CHECK (state IN ('DRAFT', 'PREFLIGHT_PASSED', 'APPROVED', 'LIVE', 'ROLLED_BACK')),
            maker             text NOT NULL CHECK (length(btrim(maker)) BETWEEN 1 AND 128),
            risk              text NOT NULL CHECK (risk IN ('LOW', 'HIGH')),
            dependency_digest text NOT NULL CHECK (dependency_digest ~ '^sha-256:[0-9a-f]{{64}}$'),
            dependencies      jsonb NOT NULL,
            scope             jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            preflight         jsonb,
            reason            text CHECK (reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500),
            recorded_by       text NOT NULL,
            recorded_at       timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (release_id, version),
            CONSTRAINT route_release_live_after_preflight CHECK (state NOT IN ('PREFLIGHT_PASSED', 'APPROVED', 'LIVE') OR preflight IS NOT NULL)
        );
        CREATE TABLE verify.route_release_approval (
            release_id        uuid NOT NULL,
            approver          text NOT NULL CHECK (length(btrim(approver)) BETWEEN 1 AND 128),
            role              text NOT NULL CHECK (role IN ('PRODUCT', 'SECURITY', 'PRIVACY', 'LEGAL')),
            dependency_digest text NOT NULL CHECK (dependency_digest ~ '^sha-256:[0-9a-f]{{64}}$'),
            recorded_at       timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (release_id, approver)
        );

        ALTER TABLE verify.route_decision ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.route_decision FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.route_decision USING {RULE} WITH CHECK {RULE};
        ALTER TABLE verify.route_attempt_event ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.route_attempt_event FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.route_attempt_event USING {RULE} WITH CHECK {RULE};
    """)
    for t in TABLES:
        _run(f"""
            CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON verify.{t} FOR EACH ROW EXECUTE FUNCTION verify.route_append_only();
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.route_release_approval;
        DROP TABLE verify.route_release;
        DROP TABLE verify.route_attempt_event;
        DROP TABLE verify.route_decision;
        DROP TABLE verify.route_capability;
        DROP FUNCTION verify.route_append_only();
    """)

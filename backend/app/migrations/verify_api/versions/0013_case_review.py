"""Case review and four-eyes control (ZID-CP-WF04 v2.0; CR-167).

Revision ID: verify_api_0013
Revises: verify_api_0012
Create Date: 2026-11-25

`api_review_case` is one session's review (or an appeal against a final one). Every change bumps `case_version`, and a change must name the version it read. `api_review_lease` is the claim on a case: one live lease per case,
enforced by a partial unique index, so two reviewers cannot both be working it. `api_review_evidence` is the case's evidence manifest (redacted by class); `api_review_reveal` records each time a sensitive item was shown, with a
reason and an expiry. `api_review_proposal` and `api_review_second` are the first and the independent second review; a database check refuses the same principal in both. `api_review_event` is the append-only chain: a trigger
refuses any update, and the runtime role cannot delete. `api_review_hold` is a regulatory hold, placed and released only by the operator plane. `api_review_advice` is what an AI adviser said (off by default), never acted on.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0013"
down_revision = "verify_api_0012"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
SCOPE = "current_setting('app.verify_api_scope', true)"
STATES = "'QUEUED','CLAIMED','INVESTIGATING','AWAITING_APPLICANT','ESCALATED','SPECIALIST_REVIEW','AWAITING_SECOND_REVIEW','RETURNED','APPROVED_FINAL'"
TABLES = (
    "api_review_case",
    "api_review_lease",
    "api_review_evidence",
    "api_review_reveal",
    "api_review_proposal",
    "api_review_second",
    "api_review_event",
    "api_review_hold",
    "api_review_advice",
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
        CREATE TABLE verify.api_review_case (
            case_id             text PRIMARY KEY CHECK (case_id ~ '^cas_[0-9a-f]{{32}}$'),
            tenant_id           uuid NOT NULL,
            session_id          text NOT NULL REFERENCES verify.api_session (session_id),
            case_type           text NOT NULL CHECK (case_type IN ('REVIEW', 'APPEAL')),
            parent_case_id      text REFERENCES verify.api_review_case (case_id),
            status              text NOT NULL CHECK (status IN ({STATES})),
            case_version        integer NOT NULL DEFAULT 1 CHECK (case_version >= 1),
            queue               text NOT NULL DEFAULT 'STANDARD' CHECK (queue IN ('STANDARD', 'SPECIALIST')),
            risk_class          text NOT NULL DEFAULT 'STANDARD',
            four_eyes_required  boolean NOT NULL DEFAULT true,
            required_checks     text[] NOT NULL,
            findings            jsonb NOT NULL DEFAULT '{{}}',
            policy_version      text NOT NULL,
            region              text NOT NULL,
            first_reviewer      uuid,
            final_outcome       text,
            final_decision_id   text,
            created_by          uuid NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            sla_due_at          timestamptz NOT NULL,
            updated_at          timestamptz NOT NULL DEFAULT now(),
            CHECK ((case_type = 'APPEAL') = (parent_case_id IS NOT NULL)),
            CHECK ((status = 'APPROVED_FINAL') = (final_outcome IS NOT NULL))
        );
        CREATE INDEX api_review_case_queue ON verify.api_review_case (tenant_id, status, sla_due_at);
        CREATE UNIQUE INDEX api_review_case_one_open ON verify.api_review_case (session_id, case_type) WHERE status <> 'APPROVED_FINAL';

        CREATE TABLE verify.api_review_lease (
            lease_id     uuid PRIMARY KEY,
            case_id      text NOT NULL REFERENCES verify.api_review_case (case_id),
            tenant_id    uuid NOT NULL,
            reviewer     uuid NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            expires_at   timestamptz NOT NULL,
            released_at  timestamptz
        );
        CREATE UNIQUE INDEX api_review_lease_one_live ON verify.api_review_lease (case_id) WHERE released_at IS NULL;

        CREATE TABLE verify.api_review_evidence (
            evidence_id     text PRIMARY KEY CHECK (evidence_id ~ '^ev_[0-9a-f]{{24}}$'),
            case_id         text NOT NULL REFERENCES verify.api_review_case (case_id),
            tenant_id       uuid NOT NULL,
            kind            text NOT NULL,
            label           text NOT NULL,
            classification  text NOT NULL CHECK (classification IN ('STANDARD', 'SENSITIVE', 'RESTRICTED')),
            content_hash    text NOT NULL,
            summary         jsonb NOT NULL DEFAULT '{{}}',
            detail          jsonb NOT NULL DEFAULT '{{}}',
            created_at      timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX api_review_evidence_case ON verify.api_review_evidence (case_id);

        CREATE TABLE verify.api_review_reveal (
            reveal_id    uuid PRIMARY KEY,
            case_id      text NOT NULL REFERENCES verify.api_review_case (case_id),
            tenant_id    uuid NOT NULL,
            evidence_id  text NOT NULL REFERENCES verify.api_review_evidence (evidence_id),
            reviewer     uuid NOT NULL,
            reason       text NOT NULL CHECK (char_length(reason) >= 3),
            created_at   timestamptz NOT NULL DEFAULT now(),
            expires_at   timestamptz NOT NULL
        );
        CREATE INDEX api_review_reveal_case ON verify.api_review_reveal (case_id);

        CREATE TABLE verify.api_review_proposal (
            proposal_id            uuid PRIMARY KEY,
            case_id                text NOT NULL REFERENCES verify.api_review_case (case_id),
            tenant_id              uuid NOT NULL,
            case_version           integer NOT NULL,
            reviewer               uuid NOT NULL,
            outcome                text NOT NULL CHECK (outcome IN ('verified', 'not_verified', 'inconclusive', 'request_information')),
            reason_code            text NOT NULL,
            evidence_manifest_hash text NOT NULL,
            policy_version         text NOT NULL,
            status                 text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'APPROVED', 'RETURNED', 'SUPERSEDED')),
            created_at             timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX api_review_proposal_case ON verify.api_review_proposal (case_id, created_at DESC);

        CREATE TABLE verify.api_review_second (
            approval_id       uuid PRIMARY KEY,
            proposal_id       uuid NOT NULL REFERENCES verify.api_review_proposal (proposal_id),
            case_id           text NOT NULL REFERENCES verify.api_review_case (case_id),
            tenant_id         uuid NOT NULL,
            reviewer          uuid NOT NULL,
            first_reviewer    uuid NOT NULL,
            decision          text NOT NULL CHECK (decision IN ('approve', 'return', 'escalate')),
            challenge_answers jsonb NOT NULL DEFAULT '{{}}',
            reason_code       text,
            note              text,
            signed_at         timestamptz NOT NULL DEFAULT now(),
            CHECK (reviewer <> first_reviewer)
        );

        CREATE TABLE verify.api_review_event (
            case_id      text NOT NULL REFERENCES verify.api_review_case (case_id),
            seq          integer NOT NULL CHECK (seq >= 1),
            tenant_id    uuid NOT NULL,
            event_type   text NOT NULL,
            actor        text NOT NULL,
            reason       text,
            data         jsonb NOT NULL DEFAULT '{{}}',
            prev_digest  text NOT NULL,
            digest       text NOT NULL,
            created_at   timestamptz NOT NULL,
            PRIMARY KEY (case_id, seq)
        );
        CREATE FUNCTION verify.api_review_event_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'review events are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER api_review_event_no_update BEFORE UPDATE OR DELETE ON verify.api_review_event FOR EACH ROW EXECUTE FUNCTION verify.api_review_event_immutable();

        CREATE TABLE verify.api_review_hold (
            hold_id      uuid PRIMARY KEY,
            case_id      text NOT NULL REFERENCES verify.api_review_case (case_id),
            tenant_id    uuid NOT NULL,
            reason_code  text NOT NULL,
            status       text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RELEASED')),
            placed_by    uuid NOT NULL,
            placed_at    timestamptz NOT NULL DEFAULT now(),
            released_by  uuid,
            released_at  timestamptz,
            note         text,
            CHECK ((status = 'RELEASED') = (released_by IS NOT NULL))
        );
        CREATE UNIQUE INDEX api_review_hold_one_active ON verify.api_review_hold (case_id) WHERE status = 'ACTIVE';

        CREATE TABLE verify.api_review_advice (
            advice_id        uuid PRIMARY KEY,
            case_id          text NOT NULL REFERENCES verify.api_review_case (case_id),
            tenant_id        uuid NOT NULL,
            case_version     integer NOT NULL,
            model_version    text NOT NULL,
            requested_by     uuid NOT NULL,
            status           text NOT NULL CHECK (status IN ('ADVICE', 'ABSTAINED', 'REJECTED', 'FAILED')),
            body             jsonb NOT NULL DEFAULT '{{}}',
            flags            text[] NOT NULL DEFAULT '{{}}',
            human_feedback   text CHECK (human_feedback IN ('helpful', 'incorrect', 'ignored')),
            created_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX api_review_advice_case ON verify.api_review_advice (case_id, created_at DESC);
        {policies}
        -- the operator plane places and releases holds and reads open cases to decide where a hold is needed
        CREATE POLICY verify_api_operator ON verify.api_review_hold USING ({SCOPE} = 'operator') WITH CHECK ({SCOPE} = 'operator');
        CREATE POLICY verify_api_operator_read ON verify.api_review_case FOR SELECT USING ({SCOPE} = 'operator');
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.api_review_advice, verify.api_review_hold, verify.api_review_event, verify.api_review_second,
            verify.api_review_proposal, verify.api_review_reveal, verify.api_review_evidence, verify.api_review_lease, verify.api_review_case;
        DROP FUNCTION verify.api_review_event_immutable();
    """)

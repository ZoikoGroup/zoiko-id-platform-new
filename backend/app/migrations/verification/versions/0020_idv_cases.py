"""Identity proofing cases, their decision history and the capture digests seen (ZID-DTV-WF-03; CR-110).

Revision ID: verification_0020
Revises: verification_0019
Create Date: 2026-11-17

idv_case: one proofing attempt for one applicant, pinned to a market's rules by the hash of the file it was opened under. The signals are the six WF03 states plus the facts the decision reads; the decision and its
reasons are re-derived on each change and stored. A case that reached APPROVED, REJECTED or ABANDONED is CLOSED and never changes again (a trigger), and nothing is deleted.
idv_case_decision: append-only history of every decision a case reached (no update, no delete).
idv_media_hash: the digest of every capture accepted, one row per tenant and digest; a second case presenting the same capture is a replay.
Row-level security by company on all three.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0020"
down_revision = "verification_0019"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE verify.idv_case (
            case_id          uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            applicant        uuid NOT NULL,
            market           text NOT NULL CHECK (market ~ '^[A-Z0-9_-]{{2,12}}$'),
            route            text NOT NULL CHECK (length(route) BETWEEN 1 AND 60),
            policy_version   text NOT NULL,
            policy_hash      text NOT NULL CHECK (policy_hash ~ '^sha256:[0-9a-f]{{64}}$'),
            signals          jsonb NOT NULL,
            decision         text NOT NULL CHECK (decision IN ('APPROVED', 'REVIEW_REQUIRED', 'REJECTED', 'INDETERMINATE', 'ABANDONED')),
            reasons          jsonb NOT NULL,
            state            text NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'CLOSED')),
            attempts         jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            reviewers        jsonb NOT NULL DEFAULT '[]'::jsonb,
            notice_shown     text,
            consent_at       timestamptz,
            withdrawn_at     timestamptz,
            nonce            text,
            nonce_issued_at  timestamptz,
            nonce_used       boolean NOT NULL DEFAULT false,
            version          integer NOT NULL DEFAULT 1 CHECK (version >= 1),
            created_at       timestamptz NOT NULL,
            updated_at       timestamptz NOT NULL,
            CHECK ((state = 'CLOSED') = (decision IN ('APPROVED', 'REJECTED', 'ABANDONED')))
        );
        CREATE INDEX idv_case_by_tenant ON verify.idv_case (tenant_id, created_at DESC);

        CREATE FUNCTION verify.idv_case_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a proofing case is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state = 'CLOSED' THEN
                RAISE EXCEPTION 'a closed proofing case never changes' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.case_id <> OLD.case_id OR NEW.tenant_id <> OLD.tenant_id OR NEW.applicant <> OLD.applicant
               OR NEW.market <> OLD.market OR NEW.route <> OLD.route OR NEW.policy_hash <> OLD.policy_hash
               OR NEW.created_at <> OLD.created_at THEN
                RAISE EXCEPTION 'a proofing case keeps its applicant, market, route and pinned rules' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER idv_case_guard BEFORE UPDATE OR DELETE ON verify.idv_case
            FOR EACH ROW EXECUTE FUNCTION verify.idv_case_guard();

        CREATE TABLE verify.idv_case_decision (
            decision_id    uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            case_id        uuid NOT NULL REFERENCES verify.idv_case (case_id),
            version        integer NOT NULL CHECK (version >= 1),
            decision       text NOT NULL,
            reasons        jsonb NOT NULL,
            claims         jsonb NOT NULL,
            allowed        jsonb NOT NULL,
            policy_version text NOT NULL,
            policy_hash    text NOT NULL,
            trigger        text NOT NULL,
            decided_at     timestamptz NOT NULL,
            UNIQUE (case_id, version)
        );
        CREATE FUNCTION verify.idv_decision_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'proofing decisions are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER idv_decision_append_only BEFORE UPDATE OR DELETE ON verify.idv_case_decision
            FOR EACH ROW EXECUTE FUNCTION verify.idv_decision_append_only();

        CREATE TABLE verify.idv_media_hash (
            tenant_id    uuid NOT NULL,
            media_sha256 text NOT NULL CHECK (media_sha256 ~ '^[0-9a-f]{{64}}$'),
            case_id      uuid NOT NULL,
            seen_at      timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, media_sha256)
        );

        ALTER TABLE verify.idv_case ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.idv_case FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.idv_case USING {RULE} WITH CHECK {RULE};
        ALTER TABLE verify.idv_case_decision ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.idv_case_decision FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.idv_case_decision USING {RULE} WITH CHECK {RULE};
        ALTER TABLE verify.idv_media_hash ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.idv_media_hash FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.idv_media_hash USING {RULE} WITH CHECK {RULE};
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.idv_case_decision;
        DROP TABLE verify.idv_media_hash;
        DROP TABLE verify.idv_case;
        DROP FUNCTION verify.idv_case_guard();
        DROP FUNCTION verify.idv_decision_append_only();
    """)

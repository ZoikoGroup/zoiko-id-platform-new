"""Credential trust: consent grants, issuer outreach, eligibility decisions, appeals, monitoring (GID-041, 049, 053, 054, 058, CR-069).

Revision ID: credential_0006
Revises: credential_0005
Create Date: 2026-11-11

All in the one cell database, schema `cred`, tenant-scoped with row-level security.
- cred.consent_grant: one person's grant to one recipient, purpose and consumer for named fields; withdrawn, never deleted. A derived grant
  names its parent, so withdrawing the parent ends the children (GLB-20).
- cred.outreach_request: a request to an issuer's registered contact, the hash of its nonce, the state machine and the dual-review votes.
- cred.eligibility_decision: an append-only record of a policy decision (professional eligibility or an engagement) with the policy version and
  the checks it read, so it can be explained and appealed.
- cred.claim_appeal: a correction or review request on a check; a person resolves it, the claim is never changed in place.
- cred.claim_monitor: what to re-ask a source about and when (expiry, freshness), with the last source sequence applied (ordering, GLB-26).
- claim_check.decision also admits INDETERMINATE (a CPD record that cannot satisfy the requirement is neither verified nor false), and
  claim_check.source_result admits NOT_QUERIED (the check stopped before any source was asked: unknown or lookalike issuer, refused contract).
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0006"
down_revision = "credential_0005"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "consent_grant",
    "outreach_request",
    "eligibility_decision",
    "claim_appeal",
    "claim_monitor",
)


def upgrade() -> None:
    _run("""
        ALTER TABLE cred.claim_check DROP CONSTRAINT claim_check_decision_check;
        ALTER TABLE cred.claim_check ADD CONSTRAINT claim_check_decision_check CHECK (decision IN (
            'VERIFIED', 'NOT_VERIFIED', 'REVIEW', 'SOURCE_UNAVAILABLE', 'UNVERIFIABLE', 'INDETERMINATE'));

        ALTER TABLE cred.claim_check DROP CONSTRAINT claim_check_source_result_check;
        ALTER TABLE cred.claim_check ADD CONSTRAINT claim_check_source_result_check CHECK (source_result IN (
            'RESPONDED', 'UNAVAILABLE', 'NO_RECORD', 'NOT_QUERIED'));

        CREATE TABLE cred.consent_grant (
            grant_id             uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            subject_principal_id uuid NOT NULL,
            recipient            text NOT NULL,
            consumer             text NOT NULL,
            purpose              text NOT NULL,
            fields               text[] NOT NULL CHECK (cardinality(fields) > 0),
            parent_id            uuid REFERENCES cred.consent_grant (grant_id),
            created_at           timestamptz NOT NULL DEFAULT now(),
            expires_at           timestamptz NOT NULL,
            withdrawn_at         timestamptz,
            CHECK (expires_at > created_at)
        );
        CREATE INDEX consent_grant_subject ON cred.consent_grant (tenant_id, subject_principal_id);

        CREATE TABLE cred.outreach_request (
            request_id      uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            issuer_id       text NOT NULL,
            claim_type      text NOT NULL,
            nonce_hash      bytea NOT NULL CHECK (length(nonce_hash) = 32),
            requested_by    uuid NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            deadline        timestamptz NOT NULL,
            state           text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING', 'RESPONDED', 'ACCEPTED', 'REJECTED', 'EXPIRED')),
            response_digest bytea,
            outcome         text CHECK (outcome IN ('CONFIRMED', 'DENIED', 'NOT_HELD')),
            reviews         jsonb NOT NULL DEFAULT '{}'::jsonb,
            check_id        uuid,
            CHECK (deadline > created_at)
        );

        CREATE TABLE cred.eligibility_decision (
            decision_id          uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            subject_principal_id uuid NOT NULL,
            kind                 text NOT NULL CHECK (kind IN ('ELIGIBILITY', 'ENGAGEMENT')),
            policy_id            text NOT NULL,
            policy_version       integer NOT NULL,
            outcome              text NOT NULL,
            reasons              text[] NOT NULL DEFAULT '{}',
            restrictions         text[] NOT NULL DEFAULT '{}',
            check_ids            uuid[] NOT NULL DEFAULT '{}',
            consumer             text,
            created_at           timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX eligibility_decision_subject ON cred.eligibility_decision (tenant_id, subject_principal_id, created_at DESC);

        CREATE TABLE cred.claim_appeal (
            appeal_id   uuid PRIMARY KEY,
            tenant_id   uuid NOT NULL,
            check_id    uuid NOT NULL REFERENCES cred.claim_check (check_id),
            raised_by   uuid NOT NULL,
            reason      text NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
            correction  jsonb NOT NULL DEFAULT '{}'::jsonb,
            state       text NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'UPHELD', 'REJECTED')),
            resolved_by uuid,
            resolution  text,
            created_at  timestamptz NOT NULL DEFAULT now(),
            resolved_at timestamptz,
            CHECK ((state = 'OPEN') = (resolved_at IS NULL))
        );

        CREATE TABLE cred.claim_monitor (
            check_id          uuid PRIMARY KEY REFERENCES cred.claim_check (check_id),
            tenant_id         uuid NOT NULL,
            claim_type        text NOT NULL,
            status            text NOT NULL DEFAULT 'UNKNOWN',
            expires_at        timestamptz,
            next_check_at     timestamptz NOT NULL,
            last_source_seq   bigint,
            last_observed_at  timestamptz,
            seen_events       text[] NOT NULL DEFAULT '{}',
            updated_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX claim_monitor_due ON cred.claim_monitor (next_check_at);

        CREATE FUNCTION cred.eligibility_decision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'eligibility decisions are append-only' USING ERRCODE = '23514';
        END $$;
        CREATE TRIGGER eligibility_decision_guard BEFORE UPDATE OR DELETE ON cred.eligibility_decision
            FOR EACH ROW EXECUTE FUNCTION cred.eligibility_decision_guard();
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE cred.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE cred.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON cred.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER eligibility_decision_guard ON cred.eligibility_decision;
        DROP FUNCTION cred.eligibility_decision_guard();
        DROP TABLE cred.claim_monitor;
        DROP TABLE cred.claim_appeal;
        DROP TABLE cred.eligibility_decision;
        DROP TABLE cred.outreach_request;
        DROP TABLE cred.consent_grant;
        ALTER TABLE cred.claim_check DROP CONSTRAINT claim_check_source_result_check;
        ALTER TABLE cred.claim_check ADD CONSTRAINT claim_check_source_result_check CHECK (source_result IN (
            'RESPONDED', 'UNAVAILABLE', 'NO_RECORD'));
        ALTER TABLE cred.claim_check DROP CONSTRAINT claim_check_decision_check;
        ALTER TABLE cred.claim_check ADD CONSTRAINT claim_check_decision_check CHECK (decision IN (
            'VERIFIED', 'NOT_VERIFIED', 'REVIEW', 'SOURCE_UNAVAILABLE', 'UNVERIFIABLE'));
    """)

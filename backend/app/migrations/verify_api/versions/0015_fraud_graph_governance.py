"""Fraud graph governance: versioned queries and the company's own blocklist (ZID-CP-WF05 v2.0, .03 and .08; CR-168).

Revision ID: verify_api_0015
Revises: verify_api_0014
Create Date: 2026-11-25

`api_fraud_query` is a typed query with a version: a database check refuses the same person as maker and approver, and only one version of a query key is published at a time. `api_fraud_blocklist` is the company's OWN list of
references with a confirmed finding behind them: nothing is shared, an entry expires and is looked at again, and a removed or expired entry stays as a row. The lead remembers the token of its group so an erasure can find it.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0015"
down_revision = "verify_api_0014"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
TABLES = ("api_fraud_query", "api_fraud_blocklist")


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
        ALTER TABLE verify.api_fraud_lead ADD COLUMN group_token text;
        CREATE INDEX api_fraud_lead_token ON verify.api_fraud_lead (tenant_id, group_token) WHERE group_token IS NOT NULL;

        CREATE TABLE verify.api_fraud_query (
            query_id        text PRIMARY KEY CHECK (query_id ~ '^qry_[0-9a-f]{{24}}$'),
            tenant_id       uuid NOT NULL,
            query_key       text NOT NULL CHECK (query_key ~ '^[a-z0-9][a-z0-9-]{{1,38}}[a-z0-9]$'),
            version         integer NOT NULL CHECK (version >= 1),
            params          jsonb NOT NULL,
            checksum        text NOT NULL,
            status          text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'IN_REVIEW', 'PUBLISHED', 'REJECTED', 'RETIRED')),
            replay          jsonb,
            replay_checksum text,
            replayed_at     timestamptz,
            maker           uuid NOT NULL,
            approver        uuid,
            decision_note   text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CHECK (approver IS NULL OR approver <> maker),
            UNIQUE (tenant_id, query_key, version)
        );
        CREATE UNIQUE INDEX api_fraud_query_one_published ON verify.api_fraud_query (tenant_id, query_key) WHERE status = 'PUBLISHED';

        CREATE TABLE verify.api_fraud_blocklist (
            entry_id       text PRIMARY KEY CHECK (entry_id ~ '^blk_[0-9a-f]{{24}}$'),
            tenant_id      uuid NOT NULL,
            token          text,
            lead_id        text NOT NULL REFERENCES verify.api_fraud_lead (lead_id),
            reason_code    text NOT NULL,
            basis_ref      text NOT NULL,
            status         text NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'ACTIVE', 'CHALLENGED', 'REMOVED', 'EXPIRED', 'REJECTED')),
            entry_version  integer NOT NULL DEFAULT 1 CHECK (entry_version >= 1),
            expires_at     timestamptz NOT NULL,
            review_due_at  timestamptz NOT NULL,
            maker          uuid NOT NULL,
            approver       uuid,
            decided_at     timestamptz,
            decision_note  text,
            challenge_note text,
            outcome_note   text,
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            CHECK (approver IS NULL OR approver <> maker)
        );
        CREATE UNIQUE INDEX api_fraud_blocklist_one_live ON verify.api_fraud_blocklist (tenant_id, token) WHERE token IS NOT NULL AND status IN ('PROPOSED', 'ACTIVE', 'CHALLENGED');
        CREATE INDEX api_fraud_blocklist_list ON verify.api_fraud_blocklist (tenant_id, status, created_at DESC);
        {policies}
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.api_fraud_blocklist, verify.api_fraud_query;
        DROP INDEX verify.api_fraud_lead_token;
        ALTER TABLE verify.api_fraud_lead DROP COLUMN group_token;
    """)

"""Verify API go-live requests and production grants (ZID-EXT-IDV-001 section 11, epic E10, gate G4; CR-153).

Revision ID: verify_api_0003
Revises: verify_api_0002
Create Date: 2026-11-24

`api_go_live_request` is a company's ask to open production: the markets, checks and purpose it wants, its own words about the use, the terms and data-processing versions it accepted and who accepted, and the
result of the automatic sandbox checks at the moment it asked. An operator takes it into review and decides; the reviewer and the decider must be different people. `api_production_grant` is what an approval
leaves behind: a record of exactly what is allowed (markets, methods, who approved, what the operator confirmed) with the SHA-256 digest of that record, so what was approved can be shown and checked later. A
company has at most one open request and at most one active grant.

Tenant-scoped under row-level security. The operator plane reads and decides every company's requests inside the `operator` value of the same `app.verify_api_scope` setting the token endpoint uses for `auth`.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0003"
down_revision = "verify_api_0002"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
OPERATOR = "current_setting('app.verify_api_scope', true) = 'operator'"
TABLES = ("api_go_live_request", "api_production_grant")


def upgrade() -> None:
    policies = "\n".join(
        f"""
        ALTER TABLE verify.{t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.{t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.{t} USING ({TENANT}) WITH CHECK ({TENANT});
        CREATE POLICY verify_api_operator ON verify.{t} USING ({OPERATOR}) WITH CHECK ({OPERATOR});
        """
        for t in TABLES
    )
    _run(f"""
        CREATE TABLE verify.api_go_live_request (
            request_id       uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            status           text NOT NULL CHECK (status IN ('SUBMITTED', 'IN_REVIEW', 'APPROVED', 'DECLINED', 'WITHDRAWN')),
            markets          text[] NOT NULL CHECK (cardinality(markets) BETWEEN 1 AND 20),
            methods          text[] NOT NULL CHECK (cardinality(methods) BETWEEN 1 AND 8),
            purpose_code     text NOT NULL,
            volume_band      text NOT NULL,
            use_case         text NOT NULL CHECK (char_length(use_case) BETWEEN 10 AND 500),
            terms_version    text NOT NULL,
            dpa_version      text NOT NULL,
            accepted_by      uuid NOT NULL,
            accepted_at      timestamptz NOT NULL DEFAULT now(),
            certification    jsonb NOT NULL,
            reviewer_id      uuid,
            reviewed_at      timestamptz,
            decided_by       uuid,
            decided_at       timestamptz,
            note             text,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            CHECK (decided_by IS NULL OR reviewer_id IS NULL OR decided_by <> reviewer_id OR status IN ('DECLINED', 'WITHDRAWN'))
        );
        CREATE UNIQUE INDEX api_go_live_one_open ON verify.api_go_live_request (tenant_id) WHERE status IN ('SUBMITTED', 'IN_REVIEW');
        CREATE INDEX api_go_live_by_status ON verify.api_go_live_request (status, created_at);

        CREATE TABLE verify.api_production_grant (
            grant_id         uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            request_id       uuid NOT NULL REFERENCES verify.api_go_live_request (request_id),
            status           text NOT NULL CHECK (status IN ('ACTIVE', 'REVOKED')),
            markets          text[] NOT NULL,
            methods          text[] NOT NULL,
            manifest         jsonb NOT NULL,
            manifest_digest  bytea NOT NULL CHECK (octet_length(manifest_digest) = 32),
            approved_by      uuid NOT NULL,
            approved_at      timestamptz NOT NULL DEFAULT now(),
            revoked_by       uuid,
            revoked_at       timestamptz,
            revoke_reason    text,
            CHECK ((status = 'REVOKED') = (revoked_at IS NOT NULL))
        );
        CREATE UNIQUE INDEX api_production_grant_one_active ON verify.api_production_grant (tenant_id) WHERE status = 'ACTIVE';
        {policies}
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.api_production_grant;
        DROP TABLE verify.api_go_live_request;
    """)

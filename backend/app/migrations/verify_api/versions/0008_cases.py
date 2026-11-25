"""Disputes, appeals and erasure requests for a partner's verifications (ZID-EXT-IDV-001 WF-14, WF-16, epic E18; CR-159).

Revision ID: verify_api_0008
Revises: verify_api_0007
Create Date: 2026-11-25

`api_case` records one company's dispute or appeal about a session, or its request to erase a customer reference. An erasure case keeps only the HASH of the reference (the reference is itself personal data and must not be
copied by the request to remove it). Zoiko iD's reviewer and a different resolving person are both recorded. Company rows are tenant-scoped; the operator plane reads every company's open cases under its own scope policy.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0008"
down_revision = "verify_api_0007"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
OPERATOR = "current_setting('app.verify_api_scope', true) = 'operator'"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE verify.api_case (
            case_id         uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            kind            text NOT NULL CHECK (kind IN ('DISPUTE', 'APPEAL', 'ERASURE')),
            status          text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'REJECTED', 'WITHDRAWN')),
            session_id      text REFERENCES verify.api_session (session_id),
            reference_hash  bytea CHECK (reference_hash IS NULL OR octet_length(reference_hash) = 32),
            reason          text NOT NULL CHECK (char_length(reason) BETWEEN 10 AND 1000),
            raised_by       uuid NOT NULL,
            reviewer_id     uuid,
            decided_by      uuid,
            note            text,
            sessions_erased integer,
            created_at      timestamptz NOT NULL DEFAULT now(),
            reviewed_at     timestamptz,
            decided_at      timestamptz,
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CHECK ((kind = 'ERASURE') = (reference_hash IS NOT NULL)),
            CHECK ((kind = 'ERASURE') = (session_id IS NULL)),
            CHECK (sessions_erased IS NULL OR (kind = 'ERASURE' AND status = 'RESOLVED'))
        );
        CREATE INDEX api_case_by_company ON verify.api_case (tenant_id, created_at DESC);
        CREATE INDEX api_case_open ON verify.api_case (created_at) WHERE status IN ('OPEN', 'IN_REVIEW');
        -- one open case of a kind per session (or per reference) so a case is not raised twice while it waits
        CREATE UNIQUE INDEX api_case_one_open_session ON verify.api_case (session_id, kind)
            WHERE session_id IS NOT NULL AND status IN ('OPEN', 'IN_REVIEW');
        CREATE UNIQUE INDEX api_case_one_open_reference ON verify.api_case (tenant_id, reference_hash)
            WHERE reference_hash IS NOT NULL AND status IN ('OPEN', 'IN_REVIEW');
        ALTER TABLE verify.api_case ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.api_case FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.api_case USING ({TENANT}) WITH CHECK ({TENANT});
        CREATE POLICY verify_api_operator ON verify.api_case USING ({OPERATOR}) WITH CHECK ({OPERATOR});
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.api_case;
    """)

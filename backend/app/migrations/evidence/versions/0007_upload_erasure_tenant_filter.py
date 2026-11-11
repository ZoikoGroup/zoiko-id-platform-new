"""The upload erasure function names its tenant explicitly (CR-071).

Revision ID: evidence_0007
Revises: evidence_0006
Create Date: 2026-11-11

A SECURITY DEFINER function runs as its owner, and an owner that bypasses row-level security would delete one person's uploads in EVERY
tenant. A test proved it. The function now filters on the tenant it was called for, in addition to refusing any other tenant id, so it
cannot reach another tenant's rows whatever role owns it.
"""

from zid_platform.migrations import execute_script as _run

revision = "evidence_0007"
down_revision = "evidence_0006"
branch_labels = None
depends_on = None

# Runtime behavior when CALLED, not a schema contract step: upgrade() itself only replaces a function.
UPGRADE_SQL = """
        CREATE OR REPLACE FUNCTION evidence.erase_uploads(p_tenant uuid, p_subject uuid) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = evidence, pg_temp AS $$
        DECLARE
            n integer;
        BEGIN
            IF p_tenant IS DISTINCT FROM nullif(current_setting('app.tenant_id', true), '')::uuid THEN
                RAISE EXCEPTION 'erasure is for the current tenant only' USING ERRCODE = '42501';
            END IF;
            DELETE FROM evidence.upload WHERE subject_principal_id = p_subject AND tenant_id = p_tenant;
            GET DIAGNOSTICS n = ROW_COUNT;
            RETURN n;
        END $$;
"""

DOWNGRADE_SQL = """
        CREATE OR REPLACE FUNCTION evidence.erase_uploads(p_tenant uuid, p_subject uuid) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = evidence, pg_temp AS $$
        DECLARE
            n integer;
        BEGIN
            IF p_tenant IS DISTINCT FROM nullif(current_setting('app.tenant_id', true), '')::uuid THEN
                RAISE EXCEPTION 'erasure is for the current tenant only' USING ERRCODE = '42501';
            END IF;
            DELETE FROM evidence.upload WHERE subject_principal_id = p_subject;
            GET DIAGNOSTICS n = ROW_COUNT;
            RETURN n;
        END $$;
"""


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)

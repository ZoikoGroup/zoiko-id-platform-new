"""Governed erasure of a person's uploaded evidence (CR-071; privacy data map).

Revision ID: evidence_0006
Revises: evidence_0005
Create Date: 2026-11-11

The runtime role cannot DELETE from any table, on purpose. Erasing a person's uploads (rows and bytes) therefore goes through one
SECURITY DEFINER function that works only for the current tenant (it refuses any other tenant id, and row-level security still applies
inside it) and removes one subject's uploads. The privacy module calls it through the evidence privacy adapter, after its own legal-hold
and retention checks. It returns how many rows it removed and is idempotent.
"""

from zid_platform.migrations import execute_script as _run

revision = "evidence_0006"
down_revision = "evidence_0005"
branch_labels = None
depends_on = None

# A function that deletes when it is CALLED is runtime behavior, not a schema contract step: upgrade() itself only adds a function.
UPGRADE_SQL = """
        CREATE FUNCTION evidence.erase_uploads(p_tenant uuid, p_subject uuid) RETURNS integer
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
    _run("DROP FUNCTION evidence.erase_uploads(uuid, uuid);")

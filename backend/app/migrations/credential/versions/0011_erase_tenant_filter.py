"""The credential erasure function names its tenant explicitly (CR-071).

Revision ID: credential_0011
Revises: credential_0010
Create Date: 2026-11-11

`cred.erase_subject` is SECURITY DEFINER, so it runs as its owner. An owner that bypasses row-level security would delete one person's
rows in EVERY tenant (a test on the matching evidence function proved the pattern). Every DELETE now also filters on the tenant the
function was called for, so it cannot reach another tenant's rows whatever role owns it. Behavior for the right tenant is unchanged.
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0011"
down_revision = "credential_0010"
branch_labels = None
depends_on = None

HEAD = """
        CREATE OR REPLACE FUNCTION cred.erase_subject(p_tenant uuid, p_subject uuid) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = cred, pg_temp AS $$
        DECLARE
            n integer := 0;
            c integer;
        BEGIN
            IF p_tenant IS DISTINCT FROM nullif(current_setting('app.tenant_id', true), '')::uuid THEN
                RAISE EXCEPTION 'erasure is for the current tenant only' USING ERRCODE = '42501';
            END IF;
            PERFORM set_config('cred.erasing', 'on', true);
"""
TAIL = """
            PERFORM set_config('cred.erasing', 'off', true);
            RETURN n;
        END $$;
"""
TABLES = (
    (
        "cred.verification_case_task",
        "case_id IN (SELECT case_id FROM cred.verification_case WHERE subject_principal_id = p_subject AND tenant_id = p_tenant)",
    ),
    ("cred.verification_case", "subject_principal_id = p_subject"),
    (
        "cred.claim_appeal",
        "check_id IN (SELECT check_id FROM cred.claim_check WHERE subject_principal_id = p_subject AND tenant_id = p_tenant)",
    ),
    (
        "cred.claim_monitor",
        "check_id IN (SELECT check_id FROM cred.claim_check WHERE subject_principal_id = p_subject AND tenant_id = p_tenant)",
    ),
    ("cred.claim_check", "subject_principal_id = p_subject"),
    ("cred.consent_grant", "subject_principal_id = p_subject"),
    ("cred.eligibility_decision", "subject_principal_id = p_subject"),
    ("cred.outreach_request", "subject_principal_id = p_subject"),
)


def _body(with_tenant: bool) -> str:
    lines = []
    for table, where in TABLES:
        scope = (
            f"{where} AND tenant_id = p_tenant" if with_tenant and "SELECT" not in where else where
        )
        lines.append(
            f"            DELETE FROM {table} WHERE {scope};\n            GET DIAGNOSTICS c = ROW_COUNT; n := n + c;\n"
        )
    return HEAD + "".join(lines) + TAIL


# Runtime behavior when CALLED, not a schema contract step: upgrade() itself only replaces a function.
UPGRADE_SQL = _body(True)
DOWNGRADE_SQL = _body(False)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)

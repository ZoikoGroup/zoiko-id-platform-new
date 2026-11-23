"""A grant points at the audit record of its own issuance (CR-143).

Revision ID: authority_0005
Revises: authority_0004
Create Date: 2026-11-20

The access readiness agent's rule R-05 asks whether the policy evidence behind a mandate can be found, and reads it from the mandate's source grant
(`authz.authority_grant.evidence_ref`). Nothing in the product ever wrote that column, so every mandate in a real company read as missing its evidence
(the tests set it by hand). New grants now carry the audit record of their issuance; this points the grants already issued through the API at the audit
record that was written when they were. A grant that was never audited (one an onboarding step created) is left as it is: it has no such record.
"""

from zid_platform.migrations import execute_script as _run

revision = "authority_0005"
down_revision = "authority_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Modules migrate one after another, and on a fresh database the evidence tables do not exist yet (and there are no grants to point at them). Skip then.
    # Both tables FORCE row-level security, which binds even their owner, and the migration runs as that plain owner role (no BYPASSRLS), so it would see no
    # rows at all and silently change nothing. The force is lifted for this one statement and restored (the owner can alter its own tables), as authority and
    # commercial backfills elsewhere do (commercial_0021).
    _run("""
        DO $$
        BEGIN
          IF to_regclass('evidence.audit_record') IS NOT NULL THEN
            ALTER TABLE authz.authority_grant NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE evidence.audit_record NO FORCE ROW LEVEL SECURITY;
            UPDATE authz.authority_grant g SET evidence_ref = (
                SELECT a.audit_id FROM evidence.audit_record a
                 WHERE a.tenant_id = g.tenant_id AND a.event_type = 'authority.grant.issued' AND a.target_ref = g.authority_grant_id::text
                 ORDER BY a.created_at LIMIT 1)
             WHERE g.evidence_ref IS NULL
               AND EXISTS (SELECT 1 FROM evidence.audit_record a
                            WHERE a.tenant_id = g.tenant_id AND a.event_type = 'authority.grant.issued' AND a.target_ref = g.authority_grant_id::text);
            ALTER TABLE authz.authority_grant FORCE ROW LEVEL SECURITY;
            ALTER TABLE evidence.audit_record FORCE ROW LEVEL SECURITY;
          END IF;
        END
        $$;
    """)


def downgrade() -> None:
    pass  # the references are evidence pointers, not data a rollback should discard

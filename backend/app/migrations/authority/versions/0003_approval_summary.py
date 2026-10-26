"""Approval requests that say, in words, what they are for.

Revision ID: authority_0003
Revises: authority_0002
Create Date: 2026-10-26

A request carries a short plain-language summary and the action, resource and change it is bound to, so an approver
sees "Set residency to Regional resident, home cell local-1" and approves it without copying identifiers or a
transaction-context string. The binding itself is unchanged: the hash of `action|resource|change`. Columns are
nullable, so requests opened the older way keep working.
"""

from zid_platform.migrations import execute_script as _run

revision = "authority_0003"
down_revision = "authority_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE authz.approval_request
            ADD COLUMN summary  text CHECK (summary IS NULL OR char_length(summary) <= 300),
            ADD COLUMN action   text,
            ADD COLUMN resource text,
            ADD COLUMN change   text;
    """)


def downgrade() -> None:
    _run(
        "ALTER TABLE authz.approval_request DROP COLUMN summary, DROP COLUMN action, DROP COLUMN resource, DROP COLUMN change;"
    )

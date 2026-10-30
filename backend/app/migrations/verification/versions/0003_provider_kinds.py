"""Provider kinds: Veriff and Sumsub beside Zoiko's own signed-JSON protocol (ZID-EDR-005; CR-043).

Revision ID: verification_0003
Revises: verification_0002
Create Date: 2026-10-30

provider_adapter.provider_kind says which protocol an adapter speaks (GENERIC, VERIFF or SUMSUB). provider_config holds the
non-secret settings for the provider kinds: the names of the secrets in the secrets broker (never the secrets), the level name,
whether the configured level includes a liveness and face match, and whether the adapter is the UK-certified flow. Existing
adapters stay GENERIC and behave exactly as before.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0003"
down_revision = "verification_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.provider_adapter
            ADD COLUMN provider_kind   text NOT NULL DEFAULT 'GENERIC'
                CHECK (provider_kind IN ('GENERIC', 'VERIFF', 'SUMSUB')),
            ADD COLUMN provider_config jsonb NOT NULL DEFAULT '{}'::jsonb;
    """)


def downgrade() -> None:
    _run(
        "ALTER TABLE verify.provider_adapter DROP COLUMN provider_config, DROP COLUMN provider_kind;"
    )

"""Approved builds: the reviewed software an agent blueprint may run (ZID-MACHINE-001 §3, §9; CR-064).

Revision ID: machine_0005
Revises: machine_0004
Create Date: 2026-11-08

A blueprint release names the artifact digest of the software it runs, and a workload token needs an attestation that
the running software matches that digest. Until now the digest was typed in with nothing behind it: any 64 hex characters
were accepted. A build is the missing record. A person who builds software registers its digest once (with where it came
from), a second person approves it, and a blueprint release can then be drafted only from an approved build. Revoking a
build suspends what runs it. One row per build, never deleted. Tenant-scoped under RLS.

The two-person rule is also a database rule: an APPROVED row must name an approver who is not the person who registered it.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0005"
down_revision = "machine_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE agent.build_artifact (
            build_id        uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            name            text NOT NULL CHECK (length(name) BETWEEN 2 AND 120),
            artifact_digest text NOT NULL CHECK (artifact_digest ~ '^sha256:[0-9a-f]{64}$'),
            source_ref      text CHECK (source_ref IS NULL OR length(source_ref) <= 300),
            provenance_ref  text CHECK (provenance_ref IS NULL OR length(provenance_ref) <= 300),
            registered_by   uuid NOT NULL,
            review_status   text NOT NULL CHECK (review_status IN ('DRAFT', 'APPROVED', 'REVOKED')),
            approved_by     uuid,
            registered_at   timestamptz NOT NULL DEFAULT now(),
            approved_at     timestamptz,
            revoked_at      timestamptz,
            revoke_reason   text,
            UNIQUE (tenant_id, artifact_digest),
            CHECK (review_status <> 'APPROVED' OR (approved_by IS NOT NULL AND approved_by <> registered_by)),
            CHECK ((review_status = 'REVOKED') = (revoked_at IS NOT NULL))
        );
        CREATE INDEX build_artifact_status ON agent.build_artifact (tenant_id, review_status, registered_at DESC);
        ALTER TABLE agent.build_artifact ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.build_artifact FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.build_artifact
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE agent.build_artifact;")

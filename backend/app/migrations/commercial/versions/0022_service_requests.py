"""Enterprise service requests: asking Zoiko iD for the contracted parts of a plan.

Revision ID: commercial_0022
Revises: commercial_0021
Create Date: 2026-11-05

Plan features advanced.dedicated-isolation-private-deployment, advanced.sovereign-service-configuration, advanced.byok-hyok-and-specialized-key-custody and
advanced.custom-sla-named-support-onboarding are delivered by people, not switched on by software. A company whose plan includes one asks for it here; the
request is a tracked record with a status Zoiko iD moves forward, so the company sees where it stands and nothing is promised that was not asked for.
- commercial.service_request: tenant-scoped. `details` is the company's own words (at most 2,000 characters), never a secret: a key for BYOK is not sent here.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0022"
down_revision = "commercial_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.service_request (
            request_id   uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            service      text NOT NULL CHECK (service IN ('DEDICATED_ISOLATION', 'SOVEREIGN_SERVICE', 'KEY_CUSTODY', 'NAMED_SUPPORT')),
            status       text NOT NULL DEFAULT 'REQUESTED' CHECK (status IN ('REQUESTED', 'IN_PROGRESS', 'ACTIVE', 'DECLINED', 'WITHDRAWN')),
            details      text NOT NULL CHECK (char_length(details) BETWEEN 10 AND 2000),
            contact      text NOT NULL CHECK (char_length(contact) BETWEEN 3 AND 200),
            requested_by uuid NOT NULL,
            note         text NOT NULL DEFAULT '' CHECK (char_length(note) <= 1000),
            created_at   timestamptz NOT NULL DEFAULT now(),
            updated_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX service_request_open ON commercial.service_request (tenant_id, service) WHERE status IN ('REQUESTED', 'IN_PROGRESS');
        ALTER TABLE commercial.service_request ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.service_request FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.service_request
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE commercial.service_request;")

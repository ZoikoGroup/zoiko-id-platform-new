"""Organization setup acknowledgements and activation records (ZID-UX-WF-013C screen 1; slice L-13, CR-049).

Revision ID: onboarding_0005
Revises: onboarding_0004
Create Date: 2026-11-02

organization_acknowledgement: one row per accepted text and version, with who accepted it, when and in which locale; append-only, unique
per organization, kind and version, so a retried request is the same acceptance.
organization_activation: one row per organization when its activation gate passed, with the gate report and the policy versions it rested
on; append-only. Readiness itself is never stored: it is derived each time from independent facts.
"""

from zid_platform.migrations import execute_script as _run

revision = "onboarding_0005"
down_revision = "onboarding_0004"
branch_labels = None
depends_on = None

_RLS = """
    ALTER TABLE platform.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE platform.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON platform.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""


TABLES = ("organization_acknowledgement", "organization_activation")


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.organization_acknowledgement (
            ack_id               uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            kind                 text NOT NULL CHECK (kind IN ('ORGANIZATIONAL_CONTROLS', 'REGION_POLICY')),
            version              text NOT NULL CHECK (length(version) BETWEEN 1 AND 40),
            actor_principal_id   uuid NOT NULL,
            locale               text NOT NULL DEFAULT 'und' CHECK (length(locale) <= 35),
            idempotency_key      text CHECK (idempotency_key IS NULL OR length(idempotency_key) <= 128),
            acknowledged_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, kind, version)
        );
        CREATE TRIGGER organization_acknowledgement_append_only BEFORE UPDATE OR DELETE ON platform.organization_acknowledgement
            FOR EACH ROW EXECUTE FUNCTION platform.registration_append_only();

        CREATE TABLE platform.organization_activation (
            tenant_id            uuid PRIMARY KEY,
            activated_by         uuid NOT NULL,
            gate_report          jsonb NOT NULL,
            policy_versions      jsonb NOT NULL,
            activated_at         timestamptz NOT NULL DEFAULT now()
        );
        CREATE TRIGGER organization_activation_append_only BEFORE UPDATE OR DELETE ON platform.organization_activation
            FOR EACH ROW EXECUTE FUNCTION platform.registration_append_only();
    """)
    for t in TABLES:
        _run(_RLS.format(t=t))


def downgrade() -> None:
    _run("""
        DROP TABLE platform.organization_activation;
        DROP TABLE platform.organization_acknowledgement;
    """)

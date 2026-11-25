"""Signed what-if analyses of a draft policy against the active one (ZID-CP-WF08 v2.0 E12; CR-170).

Revision ID: policy_authoring_0002
Revises: policy_authoring_0001
Create Date: 2026-11-25

An analysis is a finding about scenarios, kept and signed so a reviewer can cite it. It is append-only and it never points at a decision receipt: it is
about what WOULD change, so a later reader cannot mistake it for what happened.
"""

from zid_platform.migrations import execute_script as _run

revision = "policy_authoring_0002"
down_revision = "policy_authoring_0001"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE authz.policy_what_if (
            analysis_id    uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            release_id     uuid NOT NULL REFERENCES authz.policy_release (release_id),
            candidate_hash text NOT NULL,
            active_hash    text,
            scenarios      integer NOT NULL CHECK (scenarios BETWEEN 1 AND 200),
            claims         jsonb NOT NULL,
            signature      text NOT NULL,
            created_by     uuid NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX policy_what_if_release ON authz.policy_what_if (tenant_id, release_id, created_at DESC);
        ALTER TABLE authz.policy_what_if ENABLE ROW LEVEL SECURITY;
        ALTER TABLE authz.policy_what_if FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON authz.policy_what_if USING {RULE} WITH CHECK {RULE};
        CREATE FUNCTION authz.refuse_what_if_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'authz.policy_what_if is append-only (% refused)', TG_OP USING ERRCODE = 'restrict_violation';
        END;
        $$;
        CREATE TRIGGER policy_what_if_append_only BEFORE UPDATE OR DELETE ON authz.policy_what_if
            FOR EACH ROW EXECUTE FUNCTION authz.refuse_what_if_change();
    """)


def downgrade() -> None:
    _run("DROP TABLE authz.policy_what_if; DROP FUNCTION authz.refuse_what_if_change();")

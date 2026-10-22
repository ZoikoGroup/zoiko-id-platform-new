"""Tenant record, lifecycle, roles and configuration revisions (ZID-OVERLAY-001 §2-4, §17;
ZID-DATA-001 §2.1; TENANCY-001 §14; G-W0; CR-015).

Revision ID: tenant_0002
Revises: tenant_0001
Create Date: 2026-10-22

- tenant: the canonical tenant configuration of OVERLAY §3 (status is the §2 lifecycle; DATA §2.1's
  PROVISIONING/CLOSING map to DRAFT/OFFBOARDING, CR-015). `status_before` is where BLOCKED or
  SUSPENDED returns to. `configuration_revision` only ever increases.
- tenant_role_assignment: OWNER / ADMIN / MEMBER. The database itself refuses an OWNER that did not
  come from the bootstrap gate or an approved owner change (OVL-13/14), and a role, principal or
  origin is never rewritten in place.
- tenant_state_transition: append-only history; every move names its actor and reason (§2 "no
  transition is inferred from elapsed time alone").
- tenant_configuration_revision: append-only, one row per configuration_revision.
- tenant_bootstrap_request: kept in the *creating* (signup) tenant, so a creator's open drafts can
  be counted under that tenant's own RLS scope.
All tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0002"
down_revision = "tenant_0001"
branch_labels = None
depends_on = None

TABLES = (
    "tenant",
    "tenant_role_assignment",
    "tenant_state_transition",
    "tenant_configuration_revision",
    "tenant_bootstrap_request",
)
STATUSES = (
    "'DRAFT', 'ORGANIZATION_VERIFYING', 'CONFIGURING', 'BLOCKED', 'INTEGRATING', 'SHADOW', "
    "'PILOT', 'ACTIVE', 'SUSPENDED', 'OFFBOARDING', 'CLOSED'"
)


def upgrade() -> None:
    _run(f"""
        CREATE TABLE platform.tenant (
            tenant_id                     uuid PRIMARY KEY,
            display_name                  text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
            tenant_slug                   text NOT NULL
                                          CHECK (tenant_slug ~ '^[a-z0-9]([a-z0-9-]{{0,61}}[a-z0-9])?$'),
            status                        text NOT NULL CHECK (status IN ({STATUSES})),
            status_before                 text CHECK (status_before IN ({STATUSES})),
            deployment_mode               text NOT NULL CHECK (deployment_mode IN ('O0', 'O1', 'O2', 'O3')),
            home_cell_id                  text NOT NULL CHECK (home_cell_id ~ '^[a-z0-9-]{{1,40}}$'),
            organization_id               uuid,
            security_profile_id           text NOT NULL DEFAULT 'standard',
            configuration_revision        bigint NOT NULL DEFAULT 1 CHECK (configuration_revision >= 1),
            security_revision             bigint NOT NULL DEFAULT 0 CHECK (security_revision >= 0),
            application_registry_revision bigint NOT NULL DEFAULT 0,
            mapping_release_id            uuid,
            onboarding_plan_id            uuid,
            creator_tenant_id             uuid NOT NULL,
            creator_principal_id          uuid NOT NULL,
            created_at                    timestamptz NOT NULL DEFAULT now(),
            state_changed_at              timestamptz NOT NULL DEFAULT now(),
            closed_at                     timestamptz,
            row_version                   bigint NOT NULL DEFAULT 1,
            CHECK ((status = 'CLOSED') = (closed_at IS NOT NULL)),
            CHECK ((status IN ('BLOCKED', 'SUSPENDED')) = (status_before IS NOT NULL))
        );
        -- Unique within the cell; global uniqueness of routing aliases is the locator's (DEC-G3).
        CREATE UNIQUE INDEX tenant_slug_unique ON platform.tenant (tenant_slug);
        CREATE INDEX tenant_status_cell ON platform.tenant (status, home_cell_id);

        CREATE FUNCTION platform.tenant_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.configuration_revision < OLD.configuration_revision
               OR NEW.security_revision < OLD.security_revision THEN
                RAISE EXCEPTION 'tenant revisions only increase' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'CLOSED' THEN
                RAISE EXCEPTION 'a CLOSED tenant is terminal' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.home_cell_id <> OLD.home_cell_id AND OLD.status NOT IN ('DRAFT', 'ORGANIZATION_VERIFYING',
                    'CONFIGURING', 'BLOCKED') THEN
                RAISE EXCEPTION 'home cell moves only through a governed migration'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER tenant_guard BEFORE UPDATE ON platform.tenant
            FOR EACH ROW EXECUTE FUNCTION platform.tenant_guard();

        CREATE TABLE platform.tenant_role_assignment (
            assignment_id        uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL REFERENCES platform.tenant (tenant_id),
            principal_id         uuid NOT NULL,
            role                 text NOT NULL CHECK (role IN ('OWNER', 'ADMIN', 'MEMBER')),
            status               text NOT NULL CHECK (status IN ('PENDING_VERIFICATION', 'INVITED',
                                 'ACTIVE', 'REVOKED')),
            origin               text NOT NULL CHECK (origin IN ('BOOTSTRAP', 'INVITATION',
                                 'OWNER_CHANGE', 'PROVISIONING')),
            granted_by           uuid,
            approved_by          uuid,
            created_at           timestamptz NOT NULL DEFAULT now(),
            activated_at         timestamptz,
            revoked_at           timestamptz,
            revoked_by           uuid,
            -- OVL-13/14: an invitation or provisioning feed never yields OWNER
            CHECK (role <> 'OWNER' OR origin IN ('BOOTSTRAP', 'OWNER_CHANGE')),
            -- an owner change is approved by someone other than the administrator who made it
            CHECK (origin <> 'OWNER_CHANGE' OR (approved_by IS NOT NULL AND approved_by <> granted_by)),
            CHECK (status <> 'PENDING_VERIFICATION' OR origin = 'BOOTSTRAP'),
            CHECK ((status = 'REVOKED') = (revoked_at IS NOT NULL))
        );
        CREATE UNIQUE INDEX tenant_role_live ON platform.tenant_role_assignment
            (tenant_id, principal_id, role) WHERE status <> 'REVOKED';

        CREATE FUNCTION platform.tenant_role_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.role <> OLD.role OR NEW.origin <> OLD.origin OR NEW.principal_id <> OLD.principal_id
               OR NEW.tenant_id <> OLD.tenant_id THEN
                RAISE EXCEPTION 'role assignments are never rewritten; revoke and assign anew'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'REVOKED' THEN
                RAISE EXCEPTION 'a revoked role assignment is terminal' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER tenant_role_guard BEFORE UPDATE ON platform.tenant_role_assignment
            FOR EACH ROW EXECUTE FUNCTION platform.tenant_role_guard();

        CREATE TABLE platform.tenant_state_transition (
            transition_id        uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL REFERENCES platform.tenant (tenant_id),
            from_status          text CHECK (from_status IN ({STATUSES})),
            to_status            text NOT NULL CHECK (to_status IN ({STATUSES})),
            move                 text NOT NULL,
            reason_code          text NOT NULL,
            actor_type           text NOT NULL CHECK (actor_type IN ('HUMAN', 'OPERATOR', 'SERVICE')),
            actor_principal_id   uuid,
            gate_report          jsonb NOT NULL DEFAULT '[]',
            approval_ref         text,
            occurred_at          timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX tenant_transition_by_tenant ON platform.tenant_state_transition (tenant_id, occurred_at);

        CREATE TABLE platform.tenant_configuration_revision (
            tenant_id            uuid NOT NULL REFERENCES platform.tenant (tenant_id),
            revision             bigint NOT NULL CHECK (revision >= 1),
            change_kind          text NOT NULL,
            change_commitment    text NOT NULL,
            actor_principal_id   uuid,
            approval_ref         text,
            created_at           timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, revision)
        );

        CREATE TABLE platform.tenant_bootstrap_request (
            request_id            uuid PRIMARY KEY,
            tenant_id             uuid NOT NULL,          -- the creating (signup) tenant
            creator_principal_id  uuid NOT NULL,
            new_tenant_id         uuid NOT NULL UNIQUE,
            status                text NOT NULL DEFAULT 'OPEN'
                                  CHECK (status IN ('OPEN', 'COMPLETED', 'ABANDONED')),
            created_at            timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX tenant_bootstrap_open ON platform.tenant_bootstrap_request
            (tenant_id, creator_principal_id) WHERE status = 'OPEN';
    """)
    for table in TABLES:
        _run(f"""
            ALTER TABLE platform.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE platform.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON platform.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)
    # Append-only history: the runtime role may insert and read, never rewrite.
    _run("""
        CREATE FUNCTION platform.tenant_history_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'tenant history is append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER tenant_state_transition_append_only
            BEFORE UPDATE OR DELETE ON platform.tenant_state_transition
            FOR EACH ROW EXECUTE FUNCTION platform.tenant_history_guard();
        CREATE TRIGGER tenant_configuration_revision_append_only
            BEFORE UPDATE OR DELETE ON platform.tenant_configuration_revision
            FOR EACH ROW EXECUTE FUNCTION platform.tenant_history_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.tenant_bootstrap_request;
        DROP TABLE platform.tenant_configuration_revision;
        DROP TABLE platform.tenant_state_transition;
        DROP FUNCTION platform.tenant_history_guard();
        DROP TABLE platform.tenant_role_assignment;
        DROP FUNCTION platform.tenant_role_guard();
        DROP TABLE platform.tenant;
        DROP FUNCTION platform.tenant_guard();
    """)

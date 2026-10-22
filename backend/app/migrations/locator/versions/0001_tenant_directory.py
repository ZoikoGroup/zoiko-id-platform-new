"""Global tenant directory: the global-minimum locator record (ZID-TENANCY-001 §2, §9; DEC-G3; G-W6;
CR-016).

Revision ID: locator_0001
Revises:
Create Date: 2026-10-22

Global-plane database only (`migrations --role global`). The directory is a locator, never a
customer identity database: tenant name, administrator email, individual identities and raw domains
are excluded by construction - the columns are opaque ids, states, cell ids, epochs and short
routing aliases, and nothing else can be stored.

- tenant_directory_record: one row per tenant, published by its home cell as a signed record;
  `placement_epoch` and `record_revision` only ever increase, so a stale cell can never move a
  tenant back (writer fencing at the locator, TENANCY §15).
- routing_alias: a routing alias (workspace name) -> tenant; unique across the fleet.
"""

from zid_platform.migrations import execute_script as _run

revision = "locator_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE global_directory.tenant_directory_record (
            tenant_id               uuid PRIMARY KEY,
            tenant_status           text NOT NULL CHECK (tenant_status IN ('PROVISIONING', 'ACTIVE',
                                    'SUSPENDED', 'MIGRATING', 'DELETING', 'DELETED')),
            home_cell_id            text NOT NULL CHECK (home_cell_id ~ '^[a-z0-9-]{1,40}$'),
            recovery_cell_id        text CHECK (recovery_cell_id ~ '^[a-z0-9-]{1,40}$'),
            placement_epoch         integer NOT NULL CHECK (placement_epoch >= 1),
            residency_profile_id    text CHECK (residency_profile_id IN ('SRP-0', 'SRP-1', 'SRP-2', 'SRP-3')),
            tenant_key_profile_ref  text CHECK (tenant_key_profile_ref ~ '^[A-Z_]{1,40}$'),
            routing_aliases         text[] NOT NULL DEFAULT '{}',
            service_entitlements    text[] NOT NULL DEFAULT '{}',
            policy_release_refs     text[] NOT NULL DEFAULT '{}',
            record_revision         bigint NOT NULL CHECK (record_revision >= 1),
            published_by_cell       text NOT NULL,
            created_at              timestamptz NOT NULL DEFAULT now(),
            state_changed_at        timestamptz NOT NULL DEFAULT now()
        );

        CREATE FUNCTION global_directory.directory_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.placement_epoch < OLD.placement_epoch OR NEW.record_revision < OLD.record_revision THEN
                RAISE EXCEPTION 'placement epoch and record revision only increase'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.placement_epoch = OLD.placement_epoch AND NEW.home_cell_id <> OLD.home_cell_id THEN
                RAISE EXCEPTION 'a new home cell needs a new placement epoch'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER directory_guard BEFORE UPDATE ON global_directory.tenant_directory_record
            FOR EACH ROW EXECUTE FUNCTION global_directory.directory_guard();

        CREATE TABLE global_directory.routing_alias (
            alias        text PRIMARY KEY CHECK (alias ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'),
            tenant_id    uuid NOT NULL REFERENCES global_directory.tenant_directory_record (tenant_id)
        );
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE global_directory.routing_alias;
        DROP TABLE global_directory.tenant_directory_record;
        DROP FUNCTION global_directory.directory_guard();
    """)

"""Tenant residency and sovereignty: placement record and cross-border transfer rules
(ZID-TENANCY-001 §4-6, §11, §13, §17, §19; DEC-G1 inputs; CR-014).

Revision ID: tenant_0001
Revises:
Create Date: 2026-10-21

- tenant_residency: the TenantPlacementDecision of TENANCY §13.1 that this cell is bound by -
  residency profile (SRP-0..SRP-3), home cell, permitted/prohibited recovery cells, operator
  jurisdictions for SRP-2/3, key profile, placement epoch. One row per tenant; every change bumps
  `revision` and needs an approval reference.
- residency_transfer_rule: the only way restricted data (DC2/DC3) leaves the home boundary
  (`cross_border_default: DENY`): data class, destination, purpose, transfer mechanism from the
  jurisdiction pack, approval, validity. Independently suspendable (TENANCY §11 "Revocation").
Both tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = ("tenant_residency", "residency_transfer_rule")


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.tenant_residency (
            tenant_id                    uuid PRIMARY KEY,
            residency_profile_id         text NOT NULL CHECK (residency_profile_id IN
                                         ('SRP-0', 'SRP-1', 'SRP-2', 'SRP-3')),
            home_cell_id                 text NOT NULL CHECK (home_cell_id ~ '^[a-z0-9-]{1,40}$'),
            permitted_recovery_cells     text[] NOT NULL DEFAULT '{}',
            prohibited_cells             text[] NOT NULL DEFAULT '{}',
            operator_jurisdictions       text[] NOT NULL DEFAULT '{}',
            key_profile                  text NOT NULL DEFAULT 'PLATFORM_MANAGED' CHECK (key_profile
                                         IN ('PLATFORM_MANAGED', 'DEDICATED_TENANT_KEY', 'BYOK',
                                         'HYOK')),
            jurisdiction_pack_id         text NOT NULL,
            placement_epoch              integer NOT NULL DEFAULT 1 CHECK (placement_epoch >= 1),
            approval_ref                 text NOT NULL,
            revision                     integer NOT NULL DEFAULT 1,
            updated_by                   uuid NOT NULL,
            updated_at                   timestamptz NOT NULL DEFAULT now(),
            CHECK (NOT (home_cell_id = ANY (prohibited_cells))),
            CHECK (NOT (permitted_recovery_cells && prohibited_cells)),
            CHECK (residency_profile_id IN ('SRP-0', 'SRP-1') OR cardinality(operator_jurisdictions) > 0)
        );

        CREATE TABLE platform.residency_transfer_rule (
            rule_id                      uuid PRIMARY KEY,
            tenant_id                    uuid NOT NULL,
            data_class                   text NOT NULL CHECK (data_class IN ('DC1', 'DC2', 'DC3')),
            destination_cell_id          text NOT NULL,
            purpose_code                 text NOT NULL,
            mechanism                    text NOT NULL,
            jurisdiction_pack_id         text NOT NULL,
            field_allowlist              text[] NOT NULL DEFAULT '{}',
            approval_ref                 text NOT NULL,
            created_by                   uuid NOT NULL,
            approved_by                  uuid NOT NULL,
            valid_from                   timestamptz NOT NULL,
            valid_to                     timestamptz,
            status                       text NOT NULL DEFAULT 'ACTIVE'
                                         CHECK (status IN ('ACTIVE', 'SUSPENDED', 'RETIRED')),
            created_at                   timestamptz NOT NULL DEFAULT now(),
            CHECK (approved_by <> created_by),
            CHECK (valid_to IS NULL OR valid_to > valid_from)
        );
        CREATE INDEX transfer_rule_lookup ON platform.residency_transfer_rule
            (tenant_id, data_class, destination_cell_id) WHERE status = 'ACTIVE';
    """)
    for table in TABLES:
        _run(f"""
            ALTER TABLE platform.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE platform.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON platform.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.residency_transfer_rule;
        DROP TABLE platform.tenant_residency;
    """)

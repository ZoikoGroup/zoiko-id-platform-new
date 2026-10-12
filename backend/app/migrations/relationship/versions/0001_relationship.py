"""Relationship facts and per-tenant relation revision (ZID-DATA-001 §4.2; ZID-AUTHZ-001 §7; DEC-C4).

Revision ID: relationship_0001
Revises:
Create Date: 2026-10-12

org.relationship_edge is the authoritative source the SpiceDB projection is derived from (DEC-C4).
authz.relation_publication holds each tenant's monotonic relation_revision: every material mutation
takes `relation_revision + 1` under that row's lock, so revisions never repeat or go backwards
(TR-04, INV-11). Self-edges are refused; cross-tenant objects need an explicit trust construct.
"""

from zid_platform.migrations import execute_script as _run

revision = "relationship_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TYPE org.relation_type AS ENUM ('MEMBER_OF', 'EMPLOYED_BY', 'OWNS', 'MANAGES', 'CONTROLS', 'OPERATES',
            'SPONSORS', 'CREATED_FROM', 'PARENT_OF', 'ASSIGNED_ROLE', 'OTHER');
        CREATE TYPE org.relation_status AS ENUM ('PENDING', 'ACTIVE', 'EXPIRED', 'REVOKED', 'SUPERSEDED');

        CREATE TABLE authz.relation_publication (
            tenant_id           uuid PRIMARY KEY,
            relation_revision   bigint NOT NULL DEFAULT 0 CHECK (relation_revision >= 0),
            updated_at          timestamptz NOT NULL DEFAULT now()
        );

        CREATE FUNCTION authz.relation_publication_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.relation_revision <= OLD.relation_revision THEN
                RAISE EXCEPTION 'relation_revision must increase' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER relation_publication_guard BEFORE UPDATE ON authz.relation_publication
            FOR EACH ROW EXECUTE FUNCTION authz.relation_publication_guard();

        CREATE TABLE org.relationship_edge (
            relationship_id         uuid PRIMARY KEY,
            tenant_id               uuid NOT NULL,
            subject_principal_id    uuid NOT NULL,
            relation_type           org.relation_type NOT NULL,
            object_principal_id     uuid NOT NULL,
            object_tenant_id        uuid NOT NULL,
            cross_tenant_trust_id   uuid,
            source_type             text NOT NULL,
            source_evidence_ref     uuid,
            assurance_class         smallint NOT NULL DEFAULT 0,
            status                  org.relation_status NOT NULL,
            valid_from              timestamptz NOT NULL,
            valid_to                timestamptz,
            recorded_at             timestamptz NOT NULL DEFAULT now(),
            superseded_at           timestamptz,
            relation_revision       bigint NOT NULL CHECK (relation_revision >= 1),
            CHECK (subject_principal_id <> object_principal_id),
            CHECK (object_tenant_id = tenant_id OR cross_tenant_trust_id IS NOT NULL)
        );
        CREATE INDEX edge_subject ON org.relationship_edge (tenant_id, subject_principal_id, relation_type, valid_to);
        CREATE INDEX edge_object ON org.relationship_edge (tenant_id, object_principal_id, relation_type, valid_to);
        CREATE INDEX edge_revision ON org.relationship_edge (tenant_id, relation_revision);
    """)
    for schema, table in (("org", "relationship_edge"), ("authz", "relation_publication")):
        _run(f"""
            ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON {schema}.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE org.relationship_edge;
        DROP TRIGGER relation_publication_guard ON authz.relation_publication;
        DROP FUNCTION authz.relation_publication_guard();
        DROP TABLE authz.relation_publication;
        DROP TYPE org.relation_status;
        DROP TYPE org.relation_type;
    """)

"""SpiceDB projection checkpoint (DEC-C4: relation_revision -> ZedToken).

Revision ID: relationship_0002
Revises: relationship_0001
Create Date: 2026-10-12

The projector records, per tenant, the highest relation_revision it has written to SpiceDB and the
ZedToken SpiceDB returned for it. R2/R3 checks compare it with the authoritative relation_revision:
a projection behind the required revision never produces an allow. projected_revision may never
exceed relation_revision and never goes backwards.
"""

from zid_platform.migrations import execute_script as _run

revision = "relationship_0002"
down_revision = "relationship_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE authz.relation_publication
            ADD COLUMN projected_revision bigint NOT NULL DEFAULT 0 CHECK (projected_revision >= 0),
            ADD COLUMN projected_zedtoken text,
            ADD COLUMN projected_at timestamptz,
            ADD CONSTRAINT projection_not_ahead CHECK (projected_revision <= relation_revision);

        CREATE OR REPLACE FUNCTION authz.relation_publication_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            -- Writers always take relation_revision + 1; a projection rebuild only refreshes the token.
            IF NEW.relation_revision < OLD.relation_revision THEN
                RAISE EXCEPTION 'relation_revision may not decrease' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.projected_revision < OLD.projected_revision THEN
                RAISE EXCEPTION 'projected_revision may not go backwards' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
    """)


def downgrade() -> None:
    _run("""
        CREATE OR REPLACE FUNCTION authz.relation_publication_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.relation_revision <= OLD.relation_revision THEN
                RAISE EXCEPTION 'relation_revision must increase' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        ALTER TABLE authz.relation_publication
            DROP CONSTRAINT projection_not_ahead,
            DROP COLUMN projected_at,
            DROP COLUMN projected_zedtoken,
            DROP COLUMN projected_revision;
    """)

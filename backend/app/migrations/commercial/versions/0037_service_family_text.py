"""Versioned wording for the sixteen service families (ZID-COM-WF-2026-11 WF-I01 catalogue editor, G01).

Revision ID: commercial_0037
Revises: commercial_0036
Create Date: 2026-11-13

The reviewed file `service-families.yaml` stays the source of everything that decides behaviour: the family codes, what depends on what, which are regulated, which need a
provider, which meter they use. This table holds only the WORDING a buyer reads (name, unit, sales qualifier) as maker-checker revisions on top of it. A revision starts as a
DRAFT, is activated by a different person than its author, and is then immutable; the next revision of that family supersedes it. With none active, the file's wording is used.
Wording can never change what a family does or what it needs.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0037"
down_revision = "commercial_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.service_family_text (
            rev_id          uuid PRIMARY KEY,
            family          text NOT NULL CHECK (family ~ '^[A-Z]{2,8}$'),
            version         integer NOT NULL CHECK (version >= 1),
            name            text NOT NULL CHECK (char_length(btrim(name)) BETWEEN 2 AND 80),
            unit            text NOT NULL CHECK (char_length(btrim(unit)) BETWEEN 2 AND 120),
            sales_qualifier text NOT NULL CHECK (char_length(sales_qualifier) <= 300),
            reason          text NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 3 AND 500),
            status          text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED', 'WITHDRAWN')),
            created_by      uuid NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            activated_by    uuid,
            activated_at    timestamptz,
            UNIQUE (family, version),
            CHECK ((status IN ('ACTIVE', 'SUPERSEDED')) = (activated_by IS NOT NULL AND activated_at IS NOT NULL) OR status IN ('DRAFT', 'WITHDRAWN')),
            CHECK (activated_by IS NULL OR activated_by <> created_by)
        );
        CREATE UNIQUE INDEX service_family_text_one_active ON commercial.service_family_text (family) WHERE status = 'ACTIVE';
        CREATE FUNCTION commercial.service_family_text_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'family wording revisions are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.rev_id, NEW.family, NEW.version, NEW.name, NEW.unit, NEW.sales_qualifier, NEW.reason, NEW.created_by, NEW.created_at)
               IS DISTINCT FROM
               (OLD.rev_id, OLD.family, OLD.version, OLD.name, OLD.unit, OLD.sales_qualifier, OLD.reason, OLD.created_by, OLD.created_at) THEN
                RAISE EXCEPTION 'a wording revision is never edited: write a new one' USING ERRCODE = 'check_violation';
            END IF;
            IF NOT (
                OLD.status = NEW.status
                OR (OLD.status = 'DRAFT' AND NEW.status IN ('ACTIVE', 'WITHDRAWN'))
                OR (OLD.status = 'ACTIVE' AND NEW.status = 'SUPERSEDED')
            ) THEN
                RAISE EXCEPTION 'that wording status change is not allowed' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER service_family_text_guard BEFORE UPDATE OR DELETE ON commercial.service_family_text
            FOR EACH ROW EXECUTE FUNCTION commercial.service_family_text_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.service_family_text;
        DROP FUNCTION commercial.service_family_text_guard();
    """)

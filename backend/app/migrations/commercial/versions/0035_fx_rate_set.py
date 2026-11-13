"""A versioned FX rate set for reporting (ZID-COM-WF-2026-11 section 05.2: "FX is a versioned quote input, never a live browser approximation").

Revision ID: commercial_0035
Revises: commercial_0034
Create Date: 2026-11-13

A rate set is a list of "USD per one unit of a currency" with the named source it came from and the date it is as of. It starts as a DRAFT, is activated by a different
person than its author, and is then immutable; the next set supersedes it. There are NO default rates: with no active set nothing is ever converted, and the reports say
so. A rate set is platform configuration (no tenant, no row-level security). It is used for reporting here; it does not price or charge anything.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0035"
down_revision = "commercial_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.fx_rate_set (
            set_id        uuid PRIMARY KEY,
            version       integer NOT NULL UNIQUE CHECK (version >= 1),
            status        text NOT NULL CHECK (status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED', 'WITHDRAWN')),
            source_ref    text NOT NULL CHECK (char_length(btrim(source_ref)) BETWEEN 3 AND 255),
            as_of         date NOT NULL,
            created_by    uuid NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            activated_by  uuid,
            activated_at  timestamptz,
            CHECK ((status IN ('ACTIVE', 'SUPERSEDED')) = (activated_by IS NOT NULL AND activated_at IS NOT NULL) OR status IN ('DRAFT', 'WITHDRAWN')),
            CHECK (activated_by IS NULL OR activated_by <> created_by)
        );
        CREATE TABLE commercial.fx_rate (
            set_id         uuid NOT NULL REFERENCES commercial.fx_rate_set (set_id) ON DELETE CASCADE,
            currency       text NOT NULL CHECK (currency ~ '^[A-Z]{3}$' AND currency <> 'USD'),
            usd_per_unit   numeric(24, 12) NOT NULL CHECK (usd_per_unit > 0),
            PRIMARY KEY (set_id, currency)
        );
        CREATE FUNCTION commercial.fx_rate_set_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status NOT IN ('DRAFT', 'WITHDRAWN') THEN
                    RAISE EXCEPTION 'an FX rate set that was ever active is never deleted' USING ERRCODE = 'check_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status = 'DRAFT' THEN
                IF NEW.status IN ('DRAFT', 'WITHDRAWN') THEN RETURN NEW; END IF;
                IF NEW.status = 'ACTIVE' THEN
                    IF NOT EXISTS (SELECT 1 FROM commercial.fx_rate WHERE set_id = OLD.set_id) THEN
                        RAISE EXCEPTION 'an FX rate set with no rates cannot be activated' USING ERRCODE = 'check_violation';
                    END IF;
                    RETURN NEW;
                END IF;
            END IF;
            IF OLD.status = 'ACTIVE' AND NEW.status = 'SUPERSEDED'
               AND (NEW.set_id, NEW.version, NEW.source_ref, NEW.as_of, NEW.created_by, NEW.created_at, NEW.activated_by, NEW.activated_at)
                   IS NOT DISTINCT FROM
                   (OLD.set_id, OLD.version, OLD.source_ref, OLD.as_of, OLD.created_by, OLD.created_at, OLD.activated_by, OLD.activated_at) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'an activated FX rate set is immutable' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER fx_rate_set_guard BEFORE UPDATE OR DELETE ON commercial.fx_rate_set
            FOR EACH ROW EXECUTE FUNCTION commercial.fx_rate_set_guard();
        CREATE FUNCTION commercial.fx_rate_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            s text;
            m uuid;
        BEGIN
            m := CASE WHEN TG_OP = 'DELETE' THEN OLD.set_id ELSE NEW.set_id END;
            SELECT status INTO s FROM commercial.fx_rate_set WHERE set_id = m;
            IF s IS NULL THEN
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RAISE EXCEPTION 'no such FX rate set' USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF s <> 'DRAFT' THEN
                RAISE EXCEPTION 'only a DRAFT FX rate set can change its rates' USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER fx_rate_guard BEFORE INSERT OR UPDATE OR DELETE ON commercial.fx_rate
            FOR EACH ROW EXECUTE FUNCTION commercial.fx_rate_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.fx_rate;
        DROP TABLE commercial.fx_rate_set;
        DROP FUNCTION commercial.fx_rate_guard();
        DROP FUNCTION commercial.fx_rate_set_guard();
    """)

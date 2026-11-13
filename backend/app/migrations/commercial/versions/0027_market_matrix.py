"""The market matrix: which service family is sellable where, with whom as seller, and on whose signed evidence (ZID-COM-WF-2026-11 v3.0 WF-I01, WF-I05, section 05.1).

Revision ID: commercial_0027
Revises: commercial_0026
Create Date: 2026-11-13

A matrix is a versioned set of rows, one per (family, country). A row names the seller entity, the merchant route, the currency, the tax class, the data
region, and a REFERENCE to each piece of signed evidence (the offer, the provider rights, the legal basis, the regulated status, the release evidence). A
reference is not proof: the control is that four DIFFERENT people, none of them the author, each sign for a named role (PRODUCT, FINANCE, LEGAL_PRIVACY,
SECURITY_QA) before the matrix can be activated, and that an activated matrix is then immutable. The database enforces all of it, as for price books:

- rows and approvals can only be added to a DRAFT matrix; a DRAFT can be edited or withdrawn;
- an approval cannot be by the author, and one person cannot sign twice (one person, one role);
- a matrix becomes ACTIVE only with all four approvals, at least one row, an effective date, and an activator who is not the author;
- an ACTIVE matrix is immutable and is only ever superseded by the next, never edited or deleted.

Platform configuration, not tenant data: no tenant_id, no row-level security. Nothing here prices anything or turns a service on by itself.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0027"
down_revision = "commercial_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.market_matrix (
            matrix_id      uuid PRIMARY KEY,
            version        integer NOT NULL UNIQUE CHECK (version >= 1),
            status         text NOT NULL CHECK (status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED', 'WITHDRAWN')),
            effective_from timestamptz,
            created_by     uuid NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            activated_by   uuid,
            activated_at   timestamptz,
            note           text CHECK (note IS NULL OR char_length(note) <= 500),
            CHECK (status IN ('DRAFT', 'WITHDRAWN') OR (effective_from IS NOT NULL AND activated_by IS NOT NULL AND activated_at IS NOT NULL)),
            CHECK (activated_by IS NULL OR activated_by <> created_by)
        );

        CREATE TABLE commercial.market_matrix_row (
            matrix_id           uuid NOT NULL REFERENCES commercial.market_matrix (matrix_id) ON DELETE CASCADE,
            family              text NOT NULL CHECK (family ~ '^[A-Z]{2,8}$'),
            country             text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            seller_entity       text NOT NULL CHECK (char_length(btrim(seller_entity)) BETWEEN 1 AND 160),
            merchant_route      text NOT NULL CHECK (char_length(btrim(merchant_route)) BETWEEN 1 AND 160),
            currency            text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            tax_class           text NOT NULL CHECK (char_length(btrim(tax_class)) BETWEEN 1 AND 80),
            data_region         text NOT NULL CHECK (char_length(btrim(data_region)) BETWEEN 1 AND 40),
            offer_ref           text NOT NULL CHECK (char_length(btrim(offer_ref)) BETWEEN 1 AND 255),
            provider_rights_ref text CHECK (provider_rights_ref IS NULL OR char_length(btrim(provider_rights_ref)) BETWEEN 1 AND 255),
            legal_basis_ref     text NOT NULL CHECK (char_length(btrim(legal_basis_ref)) BETWEEN 1 AND 255),
            regulated_status_ref text CHECK (regulated_status_ref IS NULL OR char_length(btrim(regulated_status_ref)) BETWEEN 1 AND 255),
            release_evidence_ref text NOT NULL CHECK (char_length(btrim(release_evidence_ref)) BETWEEN 1 AND 255),
            payment_configured  boolean NOT NULL,
            tax_configured      boolean NOT NULL,
            PRIMARY KEY (matrix_id, family, country)
        );

        CREATE TABLE commercial.market_matrix_approval (
            matrix_id   uuid NOT NULL REFERENCES commercial.market_matrix (matrix_id) ON DELETE CASCADE,
            role        text NOT NULL CHECK (role IN ('PRODUCT', 'FINANCE', 'LEGAL_PRIVACY', 'SECURITY_QA')),
            approver    uuid NOT NULL,
            approved_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (matrix_id, role),
            UNIQUE (matrix_id, approver)
        );

        CREATE FUNCTION commercial.market_matrix_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'DRAFT' THEN
                    RAISE EXCEPTION 'a market matrix that was ever active is never deleted' USING ERRCODE = 'check_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status = 'DRAFT' THEN
                IF NEW.status = 'DRAFT' OR NEW.status = 'WITHDRAWN' THEN
                    RETURN NEW;
                END IF;
                IF NEW.status = 'ACTIVE' THEN
                    IF (SELECT count(*) FROM commercial.market_matrix_approval WHERE matrix_id = OLD.matrix_id) <> 4 THEN
                        RAISE EXCEPTION 'activation needs all four independent approvals' USING ERRCODE = 'check_violation';
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM commercial.market_matrix_row WHERE matrix_id = OLD.matrix_id) THEN
                        RAISE EXCEPTION 'a matrix with no rows cannot be activated' USING ERRCODE = 'check_violation';
                    END IF;
                    RETURN NEW;
                END IF;
            END IF;
            IF OLD.status = 'ACTIVE' AND NEW.status = 'SUPERSEDED'
               AND (NEW.matrix_id, NEW.version, NEW.effective_from, NEW.created_by, NEW.created_at, NEW.activated_by, NEW.activated_at, NEW.note)
                   IS NOT DISTINCT FROM
                   (OLD.matrix_id, OLD.version, OLD.effective_from, OLD.created_by, OLD.created_at, OLD.activated_by, OLD.activated_at, OLD.note) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'an activated market matrix is immutable' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER market_matrix_guard BEFORE UPDATE OR DELETE ON commercial.market_matrix
            FOR EACH ROW EXECUTE FUNCTION commercial.market_matrix_guard();

        CREATE FUNCTION commercial.market_matrix_row_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            m uuid;
            s text;
        BEGIN
            m := CASE WHEN TG_OP = 'DELETE' THEN OLD.matrix_id ELSE NEW.matrix_id END;
            SELECT status INTO s FROM commercial.market_matrix WHERE matrix_id = m;
            IF s IS NULL THEN
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RAISE EXCEPTION 'no such market matrix' USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF s <> 'DRAFT' THEN
                RAISE EXCEPTION 'only a DRAFT market matrix can change its rows' USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER market_matrix_row_guard BEFORE INSERT OR UPDATE OR DELETE ON commercial.market_matrix_row
            FOR EACH ROW EXECUTE FUNCTION commercial.market_matrix_row_guard();

        CREATE FUNCTION commercial.market_matrix_approval_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            s text;
            author uuid;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                -- cascading deletes of a withdrawn or draft matrix are the only removal
                IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM commercial.market_matrix WHERE matrix_id = OLD.matrix_id) THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'an approval is never changed or removed' USING ERRCODE = 'check_violation';
            END IF;
            SELECT status, created_by INTO s, author FROM commercial.market_matrix WHERE matrix_id = NEW.matrix_id;
            IF s IS NULL THEN
                RAISE EXCEPTION 'no such market matrix' USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF s <> 'DRAFT' THEN
                RAISE EXCEPTION 'only a DRAFT market matrix can be approved' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.approver = author THEN
                RAISE EXCEPTION 'the author cannot approve their own market matrix' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER market_matrix_approval_guard BEFORE INSERT OR UPDATE OR DELETE ON commercial.market_matrix_approval
            FOR EACH ROW EXECUTE FUNCTION commercial.market_matrix_approval_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.market_matrix_approval;
        DROP TABLE commercial.market_matrix_row;
        DROP TABLE commercial.market_matrix;
        DROP FUNCTION commercial.market_matrix_approval_guard();
        DROP FUNCTION commercial.market_matrix_row_guard();
        DROP FUNCTION commercial.market_matrix_guard();
    """)

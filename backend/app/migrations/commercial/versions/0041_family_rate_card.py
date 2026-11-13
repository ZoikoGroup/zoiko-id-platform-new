"""The family rate card: signed rates per service family, market and billing period (ZID-UX-PRICE-004 sections 03.3, 06; ZID-COM-WF-2026-11 section 03, G1 price book).

Revision ID: commercial_0041
Revises: commercial_0040
Create Date: 2026-11-13

A rate card is a versioned set of rate lines, one per (family, payer country, billing period): the currency, the exact unit it is charged in, the units included, the amount per unit above that, and an
optional minimum that declares whether it is a spend FLOOR applied against usage or a PLATFORM_FEE billed in addition (never silently both). There are NO default rates and no example rates anywhere: every
line is entered by an owner from a signed source, with its reference. It follows the price-book discipline:

- lines can only be added to a DRAFT; a DRAFT can be edited or withdrawn;
- three DIFFERENT people sign, each for one role (CFO, CCO, FOUNDER), none of them the author, before the card can be activated; activation is by someone who is not the author;
- an ACTIVE card is immutable and is only ever superseded by the next, never edited or deleted.

Platform configuration, not tenant data: no tenant id, no row-level security. A card prices nothing by itself: the public page and the estimator read the ACTIVE card, only for a market and service the market matrix has
released, and say Quote required for everything else.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0041"
down_revision = "commercial_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.family_rate_card (
            card_id        uuid PRIMARY KEY,
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
        CREATE TABLE commercial.family_rate (
            card_id         uuid NOT NULL REFERENCES commercial.family_rate_card (card_id) ON DELETE CASCADE,
            family          text NOT NULL CHECK (family ~ '^[A-Z]{2,8}$'),
            country         text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            period          text NOT NULL CHECK (period IN ('MONTHLY', 'ANNUAL')),
            currency        text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            charge_unit     text NOT NULL CHECK (char_length(btrim(charge_unit)) BETWEEN 3 AND 120),
            included_units  bigint NOT NULL DEFAULT 0 CHECK (included_units >= 0),
            unit_amount     numeric(18, 6) NOT NULL DEFAULT 0 CHECK (unit_amount >= 0),
            minimum_amount  numeric(18, 6) CHECK (minimum_amount IS NULL OR minimum_amount > 0),
            minimum_model   text CHECK (minimum_model IN ('FLOOR', 'PLATFORM_FEE')),
            source_ref      text NOT NULL CHECK (char_length(btrim(source_ref)) BETWEEN 1 AND 255),
            PRIMARY KEY (card_id, family, country, period),
            CHECK ((minimum_amount IS NULL) = (minimum_model IS NULL)),
            CHECK (unit_amount > 0 OR minimum_amount IS NOT NULL)
        );
        CREATE TABLE commercial.family_rate_card_approval (
            card_id     uuid NOT NULL REFERENCES commercial.family_rate_card (card_id) ON DELETE CASCADE,
            role        text NOT NULL CHECK (role IN ('CFO', 'CCO', 'FOUNDER')),
            approver    uuid NOT NULL,
            approved_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (card_id, role),
            UNIQUE (card_id, approver)
        );

        CREATE FUNCTION commercial.family_rate_card_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status NOT IN ('DRAFT', 'WITHDRAWN') THEN
                    RAISE EXCEPTION 'a rate card that was ever active is never deleted' USING ERRCODE = 'check_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status = 'DRAFT' THEN
                IF NEW.status IN ('DRAFT', 'WITHDRAWN') THEN RETURN NEW; END IF;
                IF NEW.status = 'ACTIVE' THEN
                    IF (SELECT count(*) FROM commercial.family_rate_card_approval WHERE card_id = OLD.card_id) <> 3 THEN
                        RAISE EXCEPTION 'activation needs all three independent approvals' USING ERRCODE = 'check_violation';
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM commercial.family_rate WHERE card_id = OLD.card_id) THEN
                        RAISE EXCEPTION 'a rate card with no lines cannot be activated' USING ERRCODE = 'check_violation';
                    END IF;
                    RETURN NEW;
                END IF;
            END IF;
            IF OLD.status = 'ACTIVE' AND NEW.status = 'SUPERSEDED'
               AND (NEW.card_id, NEW.version, NEW.effective_from, NEW.created_by, NEW.created_at, NEW.activated_by, NEW.activated_at, NEW.note)
                   IS NOT DISTINCT FROM
                   (OLD.card_id, OLD.version, OLD.effective_from, OLD.created_by, OLD.created_at, OLD.activated_by, OLD.activated_at, OLD.note) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'an activated rate card is immutable' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER family_rate_card_guard BEFORE UPDATE OR DELETE ON commercial.family_rate_card
            FOR EACH ROW EXECUTE FUNCTION commercial.family_rate_card_guard();

        CREATE FUNCTION commercial.family_rate_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            m uuid;
            s text;
        BEGIN
            m := CASE WHEN TG_OP = 'DELETE' THEN OLD.card_id ELSE NEW.card_id END;
            SELECT status INTO s FROM commercial.family_rate_card WHERE card_id = m;
            IF s IS NULL THEN
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RAISE EXCEPTION 'no such rate card' USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF s <> 'DRAFT' THEN
                RAISE EXCEPTION 'only a DRAFT rate card can change its rates' USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER family_rate_guard BEFORE INSERT OR UPDATE OR DELETE ON commercial.family_rate
            FOR EACH ROW EXECUTE FUNCTION commercial.family_rate_guard();

        CREATE FUNCTION commercial.family_rate_card_approval_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            s text;
            author uuid;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM commercial.family_rate_card WHERE card_id = OLD.card_id) THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'an approval is never changed or removed' USING ERRCODE = 'check_violation';
            END IF;
            SELECT status, created_by INTO s, author FROM commercial.family_rate_card WHERE card_id = NEW.card_id;
            IF s IS NULL THEN
                RAISE EXCEPTION 'no such rate card' USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF s <> 'DRAFT' THEN
                RAISE EXCEPTION 'only a DRAFT rate card can be approved' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.approver = author THEN
                RAISE EXCEPTION 'the author cannot approve their own rate card' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER family_rate_card_approval_guard BEFORE INSERT OR UPDATE OR DELETE ON commercial.family_rate_card_approval
            FOR EACH ROW EXECUTE FUNCTION commercial.family_rate_card_approval_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.family_rate_card_approval;
        DROP TABLE commercial.family_rate;
        DROP TABLE commercial.family_rate_card;
        DROP FUNCTION commercial.family_rate_card_approval_guard();
        DROP FUNCTION commercial.family_rate_guard();
        DROP FUNCTION commercial.family_rate_card_guard();
    """)

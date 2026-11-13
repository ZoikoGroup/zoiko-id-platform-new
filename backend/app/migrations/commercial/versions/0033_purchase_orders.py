"""Customer purchase orders and the draw-down of invoices against them (ZID-COM-WF-2026-11 CW-11).

Revision ID: commercial_0033
Revises: commercial_0032
Create Date: 2026-11-13

A company that issues purchase orders has them recorded here (by the operator plane, from the order the company supplied). From then on an invoice for that company
is issued only against a PO in the invoice's currency, inside its validity, with enough value left; the amount is drawn down in a separate, append-only row, so the
invoice document and its hash chain are untouched. A company that has no PO on record is unaffected. A PO is never deleted: it is closed.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0033"
down_revision = "commercial_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.purchase_order (
            po_id         uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            po_number     text NOT NULL CHECK (char_length(btrim(po_number)) BETWEEN 1 AND 80),
            currency      text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            value         numeric(18, 6) NOT NULL CHECK (value > 0),
            valid_from    date NOT NULL,
            valid_until   date NOT NULL,
            status        text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CLOSED')),
            created_by    uuid NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            closed_at     timestamptz,
            UNIQUE (tenant_id, po_number),
            CHECK (valid_until >= valid_from),
            CHECK ((status = 'CLOSED') = (closed_at IS NOT NULL))
        );
        CREATE FUNCTION commercial.purchase_order_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a purchase order is never deleted; close it' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'ACTIVE' AND NEW.status = 'CLOSED'
               AND (NEW.po_id, NEW.tenant_id, NEW.po_number, NEW.currency, NEW.value, NEW.valid_from, NEW.valid_until, NEW.created_by, NEW.created_at)
                   IS NOT DISTINCT FROM
                   (OLD.po_id, OLD.tenant_id, OLD.po_number, OLD.currency, OLD.value, OLD.valid_from, OLD.valid_until, OLD.created_by, OLD.created_at) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'a purchase order changes only by being closed' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER purchase_order_guard BEFORE UPDATE OR DELETE ON commercial.purchase_order
            FOR EACH ROW EXECUTE FUNCTION commercial.purchase_order_guard();
        ALTER TABLE commercial.purchase_order ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.purchase_order FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.purchase_order
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        CREATE POLICY operator_scope ON commercial.purchase_order
            USING (current_setting('app.commercial_scope', true) = 'operator')
            WITH CHECK (current_setting('app.commercial_scope', true) = 'operator');

        CREATE TABLE commercial.purchase_order_draw (
            invoice_id  uuid PRIMARY KEY,
            po_id       uuid NOT NULL REFERENCES commercial.purchase_order (po_id),
            tenant_id   uuid NOT NULL,
            amount      numeric(18, 6) NOT NULL CHECK (amount >= 0),
            drawn_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE FUNCTION commercial.purchase_order_draw_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF (SELECT coalesce(sum(amount), 0) FROM commercial.purchase_order_draw WHERE po_id = NEW.po_id) + NEW.amount
                   > (SELECT value FROM commercial.purchase_order WHERE po_id = NEW.po_id) THEN
                    RAISE EXCEPTION 'a purchase order cannot be drawn beyond its value' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'purchase order draws are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER purchase_order_draw_guard BEFORE INSERT OR UPDATE OR DELETE ON commercial.purchase_order_draw
            FOR EACH ROW EXECUTE FUNCTION commercial.purchase_order_draw_guard();
        ALTER TABLE commercial.purchase_order_draw ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.purchase_order_draw FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.purchase_order_draw
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        CREATE POLICY operator_scope ON commercial.purchase_order_draw
            USING (current_setting('app.commercial_scope', true) = 'operator');
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.purchase_order_draw;
        DROP TABLE commercial.purchase_order;
        DROP FUNCTION commercial.purchase_order_draw_guard();
        DROP FUNCTION commercial.purchase_order_guard();
    """)

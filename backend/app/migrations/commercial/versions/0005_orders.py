"""Commercial accounts, quotes, approvals and orders (ZID-COM-001 §10, §12, §14, §17, §18; CR-028).

Revision ID: commercial_0005
Revises: commercial_0004
Create Date: 2026-10-24

- `account`: the legal buyer. It owns tenants through `account_tenant` (one ACTIVE account per tenant); a
  reseller's end customer points at its reseller; INTERNAL accounts are Zoiko Group affiliates, excluded from
  external revenue.
- `quote` / `quote_line` / `quote_approval`: an approval binds the quote's digest, so any change needs new
  approvals; the quote's author never approves it.
- `sales_order` / `sales_order_line`: an accepted quote signed by the buyer. Activation is the only route that
  raises a tenant's entitlement from an order; it is idempotent on the order.
- Commercial truth tables, all shipped EMPTY because each is a business decision, not a code default:
  `region_release` (a cell whose SRE gate has passed may be sold), `price_floor` (minimum net unit price by
  SKU and region), `commercial_role_holder` (who holds AE / CCO / CFO / FOUNDER / GROUP_CFO).
- `discount_policy` is seeded from the proposal's §12 bands, marked as the proposal.

These are operator-plane records: accounts and orders are not tenant data and are read and written only by
operators; a tenant sees its own orders through the server-derived account mapping.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0005"
down_revision = "commercial_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.account (
            account_id        uuid PRIMARY KEY,
            legal_name        text NOT NULL CHECK (length(legal_name) BETWEEN 1 AND 300),
            registration      text,
            country           text CHECK (country ~ '^[A-Z]{2}$'),
            currency          text NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{3}$'),
            kind              text NOT NULL CHECK (kind IN ('DIRECT', 'PARTNER', 'INTERNAL')),
            parent_account_id uuid REFERENCES commercial.account (account_id),
            reseller_of       uuid REFERENCES commercial.account (account_id),
            risk_status       text NOT NULL DEFAULT 'STANDARD' CHECK (risk_status IN ('STANDARD', 'REVIEW', 'BLOCKED')),
            created_by        uuid NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            CHECK (reseller_of IS NULL OR reseller_of <> account_id)
        );

        CREATE TABLE commercial.account_tenant (
            account_id     uuid NOT NULL REFERENCES commercial.account (account_id),
            tenant_id      uuid NOT NULL,
            effective_from timestamptz NOT NULL DEFAULT now(),
            effective_to   timestamptz,
            PRIMARY KEY (account_id, tenant_id, effective_from)
        );
        CREATE UNIQUE INDEX account_tenant_one_active ON commercial.account_tenant (tenant_id)
            WHERE effective_to IS NULL;

        CREATE TABLE commercial.region_release (
            region          text PRIMARY KEY CHECK (region ~ '^[a-z0-9-]{1,40}$'),
            sre_gate_passed boolean NOT NULL,
            evidence_ref    text NOT NULL,
            recorded_by     uuid NOT NULL,
            recorded_at     timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE commercial.price_floor (
            sku_id      text NOT NULL REFERENCES commercial.sku (sku_id),
            region      text NOT NULL,
            floor_price numeric(18, 6) NOT NULL CHECK (floor_price >= 0),
            source      text NOT NULL,
            PRIMARY KEY (sku_id, region)
        );

        CREATE TABLE commercial.commercial_role_holder (
            principal_id uuid NOT NULL,
            role         text NOT NULL CHECK (role IN ('AE', 'CCO', 'CFO', 'FOUNDER', 'GROUP_CFO')),
            granted_by   uuid NOT NULL,
            granted_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (principal_id, role),
            CHECK (granted_by <> principal_id)
        );

        CREATE TABLE commercial.discount_policy (
            band_max  numeric(6, 4),
            approvers text[] NOT NULL CHECK (cardinality(approvers) >= 1),
            source    text NOT NULL
        );
        CREATE UNIQUE INDEX discount_policy_band ON commercial.discount_policy (coalesce(band_max, 99));
        INSERT INTO commercial.discount_policy (band_max, approvers, source) VALUES
            (0.05, ARRAY['AE'], 'ZID-COM-001 12 (proposal)'),
            (0.12, ARRAY['CCO'], 'ZID-COM-001 12 (proposal)'),
            (NULL, ARRAY['CFO', 'FOUNDER'], 'ZID-COM-001 12 (proposal)');

        CREATE TABLE commercial.quote (
            quote_id        uuid PRIMARY KEY,
            account_id      uuid NOT NULL REFERENCES commercial.account (account_id),
            price_book_id   uuid NOT NULL REFERENCES commercial.price_book (price_book_id),
            currency        text NOT NULL,
            region          text NOT NULL,
            term_months     integer NOT NULL CHECK (term_months BETWEEN 1 AND 60),
            prepaid         boolean NOT NULL DEFAULT false,
            prepaid_discount numeric(6, 4) NOT NULL DEFAULT 0,
            liability_capped boolean NOT NULL DEFAULT true,
            residency_commitment text,
            pilot           boolean NOT NULL DEFAULT false,
            status          text NOT NULL CHECK (status IN
                ('VALIDATED', 'NEEDS_APPROVAL', 'APPROVED', 'REJECTED', 'ACCEPTED', 'EXPIRED')),
            reasons         text[] NOT NULL DEFAULT '{}',
            approvers_required text[] NOT NULL DEFAULT '{}',
            digest          text NOT NULL CHECK (length(digest) = 64),
            valid_until     timestamptz NOT NULL,
            created_by      uuid NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE commercial.quote_line (
            quote_id      uuid NOT NULL REFERENCES commercial.quote (quote_id),
            seq           integer NOT NULL CHECK (seq >= 1),
            sku_id        text NOT NULL,
            plan_code     text NOT NULL,
            quantity      numeric(18, 6) NOT NULL CHECK (quantity > 0),
            discount_pct  numeric(6, 4) NOT NULL DEFAULT 0 CHECK (discount_pct >= 0 AND discount_pct < 1),
            list_price    numeric(18, 6),
            net_price     numeric(18, 6),
            PRIMARY KEY (quote_id, seq)
        );

        CREATE TABLE commercial.quote_approval (
            quote_id    uuid NOT NULL REFERENCES commercial.quote (quote_id),
            role        text NOT NULL,
            approver    uuid NOT NULL,
            digest      text NOT NULL,
            decided_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (quote_id, role)
        );

        CREATE TABLE commercial.sales_order (
            order_id         uuid PRIMARY KEY,
            account_id       uuid NOT NULL REFERENCES commercial.account (account_id),
            quote_id         uuid NOT NULL REFERENCES commercial.quote (quote_id),
            tenant_id        uuid NOT NULL,
            plan_code        text NOT NULL REFERENCES commercial.plan (plan_code),
            price_book_id    uuid NOT NULL REFERENCES commercial.price_book (price_book_id),
            signer_ref       text NOT NULL CHECK (length(signer_ref) BETWEEN 3 AND 200),
            signed_document_digest text NOT NULL CHECK (length(signed_document_digest) = 64),
            effective_from   timestamptz NOT NULL,
            term_months      integer NOT NULL,
            amends_order_id  uuid REFERENCES commercial.sales_order (order_id),
            pilot            boolean NOT NULL DEFAULT false,
            pilot_ends_at    timestamptz,
            status           text NOT NULL CHECK (status IN ('SIGNED', 'ACTIVATED', 'SUPERSEDED')),
            activated_at     timestamptz,
            activated_by     uuid,
            created_by       uuid NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now(),
            UNIQUE (quote_id),
            CHECK (NOT pilot OR pilot_ends_at IS NOT NULL)
        );

        CREATE TABLE commercial.sales_order_line (
            order_id   uuid NOT NULL REFERENCES commercial.sales_order (order_id),
            seq        integer NOT NULL,
            sku_id     text NOT NULL,
            plan_code  text NOT NULL,
            quantity   numeric(18, 6) NOT NULL,
            net_price  numeric(18, 6),
            PRIMARY KEY (order_id, seq)
        );

        CREATE FUNCTION commercial.order_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'orders are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.order_id, NEW.account_id, NEW.quote_id, NEW.tenant_id, NEW.plan_code, NEW.price_book_id,
                NEW.signer_ref, NEW.signed_document_digest, NEW.effective_from, NEW.term_months, NEW.amends_order_id,
                NEW.pilot, NEW.pilot_ends_at, NEW.created_by, NEW.created_at)
               IS DISTINCT FROM
               (OLD.order_id, OLD.account_id, OLD.quote_id, OLD.tenant_id, OLD.plan_code, OLD.price_book_id,
                OLD.signer_ref, OLD.signed_document_digest, OLD.effective_from, OLD.term_months, OLD.amends_order_id,
                OLD.pilot, OLD.pilot_ends_at, OLD.created_by, OLD.created_at) THEN
                RAISE EXCEPTION 'a signed order is never repriced or rewritten' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER order_guard BEFORE UPDATE OR DELETE ON commercial.sales_order
            FOR EACH ROW EXECUTE FUNCTION commercial.order_guard();
        CREATE TRIGGER order_line_immutable BEFORE UPDATE OR DELETE ON commercial.sales_order_line
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();
        CREATE TRIGGER quote_line_immutable BEFORE UPDATE OR DELETE ON commercial.quote_line
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();
        CREATE TRIGGER quote_approval_immutable BEFORE UPDATE OR DELETE ON commercial.quote_approval
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.sales_order_line;
        DROP TABLE commercial.sales_order;
        DROP FUNCTION commercial.order_guard();
        DROP TABLE commercial.quote_approval;
        DROP TABLE commercial.quote_line;
        DROP TABLE commercial.quote;
        DROP TABLE commercial.discount_policy;
        DROP TABLE commercial.commercial_role_holder;
        DROP TABLE commercial.price_floor;
        DROP TABLE commercial.region_release;
        DROP TABLE commercial.account_tenant;
        DROP TABLE commercial.account;
    """)

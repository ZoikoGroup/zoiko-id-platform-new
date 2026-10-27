"""Vendor-cost ledger for third-party checks (ZID-COM-001 §7, §8, §23, §31; CR-029).

Revision ID: commercial_0007
Revises: commercial_0006
Create Date: 2026-10-24

- `vendor`: a supplier, its legal role (sub-processor or independent controller), the verification adapter it
  answers for, the recognition basis Finance assigns (PRINCIPAL / AGENT / UNDECIDED) and an optional
  rate-spike threshold. Ships EMPTY: no supplier is assumed.
- `vendor_rate`: versioned price per (vendor, check type, country, modality); a new rate supersedes the old.
  Only SIGNED_TARIFF confirms a route for quoting.
- `vendor_route`: ACTIVE / BLOCKED / NEEDS_APPROVAL per (vendor, check type, country). An unsupported country
  or an unapproved rate spike blocks new quotes, not existing orders.
- `vendor_cost_event`: what one attempt cost, whether the customer may be charged and at which basis.
  Insert-only, unique per supplier reference. A platform cost ledger, not tenant data: it carries the tenant
  only for attribution and an opaque subject reference, and is read by the operator plane alone, because a
  supplier invoice spans every tenant and must be reconciled as a whole.
- `vendor_invoice_line`: imported supplier invoice lines; `vendor_dispute`: reconciliation findings with an owner.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0007"
down_revision = "commercial_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.vendor (
            vendor_id         text PRIMARY KEY CHECK (vendor_id ~ '^[a-z0-9-]{2,40}$'),
            display_name      text NOT NULL,
            legal_role        text NOT NULL CHECK (legal_role IN ('SUBPROCESSOR', 'INDEPENDENT_CONTROLLER')),
            adapter_id        text UNIQUE,
            sku_id            text NOT NULL REFERENCES commercial.sku (sku_id),
            recognition_basis text NOT NULL DEFAULT 'UNDECIDED' CHECK (recognition_basis IN ('PRINCIPAL', 'AGENT', 'UNDECIDED')),
            spike_threshold_pct numeric(6, 2) CHECK (spike_threshold_pct > 0),
            status            text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED')),
            created_by        uuid NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE commercial.vendor_rate (
            rate_id      uuid PRIMARY KEY,
            vendor_id    text NOT NULL REFERENCES commercial.vendor (vendor_id),
            check_type   text NOT NULL CHECK (check_type ~ '^[A-Z][A-Z0-9_]{1,40}$'),
            country      text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            modality     text NOT NULL DEFAULT 'STANDARD',
            base_price   numeric(18, 6) NOT NULL CHECK (base_price >= 0),
            retry_price  numeric(18, 6) NOT NULL CHECK (retry_price >= 0),
            currency     text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            confidence   text NOT NULL CHECK (confidence IN ('SIGNED_TARIFF', 'QUOTED', 'ESTIMATE')),
            source_ref   text NOT NULL,
            valid_from   timestamptz NOT NULL DEFAULT now(),
            superseded_at timestamptz,
            recorded_by  uuid NOT NULL
        );
        CREATE UNIQUE INDEX vendor_rate_current ON commercial.vendor_rate (vendor_id, check_type, country, modality)
            WHERE superseded_at IS NULL;

        CREATE TABLE commercial.vendor_route (
            vendor_id  text NOT NULL REFERENCES commercial.vendor (vendor_id),
            check_type text NOT NULL,
            country    text NOT NULL,
            status     text NOT NULL CHECK (status IN ('ACTIVE', 'BLOCKED', 'NEEDS_APPROVAL')),
            reason     text NOT NULL,
            changed_by uuid,
            changed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (vendor_id, check_type, country)
        );

        CREATE TABLE commercial.vendor_cost_event (
            cost_id       uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            vendor_id     text NOT NULL REFERENCES commercial.vendor (vendor_id),
            supplier_ref  text NOT NULL CHECK (length(supplier_ref) BETWEEN 1 AND 200),
            check_type    text NOT NULL,
            country       text NOT NULL,
            outcome       text NOT NULL CHECK (outcome IN
                ('COMPLETED', 'INSUFFICIENT_DATA', 'CUSTOMER_RETRY', 'PROVIDER_ERROR', 'PLATFORM_ERROR')),
            attempt_no    integer NOT NULL CHECK (attempt_no >= 1),
            cost          numeric(18, 6) NOT NULL CHECK (cost >= 0),
            cost_currency text NOT NULL,
            rate_id       uuid REFERENCES commercial.vendor_rate (rate_id),
            chargeable    boolean NOT NULL,
            charge_basis  text NOT NULL CHECK (charge_basis IN ('BASE', 'RETRY', 'NONE')),
            subject_ref   text,
            occurred_at   timestamptz NOT NULL,
            recorded_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (vendor_id, supplier_ref),
            CHECK (chargeable = (charge_basis <> 'NONE'))
        );
        CREATE TRIGGER vendor_cost_event_immutable BEFORE UPDATE OR DELETE ON commercial.vendor_cost_event
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();

        CREATE TABLE commercial.vendor_invoice_line (
            line_id      uuid PRIMARY KEY,
            vendor_id    text NOT NULL REFERENCES commercial.vendor (vendor_id),
            invoice_ref  text NOT NULL,
            supplier_ref text NOT NULL,
            amount       numeric(18, 6) NOT NULL,
            currency     text NOT NULL,
            imported_by  uuid NOT NULL,
            imported_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE TRIGGER vendor_invoice_line_immutable BEFORE UPDATE OR DELETE ON commercial.vendor_invoice_line
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();

        CREATE TABLE commercial.vendor_dispute (
            dispute_id   uuid PRIMARY KEY,
            vendor_id    text NOT NULL REFERENCES commercial.vendor (vendor_id),
            invoice_ref  text NOT NULL,
            supplier_ref text NOT NULL,
            kind         text NOT NULL CHECK (kind IN ('AMOUNT_MISMATCH', 'MISSING_ON_INVOICE', 'UNKNOWN_ON_INVOICE', 'DUPLICATE_ON_INVOICE')),
            ours         numeric(18, 6),
            theirs       numeric(18, 6),
            status       text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
            owner        uuid,
            opened_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (vendor_id, invoice_ref, supplier_ref, kind)
        );
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.vendor_dispute;
        DROP TABLE commercial.vendor_invoice_line;
        DROP TABLE commercial.vendor_cost_event;
        DROP TABLE commercial.vendor_route;
        DROP TABLE commercial.vendor_rate;
        DROP TABLE commercial.vendor;
    """)

# Migrations: `commercial` module

Alembic stream owned by the **commercial** module (zoiko-id-platform, DEC-D6). Version table: `zid_migrations.alembic_version__commercial`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `commercial.sku`
- `commercial.capability`
- `commercial.plan`
- `commercial.plan_capability`
- `commercial.price_book`
- `commercial.price_book_line`
- `commercial.entitlement`
- `commercial.entitlement_override`
- `commercial.billable_event`
- `commercial.billable_adjustment`
- `commercial.meter_window`
- `commercial.mah_activity`
- `commercial.rating_run`
- `commercial.rated_usage`
- `commercial.invoice_sequence`
- `commercial.invoice_chain`
- `commercial.invoice`
- `commercial.invoice_line`
- `commercial.credit_note`
- `commercial.payment_attempt`
- `commercial.billing_account_state`
- `commercial.billing_state_history`
- `commercial.dunning_policy`
- `commercial.account`
- `commercial.account_tenant`
- `commercial.region_release`
- `commercial.price_floor`
- `commercial.commercial_role_holder`
- `commercial.discount_policy`
- `commercial.quote`
- `commercial.quote_line`
- `commercial.quote_approval`
- `commercial.sales_order`
- `commercial.sales_order_line`
- `commercial.budget`
- `commercial.budget_alert`
- `commercial.vendor`
- `commercial.vendor_rate`
- `commercial.vendor_route`
- `commercial.vendor_cost_event`
- `commercial.vendor_invoice_line`
- `commercial.vendor_dispute`
- `commercial.change_log_head`
- `commercial.change_log`
- `commercial.assurance_exception`
- `commercial.close_run`
- `commercial.mah_merge`
- `commercial.payment_customer`

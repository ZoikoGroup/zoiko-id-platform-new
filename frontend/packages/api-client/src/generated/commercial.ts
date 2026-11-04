// GENERATED from contracts/openapi/public/commercial.json by packages/api-client/openapi/codegen.mjs. Do not edit.
export interface paths {
    "/v1/billing/estimate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Billing Estimate
         * @description Public pricing calculator: the same rating function over the approved price book. Never a quotation.
         */
        post: operations["billing_estimate_v1_billing_estimate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/catalog": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Catalog
         * @description Public and cached: only published plans, released capabilities and APPROVED prices. No tenant data.
         */
        get: operations["catalog_v1_catalog_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/accounts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Account */
        post: operations["create_account_v1_commercial_accounts_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/accounts/{account_id}/payment-customer": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Payment Customer
         * @description Link an account to its customer id at the payment processor: a reference, never card data.
         */
        put: operations["set_payment_customer_v1_commercial_accounts__account_id__payment_customer_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/accounts/{account_id}/tenants/{tenant_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Link Account Tenant */
        post: operations["link_account_tenant_v1_commercial_accounts__account_id__tenants__tenant_id__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/assurance:run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Run Platform Assurance */
        post: operations["run_platform_assurance_v1_commercial_assurance_run_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/audit-pack:verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Verify Audit Pack
         * @description Check a signed audit pack against the trusted evidence keys: signature, unaltered body, bundle digest.
         */
        post: operations["verify_audit_pack_v1_commercial_audit_pack_verify_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/change-log:verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Verify Commercial Change Log
         * @description Recompute every entry's digest and the hash chain; report the first broken entry.
         */
        get: operations["verify_commercial_change_log_v1_commercial_change_log_verify_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/exceptions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Exceptions */
        get: operations["list_exceptions_v1_commercial_exceptions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/exceptions/{exception_id}:resolve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resolve Exception */
        post: operations["resolve_exception_v1_commercial_exceptions__exception_id__resolve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/finance/appointments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Appointments */
        get: operations["list_appointments_v1_commercial_finance_appointments_get"];
        put?: never;
        /** Appoint */
        post: operations["appoint_v1_commercial_finance_appointments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/finance/appointments/{appointment_id}:end": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** End Appointment */
        post: operations["end_appointment_v1_commercial_finance_appointments__appointment_id__end_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/finance/commitments/{target_tenant_id}/{request_id}:review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Review Commitment
         * @description A Controller or a CFO marks a customer's nonstandard terms reviewed (contract, legal and finance), once; only then do the customer's signatures count.
         */
        post: operations["review_commitment_v1_commercial_finance_commitments__target_tenant_id___request_id__review_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/finance/eligibility/{target_tenant_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Commercial Eligibility
         * @description O01: the classification, the nine gates and what blocks paid activation for one organization. Reading it never activates anything.
         */
        get: operations["commercial_eligibility_v1_commercial_finance_eligibility__target_tenant_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/finance/overview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Finance Overview
         * @description F01: the workload summary, the requests that wait on this person, and the exceptions that stop money moving.
         */
        get: operations["finance_overview_v1_commercial_finance_overview_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/finance/policy": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Policy */
        get: operations["get_policy_v1_commercial_finance_policy_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/finance/policy:sign": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Sign Policy */
        post: operations["sign_policy_v1_commercial_finance_policy_sign_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/finance/requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Requests */
        get: operations["list_requests_v1_commercial_finance_requests_get"];
        put?: never;
        /** Open Request */
        post: operations["open_request_v1_commercial_finance_requests_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/finance/requests/{request_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Request */
        get: operations["get_request_v1_commercial_finance_requests__request_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/finance/requests/{request_id}:cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel Request */
        post: operations["cancel_request_v1_commercial_finance_requests__request_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/finance/requests/{request_id}:decide": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decide Request */
        post: operations["decide_request_v1_commercial_finance_requests__request_id__decide_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/finance/requests/{request_id}:execute": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Execute Request */
        post: operations["execute_request_v1_commercial_finance_requests__request_id__execute_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/invoice-chain:verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Verify Invoice Chain
         * @description Walk this cell's issuing entity's chain: digests only, no tenant data. Reports the first broken link.
         */
        get: operations["verify_invoice_chain_v1_commercial_invoice_chain_verify_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/kpis": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Commercial Kpis */
        get: operations["commercial_kpis_v1_commercial_kpis_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/orders/{order_id}:activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Activate Order
         * @description The only route from a signed order to a tenant's entitlement. Idempotent; a second person activates.
         */
        post: operations["activate_order_v1_commercial_orders__order_id__activate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/payment-events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Payment Events
         * @description What the payment processor reported that needs a person: refunds, disputes, money for an invoice that cannot
         *     take it, and payments naming no invoice (CR-041). Resolved items too when `resolved=true`.
         */
        get: operations["payment_events_v1_commercial_payment_events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/payment-events/{event_id}:credit-note": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Credit Refund
         * @description Turn a refund Stripe reported into a credit note on the invoice it refunded, and close the review item, in one
         *     transaction (CR-041). Only a refund that names the company's own invoice qualifies, and the credit can never
         *     exceed what is left of that invoice (the credit-note rule itself).
         */
        post: operations["credit_refund_v1_commercial_payment_events__event_id__credit_note_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/payment-events/{event_id}:resolve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resolve Payment Event Route */
        post: operations["resolve_payment_event_route_v1_commercial_payment_events__event_id__resolve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/plan-requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Open Plan Requests
         * @description Every company's open requests for a different plan, oldest first (CR-032).
         */
        get: operations["open_plan_requests_v1_commercial_plan_requests_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/plan-requests/{request_id}:answer": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Answer Plan Request
         * @description ACCEPTED: the team will raise the order (the plan itself still moves only by a signed order or the plan change
         *     below). DECLINED: the company is told why. The company's own entitlement is untouched either way.
         */
        post: operations["answer_plan_request_v1_commercial_plan_requests__request_id__answer_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/price-books": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Books */
        get: operations["list_books_v1_commercial_price_books_get"];
        put?: never;
        /** Create Book */
        post: operations["create_book_v1_commercial_price_books_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/price-books/{price_book_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Book */
        get: operations["get_book_v1_commercial_price_books__price_book_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/price-books/{price_book_id}/lines": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Put Line */
        put: operations["put_line_v1_commercial_price_books__price_book_id__lines_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/price-books/{price_book_id}:approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Approve Book */
        post: operations["approve_book_v1_commercial_price_books__price_book_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/quotes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Quote */
        post: operations["create_quote_v1_commercial_quotes_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/quotes/{quote_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Quote */
        get: operations["get_quote_v1_commercial_quotes__quote_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/quotes/{quote_id}:approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Approve Quote */
        post: operations["approve_quote_v1_commercial_quotes__quote_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/regions/{region}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Record Region Release
         * @description Record whether a cell's SRE release gate has passed; only released regions can be quoted.
         */
        put: operations["record_region_release_v1_commercial_regions__region__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/role-holders": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Grant Commercial Role
         * @description Who may approve discounts in which band. Never granted to oneself.
         */
        post: operations["grant_commercial_role_v1_commercial_role_holders_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/adjustments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Request Adjustment
         * @description Ask to correct a billable event. The original is never edited: an approved adjustment is a signed
         *     delta in the window that is still open.
         */
        post: operations["request_adjustment_v1_commercial_tenants__tenant_id__adjustments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/adjustments/{adjustment_id}:decide": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decide Adjustment */
        post: operations["decide_adjustment_v1_commercial_tenants__tenant_id__adjustments__adjustment_id__decide_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/assurance:run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Run Tenant Assurance */
        post: operations["run_tenant_assurance_v1_commercial_tenants__tenant_id__assurance_run_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/audit-pack": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Audit Pack */
        get: operations["audit_pack_v1_commercial_tenants__tenant_id__audit_pack_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/billing-state": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Move Billing State */
        post: operations["move_billing_state_v1_commercial_tenants__tenant_id__billing_state_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/close": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Close Month
         * @description Freeze, rate and draft one tenant-month; resumable. Issuing stays a second person's step.
         */
        post: operations["close_month_v1_commercial_tenants__tenant_id__close_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/data-deletion": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Data Deletion Status */
        get: operations["data_deletion_status_v1_commercial_tenants__tenant_id__data_deletion_get"];
        put?: never;
        /**
         * Request Data Deletion
         * @description Ask for a CLOSED tenant's data to be deleted. The worker erases what no legal hold protects and reports what it
         *     kept; the tenant must already be closed (offboarding drains it first). TEN-25.
         */
        post: operations["request_data_deletion_v1_commercial_tenants__tenant_id__data_deletion_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/dunning:run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Dunning */
        post: operations["dunning_v1_commercial_tenants__tenant_id__dunning_run_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/entitlement": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Plan */
        put: operations["set_plan_v1_commercial_tenants__tenant_id__entitlement_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/invoices": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Draft Invoice */
        post: operations["draft_invoice_v1_commercial_tenants__tenant_id__invoices_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/invoices/{invoice_id}/credit-notes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Issue Credit Note */
        post: operations["issue_credit_note_v1_commercial_tenants__tenant_id__invoices__invoice_id__credit_notes_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/invoices/{invoice_id}:collect": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Collect Invoice */
        post: operations["collect_invoice_v1_commercial_tenants__tenant_id__invoices__invoice_id__collect_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/invoices/{invoice_id}:issue": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Issue Invoice */
        post: operations["issue_invoice_v1_commercial_tenants__tenant_id__invoices__invoice_id__issue_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/invoices/{invoice_id}:void": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Void Invoice */
        post: operations["void_invoice_v1_commercial_tenants__tenant_id__invoices__invoice_id__void_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/journal": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Journal
         * @description Balanced journal lines for the period. `format=csv` is the file the finance team imports into the
         *     general ledger: this platform exports journals and never posts them.
         */
        get: operations["journal_v1_commercial_tenants__tenant_id__journal_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/meter-windows/{month}:freeze": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Freeze Window
         * @description Close every meter of one tenant for a finished UTC month; the database then refuses new events there.
         */
        post: operations["freeze_window_v1_commercial_tenants__tenant_id__meter_windows__month__freeze_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/orders": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Accept Quote
         * @description Record the buyer's signature on a quote: it becomes an order for this tenant of the quoted account.
         */
        post: operations["accept_quote_v1_commercial_tenants__tenant_id__orders_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/overrides": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Add Override */
        post: operations["add_override_v1_commercial_tenants__tenant_id__overrides_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/rating-runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create Rating Run
         * @description Rate one tenant's frozen month. The same inputs return the same run (the replay proof); new inputs
         *     supersede the previous run and keep its lines. Connections are counted now, at the cut-off.
         */
        post: operations["create_rating_run_v1_commercial_tenants__tenant_id__rating_runs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/reconcile/{month}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reconcile Ledger
         * @description Compare the ledger with the authoritative rows (sessions, credentials) for one month and record what
         *     is missing, marked `recovered`. Nothing is invented without a source row and nothing existing is touched.
         *     A frozen window refuses new events, so the recovered ones land in the next open window, flagged late.
         */
        post: operations["reconcile_ledger_v1_commercial_tenants__tenant_id__reconcile__month__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/tenants/{tenant_id}/safety-hold": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Safety Hold
         * @description An abuse or acceptable-use hold: separate from billing, never cleared by paying (CR-027).
         */
        post: operations["safety_hold_v1_commercial_tenants__tenant_id__safety_hold_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/vendor-costs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Vendor Costs */
        get: operations["vendor_costs_v1_commercial_vendor_costs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/vendors": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Register Vendor */
        post: operations["register_vendor_v1_commercial_vendors_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/vendors/{vendor_id}/invoices/{invoice_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Import Vendor Invoice */
        post: operations["import_vendor_invoice_v1_commercial_vendors__vendor_id__invoices__invoice_ref__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/vendors/{vendor_id}/invoices/{invoice_ref}:reconcile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reconcile Vendor Invoice */
        post: operations["reconcile_vendor_invoice_v1_commercial_vendors__vendor_id__invoices__invoice_ref__reconcile_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/vendors/{vendor_id}/rates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Vendor Rate */
        post: operations["vendor_rate_v1_commercial_vendors__vendor_id__rates_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/vendors/{vendor_id}/recognition": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Vendor Recognition */
        put: operations["vendor_recognition_v1_commercial_vendors__vendor_id__recognition_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/commercial/vendors/{vendor_id}/routes/{check_type}/{country}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Vendor Route */
        put: operations["vendor_route_v1_commercial_vendors__vendor_id__routes__check_type___country__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/tenants/{tenant_id}/entitlements": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Tenant Entitlements */
        get: operations["tenant_entitlements_v1_tenants__tenant_id__entitlements_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AcceptBody */
        AcceptBody: {
            /** Amends Order Id */
            amends_order_id?: string | null;
            /**
             * Effective From
             * Format: date-time
             */
            effective_from: string;
            /**
             * Quote Id
             * Format: uuid
             */
            quote_id: string;
            /** Signed Document Digest */
            signed_document_digest: string;
            /** Signer Ref */
            signer_ref: string;
        };
        /** AccountBody */
        AccountBody: {
            /** Country */
            country?: string | null;
            /**
             * Currency
             * @default USD
             */
            currency: string;
            /**
             * Kind
             * @default DIRECT
             * @enum {string}
             */
            kind: "DIRECT" | "PARTNER" | "INTERNAL";
            /** Legal Name */
            legal_name: string;
            /** Reseller Of */
            reseller_of?: string | null;
        };
        /** AdjustmentBody */
        AdjustmentBody: {
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /** Quantity Delta */
            quantity_delta: number | string;
            /** Reason Code */
            reason_code: string;
        };
        /** AppointBody */
        AppointBody: {
            /** Effective From */
            effective_from?: string | null;
            /** Effective To */
            effective_to?: string | null;
            /**
             * Principal Id
             * Format: uuid
             */
            principal_id: string;
            /** Role */
            role: string;
        };
        /** ApproveBody */
        ApproveBody: {
            /** Effective From */
            effective_from?: string | null;
        };
        /** ApproveQuoteBody */
        ApproveQuoteBody: {
            /**
             * Role
             * @enum {string}
             */
            role: "AE" | "CCO" | "CFO" | "FOUNDER" | "GROUP_CFO";
        };
        /** CollectBody */
        CollectBody: {
            /** Idempotency Key */
            idempotency_key: string;
        };
        /** CreditBody */
        CreditBody: {
            /** Amount */
            amount: number | string;
            /** Reason Code */
            reason_code: string;
            /** Reverses */
            reverses?: string | null;
        };
        /** CreditNoteBody */
        CreditNoteBody: {
            /** Amount */
            amount: number | string;
            /** Reason Code */
            reason_code: string;
            /** Reverses Line */
            reverses_line?: number | null;
        };
        /** DecideBody */
        DecideBody: {
            /**
             * Decision
             * @enum {string}
             */
            decision: "APPROVE" | "REJECT";
            /** Reason */
            reason?: string | null;
            /** Role */
            role: string;
            /** Snapshot Hash */
            snapshot_hash: string;
        };
        /** DeletionBody */
        DeletionBody: {
            /** Reason Ref */
            reason_ref: string;
        };
        /** DraftBody */
        DraftBody: {
            /** Copy Of */
            copy_of?: string | null;
            /** Note */
            note?: string | null;
        };
        /** DraftInvoiceBody */
        DraftInvoiceBody: {
            /** Month */
            month: string;
        };
        /** EstimateBody */
        EstimateBody: {
            /**
             * Auth Events
             * @default 0
             */
            auth_events: number;
            /**
             * M2M Grants
             * @default 0
             */
            m2m_grants: number;
            /**
             * Mah
             * @default 0
             */
            mah: number;
            /** Plan */
            plan: string;
            /**
             * Policy Decisions
             * @default 0
             */
            policy_decisions: number;
            /**
             * Scim Connections
             * @default 0
             */
            scim_connections: number;
            /**
             * Sso Connections
             * @default 0
             */
            sso_connections: number;
        };
        /** ExecuteBody */
        ExecuteBody: {
            /** Idempotency Key */
            idempotency_key: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** IssueBody */
        IssueBody: {
            /**
             * Allow Zero Tax
             * @default false
             */
            allow_zero_tax: boolean;
            /** Payment Terms Days */
            payment_terms_days: number;
        };
        /** LineBody */
        LineBody: {
            /** Included Quantity */
            included_quantity?: number | null;
            /** Minimum Amount */
            minimum_amount?: number | string | null;
            /**
             * Plan
             * @enum {string}
             */
            plan: "DEVELOPER" | "LAUNCH" | "BUSINESS" | "SCALE" | "ENTERPRISE";
            /** Sku */
            sku: string;
            /**
             * Unit Divisor
             * @default 1
             */
            unit_divisor: number;
            /** Unit Rate */
            unit_rate: number | string;
        };
        /** OverrideBody */
        OverrideBody: {
            /** Approver Id */
            approver_id?: string | null;
            /** Capability */
            capability: string;
            /** Expires At */
            expires_at?: string | null;
            /** Extra Count */
            extra_count?: number | null;
            /** Grant Mode */
            grant_mode?: ("INCLUDED" | "ADD_ON" | "METERED" | "CONTRACT" | "QUALIFIED") | null;
            /** Reason */
            reason: string;
        };
        /** PaymentCustomerBody */
        PaymentCustomerBody: {
            /** Customer Ref */
            customer_ref: string;
            /**
             * Provider
             * @default stripe
             * @constant
             */
            provider: "stripe";
        };
        /** PeriodQuery */
        PeriodQuery: {
            /** Month */
            month: string;
        };
        /** PlanRequestAnswer */
        PlanRequestAnswer: {
            /**
             * Decision
             * @enum {string}
             */
            decision: "ACCEPTED" | "DECLINED";
            /** Note */
            note?: string | null;
        };
        /** QuoteBody */
        QuoteBody: {
            /**
             * Account Id
             * Format: uuid
             */
            account_id: string;
            /**
             * Liability Capped
             * @default true
             */
            liability_capped: boolean;
            /** Lines */
            lines: components["schemas"]["QuoteLineBody"][];
            /**
             * Pilot
             * @default false
             */
            pilot: boolean;
            /**
             * Prepaid
             * @default false
             */
            prepaid: boolean;
            /**
             * Prepaid Discount
             * @default 0
             */
            prepaid_discount: number | string;
            /**
             * Price Book Id
             * Format: uuid
             */
            price_book_id: string;
            /** Region */
            region: string;
            /** Residency Commitment */
            residency_commitment?: string | null;
            /** Term Months */
            term_months: number;
        };
        /** QuoteLineBody */
        QuoteLineBody: {
            /**
             * Discount Pct
             * @default 0
             */
            discount_pct: number | string;
            /**
             * Plan
             * @enum {string}
             */
            plan: "DEVELOPER" | "LAUNCH" | "BUSINESS" | "SCALE" | "ENTERPRISE";
            /** Promotions */
            promotions?: (number | string)[];
            /** Quantity */
            quantity: number | string;
            /** Sku */
            sku: string;
        };
        /** RatingBody */
        RatingBody: {
            /** Credits */
            credits?: components["schemas"]["CreditBody"][];
            /** Month */
            month: string;
        };
        /** RecognitionBody */
        RecognitionBody: {
            /**
             * Basis
             * @enum {string}
             */
            basis: "PRINCIPAL" | "AGENT" | "UNDECIDED";
        };
        /** ReconcileBody */
        ReconcileBody: {
            /**
             * Period End
             * Format: date-time
             */
            period_end: string;
            /**
             * Period Start
             * Format: date-time
             */
            period_start: string;
        };
        /** RefundCreditBody */
        RefundCreditBody: {
            /** Amount */
            amount: number | string;
        };
        /** RegionBody */
        RegionBody: {
            /** Evidence Ref */
            evidence_ref: string;
            /** Sre Gate Passed */
            sre_gate_passed: boolean;
        };
        /** SafetyHoldBody */
        SafetyHoldBody: {
            /** On */
            on: boolean;
            /** Reason */
            reason?: string | null;
        };
        /** SignBody */
        SignBody: {
            /** Content Hash */
            content_hash: string;
        };
        /** StateBody */
        StateBody: {
            /** Reason Code */
            reason_code: string;
            /** To */
            to: string;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** VendorBody */
        VendorBody: {
            /** Adapter Id */
            adapter_id?: string | null;
            /** Display Name */
            display_name: string;
            /**
             * Legal Role
             * @enum {string}
             */
            legal_role: "SUBPROCESSOR" | "INDEPENDENT_CONTROLLER";
            /**
             * Sku
             * @enum {string}
             */
            sku: "ZID-IDV" | "ZID-KYB" | "ZID-RISK" | "ZID-MSG";
            /** Spike Threshold Pct */
            spike_threshold_pct?: number | string | null;
            /** Vendor Id */
            vendor_id: string;
        };
        /** VendorInvoiceBody */
        VendorInvoiceBody: {
            /** Lines */
            lines: components["schemas"]["VendorInvoiceLineBody"][];
        };
        /** VendorInvoiceLineBody */
        VendorInvoiceLineBody: {
            /** Amount */
            amount: number | string;
            /** Currency */
            currency: string;
            /** Supplier Ref */
            supplier_ref: string;
        };
        /** VendorRateBody */
        VendorRateBody: {
            /** Base Price */
            base_price: number | string;
            /** Check Type */
            check_type: string;
            /**
             * Confidence
             * @enum {string}
             */
            confidence: "SIGNED_TARIFF" | "QUOTED" | "ESTIMATE";
            /** Country */
            country: string;
            /** Currency */
            currency: string;
            /** Retry Price */
            retry_price: number | string;
            /** Source Ref */
            source_ref: string;
        };
        /** VerifyPackBody */
        VerifyPackBody: {
            /** Pack */
            pack: {
                [key: string]: unknown;
            };
        };
        /** PlanBody */
        zid_platform__modules__operator_access__api__commercial_admin__PlanBody: {
            /**
             * Plan
             * @enum {string}
             */
            plan: "DEVELOPER" | "LAUNCH" | "BUSINESS" | "SCALE" | "ENTERPRISE";
        };
        /** ResolveBody */
        zid_platform__modules__operator_access__api__commercial_admin__ResolveBody: {
            /** Resolution */
            resolution: string;
        };
        /** RoleBody */
        zid_platform__modules__operator_access__api__commercial_admin__RoleBody: {
            /**
             * Principal Id
             * Format: uuid
             */
            principal_id: string;
            /**
             * Role
             * @enum {string}
             */
            role: "AE" | "CCO" | "CFO" | "FOUNDER" | "GROUP_CFO";
        };
        /** RouteBody */
        zid_platform__modules__operator_access__api__commercial_admin__RouteBody: {
            /** Reason */
            reason: string;
            /**
             * Status
             * @enum {string}
             */
            status: "ACTIVE" | "BLOCKED" | "NEEDS_APPROVAL";
        };
        /** RequestBody */
        zid_platform__modules__operator_access__api__commercial_finance__RequestBody: {
            /** Amount */
            amount: number | string;
            /**
             * Category
             * @enum {string}
             */
            category: "CORRECTION" | "GOODWILL" | "PILOT";
            /** Currency */
            currency: string;
            /** Disposition */
            disposition?: ("INVOICE_BALANCE" | "REFUND" | "CUSTOMER_BALANCE" | "OUT_OF_BAND") | null;
            /** Ends At */
            ends_at?: string | null;
            /** Evidence Ref */
            evidence_ref?: string | null;
            /** Invoice Id */
            invoice_id?: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "CREDIT_NOTE" | "PILOT_CREDIT";
            /** Program Ref */
            program_ref?: string | null;
            /** Reason */
            reason: string;
            /** Sponsor */
            sponsor?: string | null;
            /**
             * Target Tenant Id
             * Format: uuid
             */
            target_tenant_id: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    billing_estimate_v1_billing_estimate_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EstimateBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    catalog_v1_catalog_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    create_account_v1_commercial_accounts_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AccountBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_payment_customer_v1_commercial_accounts__account_id__payment_customer_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                account_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PaymentCustomerBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    link_account_tenant_v1_commercial_accounts__account_id__tenants__tenant_id__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                account_id: string;
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    run_platform_assurance_v1_commercial_assurance_run_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    verify_audit_pack_v1_commercial_audit_pack_verify_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["VerifyPackBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    verify_commercial_change_log_v1_commercial_change_log_verify_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    list_exceptions_v1_commercial_exceptions_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    resolve_exception_v1_commercial_exceptions__exception_id__resolve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                exception_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__operator_access__api__commercial_admin__ResolveBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_appointments_v1_commercial_finance_appointments_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    appoint_v1_commercial_finance_appointments_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AppointBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    end_appointment_v1_commercial_finance_appointments__appointment_id__end_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                appointment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    review_commitment_v1_commercial_finance_commitments__target_tenant_id___request_id__review_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                target_tenant_id: string;
                request_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    commercial_eligibility_v1_commercial_finance_eligibility__target_tenant_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                target_tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    finance_overview_v1_commercial_finance_overview_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    get_policy_v1_commercial_finance_policy_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    sign_policy_v1_commercial_finance_policy_sign_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SignBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_requests_v1_commercial_finance_requests_get: {
        parameters: {
            query?: {
                state?: string | null;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    open_request_v1_commercial_finance_requests_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__operator_access__api__commercial_finance__RequestBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_request_v1_commercial_finance_requests__request_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cancel_request_v1_commercial_finance_requests__request_id__cancel_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    decide_request_v1_commercial_finance_requests__request_id__decide_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DecideBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    execute_request_v1_commercial_finance_requests__request_id__execute_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExecuteBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    verify_invoice_chain_v1_commercial_invoice_chain_verify_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    commercial_kpis_v1_commercial_kpis_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    activate_order_v1_commercial_orders__order_id__activate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                order_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    payment_events_v1_commercial_payment_events_get: {
        parameters: {
            query?: {
                resolved?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    credit_refund_v1_commercial_payment_events__event_id__credit_note_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                event_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RefundCreditBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resolve_payment_event_route_v1_commercial_payment_events__event_id__resolve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                event_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__operator_access__api__commercial_admin__ResolveBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    open_plan_requests_v1_commercial_plan_requests_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    answer_plan_request_v1_commercial_plan_requests__request_id__answer_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PlanRequestAnswer"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_books_v1_commercial_price_books_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    create_book_v1_commercial_price_books_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DraftBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_book_v1_commercial_price_books__price_book_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                price_book_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    put_line_v1_commercial_price_books__price_book_id__lines_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                price_book_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LineBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_book_v1_commercial_price_books__price_book_id__approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                price_book_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApproveBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_quote_v1_commercial_quotes_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QuoteBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_quote_v1_commercial_quotes__quote_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                quote_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_quote_v1_commercial_quotes__quote_id__approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                quote_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApproveQuoteBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    record_region_release_v1_commercial_regions__region__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                region: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RegionBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    grant_commercial_role_v1_commercial_role_holders_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__operator_access__api__commercial_admin__RoleBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    request_adjustment_v1_commercial_tenants__tenant_id__adjustments_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AdjustmentBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    decide_adjustment_v1_commercial_tenants__tenant_id__adjustments__adjustment_id__decide_post: {
        parameters: {
            query: {
                approve: boolean;
            };
            header?: never;
            path: {
                tenant_id: string;
                adjustment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    run_tenant_assurance_v1_commercial_tenants__tenant_id__assurance_run_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PeriodQuery"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    audit_pack_v1_commercial_tenants__tenant_id__audit_pack_get: {
        parameters: {
            query: {
                month: string;
            };
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    move_billing_state_v1_commercial_tenants__tenant_id__billing_state_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["StateBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    close_month_v1_commercial_tenants__tenant_id__close_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PeriodQuery"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    data_deletion_status_v1_commercial_tenants__tenant_id__data_deletion_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    request_data_deletion_v1_commercial_tenants__tenant_id__data_deletion_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeletionBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    dunning_v1_commercial_tenants__tenant_id__dunning_run_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_plan_v1_commercial_tenants__tenant_id__entitlement_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__operator_access__api__commercial_admin__PlanBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    draft_invoice_v1_commercial_tenants__tenant_id__invoices_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DraftInvoiceBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    issue_credit_note_v1_commercial_tenants__tenant_id__invoices__invoice_id__credit_notes_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
                invoice_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreditNoteBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    collect_invoice_v1_commercial_tenants__tenant_id__invoices__invoice_id__collect_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
                invoice_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CollectBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    issue_invoice_v1_commercial_tenants__tenant_id__invoices__invoice_id__issue_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
                invoice_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["IssueBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    void_invoice_v1_commercial_tenants__tenant_id__invoices__invoice_id__void_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
                invoice_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    journal_v1_commercial_tenants__tenant_id__journal_get: {
        parameters: {
            query: {
                start: string;
                end: string;
                format?: "json" | "csv";
            };
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    freeze_window_v1_commercial_tenants__tenant_id__meter_windows__month__freeze_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
                month: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    accept_quote_v1_commercial_tenants__tenant_id__orders_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AcceptBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_override_v1_commercial_tenants__tenant_id__overrides_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OverrideBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_rating_run_v1_commercial_tenants__tenant_id__rating_runs_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RatingBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reconcile_ledger_v1_commercial_tenants__tenant_id__reconcile__month__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
                month: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    safety_hold_v1_commercial_tenants__tenant_id__safety_hold_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SafetyHoldBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    vendor_costs_v1_commercial_vendor_costs_get: {
        parameters: {
            query: {
                start: string;
                end: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    register_vendor_v1_commercial_vendors_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["VendorBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    import_vendor_invoice_v1_commercial_vendors__vendor_id__invoices__invoice_ref__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                vendor_id: string;
                invoice_ref: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["VendorInvoiceBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reconcile_vendor_invoice_v1_commercial_vendors__vendor_id__invoices__invoice_ref__reconcile_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                vendor_id: string;
                invoice_ref: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReconcileBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    vendor_rate_v1_commercial_vendors__vendor_id__rates_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                vendor_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["VendorRateBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    vendor_recognition_v1_commercial_vendors__vendor_id__recognition_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                vendor_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RecognitionBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    vendor_route_v1_commercial_vendors__vendor_id__routes__check_type___country__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                vendor_id: string;
                check_type: string;
                country: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__operator_access__api__commercial_admin__RouteBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    tenant_entitlements_v1_tenants__tenant_id__entitlements_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}

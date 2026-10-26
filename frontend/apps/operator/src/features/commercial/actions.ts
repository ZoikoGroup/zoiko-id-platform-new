/** The operator commercial surface as data (ZID-COM-001; CR-024 to CR-030).
 *
 * Every write route has exactly one description here: its method, path, who needs which capability, and the
 * fields of its body. The console renders a form from it and the cell validates the body again, so a field
 * added on the server needs one line here, not a new screen. Nothing in this file decides anything. */
export type FieldKind = "text" | "integer" | "decimal" | "select" | "boolean" | "datetime" | "json";

export type Field = {
  key: string;
  label: string;
  kind?: FieldKind;
  /** Where the value goes: a `{placeholder}` in the path, the query string, or the JSON body (default). */
  in?: "path" | "query" | "body";
  required?: boolean;
  options?: { id: string; label: string }[];
  placeholder?: string;
  help?: string;
};

export type Action = {
  id: string;
  group: "catalogue" | "plans" | "sales" | "billing" | "vendors" | "assurance";
  title: string;
  description: string;
  method: "POST" | "PUT";
  path: string;
  /** The JIT capability the operator must hold (shown so a refusal is understood, never enforced here). */
  capability: string;
  fields: Field[];
  /** Irreversible or externally visible: asks for a confirmation sentence before it runs. */
  confirm?: string;
};

const PLANS = ["DEVELOPER", "LAUNCH", "BUSINESS", "SCALE", "ENTERPRISE"].map((id) => ({ id, label: id }));
const ROLES = ["AE", "CCO", "CFO", "FOUNDER", "GROUP_CFO"].map((id) => ({ id, label: id }));
const tenant: Field = { key: "tenant_id", label: "Tenant ID", in: "path", required: true, placeholder: "UUID" };
const month: Field = { key: "month", label: "Month (YYYY-MM)", required: true, placeholder: "2026-10" };
const monthPath: Field = { ...month, in: "path" };
const reason: Field = { key: "reason_code", label: "Reason code", required: true, placeholder: "UPPER_SNAKE_CASE" };

export const ACTIONS: Action[] = [
  // ------------------------------------------------------------------ catalogue
  {
    id: "book-create",
    group: "catalogue",
    title: "New draft price book",
    description: "Start a DRAFT, optionally as a copy of an existing book. Nothing is visible to customers until a second operator approves it.",
    method: "POST",
    path: "/v1/commercial/price-books",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "copy_of", label: "Copy of (price book ID)", placeholder: "optional" },
      { key: "note", label: "Note" },
    ],
  },
  {
    id: "book-line",
    group: "catalogue",
    title: "Set a price line on a draft",
    description: "One SKU on one plan. An approved book is immutable; the database refuses the change.",
    method: "PUT",
    path: "/v1/commercial/price-books/{price_book_id}/lines",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "price_book_id", label: "Draft price book ID", in: "path", required: true },
      { key: "sku", label: "SKU", required: true, placeholder: "ZID-HUM" },
      { key: "plan", label: "Plan", kind: "select", options: PLANS, required: true },
      { key: "unit_rate", label: "Unit rate", kind: "decimal", required: true },
      { key: "unit_divisor", label: "Unit divisor", kind: "integer", placeholder: "1" },
      { key: "included_quantity", label: "Included quantity", kind: "integer" },
      { key: "minimum_amount", label: "Minimum amount", kind: "decimal" },
    ],
  },
  {
    id: "book-approve",
    group: "catalogue",
    title: "Approve a price book",
    description: "Dual control: a different operator from the one who drafted it. Rates then apply to new orders only, never retroactively.",
    method: "POST",
    path: "/v1/commercial/price-books/{price_book_id}:approve",
    capability: "commercial.catalog.manage",
    confirm: "approve this price book",
    fields: [
      { key: "price_book_id", label: "Price book ID", in: "path", required: true },
      { key: "effective_from", label: "Effective from", kind: "datetime", help: "Defaults to now." },
    ],
  },

  // ------------------------------------------------------------------ plans and accounts
  {
    id: "plan-set",
    group: "plans",
    title: "Move a tenant to a plan",
    description: "From now, on the currently approved price book. Recorded in the change log; revenue assurance flags it if no signed order backs it.",
    method: "PUT",
    path: "/v1/commercial/tenants/{tenant_id}/entitlement",
    capability: "tenant.config.change",
    confirm: "change this tenant's plan",
    fields: [tenant, { key: "plan", label: "Plan", kind: "select", options: PLANS, required: true }],
  },
  {
    id: "override-add",
    group: "plans",
    title: "Contracted override",
    description: "A capability or extra connections beyond the plan, with a second approver and an expiry.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/overrides",
    capability: "tenant.config.change",
    fields: [
      tenant,
      { key: "capability", label: "Capability key", required: true, placeholder: "saml_sso" },
      {
        key: "grant_mode",
        label: "Grant mode",
        kind: "select",
        options: ["INCLUDED", "ADD_ON", "METERED", "CONTRACT", "QUALIFIED"].map((id) => ({ id, label: id })),
      },
      { key: "extra_count", label: "Extra count", kind: "integer" },
      { key: "reason", label: "Reason", required: true },
      { key: "approver_id", label: "Approver (another operator's principal ID)", required: true },
      { key: "expires_at", label: "Expires at", kind: "datetime" },
    ],
  },
  {
    id: "account-create",
    group: "plans",
    title: "Commercial account",
    description: "The legal customer that owns tenants. A platform identity is never assumed to be the billing buyer.",
    method: "POST",
    path: "/v1/commercial/accounts",
    capability: "commercial.sales.manage",
    fields: [
      { key: "legal_name", label: "Legal name", required: true },
      { key: "kind", label: "Kind", kind: "select", options: ["DIRECT", "PARTNER", "INTERNAL"].map((id) => ({ id, label: id })) },
      { key: "country", label: "Country (ISO 2)", placeholder: "GB" },
      { key: "currency", label: "Currency", placeholder: "USD" },
      { key: "reseller_of", label: "Reseller of (account ID)" },
    ],
  },
  {
    id: "account-link",
    group: "plans",
    title: "Link a tenant to an account",
    description: "A tenant belongs to exactly one commercial account.",
    method: "POST",
    path: "/v1/commercial/accounts/{account_id}/tenants/{tenant_id}",
    capability: "commercial.sales.manage",
    fields: [{ key: "account_id", label: "Account ID", in: "path", required: true }, tenant],
  },
  {
    id: "payment-customer",
    group: "plans",
    title: "Link the payment processor customer",
    description: "The account's customer id at Stripe (cus_...). A reference only: the platform never stores card details.",
    method: "PUT",
    path: "/v1/commercial/accounts/{account_id}/payment-customer",
    capability: "commercial.sales.manage",
    fields: [
      { key: "account_id", label: "Account ID", in: "path", required: true },
      { key: "customer_ref", label: "Stripe customer ID", required: true, placeholder: "cus_..." },
    ],
  },
  {
    id: "region-release",
    group: "plans",
    title: "Region release gate",
    description: "A quote cannot promise a region or SLA until its SRE gate has passed with evidence.",
    method: "PUT",
    path: "/v1/commercial/regions/{region}",
    capability: "commercial.sales.manage",
    fields: [
      { key: "region", label: "Region", in: "path", required: true, placeholder: "uk-1" },
      { key: "sre_gate_passed", label: "SRE release gate passed", kind: "boolean" },
      { key: "evidence_ref", label: "Evidence reference", required: true },
    ],
  },
  {
    id: "role-grant",
    group: "plans",
    title: "Commercial approval role",
    description: "Who may approve which discount band. Roles come from here, never from a request.",
    method: "POST",
    path: "/v1/commercial/role-holders",
    capability: "commercial.sales.manage",
    fields: [
      { key: "principal_id", label: "Principal ID", required: true },
      { key: "role", label: "Role", kind: "select", options: ROLES, required: true },
    ],
  },

  // ------------------------------------------------------------------ sales
  {
    id: "quote-create",
    group: "sales",
    title: "Create and validate a quote",
    description: "Rejected when a price floor, vendor-cost confidence or regional release is missing.",
    method: "POST",
    path: "/v1/commercial/quotes",
    capability: "commercial.sales.manage",
    fields: [
      { key: "account_id", label: "Account ID", required: true },
      { key: "price_book_id", label: "Price book ID", required: true },
      { key: "region", label: "Region", required: true, placeholder: "uk-1" },
      { key: "term_months", label: "Term (months)", kind: "integer", required: true },
      {
        key: "lines",
        label: "Lines (JSON)",
        kind: "json",
        required: true,
        placeholder: '[{"sku":"ZID-BASE","plan":"BUSINESS","quantity":1,"discount_pct":0}]',
        help: "discount_pct is a fraction: 0.05 is 5%.",
      },
      { key: "prepaid", label: "Prepaid", kind: "boolean" },
      { key: "prepaid_discount", label: "Prepaid discount (fraction)", kind: "decimal" },
      { key: "liability_capped", label: "Liability capped", kind: "boolean" },
      { key: "residency_commitment", label: "Residency commitment (region)" },
      { key: "pilot", label: "Pilot", kind: "boolean" },
    ],
  },
  {
    id: "quote-approve",
    group: "sales",
    title: "Approve a quote as a role",
    description: "Each approval binds the quote's digest and the approver; a changed quote needs new approvals.",
    method: "POST",
    path: "/v1/commercial/quotes/{quote_id}:approve",
    capability: "commercial.sales.manage",
    fields: [
      { key: "quote_id", label: "Quote ID", in: "path", required: true },
      { key: "role", label: "Role", kind: "select", options: ROLES, required: true },
    ],
  },
  {
    id: "order-accept",
    group: "sales",
    title: "Record a signed order",
    description: "Only for the account's own tenant, against an approved quote and the digest of the signed document.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/orders",
    capability: "commercial.sales.manage",
    fields: [
      tenant,
      { key: "quote_id", label: "Quote ID", required: true },
      { key: "signer_ref", label: "Signer reference", required: true },
      { key: "signed_document_digest", label: "Signed document SHA-256", required: true, placeholder: "64 hex characters" },
      { key: "effective_from", label: "Effective from", kind: "datetime", required: true },
      { key: "amends_order_id", label: "Amends order ID" },
    ],
  },
  {
    id: "order-activate",
    group: "sales",
    title: "Activate an order",
    description: "Two-person, idempotent: the order's plan becomes the tenant's entitlement from its effective date.",
    method: "POST",
    path: "/v1/commercial/orders/{order_id}:activate",
    capability: "commercial.sales.manage",
    confirm: "activate this order",
    fields: [{ key: "order_id", label: "Order ID", in: "path", required: true }],
  },

  // ------------------------------------------------------------------ billing runs
  {
    id: "window-freeze",
    group: "billing",
    title: "Freeze a meter window",
    description: "After this the database refuses new events for that tenant-month; late events land in the next month, flagged.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/meter-windows/{month}:freeze",
    capability: "commercial.billing.manage",
    confirm: "freeze this month",
    fields: [tenant, monthPath],
  },
  {
    id: "ledger-reconcile",
    group: "billing",
    title: "Reconcile the ledger with its sources",
    description: "Records only what has a source row and is missing, marked recovered. Nothing is invented.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/reconcile/{month}",
    capability: "commercial.billing.manage",
    fields: [tenant, monthPath],
  },
  {
    id: "adjustment-request",
    group: "billing",
    title: "Request a usage adjustment",
    description: "Corrects an event without editing it. A different operator must decide it.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/adjustments",
    capability: "commercial.billing.manage",
    fields: [
      tenant,
      { key: "event_id", label: "Event ID", required: true },
      { key: "quantity_delta", label: "Quantity change (signed)", kind: "decimal", required: true },
      reason,
    ],
  },
  {
    id: "adjustment-decide",
    group: "billing",
    title: "Decide a usage adjustment",
    description: "Approve or reject another operator's request. You cannot decide your own.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/adjustments/{adjustment_id}:decide",
    capability: "commercial.billing.manage",
    fields: [
      tenant,
      { key: "adjustment_id", label: "Adjustment ID", in: "path", required: true },
      { key: "approve", label: "Approve (otherwise reject)", kind: "boolean", in: "query" },
    ],
  },
  {
    id: "rating-run",
    group: "billing",
    title: "Rate a frozen month",
    description: "The same inputs return the same run (the replay proof); new inputs supersede it and keep its lines.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/rating-runs",
    capability: "commercial.billing.manage",
    fields: [
      tenant,
      month,
      { key: "credits", label: "Credits (JSON)", kind: "json", placeholder: '[{"amount":"10.00","reason_code":"PILOT_CREDIT"}]' },
    ],
  },
  {
    id: "month-close",
    group: "billing",
    title: "Close a month",
    description: "Freeze, rate and draft in one resumable run. Issuing stays a second person's step.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/close",
    capability: "commercial.billing.manage",
    fields: [tenant, month],
  },
  {
    id: "invoice-draft",
    group: "billing",
    title: "Draft an invoice",
    description: "From the month's computed rating.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/invoices",
    capability: "commercial.billing.manage",
    fields: [tenant, month],
  },
  {
    id: "invoice-issue",
    group: "billing",
    title: "Issue an invoice",
    description: "A second person issues it. The invoice is then immutable; a correction is a credit note.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/invoices/{invoice_id}:issue",
    capability: "commercial.billing.manage",
    confirm: "issue this invoice",
    fields: [
      tenant,
      { key: "invoice_id", label: "Invoice ID", in: "path", required: true },
      { key: "payment_terms_days", label: "Payment terms (days)", kind: "integer", required: true },
      { key: "allow_zero_tax", label: "Allow zero tax (no tax engine configured)", kind: "boolean" },
    ],
  },
  {
    id: "invoice-void",
    group: "billing",
    title: "Void a draft invoice",
    description: "Only a draft can be voided; an issued invoice is never edited or removed.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/invoices/{invoice_id}:void",
    capability: "commercial.billing.manage",
    confirm: "void this draft",
    fields: [tenant, { key: "invoice_id", label: "Invoice ID", in: "path", required: true }],
  },
  {
    id: "credit-note",
    group: "billing",
    title: "Issue a credit note",
    description: "Linked to the original invoice, with a ledger reversal. Cannot exceed what is outstanding.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/invoices/{invoice_id}/credit-notes",
    capability: "commercial.billing.manage",
    confirm: "issue this credit note",
    fields: [
      tenant,
      { key: "invoice_id", label: "Invoice ID", in: "path", required: true },
      { key: "amount", label: "Amount", kind: "decimal", required: true },
      reason,
      { key: "reverses_line", label: "Reverses line number", kind: "integer" },
    ],
  },
  {
    id: "invoice-collect",
    group: "billing",
    title: "Collect payment",
    description: "Through the payment port. The idempotency key makes a retry safe.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/invoices/{invoice_id}:collect",
    capability: "commercial.billing.manage",
    fields: [
      tenant,
      { key: "invoice_id", label: "Invoice ID", in: "path", required: true },
      { key: "idempotency_key", label: "Idempotency key", required: true, placeholder: "8 to 128 characters" },
    ],
  },
  {
    id: "billing-state",
    group: "billing",
    title: "Move the billing state",
    description: "Past due, restricted, suspended and so on. Restrictions never touch sign-in, token issuing or revocation.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/billing-state",
    capability: "commercial.billing.manage",
    confirm: "change this tenant's billing state",
    fields: [
      tenant,
      { key: "to", label: "Target state", required: true, placeholder: "PAST_DUE" },
      reason,
    ],
  },
  {
    id: "safety-hold",
    group: "billing",
    title: "Safety hold",
    description: "A security or abuse hold, separate from payment state.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/safety-hold",
    capability: "commercial.billing.manage",
    confirm: "change the safety hold",
    fields: [
      tenant,
      { key: "on", label: "Hold on", kind: "boolean" },
      { key: "reason", label: "Reason (required when turning on)" },
    ],
  },
  {
    id: "dunning",
    group: "billing",
    title: "Run dunning",
    description: "Follows only the dunning policy; paying restores.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/dunning:run",
    capability: "commercial.billing.manage",
    fields: [tenant],
  },

  // ------------------------------------------------------------------ vendors
  {
    id: "vendor-register",
    group: "vendors",
    title: "Register a supplier",
    description: "Its legal role (subprocessor or independent controller) is recorded and shown to customers.",
    method: "POST",
    path: "/v1/commercial/vendors",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "vendor_id", label: "Vendor ID", required: true, placeholder: "lowercase-with-dashes" },
      { key: "display_name", label: "Display name", required: true },
      { key: "legal_role", label: "Legal role", kind: "select", options: ["SUBPROCESSOR", "INDEPENDENT_CONTROLLER"].map((id) => ({ id, label: id })), required: true },
      { key: "adapter_id", label: "Adapter ID" },
      { key: "sku", label: "SKU", kind: "select", options: ["ZID-IDV", "ZID-KYB", "ZID-RISK", "ZID-MSG"].map((id) => ({ id, label: id })), required: true },
      { key: "spike_threshold_pct", label: "Rate spike threshold (%)", kind: "decimal" },
    ],
  },
  {
    id: "vendor-recognition",
    group: "vendors",
    title: "Revenue recognition basis",
    description: "Principal or agent is Finance's and Legal's decision; UNDECIDED blocks gross reporting.",
    method: "PUT",
    path: "/v1/commercial/vendors/{vendor_id}/recognition",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "vendor_id", label: "Vendor ID", in: "path", required: true },
      { key: "basis", label: "Basis", kind: "select", options: ["PRINCIPAL", "AGENT", "UNDECIDED"].map((id) => ({ id, label: id })), required: true },
    ],
  },
  {
    id: "vendor-rate",
    group: "vendors",
    title: "Record a supplier rate",
    description: "Per check type and country, with how sure the number is. An ESTIMATE cannot back a customer quote.",
    method: "POST",
    path: "/v1/commercial/vendors/{vendor_id}/rates",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "vendor_id", label: "Vendor ID", in: "path", required: true },
      { key: "check_type", label: "Check type", required: true, placeholder: "DOCUMENT" },
      { key: "country", label: "Country (ISO 2)", required: true },
      { key: "base_price", label: "Base price", kind: "decimal", required: true },
      { key: "retry_price", label: "Retry price", kind: "decimal", required: true },
      { key: "currency", label: "Currency", required: true, placeholder: "USD" },
      { key: "confidence", label: "Confidence", kind: "select", options: ["SIGNED_TARIFF", "QUOTED", "ESTIMATE"].map((id) => ({ id, label: id })), required: true },
      { key: "source_ref", label: "Source reference", required: true, placeholder: "contract or quote reference" },
    ],
  },
  {
    id: "vendor-route",
    group: "vendors",
    title: "Route status",
    description: "Block or require approval for a check type in a country.",
    method: "PUT",
    path: "/v1/commercial/vendors/{vendor_id}/routes/{check_type}/{country}",
    capability: "commercial.catalog.manage",
    confirm: "change this route",
    fields: [
      { key: "vendor_id", label: "Vendor ID", in: "path", required: true },
      { key: "check_type", label: "Check type", in: "path", required: true },
      { key: "country", label: "Country (ISO 2)", in: "path", required: true },
      { key: "status", label: "Status", kind: "select", options: ["ACTIVE", "BLOCKED", "NEEDS_APPROVAL"].map((id) => ({ id, label: id })), required: true },
      { key: "reason", label: "Reason", required: true },
    ],
  },
  {
    id: "vendor-invoice-import",
    group: "vendors",
    title: "Import a supplier invoice",
    description: "Lines are matched to recorded check events when reconciled.",
    method: "POST",
    path: "/v1/commercial/vendors/{vendor_id}/invoices/{invoice_ref}",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "vendor_id", label: "Vendor ID", in: "path", required: true },
      { key: "invoice_ref", label: "Supplier invoice reference", in: "path", required: true },
      {
        key: "lines",
        label: "Lines (JSON)",
        kind: "json",
        required: true,
        placeholder: '[{"supplier_ref":"chk_123","amount":"0.55","currency":"USD"}]',
      },
    ],
  },
  {
    id: "vendor-invoice-reconcile",
    group: "vendors",
    title: "Reconcile a supplier invoice",
    description: "Unmatched or mismatched lines become disputes.",
    method: "POST",
    path: "/v1/commercial/vendors/{vendor_id}/invoices/{invoice_ref}:reconcile",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "vendor_id", label: "Vendor ID", in: "path", required: true },
      { key: "invoice_ref", label: "Supplier invoice reference", in: "path", required: true },
      { key: "period_start", label: "Period start", kind: "datetime", required: true },
      { key: "period_end", label: "Period end", kind: "datetime", required: true },
    ],
  },

  // ------------------------------------------------------------------ assurance
  {
    id: "assurance-tenant",
    group: "assurance",
    title: "Run detectors for a tenant",
    description: "Entitlement without an order, price-book drift, meter loss, unrated or uninvoiced months.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/assurance:run",
    capability: "commercial.billing.manage",
    fields: [tenant, month],
  },
  {
    id: "assurance-platform",
    group: "assurance",
    title: "Run platform detectors",
    description: "Order lines below their floor without CFO acceptance, open supplier disputes.",
    method: "POST",
    path: "/v1/commercial/assurance:run",
    capability: "commercial.catalog.manage",
    fields: [],
  },
  {
    id: "exception-resolve",
    group: "assurance",
    title: "Resolve an exception",
    description: "Names you as owner and records what was done.",
    method: "POST",
    path: "/v1/commercial/exceptions/{exception_id}:resolve",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "exception_id", label: "Exception ID", in: "path", required: true },
      { key: "resolution", label: "Resolution", kind: "text", required: true },
    ],
  },
];

export const GROUP_LABEL: Record<Action["group"], string> = {
  catalogue: "Catalogue",
  plans: "Plans and accounts",
  sales: "Quotes and orders",
  billing: "Billing runs",
  vendors: "Suppliers",
  assurance: "Assurance",
};

/** Turn form values into a request. Throws a readable message for anything the form cannot express. */
export function buildRequest(action: Action, values: Record<string, string | boolean>): { path: string; body: Record<string, unknown> } {
  let path = action.path;
  const query = new URLSearchParams();
  const body: Record<string, unknown> = {};
  for (const f of action.fields) {
    const raw = values[f.key];
    const kind = f.kind ?? "text";
    const empty = raw === undefined || raw === "" || (kind === "boolean" && raw === undefined);
    if (empty && kind !== "boolean") {
      if (f.required) throw new Error(`${f.label} is required.`);
      continue;
    }
    let value: unknown;
    if (kind === "boolean") value = raw === true;
    else if (kind === "integer") {
      if (!/^-?\d+$/.test(String(raw).trim())) throw new Error(`${f.label} must be a whole number.`);
      value = Number(String(raw).trim());
    } else if (kind === "decimal") {
      if (!/^-?\d+(\.\d+)?$/.test(String(raw).trim())) throw new Error(`${f.label} must be a number.`);
      value = String(raw).trim();
    } else if (kind === "datetime") {
      const d = new Date(String(raw));
      if (Number.isNaN(d.getTime())) throw new Error(`${f.label} is not a valid date and time.`);
      value = d.toISOString();
    } else if (kind === "json") {
      try {
        value = JSON.parse(String(raw));
      } catch {
        throw new Error(`${f.label} is not valid JSON.`);
      }
    } else value = String(raw).trim();

    if (f.in === "path") path = path.replace(`{${f.key}}`, encodeURIComponent(String(value)));
    else if (f.in === "query") query.set(f.key, String(value));
    else body[f.key] = value;
  }
  const qs = query.toString();
  return { path: qs ? `${path}?${qs}` : path, body };
}

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
  group: "catalogue" | "plans" | "sales" | "billing" | "vendors" | "assurance" | "payments";
  title: string;
  description: string;
  /** Shown instead of `description` when the cell runs the operator plane in direct mode (no second approver). */
  directDescription?: string;
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
    directDescription: "Start a DRAFT, optionally as a copy of an existing book. Nothing is visible to customers until the book is approved.",
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
    directDescription: "Approves the book yourself. Rates then apply to new orders only, never retroactively, and an approved book can no longer be edited.",
    method: "POST",
    path: "/v1/commercial/price-books/{price_book_id}:approve",
    capability: "commercial.catalog.manage",
    confirm: "approve this price book",
    fields: [
      { key: "price_book_id", label: "Price book ID", in: "path", required: true },
      { key: "effective_from", label: "Effective from", kind: "datetime", help: "Defaults to now." },
    ],
  },

  {
    id: "matrix-create",
    group: "catalogue",
    title: "New draft market matrix",
    description:
      "Start a DRAFT of which service family is sold where, by whom, and on whose signed evidence, optionally as a copy. A copy is signed afresh: the approvals are not copied.",
    method: "POST",
    path: "/v1/commercial/market-matrices",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "copy_of", label: "Copy of (matrix ID)", placeholder: "optional" },
      { key: "note", label: "Note" },
    ],
  },
  {
    id: "matrix-row",
    group: "catalogue",
    title: "Set a market row on a draft",
    description:
      "One service family in one country: seller entity, merchant route, currency, tax class, data region, and a reference to each piece of signed evidence. A reference is not proof; four independent people sign before the matrix can be active.",
    method: "PUT",
    path: "/v1/commercial/market-matrices/{matrix_id}/rows",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "matrix_id", label: "Draft matrix ID", in: "path", required: true },
      { key: "family", label: "Family", required: true, placeholder: "IDV" },
      { key: "country", label: "Country (ISO 3166-1 alpha-2)", required: true, placeholder: "DE" },
      { key: "seller_entity", label: "Seller entity", required: true },
      { key: "merchant_route", label: "Merchant route", required: true },
      { key: "currency", label: "Currency", required: true, placeholder: "EUR" },
      { key: "tax_class", label: "Tax class", required: true },
      { key: "data_region", label: "Data region", required: true },
      { key: "offer_ref", label: "Signed offer reference", required: true },
      { key: "legal_basis_ref", label: "Legal basis reference", required: true },
      { key: "release_evidence_ref", label: "Release evidence reference", required: true },
      { key: "provider_rights_ref", label: "Provider rights reference", help: "Required for a family that needs a provider." },
      { key: "regulated_status_ref", label: "Regulated status reference", help: "Required for a regulated family." },
      { key: "provider_rights_expires", label: "Provider rights expire on (YYYY-MM-DD)", help: "Required whenever a provider rights reference is given." },
      { key: "regulated_status_expires", label: "Regulated status expires on (YYYY-MM-DD)", help: "Required whenever a regulated status reference is given." },
      { key: "payment_configured", label: "Payment route configured", kind: "boolean", required: true },
      { key: "tax_configured", label: "Tax configuration in place", kind: "boolean", required: true },
      { key: "document_issuer_countries", label: "Document issuer countries (JSON list)", kind: "json", placeholder: '["DE","FR"]', help: "Only for a service whose coverage depends on the country that issued the document. A buyer's issuer country is judged against this list." },
    ],
  },
  {
    id: "matrix-import",
    group: "catalogue",
    title: "Import many rows into a draft market matrix",
    description:
      "Paste a JSON list of rows (up to 500). All or nothing: if any row is incomplete or repeats a family and country, nothing is written and every problem is listed by row. A row is a reference to signed evidence, never the evidence.",
    method: "POST",
    path: "/v1/commercial/market-matrices/{matrix_id}/rows:import",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "matrix_id", label: "Draft matrix ID", in: "path", required: true },
      { key: "rows", label: "Rows (JSON list)", kind: "json", required: true, help: "Each row has family, country, seller_entity, merchant_route, currency, tax_class, data_region, offer_ref, legal_basis_ref, release_evidence_ref, payment_configured, tax_configured, and where needed provider_rights_ref, regulated_status_ref and their expiry dates." },
    ],
  },
  {
    id: "matrix-approve",
    group: "catalogue",
    title: "Sign a market matrix for a role",
    description:
      "Four different people sign, each for one role. The author cannot sign, and one person cannot sign for two roles.",
    method: "POST",
    path: "/v1/commercial/market-matrices/{matrix_id}:approve",
    capability: "commercial.catalog.manage",
    confirm: "sign this market matrix for the role chosen",
    fields: [
      { key: "matrix_id", label: "Matrix ID", in: "path", required: true },
      {
        key: "role",
        label: "Role you sign for",
        kind: "select",
        required: true,
        options: ["PRODUCT", "FINANCE", "LEGAL_PRIVACY", "SECURITY_QA"].map((id) => ({ id, label: id })),
      },
    ],
  },
  {
    id: "matrix-activate",
    group: "catalogue",
    title: "Activate a market matrix",
    description:
      "Needs all four signatures, complete rows, and someone other than the author. The matrix then becomes immutable and the previous one is superseded.",
    method: "POST",
    path: "/v1/commercial/market-matrices/{matrix_id}:activate",
    capability: "commercial.catalog.manage",
    confirm: "activate this market matrix",
    fields: [
      { key: "matrix_id", label: "Matrix ID", in: "path", required: true },
      { key: "effective_from", label: "Effective from", kind: "datetime", help: "Defaults to now." },
    ],
  },
  {
    id: "matrix-rollout",
    group: "catalogue",
    title: "Move a market through its rollout",
    description:
      "A market of the active matrix goes SANDBOX, STAGING, PILOT, CANARY, PRODUCTION one stage at a time, each move by a different person. It is sold only in CANARY or PRODUCTION. Pause or roll back at any time; stopping never needs a second person.",
    method: "POST",
    path: "/v1/commercial/market-rollout",
    capability: "commercial.catalog.manage",
    confirm: "move this market to the stage chosen",
    fields: [
      { key: "family", label: "Family code", required: true, placeholder: "API" },
      { key: "country", label: "Country (two letters)", required: true, placeholder: "DE" },
      {
        key: "stage",
        label: "Move to",
        kind: "select",
        required: true,
        options: ["SANDBOX", "STAGING", "PILOT", "CANARY", "PRODUCTION", "PAUSED", "ROLLED_BACK"].map((id) => ({ id, label: id })),
      },
      { key: "reason", label: "Reason", required: true, help: "Recorded in the append-only rollout log." },
    ],
  },
  {
    id: "fx-set-create",
    group: "catalogue",
    title: "New draft FX rate set",
    description: "A rate set names its source and as-of date. There are no default rates: you enter each one from that source. Another person activates it.",
    method: "POST",
    path: "/v1/commercial/fx-rate-sets",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "source_ref", label: "Named source", required: true, placeholder: "ECB reference rates 2026-09-30" },
      { key: "as_of", label: "Rates as of (YYYY-MM-DD)", required: true },
    ],
  },
  {
    id: "fx-rate-put",
    group: "catalogue",
    title: "Set one rate in a draft FX rate set",
    description: "US dollars per one unit of the currency, up to 12 decimals. Only a draft can change.",
    method: "PUT",
    path: "/v1/commercial/fx-rate-sets/{set_id}/rates",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "set_id", label: "Rate set ID", in: "path", required: true },
      { key: "currency", label: "Currency (three letters)", required: true, placeholder: "EUR" },
      { key: "usd_per_unit", label: "USD per unit", kind: "decimal", required: true },
    ],
  },
  {
    id: "fx-set-activate",
    group: "catalogue",
    title: "Activate an FX rate set",
    description: "Needs at least one rate and someone other than the author. The set then becomes immutable and the previous one is superseded.",
    method: "POST",
    path: "/v1/commercial/fx-rate-sets/{set_id}:activate",
    capability: "commercial.catalog.manage",
    confirm: "activate this FX rate set",
    fields: [{ key: "set_id", label: "Rate set ID", in: "path", required: true }],
  },
  {
    id: "fx-set-withdraw",
    group: "catalogue",
    title: "Withdraw a draft FX rate set",
    description: "Only a draft can be withdrawn.",
    method: "POST",
    path: "/v1/commercial/fx-rate-sets/{set_id}:withdraw",
    capability: "commercial.catalog.manage",
    confirm: "withdraw this draft FX rate set",
    fields: [{ key: "set_id", label: "Draft rate set ID", in: "path", required: true }],
  },
  {
    id: "family-text-propose",
    group: "catalogue",
    title: "Propose new wording for a service family",
    description:
      "Changes only the name, unit and sales qualifier a buyer reads. It cannot change what the family needs or does, it may not contain a price or a status claim such as certified or qualified in the name or unit, and it changes nothing until a different person activates it.",
    method: "POST",
    path: "/v1/commercial/service-family-texts",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "family", label: "Family code", required: true, placeholder: "DOC" },
      { key: "name", label: "Name", required: true },
      { key: "unit", label: "Commercial unit", required: true },
      { key: "sales_qualifier", label: "Sales qualifier", help: "Conditions the sale depends on. Optional." },
      { key: "reason", label: "Why", required: true },
    ],
  },
  {
    id: "family-text-activate",
    group: "catalogue",
    title: "Activate proposed family wording",
    description: "Needs someone other than the author. The wording then becomes immutable and the previous wording of that family is superseded.",
    method: "POST",
    path: "/v1/commercial/service-family-texts/{rev_id}:activate",
    capability: "commercial.catalog.manage",
    confirm: "activate this wording",
    fields: [{ key: "rev_id", label: "Revision ID", in: "path", required: true }],
  },
  {
    id: "family-text-withdraw",
    group: "catalogue",
    title: "Withdraw proposed family wording",
    description: "Only a draft can be withdrawn.",
    method: "POST",
    path: "/v1/commercial/service-family-texts/{rev_id}:withdraw",
    capability: "commercial.catalog.manage",
    confirm: "withdraw this draft wording",
    fields: [{ key: "rev_id", label: "Draft revision ID", in: "path", required: true }],
  },
  {
    id: "rate-card-create",
    group: "catalogue",
    title: "New draft family rate card",
    description: "A rate card holds the rate for each service, payer country and billing period. There are no default rates: every line is entered from a signed source. The CFO, CCO and Founder sign it, none of them its author.",
    method: "POST",
    path: "/v1/commercial/family-rate-cards",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "copy_of", label: "Copy of (rate card ID)", placeholder: "optional" },
      { key: "note", label: "Note" },
    ],
  },
  {
    id: "rate-card-line",
    group: "catalogue",
    title: "Set one rate on a draft rate card",
    description: "One service, payer country and period: currency, charge unit, units included, amount per unit, and an optional minimum that is either a spend floor or a platform fee, never both. Only a draft can change.",
    method: "PUT",
    path: "/v1/commercial/family-rate-cards/{card_id}/lines",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "card_id", label: "Draft rate card ID", in: "path", required: true },
      { key: "family", label: "Family code", required: true, placeholder: "API" },
      { key: "country", label: "Payer country (two letters)", required: true, placeholder: "DE" },
      { key: "period", label: "Period", kind: "select", required: true, options: [{ id: "MONTHLY", label: "MONTHLY" }, { id: "ANNUAL", label: "ANNUAL" }] },
      { key: "currency", label: "Currency (three letters)", required: true, placeholder: "EUR" },
      { key: "charge_unit", label: "Charge unit", required: true, placeholder: "completed check" },
      { key: "included_units", label: "Units included", kind: "integer" },
      { key: "unit_amount", label: "Amount per unit above the included", kind: "decimal", help: "Up to six decimals. Leave at zero only when there is a minimum." },
      { key: "minimum_amount", label: "Minimum amount", kind: "decimal", help: "Optional. Choose below whether it is a floor or a fee." },
      { key: "minimum_model", label: "The minimum is", kind: "select", options: [{ id: "FLOOR", label: "FLOOR: the greater of the minimum and usage" }, { id: "PLATFORM_FEE", label: "PLATFORM_FEE: billed in addition to usage" }] },
      { key: "source_ref", label: "Source reference", required: true, placeholder: "signed rate schedule reference" },
    ],
  },
  {
    id: "rate-card-import",
    group: "catalogue",
    title: "Import many rates into a draft rate card",
    description: "Paste a JSON list of rate lines (up to 500). All or nothing: if any line is invalid or repeats a service, country and period, nothing is written and every problem is listed by line.",
    method: "POST",
    path: "/v1/commercial/family-rate-cards/{card_id}/lines:import",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "card_id", label: "Draft rate card ID", in: "path", required: true },
      { key: "lines", label: "Lines (JSON list)", kind: "json", required: true, help: "Each line has family, country, period, currency, charge_unit, source_ref and, as needed, included_units, unit_amount, minimum_amount and minimum_model." },
    ],
  },
  {
    id: "rate-card-approve",
    group: "catalogue",
    title: "Sign a family rate card for a role",
    description: "The CFO, the CCO and the Founder sign, as three different people, none the author. You must hold the appointment for the role you choose.",
    method: "POST",
    path: "/v1/commercial/family-rate-cards/{card_id}:approve",
    capability: "commercial.catalog.manage",
    confirm: "sign this rate card for the role chosen",
    fields: [
      { key: "card_id", label: "Rate card ID", in: "path", required: true },
      { key: "role", label: "Role you sign for", kind: "select", required: true, options: ["CFO", "CCO", "FOUNDER"].map((id) => ({ id, label: id })) },
    ],
  },
  {
    id: "rate-card-activate",
    group: "catalogue",
    title: "Activate a family rate card",
    description: "Needs all three signatures and someone other than the author. The card then becomes immutable and the previous one is superseded. Public prices and estimates change at once.",
    method: "POST",
    path: "/v1/commercial/family-rate-cards/{card_id}:activate",
    capability: "commercial.catalog.manage",
    confirm: "activate this rate card and show its rates publicly",
    fields: [
      { key: "card_id", label: "Rate card ID", in: "path", required: true },
      { key: "effective_from", label: "Effective from", kind: "datetime", help: "Leave blank to take effect now." },
    ],
  },
  {
    id: "rate-card-withdraw",
    group: "catalogue",
    title: "Withdraw a draft family rate card",
    description: "Only a draft can be withdrawn.",
    method: "POST",
    path: "/v1/commercial/family-rate-cards/{card_id}:withdraw",
    capability: "commercial.catalog.manage",
    confirm: "withdraw this draft rate card",
    fields: [{ key: "card_id", label: "Draft rate card ID", in: "path", required: true }],
  },
  {
    id: "page-sign",
    group: "catalogue",
    title: "Sign the public pricing page packaging",
    description: "An appointed Founder or CCO signs the page file by the hash shown on this tab. Both, as different people, make the packaging approved; any change to the file removes the signatures.",
    method: "POST",
    path: "/v1/commercial/pricing-page:sign",
    capability: "commercial.catalog.manage",
    confirm: "sign the pricing page packaging as it is now",
    fields: [{ key: "content_hash", label: "Content hash shown", required: true, help: "Copy it from the pricing page card. If the file has changed, the signature is refused." }],
  },
  {
    id: "notice-propose",
    group: "catalogue",
    title: "Propose the inquiry form's privacy notice",
    description: "Legal's text, as a draft. It changes nothing a visitor sees until a different person activates it. There is no default text beyond a marked draft.",
    method: "POST",
    path: "/v1/commercial/public-notices",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "version", label: "Version", required: true, placeholder: "pricing-inquiry-notice-2026-12" },
      { key: "body", label: "Notice text", required: true, help: "At least 40 characters. Shown beside the inquiry form." },
    ],
  },
  {
    id: "notice-activate",
    group: "catalogue",
    title: "Activate a privacy notice",
    description: "Someone other than its author. The previous notice is superseded and every inquiry from then on records this version.",
    method: "POST",
    path: "/v1/commercial/public-notices/{notice_id}:activate",
    capability: "commercial.catalog.manage",
    confirm: "activate this privacy notice",
    fields: [{ key: "notice_id", label: "Notice ID", in: "path", required: true }],
  },
  {
    id: "notice-withdraw",
    group: "catalogue",
    title: "Withdraw a draft privacy notice",
    description: "Only a draft can be withdrawn.",
    method: "POST",
    path: "/v1/commercial/public-notices/{notice_id}:withdraw",
    capability: "commercial.catalog.manage",
    confirm: "withdraw this draft notice",
    fields: [{ key: "notice_id", label: "Draft notice ID", in: "path", required: true }],
  },
  {
    id: "lead-advance",
    group: "sales",
    title: "Move a sales inquiry on, or close it",
    description:
      "One step at a time: received, qualified, scoped, in review, offer sent. Closing or marking spam needs a reason. Nothing moves backwards, and no inquiry ever creates an account, quote, contract or payment.",
    method: "POST",
    path: "/v1/commercial/sales-leads/{lead_id}:advance",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "lead_id", label: "Inquiry ID", in: "path", required: true },
      {
        key: "status",
        label: "Move to",
        kind: "select",
        required: true,
        options: ["QUALIFIED", "SCOPED", "REVIEW", "OFFER_SENT", "CLOSED", "SPAM"].map((id) => ({ id, label: id })),
      },
      { key: "reason", label: "Reason", help: "Required to close or mark as spam." },
    ],
  },
  {
    id: "matrix-withdraw",
    group: "catalogue",
    title: "Withdraw a draft market matrix",
    description: "Only a DRAFT can be withdrawn. An activated matrix is never edited or deleted; the next one supersedes it.",
    method: "POST",
    path: "/v1/commercial/market-matrices/{matrix_id}:withdraw",
    capability: "commercial.catalog.manage",
    confirm: "withdraw this draft market matrix",
    fields: [{ key: "matrix_id", label: "Draft matrix ID", in: "path", required: true }],
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
    id: "enforcement-set",
    group: "plans",
    title: "Enforce plan limits for one company",
    description:
      "ENFORCE makes this company's plan limits refuse for real (HTTP 402) while the rest of the deployment only reports; SHADOW sets it back. It can only make a company stricter than the deployment, never looser. Recorded in the change log.",
    method: "PUT",
    path: "/v1/commercial/tenants/{tenant_id}/enforcement",
    capability: "tenant.config.change",
    confirm: "change how this company's plan limits are applied",
    fields: [
      tenant,
      { key: "mode", label: "Mode", kind: "select", options: [{ id: "ENFORCE", label: "Enforce (refuse beyond the plan)" }, { id: "SHADOW", label: "Shadow (report only)" }], required: true },
      { key: "reason", label: "Reason", required: true, placeholder: "Pilot company; contract signed" },
    ],
  },
  {
    id: "tenant-data-deletion",
    group: "plans",
    title: "Delete a closed company's data",
    description: "Only for a company that is already closed. The worker erases what no legal hold protects and reports what it kept. Give the case or legal reference that asks for it.",
    method: "POST",
    path: "/v1/commercial/tenants/{tenant_id}/data-deletion",
    capability: "tenant.data.delete",
    confirm: "delete this closed company's data",
    fields: [tenant, { key: "reason_ref", label: "Case or legal reference", required: true, placeholder: "Ticket or legal reference" }],
  },
  {
    id: "override-add",
    group: "plans",
    title: "Contracted override",
    description: "A capability or extra connections beyond the plan, with a second approver and an expiry.",
    directDescription: "A capability or extra connections beyond the plan, with an expiry.",
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
    description: "Two-person, idempotent. An order with a monthly fee first waits for the company to pay the first month on Stripe; only then does the plan become the tenant's entitlement. An order with nothing to pay switches at once.",
    directDescription: "Idempotent. An order with a monthly fee first waits for the company to pay the first month on Stripe; only then does the plan become the tenant's entitlement.",
    method: "POST",
    path: "/v1/commercial/orders/{order_id}:activate",
    capability: "commercial.sales.manage",
    confirm: "activate this order",
    fields: [{ key: "order_id", label: "Order ID", in: "path", required: true }],
  },
  {
    id: "order-cancel-payment-wait",
    group: "sales",
    title: "Take an order back from awaiting payment",
    description: "Returns an order that is waiting for its first payment to signed. Nothing was charged; a paid order cannot be taken back here.",
    method: "POST",
    path: "/v1/commercial/orders/{order_id}:cancel-payment-wait",
    capability: "commercial.sales.manage",
    confirm: "take this order back from awaiting payment",
    fields: [{ key: "order_id", label: "Order ID", in: "path", required: true }],
  },
  {
    id: "order-refund-activation",
    group: "sales",
    title: "Return a paid activation payment",
    description: "Marks the first-month payment for an order as to be returned. Make the refund at the processor with the reference shown under 'Activation payments to return'; its report marks it refunded. Only a payment no invoice has used.",
    method: "POST",
    path: "/v1/commercial/orders/{order_id}:refund-activation",
    capability: "commercial.sales.manage",
    confirm: "mark this activation payment to be returned",
    fields: [
      { key: "order_id", label: "Order ID", in: "path", required: true },
      { key: "reason", label: "Why it is returned", required: true },
    ],
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
    directDescription: "Corrects an event without editing it. You decide it yourself.",
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
    directDescription: "Approve or reject an adjustment request.",
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
    directDescription: "Freeze, rate and draft in one resumable run. Issuing is a separate step.",
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
    directDescription: "Issues the invoice. It is then immutable; a correction is a credit note.",
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
    id: "payment-event-credit",
    group: "payments",
    title: "Credit a refund",
    description:
      "Issues a credit note for a refund Stripe reported, on the invoice it refunded, and closes the item. Stripe reports the total refunded on a charge, so enter only the amount not yet credited.",
    method: "POST",
    path: "/v1/commercial/payment-events/{event_id}:credit-note",
    capability: "commercial.billing.manage",
    confirm: "credit this refund",
    fields: [
      { key: "event_id", label: "Event ID", in: "path", required: true },
      { key: "amount", label: "Amount to credit", kind: "decimal", required: true },
    ],
  },
  {
    id: "payment-event-resolve",
    group: "payments",
    title: "Close a payment event",
    description: "Records what was done about a dispute, an unmatched payment or any other item, and removes it from the list.",
    method: "POST",
    path: "/v1/commercial/payment-events/{event_id}:resolve",
    capability: "commercial.catalog.manage",
    fields: [
      { key: "event_id", label: "Event ID", in: "path", required: true },
      { key: "resolution", label: "What was done", kind: "text", required: true },
    ],
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
  payments: "Payment events",
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

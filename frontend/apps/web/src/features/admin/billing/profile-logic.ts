import type { BillingProfile, BillingProfileIn, TaxIdType } from "@zoiko/api-client";

/** The billing profile form (ZID-UX-COM-008 B04). The server checks every rule again; these only stop an obviously wrong save from
 * leaving the page, and they follow the same patterns (commercial/application/billing_workspace.py). */

export const TAX_ID_TYPES: { id: TaxIdType; label: string }[] = [
  { id: "VAT", label: "VAT number" },
  { id: "GST", label: "GST number" },
  { id: "EIN", label: "EIN" },
  { id: "COMPANY_REGISTRATION", label: "Company registration number" },
  { id: "OTHER", label: "Other tax identifier" },
];

export const MAX_TAX_IDS = 5;
const EMAIL = /^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$/;
const TAX_VALUE = /^[A-Za-z0-9 .\-/]{4,40}$/;

export type ProfileForm = {
  line1: string;
  line2: string;
  city: string;
  region: string;
  postal_code: string;
  country: string;
  tax_ids: { type: TaxIdType; value: string }[];
  invoice_email: string;
  notices_email: string;
  tax_document_email: string;
};

export function formFromProfile(p: BillingProfile): ProfileForm {
  return {
    line1: p.address?.line1 ?? "",
    line2: p.address?.line2 ?? "",
    city: p.address?.city ?? "",
    region: p.address?.region ?? "",
    postal_code: p.address?.postal_code ?? "",
    country: p.address?.country ?? p.payer.country ?? "",
    tax_ids: p.tax_ids.map((t) => ({ type: t.type, value: t.value })),
    invoice_email: p.contacts?.invoice_email ?? "",
    notices_email: p.contacts?.notices_email ?? "",
    tax_document_email: p.contacts?.tax_document_email ?? "",
  };
}

export type ProfileErrors = Partial<Record<"line1" | "city" | "country" | "invoice_email" | "notices_email" | "tax_document_email" | "tax_ids", string>>;

export function validateProfile(f: ProfileForm): ProfileErrors {
  const e: ProfileErrors = {};
  if (!f.line1.trim()) e.line1 = "Enter the first line of the address.";
  if (!f.city.trim()) e.city = "Enter the city.";
  if (!/^[A-Z]{2}$/.test(f.country.trim().toUpperCase())) e.country = "Use the two-letter country code, for example GB.";
  if (!f.invoice_email.trim()) e.invoice_email = "Enter the address invoices are sent to.";
  else if (!EMAIL.test(f.invoice_email.trim())) e.invoice_email = "That does not look like an email address.";
  if (f.notices_email.trim() && !EMAIL.test(f.notices_email.trim())) e.notices_email = "That does not look like an email address.";
  if (f.tax_document_email.trim() && !EMAIL.test(f.tax_document_email.trim())) e.tax_document_email = "That does not look like an email address.";
  if (f.tax_ids.length > MAX_TAX_IDS) e.tax_ids = `At most ${MAX_TAX_IDS} tax identifiers.`;
  else if (f.tax_ids.some((t) => !TAX_VALUE.test(t.value.trim()))) e.tax_ids = "Each tax identifier is 4 to 40 letters, digits, spaces, dots, dashes or slashes.";
  return e;
}

export function profileBody(f: ProfileForm, expectedVersion: number): BillingProfileIn {
  return {
    expected_version: expectedVersion,
    address: {
      line1: f.line1.trim(),
      line2: f.line2.trim(),
      city: f.city.trim(),
      region: f.region.trim(),
      postal_code: f.postal_code.trim(),
      country: f.country.trim().toUpperCase(),
    },
    tax_ids: f.tax_ids.map((t) => ({ type: t.type, value: t.value.trim() })),
    contacts: {
      invoice_email: f.invoice_email.trim(),
      notices_email: f.notices_email.trim(),
      tax_document_email: f.tax_document_email.trim(),
    },
  };
}

/** True when the form differs from what was saved, so Save stays off until there is something to save (the server refuses NO_CHANGE). */
export function profileChanged(a: ProfileForm, b: ProfileForm): boolean {
  return JSON.stringify(normalise(a)) !== JSON.stringify(normalise(b));
}

function normalise(f: ProfileForm) {
  return {
    ...Object.fromEntries(Object.entries(f).filter(([k]) => k !== "tax_ids").map(([k, v]) => [k, String(v).trim()])),
    tax_ids: f.tax_ids.map((t) => ({ type: t.type, value: t.value.trim() })),
  };
}

export const PROFILE_FIELD_LABEL: Record<string, string> = {
  address: "Address",
  tax_ids: "Tax identifiers",
  contacts: "Contacts",
  line1: "Address line 1",
  line2: "Address line 2",
  city: "City",
  region: "Region",
  postal_code: "Postal code",
  country: "Country",
  invoice_email: "Invoice email",
  notices_email: "Notices email",
  tax_document_email: "Tax document email",
};

export const profileFieldLabel = (k: string) => PROFILE_FIELD_LABEL[k] ?? k;

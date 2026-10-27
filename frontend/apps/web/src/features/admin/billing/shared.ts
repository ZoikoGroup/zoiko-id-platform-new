/** Billing display helpers. Amounts are decimal strings from the cell (never floats on the wire); they are
 * converted only to be shown. */
export const METER_LABEL: Record<string, string> = {
  MAH: "Monthly active people",
  AUTH_EVENT: "Sign-ins",
  POLICY_DECISION: "Access decisions",
  M2M_GRANT: "Machine grants",
  VC_ISSUED: "Credentials issued",
  VC_PRESENTED: "Credential presentations checked",
};

/** The plan allowance that matches each metered quantity (VC meters have no included allowance). */
export const ALLOWANCE_FIELD = {
  MAH: "mah_included",
  AUTH_EVENT: "auth_events_included",
  POLICY_DECISION: "policy_decisions_included",
  M2M_GRANT: "m2m_grants_included",
} as const;

export function meterLabel(meter: string | null): string {
  return meter ? (METER_LABEL[meter] ?? meter) : "Platform";
}

export function money(amount: string | null | undefined, currency: string | null | undefined): string {
  if (amount === null || amount === undefined) return "—";
  const n = Number(amount);
  if (!Number.isFinite(n)) return amount;
  return new Intl.NumberFormat("en-US", { style: "currency", currency: currency ?? "USD", currencyDisplay: "code" }).format(n);
}

export function count(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  return Number.isFinite(n) ? new Intl.NumberFormat("en-US").format(n) : String(value);
}

/** Warning bands match the cell's 50/80/100 budget thresholds (ZID-COM-001 §7). */
export function usageTone(pct: number): "brand" | "warn" | "danger" {
  if (pct >= 100) return "danger";
  if (pct >= 80) return "warn";
  return "brand";
}

export const BILLING_KEY = ["billing"] as const;

export const STATE_COPY: Record<string, string> = {
  ACTIVE: "Your subscription is active.",
  PAST_DUE: "A payment is overdue. Everything still works; settle the balance to avoid restrictions on paid extras.",
  RESTRICTED: "Paid extras that are not security-critical are restricted until the balance is settled.",
  SUSPENDED: "The account is suspended for billing. Sign-in and revocation keep working so nobody is locked out.",
  TERMINATING: "The account is being closed. Export what you need before the final date.",
  CLOSED: "The account is closed.",
};

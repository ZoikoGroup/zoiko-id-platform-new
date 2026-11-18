import type { ScreeningProvider } from "@zoiko/api-client";

const TEXT: Record<string, string> = {
  STATE_DISABLED: "The provider is disabled in the registry.",
  STATE_SANDBOX: "The provider is sandbox only and is never used in production.",
  CONTRACT_VERSION_MISSING: "No contract version is recorded.",
  PRICE_SCHEDULE_MISSING: "No price schedule is recorded.",
  FINANCE_AUTHORISATION_MISSING: "Finance has not authorised the price schedule.",
  NO_CONTRACTED_CLASSES: "The contract names no check class.",
  NO_CONTRACTED_MARKETS: "The contract names no market.",
  NO_APPROVED_REGIONS: "No processing region is approved for the provider.",
  UNIT_COST_NOT_SET: "The price of a check is not set.",
  NO_MONTHLY_BUDGET: "There is no monthly budget cap: a paid provider without one can run up cost unnoticed.",
  NO_MONTHLY_QUOTA: "There is no monthly quota.",
  ADAPTER_NOT_REGISTERED: "The provider's adapter is not installed in this deployment.",
};

export function blockedText(code: string): string {
  return TEXT[code] ?? code;
}

export function providerTone(p: Pick<ScreeningProvider, "state" | "blocked_by" | "circuit">): "neutral" | "ok" | "warn" | "danger" {
  if (p.circuit.open) return "danger";
  if (p.state === "APPROVED" && p.blocked_by.length === 0) return "ok";
  return p.state === "APPROVED" ? "warn" : "neutral";
}

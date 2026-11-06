import type { Catalog, CatalogPlan } from "@zoiko/api-client";

/** One line of the review: what the company has now and what the plan it asks for would include. */
export type ReviewRow = { label: string; from: string; to: string; change: "SAME" | "MORE" | "LESS" | "UNKNOWN" };

const LIMITS: [Exclude<keyof CatalogPlan["limits"], "support_level">, string][] = [
  ["mah_included", "People included"],
  ["auth_events_included", "Sign-ins included"],
  ["policy_decisions_included", "Access decisions included"],
  ["m2m_grants_included", "Machine grants included"],
  ["sso_included", "SSO connections"],
  ["production_environments", "Production environments"],
];

const show = (n: number | null | undefined): string => (n === null || n === undefined ? "Agreed per order" : n.toLocaleString("en-GB"));

function compare(a: number | null | undefined, b: number | null | undefined): ReviewRow["change"] {
  if (a === null || a === undefined || b === null || b === undefined) return "UNKNOWN";
  return a === b ? "SAME" : b > a ? "MORE" : "LESS";
}

/** The fee the approved price book gives a plan, or null when there is none: the review never invents a price. */
export function monthlyFee(catalog: Pick<Catalog, "pricing"> | undefined, plan: string): string | null {
  return catalog?.pricing?.lines.find((l) => l.sku === "ZID-BASE" && l.plan === plan)?.unit_rate ?? null;
}

/** Before and after, only from the catalogue the cell published. `UNKNOWN` means the catalogue does not say, which the screen shows as agreed per order. */
export function reviewRows(from: CatalogPlan | undefined, to: CatalogPlan): ReviewRow[] {
  const rows: ReviewRow[] = LIMITS.map(([key, label]) => ({ label, from: show(from?.limits[key]), to: show(to.limits[key]), change: compare(from?.limits[key], to.limits[key]) }));
  rows.push({ label: "Support", from: from?.limits.support_level ?? "—", to: to.limits.support_level, change: from?.limits.support_level === to.limits.support_level ? "SAME" : "UNKNOWN" });
  return rows;
}

/** What the company would lose by moving: a smaller allowance, or a capability the current plan has and the new one does not. */
export function losses(from: CatalogPlan | undefined, to: CatalogPlan): string[] {
  if (!from) return [];
  const out = reviewRows(from, to).filter((r) => r.change === "LESS").map((r) => `${r.label}: ${r.from} becomes ${r.to}`);
  const has = new Set(to.capabilities.map((c) => c.capability));
  for (const c of from.capabilities) if (!has.has(c.capability)) out.push(`Capability ${c.capability} is not in the new plan`);
  return out;
}

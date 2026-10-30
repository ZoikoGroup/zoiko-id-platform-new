/** What a plan includes, as the customer reads it (ZID-ENG-DEC-008). Every word here is the server's answer made readable:
 * this module decides nothing, and a higher plan is never offered as a way past a region or a compliance hold. */
import type { EffectiveEntitlements, PlanFeature, UsageMeter } from "@zoiko/api-client";

export type FeatureTone = "ok" | "info" | "warn" | "neutral";

export const SECTION_TITLE: Record<string, { title: string; help: string }> = {
  baseline: { title: "Always included", help: "Security and trust protections on every plan. No plan can remove them." },
  standard: { title: "Standard capabilities", help: "Included on every plan, subject to the allowances in your catalogue." },
  advanced: { title: "Advanced capabilities", help: "Available from higher plans, or by a separately agreed contract." },
};

const REASON: Record<string, string> = {
  NOT_IN_BAND: "Not part of your plan.",
  NOT_IN_PLAN: "Your plan does not include this.",
  ADD_ON_REQUIRED: "Available as an add-on under a signed order.",
  NOT_RELEASED: "Not released yet.",
  SUSPENDED: "Paused while the subscription is suspended.",
  BILLING_RESTRICTED: "Paused while a balance is overdue.",
  QUOTA_EXHAUSTED: "The allowance for this period is used.",
  NOT_AVAILABLE_IN_REGION: "Not available in your region.",
  COVERAGE_UNKNOWN: "We cannot confirm this right now.",
  PROVIDER_ROUTE_OUT_OF_COVERAGE: "No approved route covers this country yet.",
  UNKNOWN_CAPABILITY: "Not part of the catalogue.",
};

export function reasonText(reasons: readonly string[]): string {
  return reasons.map((r) => REASON[r] ?? "Not available.").join(" ");
}

/** A compliance reason is never answered with an upgrade. */
export function isCompliance(reasons: readonly string[]): boolean {
  return reasons.some((r) => r === "NOT_AVAILABLE_IN_REGION" || r === "COVERAGE_UNKNOWN" || r === "PROVIDER_ROUTE_OUT_OF_COVERAGE");
}

export function featureTone(f: PlanFeature): FeatureTone {
  if (f.available) return f.band_state === "ADD_ON" ? "info" : "ok";
  return isCompliance(f.reasons) ? "warn" : "neutral";
}

/** The sentence under a feature that is not available: the band that would include it, unless the reason is compliance. */
export function upgradeHint(f: PlanFeature): string | null {
  if (f.available || isCompliance(f.reasons)) return null;
  if (f.upgrade_band) return `Included from band ${f.upgrade_band}. Ask your account manager to change plan.`;
  if (f.band_state === "ADD_ON") return "Ask your account manager for the add-on.";
  return null;
}

export function bySection(features: readonly PlanFeature[]): { section: string; title: string; help: string; items: PlanFeature[] }[] {
  return (["baseline", "standard", "advanced"] as const)
    .map((section) => ({ section, ...SECTION_TITLE[section]!, items: features.filter((f) => f.section === section) }))
    .filter((g) => g.items.length > 0);
}

export function summary(v: EffectiveEntitlements): { included: number; upgrade: number; held: number; total: number } {
  const included = v.features.filter((f) => f.available).length;
  const held = v.features.filter((f) => !f.available && isCompliance(f.reasons)).length;
  return { included, held, upgrade: v.features.length - included - held, total: v.features.length };
}

export const BANDS = ["C1", "C2", "C3", "C4", "C5"] as const;

/** Bands other than the current one: only a lower band has a downgrade to disclose. */
export function lowerBands(band: string | null): string[] {
  const at = BANDS.indexOf(band as (typeof BANDS)[number]);
  return at <= 0 ? [] : BANDS.slice(0, at);
}

export function meterNote(m: UsageMeter): string {
  if (!m.quota_defined) return "A limit applies but has not been set. Usage is held for a commercial decision.";
  if (m.limit === null) return m.priced ? "No cap." : "Recorded, not priced and not capped.";
  return `${m.remaining ?? 0} of ${m.limit} left this period.`;
}

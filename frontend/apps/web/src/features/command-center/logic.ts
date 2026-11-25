import type { VerifyAnalytics as VA } from "@zoiko/api-client";

/** The twelve governed views of the Command Center (ZID-CPS-WF-02 section 06.1). */
export const VIEWS = [
  { slug: "overview", label: "Overview" },
  { slug: "conversion", label: "Conversion" },
  { slug: "first-attempt", label: "First attempt" },
  { slug: "decisions", label: "Decisions & review" },
  { slug: "risk", label: "Risk signals" },
  { slug: "providers", label: "Providers" },
  { slug: "economics", label: "Economics" },
  { slug: "delivery", label: "API & webhooks" },
  { slug: "incidents", label: "Incidents & SLOs" },
  { slug: "reports", label: "Reports & exports" },
  { slug: "definitions", label: "Definitions & quality" },
  { slug: "access", label: "Access & privacy" },
] as const;

export type ViewSlug = (typeof VIEWS)[number]["slug"];

export function viewOf(slug: string | undefined): ViewSlug {
  return (VIEWS.find((v) => v.slug === slug)?.slug ?? "overview") as ViewSlug;
}

/** A figure as the reader sees it. Withheld is never shown as zero, and no data is never shown as a value (section 06 truthfulness). */
export function shown(value: number | null | undefined, unit: string, suppressed?: boolean): string {
  if (suppressed) return "Withheld";
  if (value === null || value === undefined) return "No data";
  if (unit === "ratio") return `${(value * 100).toFixed(1)}%`;
  if (unit === "seconds") return value >= 120 ? `${(value / 60).toFixed(1)} min` : `${value.toFixed(1)} s`;
  if (unit === "ms") return `${Math.round(value)} ms`;
  return new Intl.NumberFormat().format(value);
}

/** The basis line under every headline figure (Q10): numerator, denominator and definition version. */
export function basis(g: Pick<VA.Governed, "numerator" | "denominator" | "definition_version" | "suppressed" | "unit">): string {
  if (g.suppressed) return `Fewer than the minimum group in this cohort · definition v${g.definition_version}`;
  const parts: string[] = [];
  if (g.unit === "ratio" && g.denominator !== null && g.denominator !== undefined) parts.push(`${g.numerator ?? 0} of ${g.denominator}`);
  else if (g.denominator !== null && g.denominator !== undefined && g.unit !== "count") parts.push(`n = ${g.denominator}`);
  parts.push(`definition v${g.definition_version}`);
  return parts.join(" · ");
}

/** What kind of figure this is (Q11): observed data, provisional (a source is behind), sandbox data, or none at all. Never "live" for sandbox. */
export function label(quality: string, dataLabel: string | undefined): { text: string; tone: "ok" | "warn" | "info" | "neutral" } {
  if (dataLabel === "SANDBOX") return { text: "Sandbox data, not production", tone: "info" };
  if (quality === "OBSERVED") return { text: "Observed", tone: "ok" };
  if (quality === "PROVISIONAL") return { text: "Provisional: a source is behind", tone: "warn" };
  if (quality === "NO_PRICE") return { text: "No approved price", tone: "neutral" };
  return { text: "No data yet", tone: "neutral" };
}

export type WindowState = { from: string; to: string; tz: string; market: string };

export function defaultWindow(now: Date, tz: string): WindowState {
  const day = (d: Date) => new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
  return { from: day(new Date(now.getTime() - 6 * 86_400_000)), to: day(now), tz, market: "" };
}

export function windowParams(w: WindowState): VA.Window {
  return { from: w.from || undefined, to: w.to || undefined, tz: w.tz, market: w.market || undefined };
}

/** Rows of a breakdown as label + figure, with withheld rows marked rather than dropped (so totals cannot be differenced from what is missing). */
export function rowLabel(r: VA.Row, key: string): string {
  const v = r[key];
  return v === null || v === undefined ? "Unknown" : String(v).replaceAll("_", " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

export const CAUSE: Record<string, string> = {
  RETAKE_NEEDED: "Image unreadable (glare, blur or cut off)",
  TECHNICAL_FAILED: "Provider or service failure",
  UNSUPPORTED_DOCUMENT: "Unsupported document or method",
  LIMIT_REACHED: "Attempt limit reached",
  NFC_UNAVAILABLE: "Chip reading not available on the device",
  RISK: "Capture origin flagged",
  UNKNOWN: "Not recorded",
};

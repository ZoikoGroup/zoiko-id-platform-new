import type { VerifyApiUsage, VerifyApiUsageCounts } from "@zoiko/api-client";

/** The plain-language side of the Usage card (CR-154). Counts only; nothing here prices anything. */

export const COUNT_LABELS: { key: keyof VerifyApiUsageCounts; label: string }[] = [
  { key: "sessions_started", label: "Started" },
  { key: "verified", label: "Verified" },
  { key: "not_verified", label: "Not verified" },
  { key: "inconclusive", label: "No answer" },
  { key: "review_required", label: "With a reviewer" },
  { key: "canceled", label: "Canceled" },
  { key: "expired", label: "Ran out of time" },
];

/** The last `n` months as `YYYY-MM`, newest first, from a given date (so the list never depends on the clock in a test). */
export function recentMonths(from: Date, n = 6): string[] {
  const out: string[] = [];
  for (let i = 0; i < n; i++) {
    const d = new Date(Date.UTC(from.getUTCFullYear(), from.getUTCMonth() - i, 1));
    out.push(`${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`);
  }
  return out;
}

export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(Date.UTC(y!, m! - 1, 1)).toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
}

/** Does the ledger agree with the sessions? In words, for the person reading it. */
export function reconciliationText(r: VerifyApiUsage["reconciliation"]): { ok: boolean; text: string } {
  if (r.reconciled) return { ok: true, text: "The usage records match your live verifications." };
  const parts: string[] = [];
  if (r.sessions_started.sessions !== r.sessions_started.ledger) parts.push(`${r.sessions_started.sessions} sessions started but ${r.sessions_started.ledger} recorded`);
  if (r.checks_completed.sessions !== r.checks_completed.ledger) parts.push(`${r.checks_completed.sessions} checks finished but ${r.checks_completed.ledger} recorded`);
  return { ok: false, text: `The usage records do not match yet: ${parts.join("; ")}. Contact support if this does not clear.` };
}

export const hasAny = (c: VerifyApiUsageCounts): boolean => Object.values(c).some((n) => n > 0);

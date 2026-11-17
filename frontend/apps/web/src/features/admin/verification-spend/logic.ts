import type { VerificationBudget } from "@zoiko/api-client";

export const SPEND_KEY = ["verification-spend"];

const SCALE = 6;
const LIMIT = /^\d{1,13}(\.\d{1,6})?$/;

export type Parsed = { ok: true; limit: string; currency: string } | { ok: false; reason: string };

/** What the person typed, checked the way the platform checks it: a plain decimal (no sign, no exponent, at most six places) and a three-letter currency. Money is never a float. */
export function parseBudget(limitText: string, currencyText: string): Parsed {
  const limit = limitText.trim();
  const currency = currencyText.trim().toUpperCase();
  if (!LIMIT.test(limit)) return { ok: false, reason: "Enter the limit as a plain number, for example 2500 or 2500.50 (at most six decimal places)." };
  if (!/^[A-Z]{3}$/.test(currency)) return { ok: false, reason: "Enter the currency as three letters, for example GBP." };
  return { ok: true, limit, currency };
}

/** A decimal string as a whole number of millionths, so two amounts compare exactly. */
export function micros(amount: string): bigint {
  const [whole = "0", frac = ""] = amount.split(".");
  return BigInt(whole) * 10n ** BigInt(SCALE) + BigInt(frac.padEnd(SCALE, "0").slice(0, SCALE));
}

/** How much of the limit is used, as a whole percent from 0 to 100 for display. A limit of zero is fully used by definition. */
export function percentUsed(spent: string, limit: string): number {
  const l = micros(limit);
  if (l === 0n) return 100;
  const p = Number((micros(spent) * 100n) / l);
  return Math.max(0, Math.min(100, p));
}

export type Standing = { tone: "ok" | "warn" | "danger" | "neutral"; title: string; text: string };

/** The one sentence a finance owner needs: is anything blocked, is it close, or is there no limit at all. */
export function standing(view: VerificationBudget | undefined): Standing {
  if (!view) return { tone: "neutral", title: "Loading", text: "" };
  if (!view.budget) {
    return { tone: "neutral", title: "No monthly limit is set", text: "Verifications are not stopped by spend. Set a limit below if you want them to be." };
  }
  const { monthly_limit: limit, currency } = view.budget;
  const spent = view.spent ?? "0";
  if (view.exceeded) {
    return { tone: "danger", title: "The monthly limit is reached", text: `${spent} of ${limit} ${currency} is used, so no new verification starts this month. Verifications already started still finish.` };
  }
  const pct = percentUsed(spent, limit);
  if (pct >= 80) return { tone: "warn", title: "Close to the monthly limit", text: `${spent} of ${limit} ${currency} is used (${pct}%).` };
  return { tone: "ok", title: "Within the monthly limit", text: `${spent} of ${limit} ${currency} is used (${pct}%).` };
}

const PROBLEMS: Record<string, string> = {
  BUDGET_INVALID: "That limit or currency is not accepted. Use a plain number and a three-letter currency.",
  PROTECTED_ACTION_DENIED: "Your authority does not include managing the verification budget. Ask an owner to grant it.",
  VERIFICATION_BUDGET_EXCEEDED: "The monthly limit is reached, so new verifications are paused until next month or until the limit is raised.",
};

export function problemText(code: string): string {
  return PROBLEMS[code.toUpperCase()] ?? code;
}

/** Outcome words for the usage table. */
export function outcomeText(status: string, outcome: string | null): string {
  return outcome ? `${status.toLowerCase().replaceAll("_", " ")}, ${outcome.toLowerCase().replaceAll("_", " ")}` : status.toLowerCase().replaceAll("_", " ");
}

import type { MyMandate } from "@zoiko/api-client";

/** Only the person a limit was given to uses it, and only while it is switched on. Someone who merely set it up for another person sees it, never spends it. */
export function canUseLimit(m: Pick<MyMandate, "status" | "i_am">): boolean {
  return m.status === "ACTIVE" && m.i_am.includes("executor");
}

export type AmountCheck = { ok: true; amount: string } | { ok: false; reason: string };

const MONEY = /^\d{1,12}(\.\d{1,2})?$/;

/**
 * The amount the person typed, checked against what they hold before anything is sent: a number, more than nothing, within each payment's
 * limit and within what is left. The cell checks again; this only spares the person a refusal they could have seen coming.
 */
export function checkAmount(
  raw: string,
  limits: { perTransaction: string | null; remaining: string | number | null; currency: string | null },
): AmountCheck {
  const text = raw.trim();
  if (!MONEY.test(text)) return { ok: false, reason: "Enter an amount such as 250 or 250.50." };
  const amount = Number(text);
  if (amount <= 0) return { ok: false, reason: "The amount must be more than zero." };
  const unit = limits.currency ? ` ${limits.currency}` : "";
  if (limits.perTransaction !== null && amount > Number(limits.perTransaction)) {
    return { ok: false, reason: `One payment can be at most ${limits.perTransaction}${unit}.` };
  }
  if (limits.remaining !== null && amount > Number(limits.remaining)) {
    return { ok: false, reason: `You only have ${limits.remaining}${unit} left.` };
  }
  return { ok: true, amount: text };
}

/** What is left after this amount, for the line under the box. */
export function leftAfter(remaining: string | number | null, amount: string): string | null {
  if (remaining === null) return null;
  const left = Number(remaining) - Number(amount);
  return Number.isFinite(left) ? left.toFixed(2) : null;
}

/** The reference the person types is what the ledger keeps (as a hash), tied to the limit so two limits never share one. */
export function transactionContext(reference: string, mandateId: string): string {
  return `use|${mandateId}|${reference.trim()}`;
}

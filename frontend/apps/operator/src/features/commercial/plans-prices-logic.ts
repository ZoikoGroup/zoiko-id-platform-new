import type { PriceBookLine, PriceBookSummary } from "@zoiko/api-client";

/** The plain-language side of pricing (CR-147). Nothing here decides a price: the cell validates and applies every change. This file only names things in words a
 * person on the sales or finance team uses, lists what can be priced, reads an amount the way it was typed, and describes the result. */

export const PLAN_LABEL: Record<string, string> = {
  DEVELOPER: "Developer",
  LAUNCH: "Launch",
  BUSINESS: "Business",
  SCALE: "Scale",
  ENTERPRISE: "Enterprise",
};

/** The plans a customer can buy online. Developer is free and automatic; Enterprise is agreed in a contract. */
export const SELF_SERVE_PLANS = ["LAUNCH", "BUSINESS", "SCALE"] as const;

export type PriceItemId = "ZID-BASE" | "ZID-HUM" | "ZID-AUTH" | "ZID-PDP" | "ZID-M2M" | "ZID-VCI" | "ZID-VCP" | "ZID-FED" | "ZID-SCIM";

export type PriceItem = {
  id: PriceItemId;
  /** What the person picks in the first dropdown. */
  label: string;
  /** The unit the amount is for, in words after the amount: "$299.00 per month". */
  per: string;
  /** Plans that have this price. The dropdown shows only these. */
  plans: string[];
  /** The most decimals the amount may have: a monthly fee is a bill amount (2); a usage rate keeps small decimals (6). */
  decimals: 2 | 6;
  help: string;
};

const PAID = ["LAUNCH", "BUSINESS", "SCALE"];

export const PRICE_ITEMS: PriceItem[] = [
  {
    id: "ZID-BASE",
    label: "Monthly plan fee",
    per: "per month",
    plans: [...PAID, "ENTERPRISE"],
    decimals: 2,
    help: "What a company pays every month for the plan itself. For Enterprise it is the minimum, not a fixed fee.",
  },
  { id: "ZID-HUM", label: "Each extra person", per: "per person", plans: PAID, decimals: 6, help: "Charged for every person above the number the plan includes." },
  { id: "ZID-AUTH", label: "Extra sign-ins (per 1,000)", per: "per 1,000 sign-ins", plans: PAID, decimals: 6, help: "Charged for every 1,000 sign-ins above what the plan includes." },
  { id: "ZID-PDP", label: "Extra access decisions (per 10,000)", per: "per 10,000 decisions", plans: PAID, decimals: 6, help: "Charged for every 10,000 access decisions above what the plan includes." },
  { id: "ZID-M2M", label: "Extra machine grants (per 1,000)", per: "per 1,000 grants", plans: PAID, decimals: 6, help: "Charged for every 1,000 machine grants above what the plan includes." },
  { id: "ZID-VCI", label: "Each credential issued", per: "per credential", plans: PAID, decimals: 6, help: "Charged each time a credential is issued." },
  { id: "ZID-VCP", label: "Each credential check", per: "per check", plans: PAID, decimals: 6, help: "Charged each time a credential is checked." },
  { id: "ZID-FED", label: "Each extra single sign-on connection", per: "per connection per month", plans: PAID, decimals: 6, help: "Charged every month for each connection above what the plan includes." },
  { id: "ZID-SCIM", label: "Each extra provisioning source", per: "per source per month", plans: PAID, decimals: 6, help: "Charged every month for each provisioning source above what the plan includes." },
];

export const itemById = (id: string): PriceItem | undefined => PRICE_ITEMS.find((i) => i.id === id);

export const planLabel = (code: string): string => PLAN_LABEL[code] ?? code;

/** The line for one item on one plan in a price book, or undefined if the book has none. */
export function lineOf(lines: PriceBookLine[], item: string, plan: string): PriceBookLine | undefined {
  return lines.find((l) => l.sku === item && l.plan === plan);
}

/** The newest approved price book: the one customers are charged from. The cell lists books newest first; this does not rely on it. */
export function approvedBook(books: PriceBookSummary[]): PriceBookSummary | undefined {
  return books.filter((b) => b.status === "APPROVED").sort((a, b) => b.version - a.version)[0];
}

export type ParsedAmount = { ok: true; value: string } | { ok: false; error: string };

/** An amount typed by a person: digits with an optional decimal part. A leading currency sign and thousands commas are accepted and dropped. The cell checks again. */
export function parseAmount(text: string, item: PriceItem): ParsedAmount {
  const t = text.trim().replace(/^\$/, "").replaceAll(",", "").trim();
  if (t === "") return { ok: false, error: "Type the new price." };
  if (!/^\d+(\.\d+)?$/.test(t)) return { ok: false, error: "Use numbers only, for example 349 or 0.015." };
  const decimals = t.includes(".") ? t.split(".")[1]!.length : 0;
  if (decimals > item.decimals) {
    return { ok: false, error: item.decimals === 2 ? "A monthly fee can have at most two decimals, for example 349.50." : "A rate can have at most six decimals." };
  }
  const n = Number(t);
  if (!Number.isFinite(n) || n <= 0) return { ok: false, error: "The price must be more than zero." };
  if (n > 1_000_000) return { ok: false, error: "That is more than 1,000,000. Check the amount." };
  return { ok: true, value: t };
}

/** A price in words: "$299.00" for a fee, "$0.015" for a small rate (small decimals are kept, never rounded to cents). */
export function priceText(amount: string, currency: string): string {
  const n = Number(amount);
  if (!Number.isFinite(n)) return amount;
  return new Intl.NumberFormat("en-US", { style: "currency", currency, currencyDisplay: "symbol", minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(n);
}

/** How far the new price is from the current one, so a slip such as an extra zero is noticed before it is saved. null when there is nothing to compare. */
export function bigChange(current: string | null, next: string): "much-higher" | "much-lower" | null {
  if (current === null) return null;
  const a = Number(current);
  const b = Number(next);
  if (!Number.isFinite(a) || !Number.isFinite(b) || a <= 0) return null;
  if (b >= a * 2) return "much-higher";
  if (b <= a / 2) return "much-lower";
  return null;
}

/** The one sentence that says what will happen, in the words a customer would use. */
export function changeSentence(item: PriceItem, plan: string, current: string | null, next: string, currency: string): string {
  const to = `${priceText(next, currency)} ${item.per}`;
  const from = current === null ? null : `${priceText(current, currency)} ${item.per}`;
  const what = `${planLabel(plan)}: ${item.label.toLowerCase()}`;
  return from === null ? `${what} will be ${to}.` : `${what} changes from ${from} to ${to}.`;
}

export type CheckItem = { id: string; ok: boolean; label: string; detail: string };

/** Is the pricing ready for customers to buy a plan online? Each line is a plain yes or no with what to do about a no. */
export function setupChecklist(book: PriceBookSummary | undefined, lines: PriceBookLine[]): CheckItem[] {
  const out: CheckItem[] = [
    {
      id: "book",
      ok: Boolean(book),
      label: "Prices are published",
      detail: book ? `Price book version ${book.version} is approved and is what customers are charged from.` : "No price book is approved yet. Approve one in Price books (advanced).",
    },
  ];
  for (const plan of SELF_SERVE_PLANS) {
    const fee = lineOf(lines, "ZID-BASE", plan);
    const ok = Boolean(fee) && Number(fee!.unit_rate) > 0;
    out.push({
      id: plan,
      ok,
      label: `${planLabel(plan)} has a monthly fee`,
      detail: ok ? `${priceText(fee!.unit_rate, book?.currency ?? "USD")} per month.` : `Set the ${planLabel(plan)} monthly fee below.`,
    });
  }
  return out;
}

/** Customers can buy online only when every check passes. */
export const readyToSell = (checks: CheckItem[]): boolean => checks.every((c) => c.ok);

/** How far below the list price an agreed price is, as a percentage with two decimals ("9.98"). Zero when it is the list price. */
export function reductionPercent(list: string, amount: string): string {
  const l = Number(list);
  const a = Number(amount);
  if (!Number.isFinite(l) || !Number.isFinite(a) || l <= 0 || a >= l) return "0.00";
  return (((l - a) / l) * 100).toFixed(2);
}

export const OFFER_DAYS = [
  { id: "7", label: "7 days" },
  { id: "14", label: "14 days" },
  { id: "30", label: "30 days" },
  { id: "60", label: "60 days" },
];

/** Why an offered price cannot be sent, or null. Typed by hand, never above the list price, and the shape of an amount only: the cell and the discount authority decide the rest. */
export function offerProblem(text: string, list: string | null): string | null {
  if (list === null) return "There is no list price for this plan yet.";
  const parsed = parseAmount(text, PRICE_ITEMS[0]!);
  if (!parsed.ok) return parsed.error;
  if (Number(parsed.value) > Number(list)) return `A price for one company cannot be above the list price (${priceText(list, "USD")}). The company can buy at the list price without an offer.`;
  return null;
}

/** The one sentence that says what an offer means, in the words the company will read. */
export function offerSentence(company: string, plan: string, amount: string, list: string, currency: string, days: string): string {
  const pct = reductionPercent(list, amount);
  const below = Number(pct) > 0 ? `, which is ${pct}% below the list price of ${priceText(list, currency)}` : ", which is the list price";
  return `${company} can buy the ${planLabel(plan)} plan for ${priceText(amount, currency)} a month${below}, for the next ${days} days. They pay that amount on Stripe, and the plan switches on only after the payment is confirmed.`;
}

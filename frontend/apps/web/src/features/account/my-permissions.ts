import type { MyGrant } from "@zoiko/api-client";

import { ACTION_GROUPS } from "../admin/authority/choice-list";

/** The words a person reads for an action id: "Release a payment", never "payment.release". An action the console has no words for is shown as it is. */
export function actionLabel(id: string): string {
  for (const g of ACTION_GROUPS) {
    const hit = g.choices.find((c) => c.id === id);
    if (hit) return hit.label;
  }
  return id;
}

/** What a permission lets the person do, in one line each; `null` actions means everything the company allows. */
export function whatItAllows(g: Pick<MyGrant, "actions">): string[] {
  if (g.actions === null) return ["Everything the company allows"];
  return g.actions.map(actionLabel);
}

/** "Expires in 5 days", "Expires today", "Expired", or "No end date": plain, relative to `now`. */
export function expiryText(validTo: string | null, now: Date = new Date()): string {
  if (!validTo) return "No end date";
  const end = new Date(validTo);
  if (Number.isNaN(end.getTime())) return "No end date";
  const days = Math.floor((end.getTime() - now.getTime()) / 86_400_000);
  if (end.getTime() <= now.getTime()) return "Expired";
  if (days < 1) return "Expires today";
  return days === 1 ? "Expires tomorrow" : `Expires in ${days} days`;
}

/** The money limit on a permission, or null when it has none. */
export function limitText(g: Pick<MyGrant, "value_limit">): string | null {
  return g.value_limit ? `Up to ${g.value_limit.amount} ${g.value_limit.currency}` : null;
}

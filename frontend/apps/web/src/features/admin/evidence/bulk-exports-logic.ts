const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const MAX_RECEIPTS = 100;

/** The receipt ids typed or pasted (one per line, or separated by commas or spaces), de-duplicated, with what is wrong with them. */
export function parseReceiptIds(text: string): { ids: string[]; invalid: string[]; tooMany: boolean } {
  const parts = text
    .split(/[\s,;]+/)
    .map((p) => p.trim())
    .filter(Boolean);
  const ids = [...new Set(parts.filter((p) => UUID.test(p)).map((p) => p.toLowerCase()))];
  const invalid = [...new Set(parts.filter((p) => !UUID.test(p)))];
  return { ids, invalid, tooMany: ids.length > MAX_RECEIPTS };
}

export const EXPIRIES: { id: string; label: string; minutes: number }[] = [
  { id: "60", label: "1 hour", minutes: 60 },
  { id: "1440", label: "24 hours", minutes: 1440 },
  { id: "10080", label: "7 days", minutes: 10080 },
];

/** "Available for 3 h 20 min" or "Expired": what is left of a package's download window. */
export function timeLeft(expiresAt: string, now: number = Date.now()): string {
  const ms = Date.parse(expiresAt) - now;
  if (!Number.isFinite(ms) || ms <= 0) return "Expired";
  const min = Math.floor(ms / 60000);
  if (min < 60) return `${Math.max(min, 1)} min left`;
  const h = Math.floor(min / 60);
  return h < 48 ? `${h} h ${min % 60} min left` : `${Math.floor(h / 24)} days left`;
}

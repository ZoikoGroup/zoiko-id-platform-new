/** The label prefix a developer typed, made safe for the API (lower-case letters, digits and dashes, starting with a letter). */
export function cleanPrefix(raw: string): string {
  const p = raw
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^[^a-z]+/, "")
    .replace(/-+$/, "")
    .slice(0, 31);
  return p || "test";
}

export const LIFETIMES = [
  { id: "1", label: "1 day" },
  { id: "7", label: "7 days" },
  { id: "14", label: "14 days" },
  { id: "30", label: "30 days" },
  { id: "90", label: "90 days" },
];

/** How many more can be made: what is left of the limit, capped at one request's worth. */
export function room(live: number, maxLive: number, perRequest = 20): number {
  return Math.max(0, Math.min(perRequest, maxLive - live));
}

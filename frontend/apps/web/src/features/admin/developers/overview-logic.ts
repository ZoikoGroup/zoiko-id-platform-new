/** The plain-language side of the identity verification overview: the success rate, and the short list of things that need attention. Pure, so a test can read it. */

export type Counts = { sessions_started: number; verified: number; not_verified: number; inconclusive: number };

/** The share of decided verifications that ended verified. Verifications still open are not counted: they have no answer yet. Null when nothing is decided. */
export function successRate(c: Pick<Counts, "verified" | "not_verified" | "inconclusive">): number | null {
  const decided = c.verified + c.not_verified + c.inconclusive;
  return decided === 0 ? null : c.verified / decided;
}

export const percent = (r: number | null): string => (r === null ? "-" : `${Math.round(r * 100)}%`);

/** A verification that nobody has finished: the person has not opened the link, or is part way through it. */
const OPEN = new Set(["created", "ready", "opened", "capture_started", "submitted", "processing", "action_required"]);
export const isWaiting = (status: string): boolean => OPEN.has(status);

export type Note = { tone: "warn" | "info" | "ok"; text: string; go?: { tab: string; label: string } };

export function attention(a: { productionOpen: boolean; activeWebhooks: number; waiting: number; inconclusive: number; verified: number }): Note[] {
  const notes: Note[] = [];
  if (!a.productionOpen) notes.push({ tone: "info", text: "Production is closed. Real customers cannot be verified yet.", go: { tab: "verify-api", label: "See what is needed" } });
  if (a.activeWebhooks === 0) notes.push({ tone: "warn", text: "No webhook is active, so results will not reach your server by themselves.", go: { tab: "endpoints", label: "Set up a webhook" } });
  if (a.waiting > 0) notes.push({ tone: "info", text: a.waiting === 1 ? "1 verification is waiting for a person to finish it." : `${a.waiting} verifications are waiting for a person to finish them.` });
  if (a.inconclusive > 0) {
    notes.push({
      tone: "warn",
      text: a.inconclusive === 1 ? "1 verification ended without an answer. Open it to see why." : `${a.inconclusive} verifications ended without an answer. Open them to see why.`,
      go: { tab: "results", label: "Open the verifications" },
    });
  }
  if (notes.length === 0 && a.verified > 0) notes.push({ tone: "ok", text: "Everything is working: production is open, results reach your server, and nothing is waiting." });
  return notes;
}

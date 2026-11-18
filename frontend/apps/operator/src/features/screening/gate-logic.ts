import type { ScreeningGateModule, ScreeningGateStatus } from "@zoiko/api-client";

export const MODULES: { id: ScreeningGateModule; label: string }[] = [
  { id: "SANCTIONS", label: "Sanctions" },
  { id: "PEP_RCA", label: "Politically exposed persons and associates" },
  { id: "ADVERSE_MEDIA", label: "Adverse media" },
  { id: "MONITORING", label: "Ongoing monitoring" },
];

export const GATES = ["G0", "G1", "G2", "G3"] as const;
export const GATE_MEANING: Record<string, string> = {
  G0: "Lawful basis and scope approved",
  G1: "Data and match quality evidenced",
  G2: "Security and privacy controls evidenced",
  G3: "Operations ready",
};

export function validMarket(m: string): boolean {
  return /^[A-Za-z]{2}$/.test(m.trim());
}

/** Why a signature cannot be sent yet, or null. It must cite its evidence and a validity of at most a year. */
export function signProblem(evidence: string, days: number): string | null {
  if (evidence.trim().length === 0) return "Cite the evidence you are signing on.";
  if (!Number.isInteger(days) || days < 1 || days > 365) return "A signature is valid for 1 to 365 days.";
  return null;
}

export function reasonProblem(text: string): string | null {
  return text.trim().length < 3 ? "Say what it is (at least three characters)." : null;
}

const BLOCKERS: Record<string, string> = {
  OPEN_P0_DEFECTS: "An open critical defect stops every gate until it is closed.",
  HELD: "A hold is in place: it must be released by someone other than the person who placed it.",
};

/** A blocker as a sentence: gate blockers name the missing roles, defect and hold blockers carry a count or a reason after a colon. */
export function blockerText(b: string): string {
  const [code, rest] = b.split(/:(.*)/s);
  if (code && BLOCKERS[code]) return rest ? `${BLOCKERS[code]} (${rest.trim()})` : BLOCKERS[code]!;
  return b;
}

export function headline(s: Pick<ScreeningGateStatus, "go" | "blockers">): { tone: "ok" | "warn"; text: string } {
  return s.go ? { tone: "ok", text: "GO: every gate is signed by its named roles, with no open defect and no hold." } : { tone: "warn", text: `NOT GO: ${s.blockers.length} thing${s.blockers.length === 1 ? "" : "s"} still stop this module in this market.` };
}

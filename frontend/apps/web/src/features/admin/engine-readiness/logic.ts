import type { EngineComponent, EngineReadiness } from "@zoiko/api-client";

export const READINESS_KEY = ["engine-readiness"];

export type Owner = "Legal" | "Privacy" | "Security and the evaluation lab" | "Product and engineering";
export type Item = { code: string; owner: Owner; text: string };

const FIXED: Record<string, { owner: Owner; text: string }> = {
  ENGINE_COMPONENTS_MISSING: { owner: "Product and engineering", text: "The engine record does not list the libraries and pretrained models it is built from, so no licence can be checked." },
  ENGINE_SCOPE_MISSING: { owner: "Product and engineering", text: "No country is approved. An evaluation covers a population, not the world: name the countries it was evaluated for." },
  ENGINE_ID_MISSING: { owner: "Product and engineering", text: "The engine has no identifier." },
  ENGINE_VERSION_MISSING: { owner: "Product and engineering", text: "The exact engine and model version is not recorded." },
  EVALUATION_ID_MISSING: { owner: "Security and the evaluation lab", text: "There is no reference to the independent evaluation of this engine and version." },
  DPIA_REFERENCE_MISSING: { owner: "Privacy", text: "There is no reference to an approved data protection impact assessment." },
  LICENCE_CHECK_NOT_RECORDED: { owner: "Legal", text: "Nobody has recorded reading the engine's licences, including those of the pretrained models." },
  PAD_EVALUATION_STANDARD_NOT_RECORDED: { owner: "Security and the evaluation lab", text: "The presentation-attack test is not recorded as ISO/IEC 30107-3 by an independent lab." },
  EVALUATION_DATE_INVALID: { owner: "Security and the evaluation lab", text: "The evaluation date is missing or not a date." },
  EVALUATION_DATE_IN_THE_FUTURE: { owner: "Security and the evaluation lab", text: "The evaluation date is in the future." },
  EVALUATION_TOO_OLD: { owner: "Security and the evaluation lab", text: "The evaluation is more than a year old and has expired. It has to be repeated." },
  FACE_THRESHOLD_INVALID: { owner: "Product and engineering", text: "The face-match threshold is missing or below the minimum." },
  LIVENESS_THRESHOLD_INVALID: { owner: "Product and engineering", text: "The liveness threshold is missing or below the minimum." },
};

const RATE_WORDS: Record<string, string> = {
  FALSE_ACCEPT: "false accept rate",
  FALSE_REJECT: "false reject rate",
  ATTACK_ACCEPT: "presentation attack accept rate",
  BONA_FIDE_REJECT: "genuine-user reject rate",
};

const COMPONENT = /^COMPONENT_(.+?)_(NOT_IN_LICENCE_REGISTER|COMMERCIAL_USE_NOT_CONFIRMED|MODEL_LICENCE_NOT_CONFIRMED|LICENCE_NOT_VERIFIED)$/;
const MEASURED = /^MEASURED_(.+)_(MISSING|ABOVE_CEILING)$/;

/** A reason the engine cannot be approved, in words, with who normally owns closing it. An unknown code shows as itself under Product and engineering. */
export function explain(code: string): Item {
  const fixed = FIXED[code];
  if (fixed) return { code, ...fixed };
  const c = COMPONENT.exec(code);
  if (c) {
    const name = (c[1] ?? "").toLowerCase().replaceAll("_", "-");
    const what = {
      NOT_IN_LICENCE_REGISTER: "is not in the licence register.",
      COMMERCIAL_USE_NOT_CONFIRMED: "is not confirmed for commercial use.",
      MODEL_LICENCE_NOT_CONFIRMED: "has no confirmation that its pretrained-model licence was read.",
      LICENCE_NOT_VERIFIED: "has no date on which a person verified its licence.",
    }[c[2] as "NOT_IN_LICENCE_REGISTER"];
    return { code, owner: "Legal", text: `The component “${name}” ${what}` };
  }
  const m = MEASURED.exec(code);
  if (m) {
    const word = RATE_WORDS[m[1] ?? ""] ?? (m[1] ?? "").toLowerCase().replaceAll("_", " ");
    return { code, owner: "Security and the evaluation lab", text: m[2] === "MISSING" ? `The measured ${word} is not recorded.` : `The measured ${word} is above the ceiling this platform allows.` };
  }
  return { code, owner: "Product and engineering", text: code };
}

export function groupByOwner(codes: string[]): { owner: Owner; items: Item[] }[] {
  const order: Owner[] = ["Legal", "Privacy", "Security and the evaluation lab", "Product and engineering"];
  const items = codes.map(explain);
  return order.map((owner) => ({ owner, items: items.filter((i) => i.owner === owner) })).filter((g) => g.items.length > 0);
}

export const MODE_WORDS: Record<EngineReadiness["mode"], { label: string; text: string }> = {
  DISABLED: { label: "Off", text: "No face engine runs. Identity verification works without one: the document chip is checked and the person is not matched to a photo." },
  LAB_ONLY: { label: "Lab only", text: "An engine may be evaluated, but it is never used for a live decision." },
  APPROVED_FOR_SCOPE: { label: "Approved for the listed countries", text: "The engine decides only for sessions in the countries below." },
};

export type LicenceState = { label: string; tone: "ok" | "warn" | "danger" | "neutral" };

/** One component's licence record in a word: usable, not for commercial use, or not read yet. Nothing here says a licence is fine unless a person recorded reading it. */
export function licenceState(c: EngineComponent): LicenceState {
  if (c.commercial_use.toUpperCase() === "NO") return { label: "Not for commercial use", tone: "danger" };
  if (c.commercial_use.toUpperCase() === "YES" && c.model_licence_confirmed && c.licence_verified_on) return { label: `Verified ${c.licence_verified_on}`, tone: "ok" };
  if (c.commercial_use.toUpperCase() === "YES") return { label: "Partly recorded", tone: "warn" };
  return { label: "Not read yet", tone: "neutral" };
}

export function percent(rate: number | null): string {
  return rate === null ? "not recorded" : `${(rate * 100).toFixed(rate < 0.01 ? 3 : 1)}%`;
}

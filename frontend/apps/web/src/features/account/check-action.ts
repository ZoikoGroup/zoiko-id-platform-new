import type { EvaluationRequest, EvaluationResult } from "@zoiko/api-client";

/** The question a Member asks about themselves: may I do this, for this much? Only ever about the signed-in person. */
export function buildCheck(input: { me: string; action: string; reference: string; amount: string; currency: string | null; mandateId: string | null }): EvaluationRequest {
  const amount = input.amount.trim();
  const context: Record<string, unknown> = {
    ...(input.mandateId ? { mandate_id: input.mandateId } : {}),
    ...(amount && input.currency ? { value: { amount, currency: input.currency } } : {}),
  };
  return {
    subject: { type: "principal", id: input.me },
    action: { name: input.action },
    resource: { type: "transaction", id: input.reference.trim() || "check" },
    ...(Object.keys(context).length ? { context: context as EvaluationRequest["context"] } : {}),
  };
}

export type Verdict = { tone: "ok" | "danger" | "warn" | "info"; title: string; why: string[] };

const REASONS: Record<string, string> = {
  AUTHORITY_MISSING: "No one has given you permission for this.",
  MANDATE_SCOPE_EXCEEDED: "This is outside what your limit covers.",
  MANDATE_VALUE_EXCEEDED: "The amount is more than your limit allows.",
  MANDATE_EXHAUSTED: "Your limit has been used up.",
  MANDATE_NOT_ACTIVE: "Your limit is not switched on.",
  TENANT_NOT_ENFORCING: "The company has not finished setting up, so nothing can be allowed yet.",
};

/** A reason in words; a code the console has no words for is kept as it is so nothing is hidden. */
export function reasonText(code: string): string {
  return REASONS[code] ?? code;
}

/** The answer in plain words: Allowed, Not allowed, or what has to happen first. */
export function verdictOf(r: EvaluationResult): Verdict {
  const why = (r.context.reason_codes ?? []).map(reasonText);
  switch (r.context.outcome) {
    case "STEP_UP":
      return { tone: "warn", title: "Allowed after you confirm it is you", why: why.length ? why : ["This action needs a fresh passkey check first."] };
    case "APPROVAL_REQUIRED":
      return { tone: "info", title: "Needs a second person to approve", why: why.length ? why : ["An owner or administrator must approve this first."] };
    case "STATE_UNAVAILABLE":
      return { tone: "warn", title: "Could not be checked right now", why: ["The check could not be completed, so nothing is allowed. Try again shortly."] };
    default:
      return r.decision ? { tone: "ok", title: "Allowed", why } : { tone: "danger", title: "Not allowed", why };
  }
}

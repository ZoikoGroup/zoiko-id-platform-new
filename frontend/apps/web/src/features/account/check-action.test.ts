/** "Check an action": the question is always about the signed-in person, and the answer is in plain words. */
import type { EvaluationResult } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { buildCheck, reasonText, verdictOf } from "./check-action";

const result = (decision: boolean, context: Partial<EvaluationResult["context"]> = {}): EvaluationResult => ({ decision, context: { decision_id: "d-1", reason_codes: [], ...context } });

describe("the question", () => {
  it("is about the person asking, for one action", () => {
    const q = buildCheck({ me: "p-1", action: "invoice.approve", reference: " INV-7 ", amount: "", currency: null, mandateId: null });
    expect(q.subject).toEqual({ type: "principal", id: "p-1" });
    expect(q.action.name).toBe("invoice.approve");
    expect(q.resource.id).toBe("INV-7");
    expect(q.context).toBeUndefined();
  });
  it("carries the amount and the limit when given", () => {
    const q = buildCheck({ me: "p-1", action: "payment.release", reference: "", amount: "250", currency: "GBP", mandateId: "m-1" });
    expect(q.context).toEqual({ mandate_id: "m-1", value: { amount: "250", currency: "GBP" } });
    expect(q.resource.id).toBe("check");
  });
});

describe("the answer", () => {
  it("says Allowed or Not allowed, with the reason in words", () => {
    expect(verdictOf(result(true, { outcome: "ALLOW" }))).toMatchObject({ tone: "ok", title: "Allowed" });
    expect(verdictOf(result(false, { outcome: "DENY", reason_codes: ["AUTHORITY_MISSING"] }))).toEqual({ tone: "danger", title: "Not allowed", why: ["No one has given you permission for this."] });
  });
  it("says what has to happen first for a step-up or an approval", () => {
    expect(verdictOf(result(false, { outcome: "STEP_UP" })).title).toBe("Allowed after you confirm it is you");
    expect(verdictOf(result(false, { outcome: "APPROVAL_REQUIRED" })).title).toBe("Needs a second person to approve");
  });
  it("refuses, rather than allows, when the check could not be made", () => {
    expect(verdictOf(result(false, { outcome: "STATE_UNAVAILABLE" })).tone).toBe("warn");
  });
  it("keeps a code it has no words for", () => {
    expect(reasonText("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
});

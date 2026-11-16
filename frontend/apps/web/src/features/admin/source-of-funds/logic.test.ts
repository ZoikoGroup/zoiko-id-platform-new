/** Source of funds screens (DTV-28, CR-096): wording never overstates, and each state offers only the decisions the platform allows. */
import { ApiError, type SofQuestion, type SofState } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { REASONS, answerText, canAppeal, classify, decisionOptions, problemText, reasonOptions, stateText } from "./logic";

const ALL: SofState[] = ["DRAFT", "INCOMPLETE", "NEEDS_REVIEW", "CLEARED", "NOT_CLEARED_PENDING_APPEAL", "APPEALED", "NOT_CLEARED_FINAL", "CANCELED"];
const err = (status: number, code: string) => new ApiError({ status, code, message: code });

describe("what a case is called", () => {
  it("never shows a lapsed clearance as cleared", () => {
    const past = new Date(Date.now() - 86_400_000).toISOString();
    const future = new Date(Date.now() + 86_400_000).toISOString();
    expect(stateText("CLEARED", future).label).toBe("Cleared");
    expect(stateText("CLEARED", past).label).toBe("Clearance expired");
    expect(stateText("CLEARED", past).tone).toBe("warn");
    expect(stateText("CLEARED", null).label).toBe("Cleared");
  });

  it("never calls a waiting or an appealed case cleared, and never calls a first adverse decision final", () => {
    expect(stateText("NEEDS_REVIEW", null).label).not.toMatch(/cleared/i);
    expect(stateText("APPEALED", null).label).not.toMatch(/^cleared/i);
    expect(stateText("NOT_CLEARED_PENDING_APPEAL", null).label).not.toMatch(/final/i);
    expect(stateText("NOT_CLEARED_PENDING_APPEAL", null).meaning).toMatch(/not final/i);
    expect(stateText("NOT_CLEARED_FINAL", null).label).toMatch(/final/i);
  });

  it("has words and a tone for every state", () => {
    for (const s of ALL) {
      const t = stateText(s, null);
      expect(t.label.length).toBeGreaterThan(2);
      expect(t.meaning.length).toBeGreaterThan(10);
    }
  });
});

describe("what a reviewer may decide", () => {
  it("offers a decision only for a case waiting for one, or an appeal", () => {
    for (const s of ALL) {
      const options = decisionOptions(s);
      if (s === "NEEDS_REVIEW") expect(options).toEqual(["CLEAR", "NOT_CLEAR", "ASK_FOR_MORE"]);
      else if (s === "APPEALED") expect(options).toEqual(["CLEAR", "NOT_CLEAR"]); // an appeal cannot ask for more
      else expect(options).toEqual([]);
    }
  });

  it("offers appeal reasons for an appeal and the other reasons for a first decision, never mixed", () => {
    const first = reasonOptions("NEEDS_REVIEW");
    const appeal = reasonOptions("APPEALED");
    expect(first.some((c) => c.startsWith("APPEAL_"))).toBe(false);
    expect(appeal.every((c) => c.startsWith("APPEAL_"))).toBe(true);
    for (const c of [...first, ...appeal]) expect(REASONS[c]).toBeTruthy();
  });

  it("allows an appeal only against a first adverse decision", () => {
    expect(ALL.filter(canAppeal)).toEqual(["NOT_CLEARED_PENDING_APPEAL"]);
  });
});

describe("answers and problems in words", () => {
  const amount = { question_id: "SOF-02", text: "How much?", answer: "AMOUNT", required: true, choices: [], evidence_required_for: {} } as SofQuestion;
  const yesNo = { ...amount, question_id: "SOF-04", answer: "YES_NO" } as SofQuestion;

  it("keeps an amount as the exact decimal string it was sent as", () => {
    expect(answerText(amount, { value: "40000.00", currency: "GBP" })).toBe("40000.00 GBP");
    expect(answerText(amount, { value: "0.10", currency: "USD" })).toBe("0.10 USD");
  });

  it("reads yes and no, text, and a missing answer", () => {
    expect(answerText(yesNo, false)).toBe("No");
    expect(answerText(yesNo, true)).toBe("Yes");
    expect(answerText(undefined, "Salary")).toBe("Salary");
    expect(answerText(undefined, undefined)).toBe("No answer");
  });

  it("turns each problem code into a sentence and shows an unknown code as itself", () => {
    expect(problemText("ANSWER_MISSING:SOF-03")).toBe("No answer to SOF-03.");
    expect(problemText("EVIDENCE_MISSING:SOF-01:PAYSLIP")).toBe("SOF-01 needs evidence of type payslip.");
    expect(problemText("EVIDENCE_MISSING:SOF-01:BANK_STATEMENT")).toBe("SOF-01 needs evidence of type bank statement.");
    expect(problemText("EVIDENCE_UPLOAD_NOT_USABLE:SOF-01")).toMatch(/not found/);
    expect(problemText("SOMETHING_NEW:X")).toBe("SOMETHING_NEW:X");
  });
});

describe("what a refusal means", () => {
  it("reads the switched-off product line as not available, never as an empty list", () => {
    expect(classify(err(409, "service_unavailable"))).toBe("off");
    expect(classify(err(409, "service_unavailable_in_market"))).toBe("off");
  });

  it("separates a forbidden role from a missing case and from everything else", () => {
    expect(classify(err(403, "protected_action_denied"))).toBe("forbidden");
    expect(classify(err(404, "case_not_found"))).toBe("missing");
    expect(classify(err(500, "boom"))).toBe("other");
    expect(classify(new Error("network"))).toBe("other");
  });
});
